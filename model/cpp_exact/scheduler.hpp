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

    long count_h0() const;
    long count_h1() const;
    long count_cross() const;
    // Unique (block, owner) state packets, sorted.
    std::vector<BlockPair> publications() const;
};

int mesh_distance(const Geometry& geometry, int node_a, int node_b);
std::map<BlockPair, int> allocate_h1_pairs(const Geometry& geometry);
CompiledSchedule compile_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs);

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
DmaCommand command_for(const WorkTarget& target, const WorkItem& work, const Geometry& geometry);

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
};

}  // namespace azilla
