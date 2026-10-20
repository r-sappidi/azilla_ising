// Multi-thread-capable variant of the Ramulator bridge tb/ramulator_dpi.cpp
// (build/cycle_model_ramulator/libazilla_ramulator.so).  Built as a SEPARATE
// shared library, libazilla_ramulator_mt.so, so the RTL testbench and the
// Python model keep using the original one unchanged.
//
// Same C ABI (az_dram_init / az_dram_init_timing / az_dram_send / az_dram_tick
// / az_dram_pop / az_dram_get_stats / az_dram_report / az_dram_finalize) with
// identical semantics, plus two entries that let a caller tick disjoint
// subsets of the independent memory systems from different threads:
//
//   az_dram_tick_system(system_id, tick_count)  tick ONE system tick_count times
//   az_dram_advance_clock(tick_count)           advance the shared issue clock
//                                               once all systems were ticked
//
// Thread-safety contract: az_dram_tick_system may run concurrently for
// DIFFERENT system ids only.  send / pop / get_stats / advance_clock are
// main-thread calls made while no az_dram_tick_system call is in flight.
//
// Why the results stay bit-identical to the serial bridge: the only shared
// mutable state the original bridge touches during a tick is the global
// memory_tick counter, read by the completion callback to compute the request
// latency (memory_tick - issue tick).  In the serial loop, during tick k of a
// call every system observes memory_tick == T0 + k.  Here each system keeps
// its own tick counter that is incremented right after its own memory->tick(),
// so a completion during its k-th tick observes exactly T0 + k as well;
// the global issue clock (used by az_dram_send) is advanced by the caller
// after the parallel phase.  Everything else (completion deque, counters,
// Ramulator controller/device state) is per system.  Ramulator2's only static
// state is init-time registries (factory, DRAM spec creators, logger names),
// none of which is touched by IMemorySystem::tick().
//
// Verified bit-identical against the serial bridge by
// results/cpp_exact_port_20261006/threaded/REPORT.md.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <fstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include <ramulator/base/config.h>
#include <ramulator/base/factory.h>
#include <ramulator/base/request.h>
#include <ramulator/frontend/i_frontend.h>
#include <ramulator/memory_system/i_memory_system.h>

