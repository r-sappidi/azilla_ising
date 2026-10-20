#include "exact_events.hpp"

#include <algorithm>
#include <filesystem>
#include <string_view>
#include <fstream>
#include <set>
#include <stdexcept>

#include "cores_only_events.hpp"
#include "metrics.hpp"
#include "sha256.hpp"

namespace azilla {

namespace {
const char* CERTIFIED_RAMULATOR_CONFIG_SHA256 = "36d1d3d56b229468a029eae8c324491d51720977a2ab9a08360d5b9e6f130960";

const char* direction_name(int port) {
    switch (port) {
        case NORTH: return "north";
        case SOUTH: return "south";
        case EAST: return "east";
        case WEST: return "west";
        default: return "local";
    }
}

bool queues_equal(const std::vector<std::deque<WorkItem>>& a, const std::vector<std::deque<WorkItem>>& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i)
        if (a[i].size() != b[i].size() || !std::equal(a[i].begin(), a[i].end(), b[i].begin())) return false;
    return true;
}
}  // namespace

std::string file_sha256(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open " + path);
    Sha256 digest;
    std::vector<unsigned char> buffer(1 << 20);
    while (input) {
        input.read(reinterpret_cast<char*>(buffer.data()), std::streamsize(buffer.size()));
        std::streamsize got = input.gcount();
        if (got > 0) digest.update(buffer.data(), size_t(got));
    }
    return digest.hexdigest();
}

RamulatorEventPerformanceModel::RamulatorEventPerformanceModel(
    const Geometry& geometry, const BlockOccupancyDataset& dataset, const std::string& dataset_path,
    const std::string& ramulator_library, const std::string& ramulator_config, int mem_lanes, int ticks_per_cycle,
    long idle_tick_modulus, const PerformanceConfig& config)
    : geometry_(geometry), dataset_(dataset), dataset_path_(dataset_path), mem_lanes_(mem_lanes),
      ticks_per_cycle_(ticks_per_cycle), idle_tick_modulus_(idle_tick_modulus), config_(config), index_{geometry} {
    ramulator_config_sha256_ = file_sha256(ramulator_config);
    if (dataset.spin_count != geometry.spin_count())
        throw std::runtime_error("dataset has " + std::to_string(dataset.spin_count) + " spins; geometry has " +
                                 std::to_string(geometry.spin_count()));
    if (config_.execution_mode != "cir" && config_.execution_mode != "cores-only" &&
        config_.execution_mode != "flat-stationary" && config_.execution_mode != "cir-h0-anchored")
        throw std::runtime_error(
            "the C++ port implements execution modes 'cir', 'cores-only', 'flat-stationary' and 'cir-h0-anchored' only");
    const bool flat = config_.execution_mode == "flat-stationary";
    const bool anchored = config_.execution_mode == "cir-h0-anchored";
    if (config_.execution_mode == "cores-only" && config_.endpoint_policy != "streaming" &&
        config_.endpoint_policy != "buffered-v3")
        throw std::runtime_error("cores-only endpoint policy must be 'streaming' or 'buffered-v3'");
    config_.interconnect.validate();
    if (config_.chiplet_link_latency_cycles < 0) throw std::runtime_error("chiplet link latency must be non-negative");

    const int total_h0 = index_.total_h0();
    const int system_count = total_h0 + 2 * geometry.node_count();
    backend_ = std::make_unique<RamulatorBackend>(ramulator_library, ramulator_config, dataset_path, system_count,
                                                  geometry.total_blocks(), /*timing_only=*/true, config_.dram_threads);
    frontends_.resize(index_.count());
    local_nodes_.resize(index_.count());
    auto make_frontend = [&](int system_id, int engines) {
        Frontend frontend;
        frontend.system_id = system_id;
        frontend.streamer =
            std::make_unique<DramWeightStreamer>(engines, geometry.total_blocks(), mem_lanes_, mem_lanes_,
                                                 config_.max_outstanding);
        frontend.request_ready.assign(mem_lanes_, false);
        frontend.responses.assign(mem_lanes_, std::nullopt);
        return frontend;
    };
    for (int global_h0 = 0; global_h0 < total_h0; ++global_h0) {
        int node = global_h0 / geometry.h0_per_h1;
        int h0 = global_h0 % geometry.h0_per_h1;
        int t = index_.h0(global_h0);
        frontends_[t] = make_frontend(global_h0, config_.h0_mvm_count);
        // Flat baseline: the H0 state table holds every block it consumes
        // (global index) and its engines run directed jobs.
        // (Anchored: global index too, symmetric engines, plus one uplink.)
        const int first_block = node * geometry.blocks_per_h1() + h0 * geometry.cores_per_h0;
        local_nodes_[t] = std::make_unique<HierarchyNode>(
            (flat || anchored) ? geometry.total_blocks() : geometry.cores_per_h0, config_.h0_mvm_count, flat);
        h0_adapters_.push_back(std::make_unique<H0Adapter>(geometry.cores_per_h0, config_.h0_mvm_count, first_block));
        if (anchored)
            uplinks_.emplace_back(first_block, geometry.cores_per_h0, geometry.blocks_per_h1(), geometry.mesh_x, node,
                                  config_.h0_mvm_count, config_.anchored_uplink_packets, config_.anchored_accumulate,
                                  geometry.total_blocks());
    }
    for (int node = 0; node < geometry.node_count(); ++node) {
        int h1_t = index_.h1(node), cross_t = index_.cross(node);
        frontends_[h1_t] = make_frontend(total_h0 + node, config_.h1_mvm_count);
        local_nodes_[h1_t] = std::make_unique<HierarchyNode>(geometry.blocks_per_h1(), config_.h1_mvm_count);
        // Anchored: the H1 child crossbar also switches the h0_per_h1 uplinks
        // (sources h1_mvm_count .. h1_mvm_count + h0_per_h1 - 1).
        h1_adapters_.push_back(std::make_unique<H1ChildAdapter>(
            geometry.h0_per_h1, geometry.cores_per_h0, config_.h1_mvm_count + (anchored ? geometry.h0_per_h1 : 0),
            node * geometry.blocks_per_h1(), /*parent_first=*/anchored));
        if (anchored) uplink_arbiters_.emplace_back(geometry.h0_per_h1);
        cross_nodes_.push_back(std::make_unique<CrossH1Node>(geometry.total_blocks(), config_.cross_mvm_count,
                                                             geometry.blocks_per_h1(), geometry.mesh_x, node));
        frontends_[cross_t] = make_frontend(total_h0 + geometry.node_count() + node, config_.cross_mvm_count);
    }
    mesh_ = std::make_unique<Mesh>(geometry.mesh_x, geometry.mesh_y, config_.fifo_depth, config_.interconnect);
    injection_arbiters_.resize(geometry.node_count());
}

