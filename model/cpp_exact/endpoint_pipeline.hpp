// SourcePort of model/azilla_cycle_model/core_iteration_pipeline.py
// (CoreIterationPipeline with cir_matched_delivery=True, use_mesh=True,
// compress_idle=True) as specialised by shared_fetch.SharedFetchPipeline,
// shared_fetch_double.DoubleFetchPipeline and streaming_cli.StreamingPipeline:
// DoubleFetchStreamer per DRAM system, EndpointCore per block (streaming or
// buffered), CoreStateBank per H0, one persistent FlooNoC mesh.
//
// The pipeline is timing-only: weight payloads and state words are never
// stored.  Every handshake, arbitration rule, lock, beat counter and the
// idle-core compression rule are ported in the Python decision order.
#pragma once

#include <deque>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "core_iteration.hpp"
#include "core_state_bank.hpp"
#include "noc.hpp"
#include "ramulator.hpp"
#include "shared_fetch.hpp"
#include "workload.hpp"

namespace azilla {

struct SourcePort {
    int system = -1;
    int engine = -1;
    bool operator==(const SourcePort& o) const { return system == o.system && engine == o.engine; }
    bool operator!=(const SourcePort& o) const { return !(*this == o); }
    bool operator<(const SourcePort& o) const { return system != o.system ? system < o.system : engine < o.engine; }
    bool valid() const { return system >= 0; }
};

// Per-tick stimulus (the Python ``phase`` dictionary).
struct PipelinePhase {
    bool dispatch_enable = false;
    std::map<int, Flit> control_packets;  // node -> flit (node order)
    bool publish_valid = false;
    int publish_index = 0;
    std::map<int, int> bank_writes;  // global h0 -> block index
    CoreInputs core_inputs;          // iter_start / partials_done / commit / ...
};

struct PipelineObservers {
    // (cycle, event, system, dst, src)  -- core_event_observer
    std::function<void(long, const char*, int, int, int)> core;
    // (cycle, scope, node, direction, flit, accepted) -- noc_observer
    std::function<void(long, const char*, int, const char*, const Flit&, bool)> noc;
    // (cycle, event, system, tag) -- core_memory_observer
    std::function<void(long, const char*, int, int)> memory;
    // (cycle, event, core, value) -- core_state_observer (value: block for request, 0 for response)
    std::function<void(long, const char*, int, int)> state;
};

class EndpointPipeline {
public:
    EndpointPipeline(const Geometry& geometry, RamulatorBackend& backend, const std::vector<int>& engines,
                     int ticks_per_cycle, int fifo_depth, const InterconnectConfig& interconnect, int mem_lanes,
                     int stall_period, bool streaming, PipelineObservers observers);

    void tick(const PipelinePhase& phase);
    bool all_streamers_idle() const;
    bool all_cores_iter_done() const;
    bool mesh_empty() const { return !mesh.has_data(); }

    Geometry geometry;
    RamulatorBackend& backend;
    std::vector<std::deque<DmaCommand>> queues;  // per system
    std::vector<int> engines;
    std::vector<DoubleFetchStreamer> streamers;
    int mem_lanes;
    std::vector<std::vector<bool>> request_ready;
    std::vector<std::vector<std::optional<MemoryResponse>>> responses;
    std::vector<EndpointCore> cores;
    std::set<int> running_cores;
    std::vector<CoreStateBank> state_banks;
    std::set<int> control_accepted;
    std::vector<std::pair<int, Flit>> control_received;
    bool last_network_activity = false;
    std::vector<SourcePort> active_source;  // per core; invalid == absent
    Mesh mesh;
    int stall_period;
    int ticks_per_cycle;
    std::map<int, SourcePort> external_locks;   // h0 -> source port
    std::vector<int> external_beats;      // per h0
    std::map<int, SourcePort> packet_locks;     // cross owner node -> port
    std::vector<int> beats;               // per flat port
    long completed = 0;
    long cycle = 0;

    // Audit (core_iteration_pipeline.audit + shared_fetch_double additions).
    std::vector<int> audit_source_capacity_blocks;
    std::vector<int> audit_peak_source_blocks;
    std::vector<int> audit_peak_outstanding_requests;
    std::vector<long> audit_local_delivery_beats;
    std::vector<bool> audit_local_delivery_touched;

    int total_h0() const { return geometry.node_count() * geometry.h0_per_h1; }
    int cross_start() const { return total_h0() + geometry.node_count(); }
    int flat_port(const SourcePort& p) const { return port_offset_[p.system] + p.engine; }

private:
    bool streaming_;
    PipelineObservers observers_;
    std::vector<int> port_offset_;
    int port_count_ = 0;

    void event(const char* name, int system, int dst, int src) {
        if (observers_.core) observers_.core(cycle, name, system, dst, src);
    }
    void noc(const char* scope, int node, const char* direction, const Flit& flit, bool accepted) {
        if (observers_.noc) observers_.noc(cycle, scope, node, direction, flit, accepted);
    }
};

}  // namespace azilla