namespace {

constexpr unsigned kSpinCount = 32;
constexpr unsigned kTransactionBytes = 32;
constexpr unsigned kBlockBytes = 1024;

struct Completion {
    uint64_t address;
    uint32_t tag;
    uint64_t issue_tick;
    uint64_t completion_tick;
};

struct MemorySystem {
    Ramulator::IFrontEnd* frontend = nullptr;
    Ramulator::IMemorySystem* memory = nullptr;
    std::deque<Completion> completed;
    uint64_t accepted = 0;
    uint64_t rejected = 0;
    uint64_t completed_count = 0;
    uint64_t latency_sum = 0;
    uint64_t latency_max = 0;
    // Per-system tick counter: equals the serial bridge's global memory_tick
    // at every point where that bridge's completion callback would read it.
    uint64_t local_tick = 0;
};

// Pointer-stable, allocated once in az_dram_init; never resized while ticking.
std::vector<MemorySystem> systems;
std::unordered_map<uint64_t, int8_t> weights;  // main thread only (pop)
uint64_t memory_tick = 0;                       // issue clock, main thread only
unsigned total_blocks = 0;

uint64_t edge_key(unsigned row, unsigned column) {
    return (uint64_t(row) << 32) | uint64_t(column);
}

int8_t lookup_weight(unsigned row, unsigned column) {
    const auto it = weights.find(edge_key(row, column));
    return it == weights.end() ? int8_t{0} : it->second;
}

void load_dataset(const std::string& path, bool load_weights) {
    std::ifstream input(path);
    if (!input)
        throw std::runtime_error("cannot open dataset " + path);

    unsigned vertices = 0;
    uint64_t known_cut = 0;
    if (!(input >> vertices >> known_cut))
        throw std::runtime_error("invalid dataset header in " + path);
    if (vertices != total_blocks * kSpinCount)
        throw std::runtime_error("dataset vertex count does not match RTL geometry");

    if (!load_weights)
        return;

    unsigned source = 0;
    unsigned destination = 0;
    int value = 0;
    while (input >> source >> destination >> value) {
        if (source == 0 || destination == 0 || source > vertices ||
            destination > vertices || value < -128 || value > 127)
            throw std::runtime_error("invalid dataset record in " + path);
        weights[edge_key(source - 1, destination - 1)] = int8_t(value);
    }
}

void fill_data(uint64_t address, uint32_t* data) {
    std::fill(data, data + 8, 0u);
    const uint64_t block_number = address / kBlockBytes;
    const unsigned block_a = unsigned(block_number / total_blocks);
    const unsigned block_b = unsigned(block_number % total_blocks);
    const unsigned byte_in_block = unsigned(address % kBlockBytes);
    const unsigned row_in_block = byte_in_block / kTransactionBytes;

    if (block_a >= total_blocks || block_b >= total_blocks ||
        row_in_block >= kSpinCount)
        throw std::runtime_error("DRAM request address is outside J layout");

    for (unsigned column = 0; column < kSpinCount; ++column) {
        const uint8_t value = uint8_t(lookup_weight(
            block_a * kSpinCount + row_in_block,
            block_b * kSpinCount + column));
        data[column / 4] |= uint32_t(value) << ((column % 4) * 8);
    }
}

void az_dram_init_common(const char* config_path,
                         const char* dataset_path,
                         int system_count,
                         int block_count,
                         bool timing_only) {
    if (!systems.empty())
        throw std::runtime_error("az_dram_init called twice");
    if (system_count <= 0 || block_count <= 0)
        throw std::runtime_error("invalid Ramulator system geometry");

    total_blocks = unsigned(block_count);
    memory_tick = 0;
    weights.clear();
    load_dataset(dataset_path, !timing_only);
    systems.resize(size_t(system_count));

    for (auto& system : systems) {
        auto config = Ramulator::Config::parse_config_file(config_path);
        system.frontend = Ramulator::Factory::create_frontend(config);
        system.memory = Ramulator::Factory::create_memory_system(config);
        system.frontend->connect_memory_system(system.memory);
        system.memory->connect_frontend(system.frontend);
        if (system.memory->get_tx_bytes() != int(kTransactionBytes))
            throw std::runtime_error("Ramulator GDDR transaction is not 32 bytes");
    }
}

inline void tick_one(MemorySystem& system) {
    system.memory->tick();
    system.local_tick++;
}

}  // namespace

extern "C" void az_dram_init(const char* config_path,
                             const char* dataset_path,
                             int system_count,
                             int block_count) {
    az_dram_init_common(
        config_path, dataset_path, system_count, block_count, false);
}

extern "C" void az_dram_init_timing(const char* config_path,
                                    const char* dataset_path,
                                    int system_count,
                                    int block_count) {
    az_dram_init_common(
        config_path, dataset_path, system_count, block_count, true);
}

extern "C" int az_dram_send(int system_id, uint64_t address, int tag) {
    if (system_id < 0 || size_t(system_id) >= systems.size())
        return 0;
    auto& system = systems[size_t(system_id)];
    const uint64_t issued = memory_tick;
    const bool accepted = system.frontend->receive_external_requests(
        Ramulator::Request::Type::Read,
        address,
        0,
        [system_id, address, tag, issued](Ramulator::Request& request) {
            // Runs on whichever thread ticks this system; touches only this
            // system's fields.
            auto& completed_system = systems[size_t(system_id)];
            const uint64_t latency = completed_system.local_tick - issued;
            completed_system.completed_count++;
            completed_system.latency_sum += latency;
            completed_system.latency_max =
                std::max(completed_system.latency_max, latency);
            completed_system.completed.push_back(
                {address, uint32_t(tag), issued, uint64_t(request.depart)});
        },
        kTransactionBytes);

    if (accepted)
        system.accepted++;
    else
        system.rejected++;
    return accepted ? 1 : 0;
}