void RamulatorEventPerformanceModel::reset_timing_state() {
    mesh_->reset();
    for (auto& arbiter : injection_arbiters_) arbiter.reset();
    for (auto& uplink : uplinks_) uplink.reset();
    for (auto& arbiter : uplink_arbiters_) arbiter.reset();
    for (auto& adapter : h0_adapters_) adapter->reset();
    for (auto& adapter : h1_adapters_) adapter->reset();
    for (auto& frontend : frontends_) {
        frontend.streamer->reset();
        frontend.request_ready.assign(mem_lanes_, false);
        frontend.responses.assign(mem_lanes_, std::nullopt);
    }
    for (auto& node : local_nodes_)
        if (node) {
            node->reset();
            node->node_state = HierarchyNode::IDLE;
        }
    for (auto& cross : cross_nodes_) {
        cross->reset();
        cross->node.node_state = HierarchyNode::IDLE;
    }
    noc_resources_.clear();
    hop_histogram_.clear();
    for (size_t node = 0; node < mesh_->routers.size(); ++node) {
        const auto& router = mesh_->routers[node];
        noc_resources_[{"inject", int(node), "local"}] = NocAccumulator{};
        noc_resources_[{"eject", int(node), "local"}] = NocAccumulator{};
        for (const auto& n : Mesh::neighbors(router))
            if (mesh_->in_mesh(n.nx, n.ny)) noc_resources_[{"link", int(node), direction_name(n.out_port)}] = NocAccumulator{};
    }
}

void RamulatorEventPerformanceModel::observe_noc_resource(const char* scope, int node, const char* direction,
                                                           const Flit& flit, bool accepted) {
    NocAccumulator& resource = noc_resources_[{scope, node, direction}];
    resource.offered_cycles += 1;
    if (!accepted) {
        resource.stall_cycles += 1;
        return;
    }
    resource.accepted_flits += 1;
    int packet_type = flit.packet_type;
    resource.type_flits[(0 <= packet_type && packet_type < 3) ? packet_type : 3] += 1;
    if (flit.last) resource.packets += 1;
    if (std::string(scope) == "inject") {
        int source_x = node % geometry_.mesh_x, source_y = node / geometry_.mesh_x;
        int hops = std::abs(source_x - flit.dest_x) + std::abs(source_y - flit.dest_y);
        hop_histogram_[hops] += 1;
    }
}

Transfer RamulatorEventPerformanceModel::make_transfer(long cycle, const char* scope, int node, const char* direction,
                                                       const Flit& flit) {
    return Transfer(cycle, scope, node, direction, flit.packet_type, flit.source_id, flit.epoch, flit.block_id,
                    flit.dest_x, flit.dest_y, flit.last ? 1 : 0);
}

void RamulatorEventPerformanceModel::record_transfer(Transfer&& transfer) {
    if (!transfer_stream_) {
        transfers.push_back(std::move(transfer));
        return;
    }
    *transfer_stream_ << format_transfer_line(transfer, geometry_);
    ++streamed_transfers_;
    if (!*transfer_stream_) throw std::runtime_error("transfer stream write failed");
}

void RamulatorEventPerformanceModel::open_transfer_stream(const std::string& path) {
    std::filesystem::path p(path);
    if (p.has_parent_path()) std::filesystem::create_directories(p.parent_path());
    transfer_stream_ = std::make_unique<std::ofstream>(path, std::ios::binary);
    if (!*transfer_stream_) throw std::runtime_error("cannot create transfer stream " + path);
    *transfer_stream_ << transfer_trace_header();
    streamed_transfers_ = 0;
}

void RamulatorEventPerformanceModel::close_transfer_stream() {
    if (!transfer_stream_) return;
    transfer_stream_->flush();
    if (!*transfer_stream_) throw std::runtime_error("transfer stream flush failed");
    transfer_stream_->close();
    transfer_stream_.reset();
}

void RamulatorEventPerformanceModel::record_network(const std::vector<RouterOutputs>& comb,
                                                    const std::vector<std::optional<Flit>>& injections,
                                                    const std::vector<bool>& local_ready,
                                                    PerformanceCounters& counters, long absolute_cycle) {
    const int node_count = geometry_.node_count();
    for (int node = 0; node < node_count; ++node) {
        if (!injections[node]) continue;
        const Flit& flit = *injections[node];
        bool accepted = comb[node].input_ready[LOCAL];
        observe_noc_resource("inject", node, "local", flit, accepted);
        if (accepted) {
            counters.injected_flits += 1;
            int packet_type = flit.packet_type;
            counters.type_flits[(0 <= packet_type && packet_type < 3) ? packet_type : 3] += 1;
            record_transfer(make_transfer(absolute_cycle, "inject", node, "local", flit));
        } else {
            counters.injection_stalls += 1;
        }
    }
    for (int node = 0; node < node_count; ++node) {
        const RouterOutputs& output = comb[node];
        if (output.output_flits[LOCAL].has_value()) {
            const Flit& flit = *output.output_flits[LOCAL];
            bool accepted = local_ready[node];
            observe_noc_resource("eject", node, "local", flit, accepted);
            if (accepted) {
                counters.ejected_flits += 1;
                record_transfer(make_transfer(absolute_cycle, "eject", node, "local", flit));
            } else {
                counters.ejection_stalls += 1;
            }
        }
        for (const auto& n : Mesh::neighbors(mesh_->routers[node])) {
            const auto& flit = output.output_flits[n.out_port];
            if (!flit) continue;
            bool ready = mesh_->link_ready(node, n.out_port, comb);
            const char* direction = direction_name(n.out_port);
            observe_noc_resource("link", node, direction, *flit, ready);
            if (ready) {
                counters.physical_link_flits += 1;
                record_transfer(make_transfer(absolute_cycle, "link", node, direction, *flit));
            } else {
                counters.link_stalls += 1;
            }
        }
    }
}

void RamulatorEventPerformanceModel::memory_edge(const std::vector<StreamerOutputs>& stream_outputs) {
    backend_->tick(ticks_per_cycle_);
    for (size_t t = 0; t < frontends_.size(); ++t) {
        Frontend& frontend = frontends_[t];
        const auto& requests = stream_outputs[t].memory_requests;
        for (int lane = 0; lane < mem_lanes_; ++lane)
            frontend.request_ready[lane] =
                requests[lane].has_value() && backend_->send(frontend.system_id, requests[lane]->address, requests[lane]->tag);
        for (int lane = 0; lane < mem_lanes_; ++lane) frontend.responses[lane] = backend_->pop(frontend.system_id);
    }
}

