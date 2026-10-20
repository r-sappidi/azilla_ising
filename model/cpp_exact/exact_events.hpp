// Port of RamulatorEventPerformanceModel (execution mode ``cir`` only) from
// model/azilla_cycle_model/exact_events.py.
#pragma once

#include <fstream>
#include <map>
#include <memory>
#include <string>
#include <tuple>
#include <vector>

#include "adapters.hpp"
#include "cross.hpp"
#include "anchored.hpp"
#include "events.hpp"
#include "hierarchy.hpp"
#include "json.hpp"
#include "memory.hpp"
#include "noc.hpp"
#include "ramulator.hpp"
#include "scheduler.hpp"
#include "workload.hpp"

namespace azilla {

struct PerformanceConfig {
    int h0_mvm_count = 1;
    int h1_mvm_count = 1;
    int cross_mvm_count = 1;
    int fifo_depth = 4;
    long max_cycles = 1000000000L;
    int chiplet_link_latency_cycles = 0;
    InterconnectConfig interconnect;
    std::string execution_mode = "cir";
    // cores-only only: endpoint policy ("streaming" = streaming_cli,
    // "buffered-v3" = shared_fetch_double_cli) and the test-only weight stall
    // period (arithmetic_fixture['stall_period'] of the Python checkers).
    std::string endpoint_policy = "streaming";
    int stall_period = 0;
    // Threads ticking the independent Ramulator systems (1 = serial bridge
    // call, the historical behaviour).  Timing results do not depend on it.
    int dram_threads = 1;
    // cir-h0-anchored only: four-beat partial packets buffered at each H0 uplink.
    int anchored_uplink_packets = 4;
    // cir-h0-anchored only: accumulate remote partials at the owner H0 and
    // flush one packet per touched remote core after its engines finish.
    bool anchored_accumulate = false;
    // Outstanding 32-byte weight requests per DRAM streamer (RTL
    // dram_weight_streamer MAX_OUTSTANDING, default 64).
    int max_outstanding = 64;
};

struct PerformanceCounters {
    long cycles = 0;
    long injected_flits = 0;
    long ejected_flits = 0;
    long physical_link_flits = 0;
    long injection_stalls = 0;
    long ejection_stalls = 0;
    long link_stalls = 0;
    long type_flits[4] = {0, 0, 0, 0};
    // cir-h0-anchored only (cycle sums over all H0s / engines).
    long uplink_sibling_beats = 0, uplink_sibling_stalls = 0;  // offered to the H1 crossbar
    long uplink_mesh_beats = 0, uplink_mesh_stalls = 0;        // offered to the mesh injection
    long engine_output_stalls = 0;       // engine partial valid, not accepted (local or uplink)
    long engine_local_output_stalls = 0;   // ... of which the partial is for a local core
    long engine_uplink_wait_stalls = 0;    // ... remote and not the uplink's selected engine
    long engine_uplink_full_stalls = 0;    // ... remote, selected, FIFO full
    long engine_result_full_cycles = 0;  // both result slots occupied (compute blocked on output)
    long flush_packets = 0;              // accumulate: packets flushed (sum over H0s)
    long flush_cycles_max = 0;           // accumulate: longest H0 flush (node DONE -> uplink drained)
};

struct NocResourceStats {
    std::string scope;
    int node, x, y;
    std::string direction;
    long offered_cycles, accepted_flits, stall_cycles, packets;
    long state_flits, partial_flits, epoch_done_flits, other_flits;
    double acceptance_fraction() const { return offered_cycles ? double(accepted_flits) / double(offered_cycles) : 0.0; }
    double backpressure() const { return offered_cycles ? double(stall_cycles) / double(offered_cycles) : 0.0; }
};

struct NodePerformanceStats {
    int node, x, y;
    long h0_jobs, h1_jobs, cross_jobs, total_jobs;
    long state_publications_sent, state_publications_received;
    long h1_completion_cycle, cross_completion_cycle, completion_cycle;
};

struct DramPerformanceStats {
    int system_id;
    std::string level;
    int node, index, engines;
    long scheduled_jobs;
    uint64_t accepted_requests, rejected_requests, completed_requests, outstanding_requests;
    double average_latency_ticks;
    uint64_t maximum_latency_ticks;
    double average_latency_cycles, maximum_latency_cycles;
};

struct ExactEventResult {
    std::string accuracy;
    long initialization_cycles, iteration_cycles, total_cycles;
    long scheduled_h0, scheduled_h1, scheduled_cross;
    PerformanceCounters counters;
    double average_hops;
    std::vector<std::pair<int, long>> hop_histogram;
    std::vector<NocResourceStats> noc_resources;
    std::vector<NodePerformanceStats> nodes;
    std::vector<DramPerformanceStats> dram;
    int chiplet_link_latency_cycles;
    InterconnectConfig interconnect;
    std::string execution_mode = "cir";
    long unordered_interaction_blocks, directed_core_jobs, weight_block_reads, logical_weight_blocks_stored,
        remote_state_packets;
    int core_weight_buffers;
    JsonObject core_pipeline_contract;  // empty for cir
};

// (cycle, scope, node, direction, packet_type, source_id, epoch, block_id, dest_x, dest_y, last)
using Transfer = std::tuple<long, std::string, int, std::string, int, int, int, int, int, int, int>;

struct NocAccumulator {
    long offered_cycles = 0, accepted_flits = 0, stall_cycles = 0, packets = 0;
    long type_flits[4] = {0, 0, 0, 0};
};

class RamulatorEventPerformanceModel {
public:
    RamulatorEventPerformanceModel(const Geometry& geometry, const BlockOccupancyDataset& dataset,
                                   const std::string& dataset_path, const std::string& ramulator_library,
                                   const std::string& ramulator_config, int mem_lanes, int ticks_per_cycle,
                                   long idle_tick_modulus /* <=0: none */, const PerformanceConfig& config);
    ExactEventResult run(const std::vector<ScheduledBlock>* schedule);
    void close() { backend_->finalize(); }
    const RamulatorBackend& backend() const { return *backend_; }

