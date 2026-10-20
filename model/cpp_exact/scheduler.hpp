// Port of model/azilla_cycle_model/scheduler.py (CIR parts only):
// allocate_h1_pairs, compile_schedule, command_for, ConcurrentDispatcher.
#pragma once

#include <deque>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "workload.hpp"

namespace azilla {

enum class Level { H0 = 0, H1 = 1, CROSS = 2 };

inline const char* level_name(Level level) {
    switch (level) {
        case Level::H0: return "h0";
        case Level::H1: return "h1";
        default: return "cross";
    }
}

struct DmaCommand {
    int state_a_index;
    int state_b_index;
    int block_a;
    int block_b;
};

struct WorkItem {
    int block_a;
    int block_b;
    bool operator==(const WorkItem& o) const { return block_a == o.block_a && block_b == o.block_b; }
};

struct WorkTarget {
    Level level;
    int node;
    int index;
};

struct CompiledSchedule {
    Geometry geometry;
    std::vector<std::deque<WorkItem>> h0;
    std::vector<std::deque<WorkItem>> h1;
    std::vector<std::deque<WorkItem>> cross;
    std::map<BlockPair, int> owners;
    // H0-resident schedules (compile_flat_schedule, compile_anchored_schedule):
    // h1/cross are empty, H0 state tables are indexed by global block
    // (global_h0_state) and the state publications are listed explicitly
    // as (block, consuming H1) pairs whose H1s differ.
    bool global_h0_state = false;
    bool has_explicit_publications = false;
    std::vector<BlockPair> explicit_publications;

    long count_h0() const;
    long count_h1() const;
    long count_cross() const;
    // Unique (block, owner) state packets, sorted.
    std::vector<BlockPair> publications() const;
};

int mesh_distance(const Geometry& geometry, int node_a, int node_b);
std::map<BlockPair, int> allocate_h1_pairs(const Geometry& geometry);
CompiledSchedule compile_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs);
// Flat weight-stationary baseline: every unordered block becomes
// WorkItem(destination, source) on both endpoint H0s (the row representation
// is stored at the destination H0; the transposed copy at the other), no
// hierarchy ownership, state publications as in compile_cores_only_schedule.
CompiledSchedule compile_flat_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs);
// H0-anchored symmetric ownership: a block inside one H0 stays there; every
// other block is owned by the endpoint H0 with fewer jobs so far, counting
// the forced within-H0 blocks first (ties to the lower endpoint), evaluated
// symmetrically once.  Publications are the
// (endpoint block, owner H1) pairs whose H1s differ.
// ``h0_engines``: engines per H0 (8 cycles per job at 4 engines) against the
// 4-cycle mesh injection cost of a cross-H1 partial at the owner's H1 port;
// the owner is the endpoint that minimizes the larger of the two loads.
CompiledSchedule compile_anchored_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs,
                                           int h0_engines);

// Port of scheduler.compile_cores_only_schedule (destination-stationary
// expansion): every unordered block becomes WorkItem(destination, source) on
// both endpoint H0s; state publications are the sorted unique
// (source_block, destination_h1) pairs whose H1s differ.
struct CoresOnlySchedule {
    Geometry geometry;
    std::vector<std::deque<WorkItem>> h0;
    std::vector<BlockPair> state_publications;
    long directed_jobs() const {
        long total = 0;
        for (const auto& q : h0) total += long(q.size());
        return total;
    }
};
CoresOnlySchedule compile_cores_only_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs);
// ``global_h0_state``: H0 state tables hold every block (flat baseline).
DmaCommand command_for(const WorkTarget& target, const WorkItem& work, const Geometry& geometry,
                       bool global_h0_state = false);

// Target/port indexing shared by the exact-event model.  Targets are
// numbered like Ramulator system ids: H0 global index, then H1 per node,
// then CROSS per node.
struct TargetIndex {
    Geometry geometry;
    int total_h0() const { return geometry.node_count() * geometry.h0_per_h1; }
    int count() const { return total_h0() + 2 * geometry.node_count(); }
    int h0(int global_h0) const { return global_h0; }
    int h1(int node) const { return total_h0() + node; }
    int cross(int node) const { return total_h0() + geometry.node_count() + node; }
    WorkTarget target(int index) const {
        if (index < total_h0())
            return {Level::H0, index / geometry.h0_per_h1, index % geometry.h0_per_h1};
        if (index < total_h0() + geometry.node_count())
            return {Level::H1, index - total_h0(), 0};
        return {Level::CROSS, index - total_h0() - geometry.node_count(), 0};
    }
};

class ConcurrentDispatcher {
public:
    ConcurrentDispatcher(const CompiledSchedule& schedule, int h0_mvm_count,
                         int h1_mvm_count, int cross_mvm_count);

    bool done() const { return retired_ == total_ && pending_count_ == 0; }
    int engines(int target) const { return engine_count_[target]; }

    // ready[target][engine]; returns pending[target][engine] (nullopt = none).
    const std::vector<std::vector<std::optional<DmaCommand>>>& drive(
        const std::vector<std::vector<bool>>& ready);

private:
    TargetIndex index_;
    std::vector<std::deque<WorkItem>> queues_;
    std::vector<int> engine_count_;
    std::vector<int> next_engine_;
    std::vector<std::vector<std::optional<DmaCommand>>> pending_;
    std::vector<std::vector<bool>> accepted_;
    long total_ = 0;
    long retired_ = 0;
    long pending_count_ = 0;
    bool global_h0_state_ = false;
};

}  // namespace azilla