std::vector<NocResourceStats> RamulatorEventPerformanceModel::noc_stats() const {
    std::vector<NocResourceStats> rows;
    for (const auto& [key, values] : noc_resources_) {
        const auto& [scope, node, direction] = key;
        rows.push_back(NocResourceStats{scope, node, node % geometry_.mesh_x, node / geometry_.mesh_x, direction,
                                        values.offered_cycles, values.accepted_flits, values.stall_cycles,
                                        values.packets, values.type_flits[0], values.type_flits[1],
                                        values.type_flits[2], values.type_flits[3]});
    }
    return rows;  // std::map iteration order == Python sorted() on (scope, node, direction)
}

std::vector<DramPerformanceStats> RamulatorEventPerformanceModel::dram_stats(const CompiledSchedule& compiled) const {
    std::vector<DramPerformanceStats> rows;
    // Frontends are indexed in system-id order already.
    for (size_t t = 0; t < frontends_.size(); ++t) {
        const Frontend& frontend = frontends_[t];
        WorkTarget target = index_.target(int(t));
        long jobs;
        if (target.level == Level::H0) jobs = long(compiled.h0[target.node * geometry_.h0_per_h1 + target.index].size());
        else if (target.level == Level::H1) jobs = long(compiled.h1[target.node].size());
        else jobs = long(compiled.cross[target.node].size());
        RamulatorSystemStats raw = backend_->stats(frontend.system_id);
        uint64_t expected_requests = uint64_t(jobs) * 32;
        if (raw.accepted != expected_requests || raw.completed != expected_requests || raw.outstanding() != 0)
            throw std::runtime_error("DRAM system " + std::to_string(frontend.system_id) + " did not drain: expected=" +
                                     std::to_string(expected_requests) + " accepted=" + std::to_string(raw.accepted) +
                                     " completed=" + std::to_string(raw.completed) +
                                     " outstanding=" + std::to_string(raw.outstanding()));
        rows.push_back(DramPerformanceStats{frontend.system_id, level_name(target.level), target.node, target.index,
                                            frontend.streamer->mvm_count, jobs, raw.accepted, raw.rejected,
                                            raw.completed, raw.outstanding(), raw.average_latency_ticks(),
                                            raw.latency_max_ticks, raw.average_latency_ticks() / double(ticks_per_cycle_),
                                            double(raw.latency_max_ticks) / double(ticks_per_cycle_)});
    }
    return rows;
}

void RamulatorEventPerformanceModel::validate_metric_totals(const PerformanceCounters& counters,
                                                            const std::vector<NocResourceStats>& res,
                                                            const std::vector<std::pair<int, long>>& hop_histogram) {
    long accepted[3] = {0, 0, 0}, stalls[3] = {0, 0, 0};
    for (const auto& row : res) {
        int which = row.scope == "inject" ? 0 : row.scope == "eject" ? 1 : 2;
        accepted[which] += row.accepted_flits;
        stalls[which] += row.stall_cycles;
    }
    if (accepted[0] != counters.injected_flits || accepted[1] != counters.ejected_flits ||
        accepted[2] != counters.physical_link_flits || stalls[0] != counters.injection_stalls ||
        stalls[1] != counters.ejection_stalls || stalls[2] != counters.link_stalls)
        throw std::runtime_error("NoC resource counters do not match aggregate counters");
    long hop_flits = 0;
    for (const auto& [hops, flits] : hop_histogram) hop_flits += long(hops) * flits;
    if (hop_flits != counters.physical_link_flits)
        throw std::runtime_error("hop histogram does not match physical-link traffic");
}

void RamulatorEventPerformanceModel::open_event_stream(const std::string& path) {
    std::filesystem::path p(path);
    if (p.has_parent_path()) std::filesystem::create_directories(p.parent_path());
    event_stream_ = std::make_unique<std::ofstream>(path, std::ios::binary);
    if (!*event_stream_) throw std::runtime_error("cannot create event stream " + path);
    *event_stream_ << "stream,cycle,event,a,b,c\n";
}

void RamulatorEventPerformanceModel::close_event_stream() {
    if (!event_stream_) return;
    event_stream_->flush();
    if (!*event_stream_) throw std::runtime_error("event stream flush failed");
    event_stream_->close();
    event_stream_.reset();
}