    // Opt-in streaming transfer trace: every accepted transfer is written to
    // ``path`` (RTL event schema, generation order, which is cycle
    // non-decreasing) as it occurs instead of being retained in
    // ``transfers``.  Lets dense multi-H1 runs with hundreds of millions of
    // transfers run in bounded memory.  Off by default: behaviour unchanged.
    void open_transfer_stream(const std::string& path);
    void close_transfer_stream();
    long streamed_transfers() const { return streamed_transfers_; }

    // Opt-in cores-only event dump (core/memory/state observer streams of the
    // Python model, one CSV row each) for the RTL trace checkers.
    void open_event_stream(const std::string& path);
    void close_event_stream();

    std::vector<Transfer> transfers;

private:
    struct Frontend {
        int system_id;
        std::unique_ptr<DramWeightStreamer> streamer;
        std::vector<bool> request_ready;
        std::vector<std::optional<MemoryResponse>> responses;
    };

    void reset_timing_state();
    ExactEventResult run_cores_only(const std::vector<ScheduledBlock>& schedule_records, long initialization);
    void observe_noc_resource(const char* scope, int node, const char* direction, const Flit& flit, bool accepted);
    void record_network(const std::vector<RouterOutputs>& comb, const std::vector<std::optional<Flit>>& injections,
                        const std::vector<bool>& local_ready, PerformanceCounters& counters, long absolute_cycle);
    void memory_edge(const std::vector<StreamerOutputs>& stream_outputs);
    std::vector<NocResourceStats> noc_stats() const;
    std::vector<DramPerformanceStats> dram_stats(const CompiledSchedule& compiled) const;
    static void validate_metric_totals(const PerformanceCounters& counters, const std::vector<NocResourceStats>& res,
                                       const std::vector<std::pair<int, long>>& hop_histogram);
    static Transfer make_transfer(long cycle, const char* scope, int node, const char* direction, const Flit& flit);
    void record_transfer(Transfer&& transfer);

    std::unique_ptr<std::ofstream> transfer_stream_;
    std::unique_ptr<std::ofstream> event_stream_;
    long streamed_transfers_ = 0;
    Geometry geometry_;
    const BlockOccupancyDataset& dataset_;
    std::string dataset_path_;
    int mem_lanes_;
    int ticks_per_cycle_;
    long idle_tick_modulus_;
    std::string ramulator_config_sha256_;
    PerformanceConfig config_;
    TargetIndex index_;
    std::unique_ptr<RamulatorBackend> backend_;
    std::vector<Frontend> frontends_;                         // by target index
    std::vector<std::unique_ptr<HierarchyNode>> local_nodes_;  // by target index; null for CROSS
    std::vector<std::unique_ptr<H0Adapter>> h0_adapters_;      // by global_h0
    std::vector<std::unique_ptr<H1ChildAdapter>> h1_adapters_; // by node
    std::vector<std::unique_ptr<CrossH1Node>> cross_nodes_;    // by node
    std::unique_ptr<Mesh> mesh_;
    std::vector<LocalInjectionArbiter> injection_arbiters_;
    // cir-h0-anchored only: one uplink per H0 and one uplink arbiter per H1.
    std::vector<H0Uplink> uplinks_;
    std::vector<UplinkInjectionArbiter> uplink_arbiters_;
    std::map<std::tuple<std::string, int, std::string>, NocAccumulator> noc_resources_;
    std::map<int, long> hop_histogram_;
};

std::string file_sha256(const std::string& path);

}  // namespace azilla
