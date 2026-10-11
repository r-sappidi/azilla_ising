// Port of model/azilla_cycle_model/shared_fetch_events.run_full_cores as
// wrapped by streaming_cli.run_full_cores (policy
// single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1) and, for the
// ``buffered`` variant, shared_fetch_double_cli.run_full_cores (policy
// single_fetch_two_slot_cir_routes_v3), together with
// core_full_controller.CoreFullController, core_iteration_driver.
// CoreIterationDriver and core_state_publication.core_state_publication_plan.
#pragma once

#include <deque>
#include <string>
#include <utility>
#include <vector>

#include "endpoint_pipeline.hpp"
#include "json.hpp"
#include "scheduler.hpp"

namespace azilla {

struct CoreStatePublicationPlan {
    int gather_cycles = 0;
    std::vector<BlockPair> remote_publications;                     // (block, destination h1)
    std::vector<std::vector<std::pair<int, int>>> local_writes_by_h1;  // (local h0, source block)
    int local_fill_cycles() const {
        size_t most = 0;
        for (const auto& w : local_writes_by_h1) most = std::max(most, w.size());
        return int(most);
    }
};

CoreStatePublicationPlan core_state_publication_plan(const Geometry& geometry, const std::vector<ScheduledBlock>& records);

class CoreIterationDriver {
public:
    enum Phase { START, COMPUTE, DONE, WAIT_UPDATE, COMMIT_DELAY, FINISH };
    CoreIterationDriver(EndpointPipeline& pipeline, const std::vector<std::deque<DmaCommand>>& queues, int iterations);
    // Returns the phase stimulus it applied (the controller adds nothing here).
    void tick();
    static const char* phase_name(Phase phase);

    EndpointPipeline& pipeline;
    std::vector<std::deque<DmaCommand>> queues;
    int iterations;
    Phase phase = START;
    int iteration = 0;
    int commit_delay = 0;
    bool finished = false;
    long jobs_per_iteration = 0;
};

class CoreFullController {
public:
    enum Mode { NONE, GATHER, PUBLICATION, FILL, COMPLETION };
    CoreFullController(EndpointPipeline& pipeline, const std::vector<std::deque<DmaCommand>>& queues,
                       const std::vector<ScheduledBlock>& records, int iterations);
    bool finished() const { return driver.finished; }
    void tick();

    EndpointPipeline& pipeline;
    CoreIterationDriver driver;
    CoreStatePublicationPlan plan;
    Mode mode = NONE;
    int offset = 0;
    int quiet = 0;
    std::vector<std::deque<Flit>> pending_publications;  // per source node
    std::set<int> pending_completion;
    size_t release_index = 0;
    // (iteration, phase) -> first cycle, in Python ``str((iteration, phase))`` key form.
    std::vector<std::pair<std::string, long>> phase_cycles;
    std::set<std::string> phase_seen;
    bool barrier_done = false;

private:
    void observe_phase(const char* name);
};

// Result of the cores-only run that exact_events.run() turns into an
// ExactEventResult (fields the Python wrapper fills).
struct CoresOnlyRunResult {
    long initialization_cycles, iteration_cycles, total_cycles;
    long directed_jobs, unordered_blocks, remote_state_packets;
    std::vector<long> h0_jobs_per_node;
    std::vector<long> publications_sent, publications_received;
    std::vector<long> dram_jobs;     // per system (canonical jobs x iterations)
    std::vector<int> dram_engines;   // per system
    JsonObject core_pipeline_contract;
    int core_weight_buffers;
};

}  // namespace azilla