ExactEventResult RamulatorEventPerformanceModel::run(const std::vector<ScheduledBlock>* schedule) {
    reset_timing_state();
    transfers.clear();
    std::vector<ScheduledBlock> schedule_records;
    if (schedule == nullptr) {
        for (const auto& [a, b] : dataset_.active_pairs) schedule_records.push_back({a, b, -1});
    } else {
        schedule_records = *schedule;
    }
    const long initialization = 3 + long(geometry_.total_blocks()) * 33;
    if (config_.execution_mode == "cores-only") return run_cores_only(schedule_records, initialization);
    const bool flat = config_.execution_mode == "flat-stationary";
    const bool anchored = config_.execution_mode == "cir-h0-anchored";
    CompiledSchedule compiled = flat       ? compile_flat_schedule(geometry_, schedule_records)
                                : anchored ? compile_anchored_schedule(geometry_, schedule_records, config_.h0_mvm_count)
                                           : compile_schedule(geometry_, schedule_records);
    const int chiplet_latency = config_.chiplet_link_latency_cycles;
    const int node_count = geometry_.node_count();
    const int total_h0 = index_.total_h0();
    const int target_count = index_.count();

    // ---------------------------------------------------------- publication
    std::vector<BlockPair> publication_pairs =
        compiled.has_explicit_publications ? compiled.explicit_publications : compiled.publications();
    std::vector<PacketRelease> publications;
    for (size_t index = 0; index < publication_pairs.size(); ++index) {
        const auto& [block, owner] = publication_pairs[index];
        publications.push_back(PacketRelease{chiplet_latency + long(index) * 2, block / geometry_.blocks_per_h1(),
                                             owner, NOC_STATE, block, 1});
    }
    Mesh publication_mesh(geometry_.mesh_x, geometry_.mesh_y, config_.fifo_depth, config_.interconnect);
    NetworkReplayResult publication = replay_packets(
        publication_mesh, publications, 0, 4,
        [&](long cycle, const char* scope, int node, const char* direction, const Flit& flit) {
            record_transfer(make_transfer(initialization + 2 + cycle, scope, node, direction, flit));
        },
        [&](long, const char* scope, int node, const char* direction, const Flit& flit, bool accepted) {
            observe_noc_resource(scope, node, direction, flit, accepted);
        });
    // Reuse the drained mesh: arbitration snapshots survive the boundary.
    *mesh_ = publication_mesh;
    mesh_->advance_idle(2);

    PerformanceCounters counters;
    counters.injected_flits = publication.injected_flits;
    counters.ejected_flits = publication.ejected_flits;
    counters.physical_link_flits = publication.physical_link_flits;
    counters.injection_stalls = publication.injection_stalls;
    counters.link_stalls = publication.link_stalls;
    counters.type_flits[0] = publication.injected_flits;

    const long compute_start = 2 + publication.elapsed_cycles + 2;

    long idle_ticks = (initialization + compute_start - 1) * ticks_per_cycle_;
    if (idle_tick_modulus_ > 0) idle_ticks %= idle_tick_modulus_;
    backend_->tick(idle_ticks);

    for (auto& cross : cross_nodes_) cross->node.node_state = HierarchyNode::RUN;

    // Flat baseline: an H0 state table is filled with one write per distinct
    // source block it consumes (local and published), like the H1 table fill.
    std::vector<long> flat_h0_sources(total_h0, 0);
    if (flat || anchored)
        for (int global_h0 = 0; global_h0 < total_h0; ++global_h0) {
            std::set<int> sources;
            for (const auto& work : compiled.h0[global_h0]) {
                sources.insert(work.block_b);
                if (anchored) sources.insert(work.block_a);
            }
            flat_h0_sources[global_h0] = long(sources.size());
        }
    std::vector<long> local_start_pulse(target_count, -1);  // -1: none
    for (int t = 0; t < target_count; ++t) {
        if (!local_nodes_[t]) continue;
        WorkTarget target = index_.target(t);
        long ready_global = target.level == Level::H0
                                ? ((flat || anchored) ? flat_h0_sources[t] : long(geometry_.cores_per_h0)) + 2
                                : geometry_.blocks_per_h1() + 2 + chiplet_latency;
        long ready_local = ready_global - compute_start;
        if (ready_local <= 0) {
            local_nodes_[t]->node_state = HierarchyNode::RUN;
        } else {
            local_nodes_[t]->node_state = HierarchyNode::IDLE;
            local_start_pulse[t] = ready_local - 1;
        }
    }

    ConcurrentDispatcher dispatcher(compiled, config_.h0_mvm_count, config_.h1_mvm_count, config_.cross_mvm_count);
    enum Phase { DISPATCH, BLANK, DRAIN, RUN, CROSS_QUIET, DONE_INJECT, DONE_QUIET, TAIL };
    Phase phase = dispatcher.done() ? BLANK : DISPATCH;
    int quiet = 0;
    std::vector<bool> done_pending(node_count, false);
    int done_pending_count = 0;
    std::vector<long> done_received(node_count, -1);
    std::vector<long> local_done(target_count, -1);
    std::vector<long> flush_end(total_h0, -1);
    int local_done_count = 0;
    const int local_node_count = total_h0 + node_count;
    std::vector<long> cross_done(node_count, -1);
    long done_quiet_end = -1;
    std::vector<long> node_completion(node_count, 0);
    long local_end = 0;
    long cycle = 0;

    // Per-cycle scratch.
    std::vector<StreamerOutputs> stream_outputs(target_count);
    std::vector<HierarchyOutputs> node_outputs(target_count);  // local nodes and cross inner nodes
    std::vector<CrossOutputs> cross_outputs(node_count);
    std::vector<H1ChildOutputs> h1_adapter_outputs(node_count);
    std::vector<H0AdapterOutputs> h0_adapter_outputs(total_h0);
    std::vector<RoutedPartial> parents(node_count);
    std::vector<bool> local_ready(node_count);
    std::vector<std::optional<Flit>> h1_packets(node_count);
    std::vector<std::optional<Flit>> injections(node_count);
    std::vector<InjectionOutputs> arbiter_outputs(node_count);
    std::vector<UplinkOutputs> uplink_outputs(anchored ? total_h0 : 0);
    std::vector<UplinkInjectionOutputs> uplink_arbiter_outputs(anchored ? node_count : 0);
    std::vector<std::optional<Flit>> remote_tx(geometry_.h0_per_h1);
    std::vector<PartialOutput> h1_sources;
    std::vector<std::vector<bool>> ready(target_count);
    std::vector<std::vector<std::optional<DmaCommand>>> empty_commands(target_count);
    for (int t = 0; t < target_count; ++t) {
        ready[t].assign(dispatcher.engines(t), false);
        empty_commands[t].assign(dispatcher.engines(t), std::nullopt);
    }

    while (true) {
        if (cycle > config_.max_cycles) throw std::runtime_error("event Ramulator simulation exceeded max cycles");

        // Pre-edge combinational snapshots (identical to repeated outputs()).
        for (int t = 0; t < target_count; ++t) {
            stream_outputs[t] = frontends_[t].streamer->outputs();
            WorkTarget target = index_.target(t);
            node_outputs[t] = target.level == Level::CROSS ? cross_nodes_[target.node]->node.outputs()
                                                           : local_nodes_[t]->outputs();
        }
        for (int node = 0; node < node_count; ++node)
            cross_outputs[node] = cross_nodes_[node]->outputs(node_outputs[index_.cross(node)], 0);

        if (phase == RUN) {
            bool all_done = true;
            for (int node = 0; node < node_count; ++node)
                if (!cross_outputs[node].iter_done) { all_done = false; break; }
            // Anchored: the H0 nodes are the mesh partial sources, so the
            // quiet window may only open once every one of them is done and
            // every uplink has drained (flushed in accumulate mode, FIFOs
            // empty otherwise; a queued sibling-bound packet is not NoC
            // activity and would otherwise be delivered after EPOCH_DONE).
            // October 15 fix: FIFO mode previously ignored the uplink FIFOs.
            if (anchored)
                for (int global_h0 = 0; global_h0 < total_h0 && all_done; ++global_h0)
                    if (!node_outputs[index_.h0(global_h0)].iter_done || !uplinks_[global_h0].drained())
                        all_done = false;
            if (all_done) {
                phase = CROSS_QUIET;
                quiet = 0;
            }
        }
        bool schedule_done = false;
        if (phase == DRAIN) {
            bool all_idle = true;
            for (int t = 0; t < target_count; ++t)
                if (!stream_outputs[t].idle) { all_idle = false; break; }
            if (all_idle) {
                schedule_done = true;
                phase = RUN;
            }
        }

        std::vector<RouterOutputs> comb = mesh_->all_outputs();
        for (int node = 0; node < node_count; ++node) {
            const auto& rx = comb[node].output_flits[LOCAL];
            bool rx_partial = rx.has_value() && rx->packet_type == NOC_PARTIAL;
            parents[node] = RoutedPartial{rx_partial, rx ? rx->block_id : 0};
            if (anchored) {
                // Uplink selection per H0, then the H1 crossbar sees H1 engines
                // followed by the sibling-bound uplink partials.
                h1_sources = node_outputs[index_.h1(node)].partials;
                for (int h0 = 0; h0 < geometry_.h0_per_h1; ++h0) {
                    int global_h0 = node * geometry_.h0_per_h1 + h0;
                    uplink_outputs[global_h0] = uplinks_[global_h0].outputs(node_outputs[index_.h0(global_h0)].partials);
                    const UplinkOutputs& u = uplink_outputs[global_h0];
                    h1_sources.push_back(u.head_valid && u.same_h1 ? u.partial : PartialOutput{});
                    remote_tx[h0] = u.head_valid && !u.same_h1 ? u.tx : std::nullopt;
                }
                h1_adapter_outputs[node] = h1_adapters_[node]->outputs(h1_sources, parents[node]);
                uplink_arbiter_outputs[node] = uplink_arbiters_[node].outputs(remote_tx);
            } else {
                h1_adapter_outputs[node] = h1_adapters_[node]->outputs(node_outputs[index_.h1(node)].partials, parents[node]);
            }
            local_ready[node] = rx_partial ? h1_adapter_outputs[node].parent_ready : true;
        }
        for (int global_h0 = 0; global_h0 < total_h0; ++global_h0)
            h0_adapter_outputs[global_h0] = h0_adapters_[global_h0]->outputs(node_outputs[index_.h0(global_h0)].partials);

        for (int node = 0; node < node_count; ++node) {
            h1_packets[node].reset();
            if (phase == DONE_INJECT && done_pending[node]) {
                Flit flit;
                flit.packet_type = NOC_EPOCH_DONE;
                flit.dest_x = node % geometry_.mesh_x;
                flit.dest_y = node / geometry_.mesh_x;
                flit.source_id = node;
                flit.last = true;
                h1_packets[node] = flit;
            }
        }

        bool any_injection = false;
        for (int node = 0; node < node_count; ++node) {
            if (anchored && cross_outputs[node].tx.has_value())
                throw std::runtime_error("cross node produced a partial in cir-h0-anchored mode");
            arbiter_outputs[node] = injection_arbiters_[node].outputs(
                anchored ? uplink_arbiter_outputs[node].tx : cross_outputs[node].tx, h1_packets[node],
                comb[node].input_ready[LOCAL]);
            injections[node] = arbiter_outputs[node].selected;
            if (injections[node]) any_injection = true;
        }

        bool activity = mesh_->has_link_data() || any_injection;
        if (!activity)
            for (const auto& output : comb) {
                for (const auto& flit : output.output_flits)
                    if (flit) { activity = true; break; }
                if (activity) break;
            }
        record_network(comb, injections, local_ready, counters, initialization + compute_start + cycle);

        const std::vector<std::vector<std::optional<DmaCommand>>>* scheduler_commands = &empty_commands;
        bool dispatch_finished = false;
        if (phase == DISPATCH) {
            for (int t = 0; t < target_count; ++t) ready[t] = stream_outputs[t].scheduler_ready;
            scheduler_commands = &dispatcher.drive(ready);
            dispatch_finished = dispatcher.done();
        }

        memory_edge(stream_outputs);

        // Hierarchy nodes (H0 and H1-local).
        for (int t = 0; t < target_count; ++t) {
            if (!local_nodes_[t]) continue;
            WorkTarget target = index_.target(t);
            const StreamerOutputs& stream = stream_outputs[t];
            std::vector<bool> partial_ready = target.level == Level::H0
                                                  ? h0_adapter_outputs[t].engine_ready
                                                  : h1_adapter_outputs[target.node].node_ready;
            if (anchored) {
                if (target.level == Level::H0) {
                    const UplinkOutputs& u = uplink_outputs[t];
                    if (u.selected_engine >= 0 && u.input_ready) partial_ready[u.selected_engine] = true;
                } else {
                    partial_ready.resize(config_.h1_mvm_count);  // crossbar sources beyond the H1 engines are uplinks
                }
            }
            std::vector<std::optional<DmaCommand>> commands(stream.node.size());
            std::vector<bool> weight_valid(stream.node.size());
            for (size_t e = 0; e < stream.node.size(); ++e) {
                if (stream.node[e].command_valid) commands[e] = stream.node[e].command;
                weight_valid[e] = stream.node[e].weight_valid;
            }
            local_nodes_[t]->tick(node_outputs[t], local_start_pulse[t] == cycle, schedule_done, commands, weight_valid,
                                  partial_ready);
        }
        // Cross-H1 nodes.
        for (int node = 0; node < node_count; ++node) {
            int t = index_.cross(node);
            const StreamerOutputs& stream = stream_outputs[t];
            std::vector<std::optional<DmaCommand>> commands(stream.node.size());
            std::vector<bool> weight_valid(stream.node.size());
            for (size_t e = 0; e < stream.node.size(); ++e) {
                if (stream.node[e].command_valid) commands[e] = stream.node[e].command;
                weight_valid[e] = stream.node[e].weight_valid;
            }
            cross_nodes_[node]->tick(node_outputs[t], cross_outputs[node], schedule_done, commands, weight_valid,
                                     arbiter_outputs[node].cross_ready);
        }
        // Adapters use pre-edge partials.
        for (int global_h0 = 0; global_h0 < total_h0; ++global_h0)
            h0_adapters_[global_h0]->tick(h0_adapter_outputs[global_h0], node_outputs[index_.h0(global_h0)].partials);
        for (int node = 0; node < node_count; ++node) {
            if (!anchored) {
                h1_adapters_[node]->tick(h1_adapter_outputs[node], node_outputs[index_.h1(node)].partials, parents[node]);
                continue;
            }
            h1_sources = node_outputs[index_.h1(node)].partials;
            for (int h0 = 0; h0 < geometry_.h0_per_h1; ++h0) {
                const UplinkOutputs& u = uplink_outputs[node * geometry_.h0_per_h1 + h0];
                h1_sources.push_back(u.head_valid && u.same_h1 ? u.partial : PartialOutput{});
            }
            h1_adapters_[node]->tick(h1_adapter_outputs[node], h1_sources, parents[node]);
            for (int h0 = 0; h0 < geometry_.h0_per_h1; ++h0) {
                int global_h0 = node * geometry_.h0_per_h1 + h0;
                const UplinkOutputs& u = uplink_outputs[global_h0];
                const HierarchyOutputs& ho = node_outputs[index_.h0(global_h0)];
                bool accepted = u.head_valid &&
                                (u.same_h1 ? h1_adapter_outputs[node].node_ready[config_.h1_mvm_count + h0]
                                           : (uplink_arbiter_outputs[node].selected_h0 == h0 &&
                                              arbiter_outputs[node].cross_ready));
                uplinks_[global_h0].tick(u, ho.partials, accepted);
                const auto& engines = local_nodes_[index_.h0(global_h0)]->engines;
                if (u.head_valid) {
                    if (u.same_h1) { counters.uplink_sibling_beats += accepted; counters.uplink_sibling_stalls += !accepted; }
                    else { counters.uplink_mesh_beats += accepted; counters.uplink_mesh_stalls += !accepted; }
                }
                for (size_t e = 0; e < ho.partials.size(); ++e) {
                    bool acc = h0_adapter_outputs[global_h0].engine_ready[e] ||
                               (int(e) == u.selected_engine && u.input_ready);
                    if (ho.partials[e].valid && !acc) {
                        counters.engine_output_stalls += 1;
                        int b = ho.partials[e].block_id;
                        bool local = uplinks_[global_h0].first_block <= b &&
                                     b < uplinks_[global_h0].first_block + uplinks_[global_h0].block_count;
                        if (local) counters.engine_local_output_stalls += 1;
                        else if (int(e) != u.selected_engine) counters.engine_uplink_wait_stalls += 1;
                        else counters.engine_uplink_full_stalls += 1;
                    }
                    if ((engines[e].result_valid[0] || engines[e].result_reserved[0]) &&
                        (engines[e].result_valid[1] || engines[e].result_reserved[1]))
                        counters.engine_result_full_cycles += 1;
                }
            }
            uplink_arbiters_[node].tick(uplink_arbiter_outputs[node], arbiter_outputs[node].cross_ready);
        }

        mesh_->tick(injections, local_ready, comb);
        for (int node = 0; node < node_count; ++node)
            injection_arbiters_[node].tick(arbiter_outputs[node], comb[node].input_ready[LOCAL]);

        for (int node = 0; node < node_count; ++node) {
            const auto& rx = comb[node].output_flits[LOCAL];
            if (rx.has_value() && local_ready[node] && rx->packet_type == NOC_EPOCH_DONE && done_received[node] < 0)
                done_received[node] = cycle;
        }

        for (int t = 0; t < target_count; ++t) {
            Frontend& frontend = frontends_[t];
            const HierarchyOutputs& before = node_outputs[t];
            frontend.streamer->tick(stream_outputs[t], (*scheduler_commands)[t], before.command_ready,
                                    before.weight_ready, frontend.request_ready, frontend.responses);
        }

        for (int t = 0; t < target_count; ++t) {
            if (!local_nodes_[t]) continue;
            if (local_done[t] < 0 && local_nodes_[t]->node_state == HierarchyNode::DONE) {
                local_done[t] = cycle + 1;
                ++local_done_count;
                if (anchored && config_.anchored_accumulate && t < total_h0) uplinks_[t].start_flush();
            }
        }
        if (anchored && config_.anchored_accumulate)
            for (int global_h0 = 0; global_h0 < total_h0; ++global_h0)
                if (flush_end[global_h0] < 0 && uplinks_[global_h0].flush_started() && uplinks_[global_h0].flushed()) {
                    flush_end[global_h0] = cycle + 1;
                    counters.flush_packets += uplinks_[global_h0].touched_remote_blocks();
                    counters.flush_cycles_max = std::max(counters.flush_cycles_max, flush_end[global_h0] - local_done[global_h0]);
                }
        for (int node = 0; node < node_count; ++node)
            if (cross_done[node] < 0 && cross_nodes_[node]->node.node_state == HierarchyNode::DONE)
                cross_done[node] = cycle + 1;

        if (phase == DISPATCH && dispatch_finished) {
            phase = BLANK;
        } else if (phase == BLANK) {
            phase = DRAIN;
        } else if (phase == CROSS_QUIET) {
            quiet = activity ? 0 : quiet + 1;
            if (quiet == 4) {
                phase = DONE_INJECT;
                std::fill(done_pending.begin(), done_pending.end(), true);
                done_pending_count = node_count;
            }
        } else if (phase == DONE_INJECT) {
            for (int node = 0; node < node_count; ++node)
                if (done_pending[node] && h1_packets[node].has_value() && arbiter_outputs[node].h1_ready) {
                    done_pending[node] = false;
                    --done_pending_count;
                }
            if (done_pending_count == 0) {
                phase = DONE_QUIET;
                quiet = 0;
            }
        } else if (phase == DONE_QUIET) {
            quiet = activity ? 0 : quiet + 1;
            if (quiet == 4) {
                done_quiet_end = cycle + 1;
                phase = TAIL;
            }
        }

        if (phase == TAIL && local_done_count == local_node_count) {
            for (int node = 0; node < node_count; ++node)
                if (done_received[node] < 0) throw std::runtime_error("completion packets did not reach every H1");
            if (done_quiet_end < 0) throw std::runtime_error("completion quiet window was not recorded");
            long tile_done = 0;
            for (int node = 0; node < node_count; ++node) {
                long h1_done = local_done[index_.h1(node)];
                long h1_to_h0 = std::max(done_received[node] + 2, h1_done) + chiplet_latency;
                long node_core_done = 0;
                for (int h0 = 0; h0 < geometry_.h0_per_h1; ++h0) {
                    long h0_done = local_done[index_.h0(node * geometry_.h0_per_h1 + h0)];
                    long core_done = std::max(h1_to_h0 + 1, h0_done) + 3;
                    node_core_done = std::max(node_core_done, core_done);
                    tile_done = std::max(tile_done, core_done);
                }
                node_completion[node] = compute_start + std::max(done_quiet_end, node_core_done) + 2;
            }
            local_end = std::max(done_quiet_end, tile_done) + 2;
            break;
        }

        counters.cycles += 1;
        cycle += 1;
    }

    const long iteration_cycles = compute_start + local_end;
    counters.cycles = iteration_cycles;
    std::vector<NocResourceStats> resources = noc_stats();
    std::vector<std::pair<int, long>> hop_histogram(hop_histogram_.begin(), hop_histogram_.end());
    validate_metric_totals(counters, resources, hop_histogram);

    std::vector<long> publications_sent(node_count, 0), publications_received(node_count, 0);
    for (const auto& [block, owner] : publication_pairs) {
        publications_sent[block / geometry_.blocks_per_h1()] += 1;
        publications_received[owner] += 1;
    }
    std::vector<NodePerformanceStats> nodes;
    for (int node = 0; node < node_count; ++node) {
        long h0_jobs = 0;
        for (int h0 = 0; h0 < geometry_.h0_per_h1; ++h0) h0_jobs += long(compiled.h0[node * geometry_.h0_per_h1 + h0].size());
        long h1_jobs = long(compiled.h1[node].size()), cross_jobs = long(compiled.cross[node].size());
        nodes.push_back(NodePerformanceStats{node, node % geometry_.mesh_x, node / geometry_.mesh_x, h0_jobs, h1_jobs,
                                             cross_jobs, h0_jobs + h1_jobs + cross_jobs, publications_sent[node],
                                             publications_received[node], compute_start + local_done[index_.h1(node)],
                                             compute_start + cross_done[node], node_completion[node]});
    }
    std::vector<DramPerformanceStats> dram = dram_stats(compiled);

    std::vector<ScheduledBlock> default_records;
    for (const auto& [a, b] : dataset_.active_pairs) default_records.push_back({a, b, -1});
    CompiledSchedule default_compiled = compile_schedule(geometry_, default_records);
    bool schedule_matches_default = queues_equal(compiled.h0, default_compiled.h0) &&
                                    queues_equal(compiled.h1, default_compiled.h1) &&
                                    queues_equal(compiled.cross, default_compiled.cross);
    bool certified_geometry =
        (geometry_ == Geometry{2, 1, 2, 2} && config_.h0_mvm_count == 1 && config_.h1_mvm_count == 1 &&
         config_.cross_mvm_count == 1) ||
        (geometry_ == Geometry{4, 4, 2, 16} && config_.h0_mvm_count == 1 && config_.h1_mvm_count == 1 &&
         config_.cross_mvm_count == 16);
    bool certified = certified_geometry && schedule_matches_default && config_.fifo_depth == 4 && chiplet_latency == 0 &&
                     config_.interconnect.is_rtl_direct() && mem_lanes_ == 16 && ticks_per_cycle_ == 40 &&
                     (idle_tick_modulus_ <= 0 || idle_tick_modulus_ == 7600) &&
                     ramulator_config_sha256_ == CERTIFIED_RAMULATOR_CONFIG_SHA256;
    std::string accuracy;
    if (flat || anchored) accuracy = "cycle-structured-unverified";  // no RTL implements these modes
    else if (certified) accuracy = "rtl-differential";
    else if (chiplet_latency != 0 || !config_.interconnect.is_rtl_direct()) accuracy = "parameterized-interconnect-projection";
    else accuracy = "cycle-structured-unverified";

    ExactEventResult result;
    result.accuracy = accuracy;
    result.initialization_cycles = initialization;
    result.iteration_cycles = iteration_cycles;
    result.total_cycles = initialization + iteration_cycles;
    result.scheduled_h0 = compiled.count_h0();
    result.scheduled_h1 = compiled.count_h1();
    result.scheduled_cross = compiled.count_cross();
    result.counters = counters;
    result.average_hops =
        counters.injected_flits ? double(counters.physical_link_flits) / double(counters.injected_flits) : 0.0;
    result.hop_histogram = hop_histogram;
    result.noc_resources = resources;
    result.nodes = nodes;
    result.dram = dram;
    result.chiplet_link_latency_cycles = chiplet_latency;
    result.interconnect = config_.interconnect;
    result.execution_mode = config_.execution_mode;
    long total_jobs = result.scheduled_h0 + result.scheduled_h1 + result.scheduled_cross;
    // Flat: two directed jobs, reads and stored copies per unordered block.
    result.unordered_interaction_blocks = flat ? total_jobs / 2 : total_jobs;
    result.directed_core_jobs = flat ? total_jobs : 0;
    result.weight_block_reads = total_jobs;
    result.logical_weight_blocks_stored = total_jobs;
    result.remote_state_packets = long(publication_pairs.size());
    result.core_weight_buffers = 2;
    return result;
}

