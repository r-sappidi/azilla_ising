#include "ramulator.hpp"

#include <dlfcn.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>

namespace azilla {

namespace {
template <typename T>
T resolve(void* handle, const char* name) {
    void* symbol = dlsym(handle, name);
    if (!symbol) throw std::runtime_error(std::string("missing Ramulator bridge symbol ") + name);
    return reinterpret_cast<T>(symbol);
}

inline void cpu_relax() {
#if defined(__x86_64__) || defined(__i386__)
    __builtin_ia32_pause();
#else
    std::this_thread::yield();
#endif
}

// How long an idle worker spins before it blocks on the condition variable.
// Between two tick dispatches the calling thread runs the serial model logic
// (tens of microseconds per accelerator cycle), so a budget of a few
// milliseconds keeps workers hot in steady state while long serial phases
// (dataset load, publication replay, metrics) put them to sleep.
long spin_budget_us() {
    const char* env = std::getenv("AZILLA_DRAM_SPIN_US");
    if (env && *env) {
        long value = std::atol(env);
        if (value >= 0) return value;
    }
    return 2000;
}

}  // namespace

RamulatorBackend::RamulatorBackend(const std::string& library, const std::string& config,
                                   const std::string& dataset, int system_count_, int block_count,
                                   bool timing_only, int dram_threads_)
    : system_count(system_count_), dram_threads(dram_threads_) {
    if (dram_threads < 1) throw std::runtime_error("--dram-threads must be at least 1");
    std::string resolved = std::filesystem::absolute(library).lexically_normal().string();
    handle_ = dlopen(resolved.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!handle_) throw std::runtime_error(std::string("cannot load Ramulator bridge: ") + dlerror());
    auto init = resolve<void (*)(const char*, const char*, int, int)>(
        handle_, timing_only ? "az_dram_init_timing" : "az_dram_init");
    send_ = resolve<int (*)(int, uint64_t, int)>(handle_, "az_dram_send");
    tick_ = resolve<void (*)(int)>(handle_, "az_dram_tick");
    pop_ = resolve<int (*)(int, uint64_t*, int*, uint32_t*)>(handle_, "az_dram_pop");
    stats_ = resolve<int (*)(int, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*)>(handle_,
                                                                                            "az_dram_get_stats");
    finalize_ = resolve<void (*)()>(handle_, "az_dram_finalize");
    if (dram_threads > 1) {
        if (!dlsym(handle_, "az_dram_tick_system") || !dlsym(handle_, "az_dram_advance_clock"))
            throw std::runtime_error("--dram-threads > 1 needs a Ramulator bridge with per-system ticking "
                                     "(az_dram_tick_system / az_dram_advance_clock): build and pass "
                                     "libazilla_ramulator_mt.so (model/cpp_exact/ramulator_bridge_mt.cpp)");
        tick_system_ = resolve<void (*)(int, int)>(handle_, "az_dram_tick_system");
        advance_clock_ = resolve<void (*)(int)>(handle_, "az_dram_advance_clock");
    }
    std::string config_abs = std::filesystem::absolute(config).lexically_normal().string();
    std::string dataset_abs = std::filesystem::absolute(dataset).lexically_normal().string();
    init(config_abs.c_str(), dataset_abs.c_str(), system_count, block_count);
    if (dram_threads > 1) {
        worker_errors_.assign(size_t(dram_threads), nullptr);
        for (int worker = 1; worker < dram_threads; ++worker)
            workers_.emplace_back([this, worker] { worker_loop(worker); });
    }
}

RamulatorBackend::~RamulatorBackend() {
    stop_pool();
    finalize();
    if (handle_) dlclose(handle_);
}

void RamulatorBackend::stop_pool() {
    if (workers_.empty()) return;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stop_.store(true);
    }
    wake_.notify_all();
    for (auto& worker : workers_) worker.join();
    workers_.clear();
}

