// Port of model/azilla_cycle_model/ramulator.py: dlopen binding to the C ABI
// of build/cycle_model_ramulator/libazilla_ramulator.so (tb/ramulator_dpi.cpp).
// DRAM timing is shared with the Python model and the RTL testbench, not
// reimplemented.
#pragma once

#include <cstdint>
#include <optional>
#include <string>

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
                     int system_count, int block_count, bool timing_only);
    ~RamulatorBackend();
    RamulatorBackend(const RamulatorBackend&) = delete;
    RamulatorBackend& operator=(const RamulatorBackend&) = delete;

    bool send(int system, uint64_t address, int tag);
    void tick(long count);
    std::optional<MemoryResponse> pop(int system);
    RamulatorSystemStats stats(int system);
    void finalize();

    int system_count;

private:
    void* handle_ = nullptr;
    bool closed_ = false;
    int (*send_)(int, uint64_t, int) = nullptr;
    void (*tick_)(int) = nullptr;
    int (*pop_)(int, uint64_t*, int*, uint32_t*) = nullptr;
    int (*stats_)(int, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*) = nullptr;
    void (*finalize_)() = nullptr;
};

}  // namespace azilla