// Port of shared_fetch_events.run_full_cores under streaming_cli /
// shared_fetch_double_cli.  ``initialization`` is bulk-advanced (no DRAM
// request or packet exists before it); every iteration phase is stepped on
// one persistent mesh with live Ramulator.
ExactEventResult RamulatorEventPerformanceModel::run_cores_only(const std::vector<ScheduledBlock>& schedule_records,
                                                                long initialization) {
    const Geometry& g = geometry_;
    if (config_.chiplet_link_latency_cycles)
        throw std::runtime_error("full cores-only chiplet-link latency awaits integrated validation");
    const bool streaming = config_.endpoint_policy == "streaming";
    CompiledSchedule canonical = compile_schedule(g, schedule_records);
    CoresOnlySchedule directed = compile_cores_only_schedule(g, schedule_records);
    const int total_h0 = g.node_count() * g.h0_per_h1;
    const int node_count = g.node_count();
    const int count = total_h0 + 2 * node_count;
    std::vector<std::deque<DmaCommand>> queues(count);
    auto fill = [&](const std::vector<std::deque<WorkItem>>& groups, int base) {
        for (size_t endpoint = 0; endpoint < groups.size(); ++endpoint)
            for (const auto& work : groups[endpoint])
                queues[base + int(endpoint)].push_back(DmaCommand{work.block_a, work.block_b, work.block_a, work.block_b});
    };
    fill(canonical.h0, 0);
    fill(canonical.h1, total_h0);
    fill(canonical.cross, total_h0 + node_count);
    std::vector<int> engines(count);
    for (int s = 0; s < count; ++s)
        engines[s] = s < total_h0 ? config_.h0_mvm_count : s < total_h0 + node_count ? config_.h1_mvm_count : config_.cross_mvm_count;

    PerformanceCounters counters;
    PipelineObservers observers;
    observers.noc = [&](long cycle, const char* scope, int node, const char* direction, const Flit& flit, bool accepted) {
        observe_noc_resource(scope, node, direction, flit, accepted);
        if (accepted) {
            record_transfer(make_transfer(cycle, scope, node, direction, flit));
            if (std::string_view(scope) == "inject") {
                counters.injected_flits += 1;
                int packet_type = flit.packet_type;
                counters.type_flits[(0 <= packet_type && packet_type < 3) ? packet_type : 3] += 1;
            } else if (std::string_view(scope) == "eject") {
                counters.ejected_flits += 1;
            } else {
                counters.physical_link_flits += 1;
            }
        } else if (std::string_view(scope) == "inject") {
            counters.injection_stalls += 1;
        } else if (std::string_view(scope) == "eject") {
            counters.ejection_stalls += 1;
        } else {
            counters.link_stalls += 1;
        }
    };
    if (event_stream_) {
        std::ofstream& sink = *event_stream_;
        observers.core = [&sink](long cycle, const char* event, int system, int dst, int src) {
            sink << "core," << cycle << ',' << event << ',' << system << ',' << dst << ',' << src << '\n';
        };
        observers.memory = [&sink](long cycle, const char* event, int system, int tag) {
            sink << "memory," << cycle << ',' << event << ',' << system << ',' << tag << ",0\n";
        };
        observers.state = [&sink](long cycle, const char* event, int core, int value) {
            sink << "state," << cycle << ',' << event << ',' << core << ',' << value << ",0\n";
        };
    }
    EndpointPipeline p(g, *backend_, engines, ticks_per_cycle_, config_.fifo_depth, config_.interconnect, mem_lanes_,
                       config_.stall_period, streaming, observers);
    for (auto& core : p.cores) {
        // Exact request-free initialization endpoint: all diagonal rows loaded,
        // init_done asserted, core idle; no iteration MVM has started yet.
        core.core_state = EndpointCore::IDLE;
        core.init_done = true;
        core.weight_beat_count = 32;
    }
    p.cycle = initialization;
    backend_->tick(initialization * ticks_per_cycle_);
    const int iterations = 1;
    CoreFullController controller(p, queues, schedule_records, iterations);
    controller.driver.jobs_per_iteration *= 2;
    while (!controller.finished()) {
        if (p.cycle - initialization >= config_.max_cycles)
            throw std::runtime_error("full cores-only iteration exceeded maximum cycles");
        controller.tick();
    }
    const long iteration_cycles = p.cycle - initialization;
    counters.cycles = iteration_cycles;

    std::vector<DramPerformanceStats> dram;
    for (int s = 0; s < count; ++s) {
        RamulatorSystemStats raw = backend_->stats(s);
        long jobs = long(queues[s].size()) * iterations;
        uint64_t expected = uint64_t(jobs) * 32;
        if (raw.accepted != expected || raw.completed != expected || raw.outstanding() != 0)
            throw std::runtime_error("cores-only DRAM conservation failed for H0 " + std::to_string(s) + ": expected=" +
                                     std::to_string(expected) + " accepted=" + std::to_string(raw.accepted) +
                                     " completed=" + std::to_string(raw.completed) +
                                     " outstanding=" + std::to_string(raw.outstanding()));
        std::string level;
        int node, index;
        if (s < total_h0) { level = "h0"; node = s / g.h0_per_h1; index = s % g.h0_per_h1; }
        else if (s < total_h0 + node_count) { level = "h1"; node = s - total_h0; index = 0; }
        else { level = "cross"; node = s - total_h0 - node_count; index = 0; }
        dram.push_back(DramPerformanceStats{s, level, node, index, engines[s], jobs, raw.accepted, raw.rejected,
                                            raw.completed, raw.outstanding(), raw.average_latency_ticks(),
                                            raw.latency_max_ticks, raw.average_latency_ticks() / double(ticks_per_cycle_),
                                            double(raw.latency_max_ticks) / double(ticks_per_cycle_)});
    }
    std::vector<long> sent(node_count, 0), received(node_count, 0);
    for (const auto& [block, destination] : directed.state_publications) {
        sent[block / g.blocks_per_h1()] += 1;
        received[destination] += 1;
    }
    std::vector<NodePerformanceStats> nodes;
    for (int n = 0; n < node_count; ++n) {
        long h0_jobs = 0;
        for (int h = 0; h < g.h0_per_h1; ++h) h0_jobs += long(directed.h0[n * g.h0_per_h1 + h].size());
        h0_jobs *= iterations;
        nodes.push_back(NodePerformanceStats{n, n % g.mesh_x, n / g.mesh_x, h0_jobs, 0, 0, h0_jobs, sent[n] * iterations,
                                             received[n] * iterations, 0, 0, p.cycle});
    }
    std::vector<NocResourceStats> resources = noc_stats();
    std::vector<std::pair<int, long>> hop_histogram(hop_histogram_.begin(), hop_histogram_.end());
    validate_metric_totals(counters, resources, hop_histogram);

    // core_pipeline_contract (shared_fetch_events + streaming_cli/double_cli overrides).
    long bank_writes = 0, bank_reads = 0, bank_stalled = 0;
    for (const auto& bank : p.state_banks) {
        bank_writes += bank.writes;
        bank_reads += bank.accepted_reads;
        bank_stalled += bank.stalled_reads;
    }
    JsonObject phases;
    for (const auto& [key, cycle] : controller.phase_cycles) phases[key] = cycle;
    // Counter dicts with int keys: Python sort_keys orders them numerically.
    JsonOrdered source_capacity, peak_source, peak_outstanding, local_beats;
    long engine_total = 0;
    for (int s = 0; s < count; ++s) {
        std::string key = std::to_string(s);
        source_capacity.emplace_back(key, Json(p.audit_source_capacity_blocks[s]));
        peak_source.emplace_back(key, Json(p.audit_peak_source_blocks[s]));
        peak_outstanding.emplace_back(key, Json(p.audit_peak_outstanding_requests[s]));
        if (p.audit_local_delivery_touched[s]) local_beats.emplace_back(key, Json(p.audit_local_delivery_beats[s]));
        engine_total += engines[s];
    }
    const char* policy = streaming ? "single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1"
                                   : "single_fetch_two_slot_cir_routes_v3";
    JsonObject audit = {{"source_capacity_blocks", source_capacity},
                        {"peak_source_blocks", peak_source},
                        {"peak_outstanding_requests", peak_outstanding},
                        {"local_delivery_beats", local_beats},
                        {"fetch_policy", policy},
                        {"delivery_contract", "H0 per-core outputs; H1 per-child packet locks; shared mesh unchanged"},
                        {"extra_replay_register_bytes", 2048 * engine_total}};
    if (streaming) audit["endpoint_compute"] = "accepted-row streaming; one shared MVM";
    JsonObject contract = {
        {"model", streaming ? "streaming_endpoint_v1" : "experimental_cir_matched_shared_fetch_full_iteration_v3"},
        {"fetch_policy", policy},
        {"one_mvm_per_core", true},
        {"canonical_transpose", true},
        {"persistent_mesh", true},
        {"state_gather_words", long(g.total_blocks()) * iterations},
        {"state_cache_write_words", bank_writes},
        {"state_operand_reads", bank_reads},
        {"state_bank_stalled_lane_cycles", bank_stalled},
        {"phases", phases},
        {"initialization_scope", "request-free bulk initialization; full iteration stepped"},
        {"capacity_not_rtl_certificate", true},
        {"iterations", iterations},
        {"pipeline_audit", audit}};
    if (streaming) contract["endpoint_compute"] = "accepted-row streaming";

    ExactEventResult result;
    result.accuracy = config_.interconnect.is_rtl_direct() ? "cycle-structured-unverified" : "parameterized-interconnect-projection";
    result.initialization_cycles = initialization;
    result.iteration_cycles = iteration_cycles;
    result.total_cycles = p.cycle;
    result.scheduled_h0 = directed.directed_jobs() * iterations;
    result.scheduled_h1 = 0;
    result.scheduled_cross = 0;
    result.counters = counters;
    result.average_hops =
        counters.injected_flits ? double(counters.physical_link_flits) / double(counters.injected_flits) : 0.0;
    result.hop_histogram = hop_histogram;
    result.noc_resources = resources;
    result.nodes = nodes;
    result.dram = dram;
    result.chiplet_link_latency_cycles = 0;
    result.interconnect = config_.interconnect;
    result.execution_mode = "cores-only";
    result.unordered_interaction_blocks = long(schedule_records.size());
    result.directed_core_jobs = directed.directed_jobs() * iterations;
    result.weight_block_reads = long(schedule_records.size()) * iterations;
    result.logical_weight_blocks_stored = long(schedule_records.size());
    result.remote_state_packets = long(directed.state_publications.size()) * iterations;
    result.core_weight_buffers = streaming ? 0 : 1;
    result.core_pipeline_contract = contract;
    return result;
}

}  // namespace azilla
