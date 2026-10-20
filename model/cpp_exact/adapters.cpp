#include "adapters.hpp"

#include <algorithm>

namespace azilla {

H0Adapter::H0Adapter(int core_count_, int mvm_count_, int base_block_id_)
    : core_count(core_count_), mvm_count(mvm_count_), base_block_id(base_block_id_), locked(core_count_, -1) {}

void H0Adapter::reset() { std::fill(locked.begin(), locked.end(), -1); }

H0AdapterOutputs H0Adapter::outputs(const std::vector<PartialOutput>& partials) const {
    H0AdapterOutputs out;
    out.engine_ready.assign(mvm_count, false);
    // Candidate cores: locked cores plus cores addressed by a valid partial.
    std::vector<int> cores;
    for (int core = 0; core < core_count; ++core)
        if (locked[core] >= 0) cores.push_back(core);
    for (const auto& partial : partials) {
        if (!partial.valid) continue;
        int core = partial.block_id - base_block_id;
        if (0 <= core && core < core_count && locked[core] < 0) cores.push_back(core);
    }
    std::sort(cores.begin(), cores.end());
    cores.erase(std::unique(cores.begin(), cores.end()), cores.end());
    for (int core : cores) {
        int selected = locked[core];
        if (selected < 0) {
            int target = base_block_id + core;
            for (int index = 0; index < int(partials.size()); ++index)
                if (partials[index].valid && partials[index].block_id == target) { selected = index; break; }
        }
        if (selected >= 0) {
            out.core_partials.emplace_back(core, RoutedPartial{partials[selected].valid, partials[selected].block_id});
            out.engine_ready[selected] = true;  // core_ready is constant True
        }
    }
    return out;
}

void H0Adapter::tick(const H0AdapterOutputs& before, const std::vector<PartialOutput>& partials) {
    for (const auto& [core, output] : before.core_partials) {
        if (!output.valid) continue;
        int selected = -1;
        for (int index = 0; index < int(partials.size()); ++index)
            if (before.engine_ready[index] && partials[index].block_id == output.block_id) { selected = index; break; }
        if (locked[core] < 0) {
            if (selected >= 0 && !partials[selected].last) locked[core] = selected;
        } else if (partials[locked[core]].last) {
            locked[core] = -1;
        }
    }
}

H1ChildAdapter::H1ChildAdapter(int child_count_, int blocks_per_child_, int mvm_count_, int base_block_id_,
                               bool parent_first_)
    : child_count(child_count_), blocks_per_child(blocks_per_child_), mvm_count(mvm_count_),
      base_block_id(base_block_id_), parent_first(parent_first_), locked_source(child_count_, -1) {}

void H1ChildAdapter::reset() {
    std::fill(locked_source.begin(), locked_source.end(), -1);
    parent_beat = 0;
}

bool H1ChildAdapter::belongs(int child, int block) const {
    int first = base_block_id + child * blocks_per_child;
    return first <= block && block < first + blocks_per_child;
}

int H1ChildAdapter::select(int child, const std::vector<PartialOutput>& partials, const RoutedPartial& parent) const {
    if (locked_source[child] >= 0) return locked_source[child];
    if (parent_first && parent.valid && belongs(child, parent.block_id)) return mvm_count;
    for (int engine = 0; engine < int(partials.size()); ++engine)
        if (partials[engine].valid && belongs(child, partials[engine].block_id)) return engine;
    if (parent.valid && belongs(child, parent.block_id)) return mvm_count;
    return -1;
}

H1ChildOutputs H1ChildAdapter::outputs(const std::vector<PartialOutput>& partials, const RoutedPartial& parent) const {
    H1ChildOutputs out;
    out.node_ready.assign(mvm_count, false);
    out.children.assign(child_count, RoutedPartial{});
    for (int child = 0; child < child_count; ++child) {
        int source = select(child, partials, parent);
        if (source < 0) continue;
        if (source == mvm_count) {
            out.children[child] = parent;
            out.parent_ready = true;  // child_ready is constant True
        } else {
            out.children[child] = RoutedPartial{partials[source].valid, partials[source].block_id};
            out.node_ready[source] = true;
        }
    }
    return out;
}

void H1ChildAdapter::tick(const H1ChildOutputs& before, const std::vector<PartialOutput>& partials,
                          const RoutedPartial& parent) {
    int old_parent_beat = parent_beat;
    if (parent.valid && before.parent_ready) parent_beat = (parent_beat + 1) % PARTIAL_BEATS;
    for (int child = 0; child < child_count; ++child) {
        const RoutedPartial& output = before.children[child];
        if (!output.valid) continue;
        int source = select(child, partials, parent);
        if (source < 0) continue;
        bool last = source == mvm_count ? (old_parent_beat == PARTIAL_BEATS - 1) : partials[source].last;
        if (locked_source[child] < 0 && !last) locked_source[child] = source;
        else if (locked_source[child] >= 0 && last) locked_source[child] = -1;
    }
}

}  // namespace azilla