// Serial entry, same order as the original bridge: all systems per tick.
extern "C" void az_dram_tick(int tick_count) {
    if (tick_count < 0)
        throw std::runtime_error("negative Ramulator tick count");
    for (int tick = 0; tick < tick_count; ++tick) {
        for (auto& system : systems)
            tick_one(system);
        memory_tick++;
    }
}

// Parallel entry: tick one system tick_count times.  Safe to call
// concurrently for distinct system ids.
extern "C" void az_dram_tick_system(int system_id, int tick_count) {
    if (tick_count < 0)
        throw std::runtime_error("negative Ramulator tick count");
    if (system_id < 0 || size_t(system_id) >= systems.size())
        throw std::runtime_error("Ramulator system id out of range");
    auto& system = systems[size_t(system_id)];
    for (int tick = 0; tick < tick_count; ++tick)
        tick_one(system);
}

// After every system received tick_count ticks through az_dram_tick_system,
// advance the shared issue clock.  Verifies the invariant that keeps the
// latency statistics identical to the serial bridge.
extern "C" void az_dram_advance_clock(int tick_count) {
    if (tick_count < 0)
        throw std::runtime_error("negative Ramulator tick count");
    memory_tick += uint64_t(tick_count);
    for (size_t index = 0; index < systems.size(); ++index)
        if (systems[index].local_tick != memory_tick)
            throw std::runtime_error(
                "Ramulator system " + std::to_string(index) + " tick counter " +
                std::to_string(systems[index].local_tick) +
                " does not match the issue clock " + std::to_string(memory_tick));
}

extern "C" int az_dram_pop(int system_id, uint64_t* address, int* tag,
                            uint32_t* data) {
    if (system_id < 0 || size_t(system_id) >= systems.size())
        return 0;
    auto& queue = systems[size_t(system_id)].completed;
    if (queue.empty())
        return 0;
    const Completion response = queue.front();
    queue.pop_front();
    *address = response.address;
    *tag = int(response.tag);
    fill_data(response.address, data);
    return 1;
}

extern "C" void az_dram_report() {
    for (size_t index = 0; index < systems.size(); ++index) {
        auto& system = systems[index];
        system.memory->update_stats_recursive();
        const double average = system.completed_count == 0
            ? 0.0 : double(system.latency_sum) / double(system.completed_count);
        std::printf("DRAM[%zu] accepted=%llu rejected=%llu completed=%llu avg_latency_ticks=%.2f max_latency_ticks=%llu\n",
                    index,
                    static_cast<unsigned long long>(system.accepted),
                    static_cast<unsigned long long>(system.rejected),
                    static_cast<unsigned long long>(system.completed_count), average,
                    static_cast<unsigned long long>(system.latency_max));
    }
}

extern "C" int az_dram_get_stats(int system_id,
                                  uint64_t* accepted,
                                  uint64_t* rejected,
                                  uint64_t* completed,
                                  uint64_t* latency_sum,
                                  uint64_t* latency_max) {
    if (system_id < 0 || size_t(system_id) >= systems.size())
        return 0;
    const auto& system = systems[size_t(system_id)];
    *accepted = system.accepted;
    *rejected = system.rejected;
    *completed = system.completed_count;
    *latency_sum = system.latency_sum;
    *latency_max = system.latency_max;
    return 1;
}

extern "C" void az_dram_finalize() {
    for (auto& system : systems) {
        system.frontend->finalize();
        system.memory->finalize();
        delete system.frontend;
        delete system.memory;
    }
    systems.clear();
    weights.clear();
    memory_tick = 0;
}
