#include "scheduler.hpp"

#include <algorithm>
#include <cstdlib>
#include <set>
#include <stdexcept>

namespace azilla {

int mesh_distance(const Geometry& geometry, int node_a, int node_b) {
    int ax = node_a % geometry.mesh_x, ay = node_a / geometry.mesh_x;
    int bx = node_b % geometry.mesh_x, by = node_b / geometry.mesh_x;
    return std::abs(ax - bx) + std::abs(ay - by);
}

std::map<BlockPair, int> allocate_h1_pairs(const Geometry& geometry) {
    const int count = geometry.node_count();
    const int pair_count = count * (count - 1) / 2;
    const int base_capacity = pair_count / count;
    const int extra_capacity = pair_count % count;
    std::vector<int> capacity(count, base_capacity);
    std::vector<int> load(count, 0);

    std::set<int> extra_selected;
    for (int round = 0; round < extra_capacity; ++round) {
        int best = -1;
        long best_sum = 0;
        for (int candidate = 0; candidate < count; ++candidate) {
            if (extra_selected.count(candidate)) continue;
            long sum = 0;
            for (int other = 0; other < count; ++other) sum += mesh_distance(geometry, candidate, other);
            // key = (sum, candidate); candidates are visited in ascending order,
            // so a strict improvement in sum is the only way to replace best.
            if (best < 0 || sum < best_sum) {
                best = candidate;
                best_sum = sum;
            }
        }
        extra_selected.insert(best);
        capacity[best] += 1;
    }

    std::set<BlockPair> unassigned;
    for (int a = 0; a < count; ++a)
        for (int b = a + 1; b < count; ++b) unassigned.insert({a, b});
    std::map<BlockPair, int> owners;
    while (!unassigned.empty()) {
        // min by (-distance, a, b): the set is already ordered by (a, b).
        BlockPair chosen = *unassigned.begin();
        int chosen_distance = mesh_distance(geometry, chosen.first, chosen.second);
        for (const auto& pair : unassigned) {
            int distance = mesh_distance(geometry, pair.first, pair.second);
            if (distance > chosen_distance) {
                chosen = pair;
                chosen_distance = distance;
            }
        }
        const int endpoint_a = chosen.first, endpoint_b = chosen.second;
        int owner = -1;
        int owner_max = 0, owner_sum = 0;
        long owner_load = 0, owner_cap = 1;
        for (int candidate = 0; candidate < count; ++candidate) {
            if (!(load[candidate] < capacity[candidate])) continue;
            int da = mesh_distance(geometry, candidate, endpoint_a);
            int db = mesh_distance(geometry, candidate, endpoint_b);
            int key_max = std::max(da, db), key_sum = da + db;
            long key_load = load[candidate], key_cap = capacity[candidate];
            bool better = false;
            if (owner < 0) {
                better = true;
            } else if (key_max != owner_max) {
                better = key_max < owner_max;
            } else if (key_sum != owner_sum) {
                better = key_sum < owner_sum;
            } else {
                // Fraction(load, capacity) comparison via cross multiplication.
                long lhs = key_load * owner_cap, rhs = owner_load * key_cap;
                if (lhs != rhs) better = lhs < rhs;
                else better = false;  // candidate index ascending: keep first
            }
            if (better) {
                owner = candidate;
                owner_max = key_max;
                owner_sum = key_sum;
                owner_load = key_load;
                owner_cap = key_cap;
            }
        }
        if (owner < 0) throw std::runtime_error("allocate_h1_pairs: no eligible owner");
        owners[{endpoint_a, endpoint_b}] = owner;
        owners[{endpoint_b, endpoint_a}] = owner;
        load[owner] += 1;
        unassigned.erase(chosen);
    }
    return owners;
}

long CompiledSchedule::count_h0() const {
    long total = 0;
    for (const auto& q : h0) total += long(q.size());
    return total;
}
long CompiledSchedule::count_h1() const {
    long total = 0;
    for (const auto& q : h1) total += long(q.size());
    return total;
}
long CompiledSchedule::count_cross() const {
    long total = 0;
    for (const auto& q : cross) total += long(q.size());
    return total;
}

std::vector<BlockPair> CompiledSchedule::publications() const {
    std::set<BlockPair> needed;
    for (size_t owner = 0; owner < cross.size(); ++owner)
        for (const auto& work : cross[owner]) {
            needed.insert({work.block_a, int(owner)});
            needed.insert({work.block_b, int(owner)});
        }
    return std::vector<BlockPair>(needed.begin(), needed.end());
}

CompiledSchedule compile_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs) {
    CompiledSchedule compiled;
    compiled.geometry = geometry;
    compiled.owners = allocate_h1_pairs(geometry);
    const int total_h0 = geometry.node_count() * geometry.h0_per_h1;
    compiled.h0.resize(total_h0);
    compiled.h1.resize(geometry.node_count());
    compiled.cross.resize(geometry.node_count());
    std::set<BlockPair> observed;
    for (const auto& record : pairs) {
        int block_a = record.block_a, block_b = record.block_b, requested_owner = record.owner;
        BlockPair pair{block_a, block_b};
        if (!(0 <= block_a && block_a < block_b && block_b < geometry.total_blocks()))
            throw std::runtime_error("invalid scheduled block pair");
        if (observed.count(pair)) throw std::runtime_error("duplicate scheduled block pair");
        observed.insert(pair);
        WorkItem work{block_a, block_b};
        int h0_a = block_a / geometry.cores_per_h0, h0_b = block_b / geometry.cores_per_h0;
        int h1_a = block_a / geometry.blocks_per_h1(), h1_b = block_b / geometry.blocks_per_h1();
        if (h0_a == h0_b) {
            compiled.h0[h0_a].push_back(work);
        } else if (h1_a == h1_b) {
            compiled.h1[h1_a].push_back(work);
        } else {
            int owner = requested_owner >= 0 ? requested_owner : compiled.owners.at({h1_a, h1_b});
            if (!(0 <= owner && owner < geometry.node_count()))
                throw std::runtime_error("cross owner out of range");
            compiled.cross[owner].push_back(work);
        }
    }
    return compiled;
}