bool RamulatorBackend::send(int system, uint64_t address, int tag) { return send_(system, address, tag) != 0; }

void RamulatorBackend::tick(long count) {
    // The bridge takes an int; split very long idle advances defensively.
    while (count > 0) {
        int step = count > 1000000000L ? 1000000000 : int(count);
        if (dram_threads > 1) tick_parallel(step);
        else tick_serial(step);
        count -= step;
    }
}

void RamulatorBackend::tick_serial(int count) { tick_(count); }

// Work distribution: systems are claimed one at a time through an atomic
// counter (first come, first served).  Which thread ticks a system does not
// affect results because the systems are independent; it only balances the
// load when per-system tick costs differ.
void RamulatorBackend::tick_share(int worker, int count) {
    (void)worker;
    for (;;) {
        int system = next_system_.fetch_add(1, std::memory_order_relaxed);
        if (system >= system_count) return;
        tick_system_(system, count);
    }
}

void RamulatorBackend::worker_loop(int worker) {
    using clock = std::chrono::steady_clock;
    const auto budget = std::chrono::microseconds(spin_budget_us());
    uint64_t seen = 0;
    for (;;) {
        // Wait for the next dispatch: spin, then block.
        auto spin_start = clock::now();
        int iteration = 0;
        for (;;) {
            if (stop_.load(std::memory_order_acquire)) return;
            uint64_t generation = generation_.load(std::memory_order_acquire);
            if (generation != seen) {
                seen = generation;
                break;
            }
            cpu_relax();
            if (++iteration == 256) {
                iteration = 0;
                if (clock::now() - spin_start >= budget) {
                    std::unique_lock<std::mutex> lock(mutex_);
                    sleepers_.fetch_add(1, std::memory_order_seq_cst);
                    wake_.wait(lock, [&] {
                        return generation_.load(std::memory_order_seq_cst) != seen ||
                               stop_.load(std::memory_order_seq_cst);
                    });
                    sleepers_.fetch_sub(1, std::memory_order_seq_cst);
                    spin_start = clock::now();
                }
            }
        }
        try {
            tick_share(worker, pending_count_.load(std::memory_order_acquire));
        } catch (...) {
            worker_errors_[size_t(worker)] = std::current_exception();
        }
        remaining_.fetch_sub(1, std::memory_order_acq_rel);
    }
}

void RamulatorBackend::tick_parallel(int count) {
    const int worker_count = int(workers_.size());
    pending_count_.store(count, std::memory_order_relaxed);
    next_system_.store(0, std::memory_order_relaxed);
    remaining_.store(worker_count, std::memory_order_relaxed);
    generation_.fetch_add(1, std::memory_order_seq_cst);
    ++parallel_dispatches;
    if (sleepers_.load(std::memory_order_seq_cst) > 0) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
        }
        wake_.notify_all();
        ++parallel_dispatches_with_sleeper;
    }
    tick_share(0, count);
    while (remaining_.load(std::memory_order_acquire) != 0) cpu_relax();
    for (auto& error : worker_errors_)
        if (error) {
            std::exception_ptr pending = error;
            error = nullptr;
            std::rethrow_exception(pending);
        }
    advance_clock_(count);
}

std::optional<MemoryResponse> RamulatorBackend::pop(int system) {
    uint64_t address = 0;
    int tag = 0;
    uint32_t words[8];
    if (!pop_(system, &address, &tag, words)) return std::nullopt;
    return MemoryResponse{tag};
}

RamulatorSystemStats RamulatorBackend::stats(int system) {
    RamulatorSystemStats s;
    if (!stats_(system, &s.accepted, &s.rejected, &s.completed, &s.latency_sum_ticks, &s.latency_max_ticks))
        throw std::out_of_range("Ramulator system is out of range");
    return s;
}

void RamulatorBackend::finalize() {
    if (!closed_) {
        stop_pool();
        finalize_();
        closed_ = true;
    }
}

}  // namespace azilla
