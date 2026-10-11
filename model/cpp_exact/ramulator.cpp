#include "ramulator.hpp"

#include <dlfcn.h>

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
}  // namespace

RamulatorBackend::RamulatorBackend(const std::string& library, const std::string& config,
                                   const std::string& dataset, int system_count_, int block_count,
                                   bool timing_only)
    : system_count(system_count_) {
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
    std::string config_abs = std::filesystem::absolute(config).lexically_normal().string();
    std::string dataset_abs = std::filesystem::absolute(dataset).lexically_normal().string();
    init(config_abs.c_str(), dataset_abs.c_str(), system_count, block_count);
}

RamulatorBackend::~RamulatorBackend() {
    finalize();
    if (handle_) dlclose(handle_);
}

bool RamulatorBackend::send(int system, uint64_t address, int tag) { return send_(system, address, tag) != 0; }

void RamulatorBackend::tick(long count) {
    // The bridge takes an int; split very long idle advances defensively.
    while (count > 0) {
        int step = count > 1000000000L ? 1000000000 : int(count);
        tick_(step);
        count -= step;
    }
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
        finalize_();
        closed_ = true;
    }
}

}  // namespace azilla
