#include "scheduler.hpp"

#include <algorithm>
#include <functional>
#include <map>
#include <cstdlib>
#include <set>
#include <utility>
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

CompiledSchedule compile_flat_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs) {
    CoresOnlySchedule directed = compile_cores_only_schedule(geometry, pairs);
    CompiledSchedule compiled;
    compiled.geometry = geometry;
    compiled.h0 = directed.h0;
    compiled.h1.resize(geometry.node_count());
    compiled.cross.resize(geometry.node_count());
    compiled.global_h0_state = true;
    compiled.has_explicit_publications = true;
    compiled.explicit_publications = directed.state_publications;
    return compiled;
}

CompiledSchedule compile_anchored_schedule(const Geometry& geometry, const std::vector<ScheduledBlock>& pairs,
                                           int h0_engines) {
    if (h0_engines < 1) throw std::runtime_error("anchored schedule needs at least one H0 engine");
    CompiledSchedule compiled;
    compiled.geometry = geometry;
    const int total_h0 = geometry.node_count() * geometry.h0_per_h1;
    const int h0_per_h1 = geometry.h0_per_h1;
    compiled.h0.resize(total_h0);
    compiled.h1.resize(geometry.node_count());
    compiled.cross.resize(geometry.node_count());
    // Port cycles charged per cross-H1 packet: four beats times 3, the
    // weight at which community 128K balanced its two injection ports under
    // eight-way packet arbitration (weights 1/2/3/4: 926K/794K/739K/739K
    // cycles).  A scheduling heuristic, not a hardware parameter;
    // AZILLA_ANCHORED_PORT_WEIGHT overrides it for experiments.
    double port_cost = 4.0 * 3.0;
    if (const char* w = std::getenv("AZILLA_ANCHORED_PORT_WEIGHT")) port_cost = 4.0 * std::atof(w);
    const double engine_cost = 32.0 / h0_engines;

    // Group the off-H0 blocks by unordered H0 pair (record order kept) and
    // count the forced within-H0 blocks.
    std::vector<long> load(total_h0, 0);
    std::map<std::pair<int, int>, std::vector<BlockPair>> groups;
    std::set<BlockPair> observed;
    for (const auto& record : pairs) {
        int block_a = record.block_a, block_b = record.block_b;
        BlockPair pair{block_a, block_b};
        if (!(0 <= block_a && block_a < block_b && block_b < geometry.total_blocks()))
            throw std::runtime_error("invalid scheduled block pair");
        if (observed.count(pair)) throw std::runtime_error("duplicate scheduled block pair");
        observed.insert(pair);
        int h0_a = block_a / geometry.cores_per_h0, h0_b = block_b / geometry.cores_per_h0;
        if (h0_a == h0_b) {
            compiled.h0[h0_a].push_back(WorkItem{block_a, block_b});
            load[h0_a] += 1;
        } else {
            groups[{h0_a, h0_b}].push_back(pair);
        }
    }

    // Per pair (i < j): the first ``k`` blocks go to i, the rest to j.
    // Start from the sequential greedy, then refine by local search on the
    // lexicographic objective (max over H0s of max(engine, port), max
    // engine): move batches of blocks off the costliest H0 while it improves.
    std::vector<std::pair<int, int>> keys;
    std::vector<long> n_blocks, k_first;
    for (const auto& [key, blocks] : groups) {
        keys.push_back(key);
        n_blocks.push_back(long(blocks.size()));
        k_first.push_back(0);
    }
    std::vector<double> port_load(geometry.node_count(), 0.0);
    auto h1_of = [&](int h0) { return h0 / h0_per_h1; };
    auto cross = [&](size_t g) { return h1_of(keys[g].first) != h1_of(keys[g].second); };
    for (size_t g = 0; g < keys.size(); ++g) {
        int i = keys[g].first, j = keys[g].second;
        for (long b = 0; b < n_blocks[g]; ++b) {
            double port_i = cross(g) ? port_load[h1_of(i)] + port_cost : 0.0;
            double port_j = cross(g) ? port_load[h1_of(j)] + port_cost : 0.0;
            auto cost_i = std::make_pair(std::max((load[i] + 1) * engine_cost, port_i), (load[i] + 1) * engine_cost);
            auto cost_j = std::make_pair(std::max((load[j] + 1) * engine_cost, port_j), (load[j] + 1) * engine_cost);
            int owner = cost_i <= cost_j ? i : j;
            if (owner == i) k_first[g] += 1;
            load[owner] += 1;
            if (cross(g)) port_load[h1_of(owner)] += port_cost;
        }
    }
    std::vector<std::vector<size_t>> groups_of(total_h0);
    for (size_t g = 0; g < keys.size(); ++g) {
        groups_of[keys[g].first].push_back(g);
        groups_of[keys[g].second].push_back(g);
    }
    // Objective: (max injection-port load over H1s, H0 loads sorted
    // descending), lexicographic; the port floor first, then the engine
    // makespan with ties resolved by the next-heaviest H0s.
    auto objective = [&]() {
        double worst_port = 0.0;
        for (double p : port_load) worst_port = std::max(worst_port, p);
        std::vector<long> sorted_loads(load.begin(), load.end());
        std::sort(sorted_loads.begin(), sorted_loads.end(), std::greater<long>());
        return std::make_pair(worst_port, sorted_loads);
    };
    auto apply = [&](size_t g, int from, long d) {  // move d blocks of group g off ``from``
        int to = keys[g].first == from ? keys[g].second : keys[g].first;
        if (keys[g].first == from) k_first[g] -= d; else k_first[g] += d;
        load[from] -= d;
        load[to] += d;
        if (cross(g)) {
            port_load[h1_of(from)] -= d * port_cost;
            port_load[h1_of(to)] += d * port_cost;
        }
    };
    auto current = objective();
    for (int iteration = 0; iteration < 200000; ++iteration) {
        // Try the heaviest H0 first, then the others in descending load, so
        // a tied maximum still finds a move.
        std::vector<int> order(total_h0);
        for (int h0 = 0; h0 < total_h0; ++h0) order[h0] = h0;
        std::sort(order.begin(), order.end(), [&](int a, int b) { return load[a] > load[b]; });
        bool improved = false;
        for (int m : order) {
        for (size_t g : groups_of[m]) {
            int other = keys[g].first == m ? keys[g].second : keys[g].first;
            long have = keys[g].first == m ? k_first[g] : n_blocks[g] - k_first[g];
            if (have <= 0) continue;
            double gap = load[m] * engine_cost - load[other] * engine_cost;
            long d = std::max(1L, long(gap / (2.0 * engine_cost)));
            d = std::min(d, have);
            for (; d >= 1; d = d / 2) {
                apply(g, m, d);
                auto candidate = objective();
                if (candidate < current) {
                    current = candidate;
                    improved = true;
                    break;
                }
                apply(g, other, d);  // undo
            }
            if (improved) break;
        }
        if (improved) break;
        }
        if (!improved) break;
    }

    std::set<BlockPair> publications;
    for (size_t g = 0; g < keys.size(); ++g) {
        const auto& blocks = groups[keys[g]];
        for (long b = 0; b < n_blocks[g]; ++b) {
            int owner = b < k_first[g] ? keys[g].first : keys[g].second;
            compiled.h0[owner].push_back(WorkItem{blocks[b].first, blocks[b].second});
            int owner_h1 = h1_of(owner);
            for (int block : {blocks[b].first, blocks[b].second})
                if (block / geometry.blocks_per_h1() != owner_h1) publications.insert({block, owner_h1});
        }
    }
    compiled.global_h0_state = true;
    compiled.has_explicit_publications = true;
    compiled.explicit_publications.assign(publications.begin(), publications.end());
    return compiled;
}

DmaCommand command_for(const WorkTarget& target, const WorkItem& work, const Geometry& geometry,
                       bool global_h0_state) {
    int state_count;
    switch (target.level) {
        case Level::H0: state_count = global_h0_state ? geometry.total_blocks() : geometry.cores_per_h0; break;
        case Level::H1: state_count = geometry.blocks_per_h1(); break;
        default: state_count = geometry.total_blocks(); break;
    }
    return {work.block_a % state_count, work.block_b % state_count, work.block_a, work.block_b};
}

ConcurrentDispatcher::ConcurrentDispatcher(const CompiledSchedule& schedule, int h0_mvm_count,
                                           int h1_mvm_count, int cross_mvm_count)
    : index_{schedule.geometry}, global_h0_state_(schedule.global_h0_state) {
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
                pending_[t][engine] = command_for(target, queues_[t].front(), index_.geometry, global_h0_state_);
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
