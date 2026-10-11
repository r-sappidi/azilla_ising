#include "endpoint_pipeline.hpp"

#include <algorithm>
#include <stdexcept>

namespace azilla {

namespace {
constexpr int SPINS = 32;
constexpr int NOC_WEIGHT = 3;  // packet_type of forwarded cross-H1 weight rows

const char* link_name(int port) {
    switch (port) {
        case NORTH: return "north";
        case SOUTH: return "south";
        case EAST: return "east";
        default: return "west";
    }
}
}  // namespace

EndpointPipeline::EndpointPipeline(const Geometry& geometry_, RamulatorBackend& backend_,
                                   const std::vector<int>& engines_, int ticks_per_cycle_, int fifo_depth,
                                   const InterconnectConfig& interconnect, int mem_lanes_, int stall_period_,
                                   bool streaming, PipelineObservers observers)
    : geometry(geometry_), backend(backend_), engines(engines_), mem_lanes(mem_lanes_),
      mesh(geometry_.mesh_x, geometry_.mesh_y, fifo_depth, interconnect), stall_period(stall_period_),
      ticks_per_cycle(ticks_per_cycle_), streaming_(streaming), observers_(std::move(observers)) {
    const int systems = int(engines.size());
    queues.resize(systems);
    request_ready.assign(systems, std::vector<bool>(mem_lanes, false));
    responses.assign(systems, std::vector<std::optional<MemoryResponse>>(mem_lanes, std::nullopt));
    streamers.reserve(systems);
    port_offset_.resize(systems);
    for (int s = 0; s < systems; ++s) {
        streamers.emplace_back(engines[s], geometry.total_blocks(), mem_lanes, mem_lanes);
        port_offset_[s] = port_count_;
        port_count_ += engines[s];
    }
    beats.assign(port_count_, 0);
    cores.reserve(geometry.total_blocks());
    for (int c = 0; c < geometry.total_blocks(); ++c) {
        cores.emplace_back(streaming_);
        running_cores.insert(c);
    }
    for (int h = 0; h < total_h0(); ++h) state_banks.emplace_back(geometry.total_blocks(), geometry.cores_per_h0);
    active_source.assign(geometry.total_blocks(), SourcePort{});
    external_beats.assign(total_h0(), 0);
    audit_source_capacity_blocks.resize(systems);
    for (int s = 0; s < systems; ++s) audit_source_capacity_blocks[s] = 2 * engines[s];
    audit_peak_source_blocks.assign(systems, 0);
    audit_peak_outstanding_requests.assign(systems, 0);
    audit_local_delivery_beats.assign(systems, 0);
    audit_local_delivery_touched.assign(systems, false);
}

bool EndpointPipeline::all_streamers_idle() const {
    for (const auto& s : streamers)
        if (!s.outputs().idle) return false;
    return true;
}

bool EndpointPipeline::all_cores_iter_done() const {
    for (const auto& c : cores)
        if (!c.outputs().iter_done) return false;
    return true;
}

void EndpointPipeline::tick(const PipelinePhase& phase) {
    control_accepted.clear();
    control_received.clear();
    const Geometry& g = geometry;
    const int total_h0_ = total_h0();
    const int cross_start_ = cross_start();
    const int systems = int(engines.size());
    const int node_count = g.node_count();

    // Pre-edge streamer outputs (base for the DRAM side, combined for ports).
    std::vector<StreamerOutputs> base_out(systems), out(systems);
    for (int s = 0; s < systems; ++s) {
        base_out[s] = streamers[s].base.outputs();
        out[s] = streamers[s].outputs_from_base(base_out[s]);
    }
    if (observers_.memory) {
        for (int s = 0; s < systems; ++s) {
            for (int lane = 0; lane < mem_lanes; ++lane) {
                const auto& request = out[s].memory_requests[lane];
                if (request.has_value() && request_ready[s][lane]) observers_.memory(cycle, "request", s, request->tag);
            }
            for (const auto& response : responses[s])
                if (response.has_value()) observers_.memory(cycle, "response", s, response->tag);
        }
    }
    auto port_node = [&](const SourcePort& p) -> const StreamerNodeOutput& { return out[p.system].node[p.engine]; };
    std::vector<bool> cr(port_count_, false), wr(port_count_, false);
    std::map<int, int> jobs;  // dst -> source block
    std::set<int> weights;    // dst
    std::set<int> used_h0;
    std::vector<SourcePort> next_active = active_source;
    const bool allow_weight = !(stall_period > 0 && cycle % stall_period < 5);
    std::vector<RouterOutputs> comb = mesh.all_outputs();
    std::vector<std::optional<Flit>> injection(node_count);
    std::vector<bool> ready(node_count, false);
    std::map<int, SourcePort> selected;
    std::map<int, SourcePort> external_choice = external_locks;
    std::vector<std::pair<int, SourcePort>> external_accepted;

    // cir_matched_delivery: H1 engines arbitrate independently per destination
    // H0; H0-local lanes arbitrate per core and never consume this link.
    for (int s = total_h0_; s < cross_start_; ++s) {
        for (int e = 0; e < engines[s]; ++e) {
            const StreamerNodeOutput& node = out[s].node[e];
            if (!node.weight_valid) continue;
            int dst = node.command->block_a;
            int h0 = dst / g.cores_per_h0;
            if (active_source[dst] == SourcePort{s, e}) external_choice.try_emplace(h0, SourcePort{s, e});
        }
    }
    for (int n = 0; n < node_count; ++n) {
        const auto& flit = comb[n].output_flits[LOCAL];
        if (flit.has_value() && flit->packet_type == NOC_WEIGHT)
            external_choice.try_emplace(flit->block_id / g.cores_per_h0, SourcePort{cross_start_ + flit->source_id, -1});
    }
    // use_mesh: cross-H1 sources inject forwarded rows into the mesh.
    for (int s = cross_start_; s < systems; ++s) {
        for (int e = 0; e < engines[s]; ++e) {
            SourcePort p{s, e};
            cr[flat_port(p)] = true;
            int owner = s - cross_start_;
            if (out[s].node[e].weight_valid && !packet_locks.count(owner)) packet_locks[owner] = p;
        }
    }
    for (const auto& [owner, p] : packet_locks) {
        const StreamerNodeOutput& node = port_node(p);
        if (!node.weight_valid) continue;
        int destination = node.command->block_a / g.blocks_per_h1();
        Flit flit;
        flit.packet_type = NOC_WEIGHT;
        flit.dest_x = destination % g.mesh_x;
        flit.dest_y = destination / g.mesh_x;
        flit.source_id = owner;
        flit.block_id = node.command->block_a;
        flit.epoch = node.command->block_b;
        flit.last = beats[flat_port(p)] == SPINS - 1;
        injection[owner] = flit;
        selected[owner] = p;
        wr[flat_port(p)] = comb[owner].input_ready[LOCAL];
        noc("inject", owner, "local", flit, wr[flat_port(p)]);
    }
    for (const auto& [node, flit] : phase.control_packets) {
        if (injection[node].has_value()) throw std::runtime_error("control phase overlaps undrained weight injection");
        injection[node] = flit;
        bool accepted = comb[node].input_ready[LOCAL];
        noc("inject", node, "local", flit, accepted);
        if (accepted) control_accepted.insert(node);
    }
    for (int node = 0; node < node_count; ++node) {
        const auto& flit_opt = comb[node].output_flits[LOCAL];
        if (!flit_opt.has_value()) continue;
        const Flit& flit = *flit_opt;
        if (flit.packet_type != NOC_WEIGHT) {
            ready[node] = true;
            control_received.emplace_back(node, flit);
            noc("eject", node, "local", flit, true);
            continue;
        }
        int dst = flit.block_id;
        EndpointCore& core = cores[dst];
        int owner = cross_start_ + flit.source_id;
        SourcePort source_port{owner, -1};
        CoreIterationOutputs co = core.outputs();
        if (co.job_ready) {
            jobs[dst] = flit.epoch;
            next_active[dst] = source_port;
        }
        int h0 = dst / g.cores_per_h0;
        auto choice = external_choice.find(h0);
        if (active_source[dst] == source_port && co.weight_ready && !used_h0.count(h0) && allow_weight &&
            choice != external_choice.end() && choice->second == source_port) {
            weights.insert(dst);
            ready[node] = true;
            used_h0.insert(h0);
            external_accepted.emplace_back(h0, source_port);
        }
        event(ready[node] ? "eject" : "eject_stall", owner, dst, flit.epoch);
        noc("eject", node, "local", flit, ready[node]);
    }
    // Local (H0 and H1) command handshake.
    for (int s = 0; s < cross_start_; ++s) {
        for (int e = 0; e < engines[s]; ++e) {
            const StreamerNodeOutput& node = out[s].node[e];
            if (!node.command_valid) continue;
            int dst = node.command->block_a;
            if (!jobs.count(dst) && cores[dst].outputs().job_ready) {
                jobs[dst] = node.command->block_b;
                cr[flat_port(SourcePort{s, e})] = true;
                next_active[dst] = SourcePort{s, e};
            }
        }
    }
    // Local weight delivery.
    for (int s = 0; s < cross_start_; ++s) {
        for (int e = 0; e < engines[s]; ++e) {
            const StreamerNodeOutput& node = out[s].node[e];
            if (!node.weight_valid) continue;
            SourcePort p{s, e};
            int dst = node.command->block_a;
            int h0 = dst / g.cores_per_h0;
            auto choice = external_choice.find(h0);
            bool route_available =
                !weights.count(dst) &&
                (s < total_h0_ || (choice != external_choice.end() && choice->second == p && !used_h0.count(h0)));
            if (active_source[dst] == p && route_available) {
                bool accept = cores[dst].outputs().weight_ready && allow_weight;
                wr[flat_port(p)] = accept;
                if (accept) weights.insert(dst);
                if (s >= total_h0_) {
                    used_h0.insert(h0);
                    if (accept) external_accepted.emplace_back(h0, p);
                }
            }
        }
    }
    for (const auto& [h0, source] : external_accepted) {
        external_beats[h0] = (external_beats[h0] + 1) % SPINS;
        if (external_beats[h0]) external_locks[h0] = source;
        else external_locks.erase(h0);
    }
    // Streamers: dispatch, handshakes, events, tick, audit.
    for (int s = 0; s < systems; ++s) {
        std::vector<std::optional<DmaCommand>> cmds(engines[s]);
        std::vector<bool> ncr(engines[s]), nwr(engines[s]);
        for (int e = 0; e < engines[s]; ++e) {
            SourcePort p{s, e};
            bool available = out[s].scheduler_ready[e];
            if (phase.dispatch_enable && available && !queues[s].empty()) {
                cmds[e] = queues[s].front();
                queues[s].pop_front();
                event("schedule", s, cmds[e]->block_a, cmds[e]->block_b);
            }
            const StreamerNodeOutput& node = out[s].node[e];
            ncr[e] = cr[flat_port(p)];
            nwr[e] = wr[flat_port(p)];
            if (node.command_valid && ncr[e]) event("command", s, node.command->block_a, node.command->block_b);
            if (node.weight_valid) {
                event(nwr[e] ? "weight" : "weight_stall", s, node.command->block_a, node.command->block_b);
                if (nwr[e] && s < cross_start_) {
                    audit_local_delivery_beats[s] += 1;
                    audit_local_delivery_touched[s] = true;
                }
            }
        }
        streamers[s].tick(base_out[s], cmds, ncr, nwr, request_ready[s], responses[s]);
        int occupied = 0;
        for (const auto& lane : streamers[s].base.slots)
            for (const auto& slot : lane)
                if (slot.allocated) ++occupied;
        audit_peak_source_blocks[s] = std::max(audit_peak_source_blocks[s], occupied);
        audit_peak_outstanding_requests[s] = std::max(audit_peak_outstanding_requests[s], streamers[s].outstanding());
    }
    // use_mesh: activity, packet-lock release, link transfers, mesh edge.
    {
        bool any_injection = false;
        for (const auto& f : injection)
            if (f.has_value()) { any_injection = true; break; }
        bool any_output = false;
        for (const auto& o : comb) {
            for (const auto& f : o.output_flits)
                if (f.has_value()) { any_output = true; break; }
            if (any_output) break;
        }
        last_network_activity = any_injection || any_output || mesh.has_link_data();
    }
    for (const auto& [owner, p] : selected) {
        if (wr[flat_port(p)]) {
            beats[flat_port(p)] = (beats[flat_port(p)] + 1) % SPINS;
            if (beats[flat_port(p)] == 0) packet_locks.erase(owner);
        }
    }
    for (int node = 0; node < node_count; ++node) {
        for (int port : {NORTH, SOUTH, EAST, WEST}) {
            const auto& flit = comb[node].output_flits[port];
            if (!flit.has_value()) continue;
            bool accepted = mesh.link_ready(node, port, comb);
            noc("link", node, link_name(port), *flit, accepted);
            if (flit->packet_type == NOC_WEIGHT)
                event(accepted ? "link" : "link_stall", cross_start_ + flit->source_id, flit->block_id, flit->epoch);
        }
    }
    mesh.tick(injection, ready, comb);

    // Cores (compress_idle): wake every core on a global control input.
    const CoreInputs& gi = phase.core_inputs;
    if (gi.rst || gi.init_start || gi.weight_init_valid || gi.iter_start || gi.partials_done || gi.commit || gi.done) {
        for (int c = 0; c < g.total_blocks(); ++c) running_cores.insert(c);
    }
    for (const auto& [dst, _] : jobs) running_cores.insert(dst);
    for (int dst : weights) running_cores.insert(dst);
    std::vector<std::map<int, int>> bank_requests(total_h0_);
    for (int dst : running_cores) {
        CoreIterationOutputs o = cores[dst].outputs();
        if (o.state_req_valid) bank_requests[dst / g.cores_per_h0][dst % g.cores_per_h0] = o.state_req_block_id;
    }
    std::vector<std::map<int, bool>> bank_ready(total_h0_);
    std::vector<std::map<int, int>> bank_responses(total_h0_);
    for (int h = 0; h < total_h0_; ++h) {
        bank_ready[h] = state_banks[h].ready(bank_requests[h]);
        bank_responses[h] = state_banks[h].responses;
    }
    for (int h = 0; h < total_h0_; ++h) {
        bool write = false;
        int write_index = 0;
        auto it = phase.bank_writes.find(h);
        if (it != phase.bank_writes.end()) {
            write = true;
            write_index = it->second;
        }
        if (phase.publish_valid) {
            write = true;
            write_index = phase.publish_index;
        }
        state_banks[h].tick(bank_requests[h], write, write_index);
    }
    std::set<int> next_running;
    for (int dst : running_cores) {
        EndpointCore& core = cores[dst];
        int h = dst / g.cores_per_h0, lane = dst % g.cores_per_h0;
        auto response = bank_responses[h].find(lane);
        bool has_response = response != bank_responses[h].end();
        auto rdy = bank_ready[h].find(lane);
        bool req_ready = rdy != bank_ready[h].end() && rdy->second;
        if (core.outputs().job_done) {
            completed += 1;
            event("retire", active_source[dst].system, dst, core.source);
        }
        if (observers_.state) {
            if (req_ready) observers_.state(cycle, "request", dst, bank_requests[h][lane]);
            if (has_response) observers_.state(cycle, "response", dst, 0);
        }
        CoreInputs in = gi;
        auto job = jobs.find(dst);
        in.job_valid = job != jobs.end();
        in.source = in.job_valid ? job->second : 0;
        in.transpose = dst > in.source;
        in.weight_valid = weights.count(dst) != 0;
        in.state_req_ready = req_ready;
        in.state_rsp_valid = has_response;
        core.tick(in);
        if (core.off_state != EndpointCore::OFF_IDLE || core.core_state == EndpointCore::INIT ||
            core.core_state == EndpointCore::FINALIZE || core.core_state == EndpointCore::COMMIT ||
            (core.core_state == EndpointCore::ACCUMULATE && (!core.diagonal_captured || core.partials_done_pending)))
            next_running.insert(dst);
    }
    running_cores.swap(next_running);
    active_source.swap(next_active);
    backend.tick(ticks_per_cycle);
    for (int s = 0; s < systems; ++s) {
        StreamerOutputs post = streamers[s].base.outputs();
        for (int lane = 0; lane < mem_lanes; ++lane) {
            const auto& r = post.memory_requests[lane];
            request_ready[s][lane] = r.has_value() && backend.send(s, r->address, r->tag);
        }
        for (int lane = 0; lane < mem_lanes; ++lane) responses[s][lane] = backend.pop(s);
    }
    cycle += 1;
}

}  // namespace azilla
