// Port of model/azilla_cycle_model/ramulator.py: dlopen binding to the C ABI
// of build/cycle_model_ramulator/libazilla_ramulator.so (tb/ramulator_dpi.cpp).
// DRAM timing is shared with the Python model and the RTL testbench, not
// reimplemented.
//
// Optional threaded ticking (dram_threads > 1): the independent memory
// systems are ticked by a persistent pool of worker threads, each owning a
// disjoint, fixed subset of systems, through the per-system entry
// az_dram_tick_system of model/cpp_exact/ramulator_bridge_mt.cpp
// (libazilla_ramulator_mt.so).  send / pop / stats stay on the calling thread
// in program order, so model results are bit-identical by construction.
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "memory.hpp"

namespace azilla {

struct RamulatorSystemStats {
    uint64_t accepted = 0;
    uint64_t rejected = 0;
    uint64_t completed = 0;
    uint64_t latency_sum_ticks = 0;
    uint64_t latency_max_ticks = 0;
    uint64_t outstanding() const { return accepted - completed; }
    double average_latency_ticks() const {
        return completed ? double(latency_sum_ticks) / double(completed) : 0.0;
    }
};

class RamulatorBackend {
public:
    RamulatorBackend(const std::string& library, const std::string& config, const std::string& dataset,
                     int system_count, int block_count, bool timing_only, int dram_threads = 1);
    ~RamulatorBackend();
    RamulatorBackend(const RamulatorBackend&) = delete;
    RamulatorBackend& operator=(const RamulatorBackend&) = delete;

    bool send(int system, uint64_t address, int tag);
    void tick(long count);
    std::optional<MemoryResponse> pop(int system);
    RamulatorSystemStats stats(int system);
    void finalize();

    int system_count;
    int dram_threads = 1;
    // Number of tick() dispatches that went through the thread pool and how
    // many of them found at least one worker asleep (diagnostic only).
    uint64_t parallel_dispatches = 0;
    uint64_t parallel_dispatches_with_sleeper = 0;

private:
    void tick_serial(int count);
    void tick_parallel(int count);
    void tick_share(int worker, int count);
    void worker_loop(int worker);
    void stop_pool();

    void* handle_ = nullptr;
    bool closed_ = false;
    int (*send_)(int, uint64_t, int) = nullptr;
    void (*tick_)(int) = nullptr;
    void (*tick_system_)(int, int) = nullptr;
    void (*advance_clock_)(int) = nullptr;
    int (*pop_)(int, uint64_t*, int*, uint32_t*) = nullptr;
    int (*stats_)(int, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*) = nullptr;
    void (*finalize_)() = nullptr;

    // Thread pool (dram_threads - 1 workers; the calling thread ticks share 0).
    std::vector<std::thread> workers_;
    std::mutex mutex_;
    std::condition_variable wake_;
    std::atomic<uint64_t> generation_{0};
    std::atomic<int> remaining_{0};
    std::atomic<int> sleepers_{0};
    std::atomic<int> pending_count_{0};
    std::atomic<int> next_system_{0};
    std::atomic<bool> stop_{false};
    std::vector<std::exception_ptr> worker_errors_;
};

}  // namespace azilla
