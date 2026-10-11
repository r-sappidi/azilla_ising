#include "cores_only_events.hpp"

#include <algorithm>
#include <set>
#include <stdexcept>

namespace azilla {

CoreStatePublicationPlan core_state_publication_plan(const Geometry& geometry,
                                                     const std::vector<ScheduledBlock>& records) {
    CoresOnlySchedule compiled = compile_cores_only_schedule(geometry, records);
    CoreStatePublicationPlan plan;
    plan.gather_cycles = geometry.blocks_per_h1();
    plan.remote_publications = compiled.state_publications;
    plan.local_writes_by_h1.resize(geometry.node_count());
    for (size_t global_h0 = 0; global_h0 < compiled.h0.size(); ++global_h0) {
        std::set<int> sources;
        for (const auto& job : compiled.h0[global_h0]) sources.insert(job.block_b);
        int h1 = int(global_h0) / geometry.h0_per_h1;
        int local_h0 = int(global_h0) % geometry.h0_per_h1;
        for (int source : sources) plan.local_writes_by_h1[h1].emplace_back(local_h0, source);
    }
    return plan;
}

// ------------------------------------------------------------------ driver

CoreIterationDriver::CoreIterationDriver(EndpointPipeline& pipeline_, const std::vector<std::deque<DmaCommand>>& queues_,
                                         int iterations_)
    : pipeline(pipeline_), queues(queues_), iterations(iterations_) {
    for (const auto& q : queues) jobs_per_iteration += long(q.size());
}

const char* CoreIterationDriver::phase_name(Phase phase) {
    switch (phase) {
        case START: return "start";
        case COMPUTE: return "compute";
        case DONE: return "done";
        case WAIT_UPDATE: return "wait_update";
        case COMMIT_DELAY: return "commit_delay";
        default: return "finish";
    }
}

void CoreIterationDriver::tick() {
    EndpointPipeline& p = pipeline;
    PipelinePhase phase;
    const Phase old = this->phase;
    if (old == START) {
        phase.core_inputs.iter_start = true;
        p.queues = queues;
        this->phase = COMPUTE;
    } else if (old == COMPUTE) {
        phase.dispatch_enable = true;
    } else if (old == DONE) {
        phase.core_inputs.partials_done = true;
        this->phase = WAIT_UPDATE;
    } else if (old == WAIT_UPDATE) {
        if (p.all_cores_iter_done()) {
            phase.core_inputs.commit = true;
            this->phase = COMMIT_DELAY;
            commit_delay = 2;
        }
    } else if (old == COMMIT_DELAY) {
        commit_delay -= 1;
        if (commit_delay == 0) {
            iteration += 1;
            if (iteration >= iterations) {
                this->phase = FINISH;
                finished = true;
            } else {
                this->phase = START;
            }
        }
    } else if (old == FINISH) {
        finished = true;
    }
    p.tick(phase);
    if (this->phase == COMPUTE && old == COMPUTE && p.completed == long(iteration + 1) * jobs_per_iteration &&
        p.all_streamers_idle())
        this->phase = DONE;
}

// -------------------------------------------------------------- controller

CoreFullController::CoreFullController(EndpointPipeline& pipeline_, const std::vector<std::deque<DmaCommand>>& queues,
                                       const std::vector<ScheduledBlock>& records, int iterations)
    : pipeline(pipeline_), driver(pipeline_, queues, iterations), plan(core_state_publication_plan(pipeline_.geometry, records)) {}

void CoreFullController::observe_phase(const char* name) {
    std::string key = "(" + std::to_string(driver.iteration) + ", '" + name + "')";
    if (phase_seen.insert(key).second) phase_cycles.emplace_back(key, pipeline.cycle);
}

void CoreFullController::tick() {
    EndpointPipeline& p = pipeline;
    const Geometry& g = p.geometry;
    const int node_count = g.node_count();
    // The start immediately following initialization is derived from
    // init_done (always asserted here); no external trace supplies its cycle.
    const bool starting = mode == NONE && driver.phase == CoreIterationDriver::START;
    if (mode == NONE && driver.phase == CoreIterationDriver::DONE && !barrier_done) {
        mode = COMPLETION;
        pending_completion.clear();
        for (int n = 0; n < node_count; ++n) pending_completion.insert(n);
        quiet = 0;
    }
    if (mode == NONE) {
        observe_phase(starting ? "start" : CoreIterationDriver::phase_name(driver.phase));
        driver.tick();
        if (starting) {
            mode = GATHER;
            offset = 0;
            barrier_done = false;
        }
        return;
    }
    const char* mode_name = mode == GATHER ? "gather" : mode == PUBLICATION ? "publication" : mode == FILL ? "fill" : "completion";
    observe_phase(mode_name);
    const Mode old = mode;
    PipelinePhase phase;
    if (old == GATHER) {
        // Gather reads each node's local state table; data is zero in timing-only.
    } else if (old == PUBLICATION) {
        const auto& pubs = plan.remote_publications;
        while (release_index < pubs.size() && 2 * long(release_index) <= long(offset)) {
            const auto& [block, destination] = pubs[release_index];
            int source = block / g.blocks_per_h1();
            Flit flit;
            flit.packet_type = NOC_STATE;
            flit.dest_x = destination % g.mesh_x;
            flit.dest_y = destination / g.mesh_x;
            flit.source_id = source;
            flit.epoch = driver.iteration;
            flit.block_id = block;
            flit.last = true;
            pending_publications[source].push_back(flit);
            release_index += 1;
        }
        for (int n = 0; n < node_count; ++n)
            if (!pending_publications[n].empty()) phase.control_packets[n] = pending_publications[n].front();
    } else if (old == FILL) {
        for (int node = 0; node < node_count; ++node) {
            const auto& queue = plan.local_writes_by_h1[node];
            if (size_t(offset) >= queue.size()) continue;
            const auto& [local, block] = queue[offset];
            phase.bank_writes[node * g.h0_per_h1 + local] = block;
        }
    } else if (old == COMPLETION) {
        for (int n : pending_completion) {
            Flit flit;
            flit.packet_type = NOC_EPOCH_DONE;
            flit.source_id = n;
            flit.epoch = driver.iteration;
            flit.dest_x = n % g.mesh_x;
            flit.dest_y = n / g.mesh_x;
            flit.last = true;
            phase.control_packets[n] = flit;
        }
    }
    p.tick(phase);
    offset += 1;
    if (old == GATHER && offset == plan.gather_cycles) {
        mode = PUBLICATION;
        offset = 0;
        release_index = 0;
        pending_publications.assign(node_count, {});
        quiet = 0;
    } else if (old == PUBLICATION) {
        for (int node : p.control_accepted) pending_publications[node].pop_front();
        for (const auto& [node, flit] : p.control_received) {
            (void)node;
            if (flit.packet_type != NOC_STATE || flit.epoch != driver.iteration)
                throw std::runtime_error("unexpected state publication response");
        }
        bool any_pending = false;
        for (const auto& q : pending_publications)
            if (!q.empty()) { any_pending = true; break; }
        quiet = (release_index == plan.remote_publications.size() && !any_pending && p.mesh_empty() &&
                 !p.last_network_activity)
                    ? quiet + 1
                    : 0;
        if (quiet == 4) {
            mode = plan.local_fill_cycles() ? FILL : NONE;
            offset = 0;
        }
    } else if (old == FILL && offset == plan.local_fill_cycles()) {
        mode = NONE;
    } else if (old == COMPLETION) {
        for (int node : p.control_accepted) pending_completion.erase(node);
        quiet = (pending_completion.empty() && p.mesh_empty() && !p.last_network_activity) ? quiet + 1 : 0;
        if (quiet == 4) {
            mode = NONE;
            barrier_done = true;
        }
    }
}

}  // namespace azilla