CoresOnlySchedule compile_cores_only_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs) {
    CoresOnlySchedule compiled;
    compiled.geometry = geometry;
    const int total_h0 = geometry.node_count() * geometry.h0_per_h1;
    compiled.h0.resize(total_h0);
    std::set<BlockPair> publications;
    std::set<BlockPair> observed;
    for (const auto& record : pairs) {
        int block_a = record.block_a, block_b = record.block_b;
        BlockPair pair{block_a, block_b};
        if (!(0 <= block_a && block_a < block_b && block_b < geometry.total_blocks()))
            throw std::runtime_error("invalid scheduled block pair");
        if (observed.count(pair)) throw std::runtime_error("duplicate scheduled block pair");
        observed.insert(pair);
        const int ends[2][2] = {{block_a, block_b}, {block_b, block_a}};
        for (const auto& end : ends) {
            int destination = end[0], source = end[1];
            compiled.h0[destination / geometry.cores_per_h0].push_back(WorkItem{destination, source});
            int source_h1 = source / geometry.blocks_per_h1(), destination_h1 = destination / geometry.blocks_per_h1();
            if (source_h1 != destination_h1) publications.insert({source, destination_h1});
        }
    }
    compiled.state_publications.assign(publications.begin(), publications.end());
    return compiled;
}

DmaCommand command_for(const WorkTarget& target, const WorkItem& work, const Geometry& geometry) {
    int state_count;
    switch (target.level) {
        case Level::H0: state_count = geometry.cores_per_h0; break;
        case Level::H1: state_count = geometry.blocks_per_h1(); break;
        default: state_count = geometry.total_blocks(); break;
    }
    return {work.block_a % state_count, work.block_b % state_count, work.block_a, work.block_b};
}

ConcurrentDispatcher::ConcurrentDispatcher(const CompiledSchedule& schedule, int h0_mvm_count,
                                           int h1_mvm_count, int cross_mvm_count)
    : index_{schedule.geometry} {
    const int count = index_.count();
    queues_.resize(count);
    engine_count_.resize(count);
    next_engine_.assign(count, 0);
    pending_.resize(count);
    accepted_.resize(count);
    for (int t = 0; t < count; ++t) {
        WorkTarget target = index_.target(t);
        switch (target.level) {
            case Level::H0:
                queues_[t] = schedule.h0[t];
                engine_count_[t] = h0_mvm_count;
                break;
            case Level::H1:
                queues_[t] = schedule.h1[target.node];
                engine_count_[t] = h1_mvm_count;
                break;
            default:
                queues_[t] = schedule.cross[target.node];
                engine_count_[t] = cross_mvm_count;
                break;
        }
        pending_[t].assign(engine_count_[t], std::nullopt);
        accepted_[t].assign(engine_count_[t], false);
        total_ += long(queues_[t].size());
    }
}

const std::vector<std::vector<std::optional<DmaCommand>>>& ConcurrentDispatcher::drive(
    const std::vector<std::vector<bool>>& ready) {
    const int count = index_.count();
    // Retire transfers sampled on the preceding rising edge; keep the accepted
    // mask through this dispatch step (see Python comment).
    for (int t = 0; t < count; ++t)
        for (int e = 0; e < engine_count_[t]; ++e)
            if (accepted_[t][e]) {
                pending_[t][e].reset();
                --pending_count_;
                ++retired_;
            }
    for (int t = 0; t < count; ++t) {
        WorkTarget target = index_.target(t);
        const int engines = engine_count_[t];
        const int base = next_engine_[t];
        for (int offset = 0; offset < engines; ++offset) {
            int engine = (base + offset) % engines;
            if (!queues_[t].empty() && !pending_[t][engine].has_value() && !accepted_[t][engine] &&
                ready[t][engine]) {
                pending_[t][engine] = command_for(target, queues_[t].front(), index_.geometry);
                queues_[t].pop_front();
                ++pending_count_;
                next_engine_[t] = (engine + 1) % engines;
            }
        }
    }
    for (int t = 0; t < count; ++t)
        for (int e = 0; e < engine_count_[t]; ++e)
            accepted_[t][e] = pending_[t][e].has_value() && ready[t][e];
    return pending_;
}

}  // namespace azilla
