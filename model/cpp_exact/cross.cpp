#include "cross.hpp"

namespace azilla {

CrossH1Node::CrossH1Node(int state_entry_count, int mvm_count, int blocks_per_h1_, int mesh_x_count_, int node_id_)
    : node(state_entry_count, mvm_count), blocks_per_h1(blocks_per_h1_), mesh_x_count(mesh_x_count_),
      node_id(node_id_) {}

void CrossH1Node::reset() {
    node.reset();
    output_locked = false;
    locked_engine = 0;
    round_robin_start = 0;
}

int CrossH1Node::selected_engine(const HierarchyOutputs& node_outputs) const {
    const auto& partials = node_outputs.partials;
    const int count = int(partials.size());
    if (output_locked) return partials[locked_engine].valid ? locked_engine : -1;
    for (int offset = 0; offset < count; ++offset) {
        int candidate = (round_robin_start + offset) % count;
        if (partials[candidate].valid) return candidate;
    }
    return -1;
}

CrossOutputs CrossH1Node::outputs(const HierarchyOutputs& node_outputs, int epoch) const {
    CrossOutputs out;
    out.iter_done = node_outputs.iter_done;
    out.selected_engine = selected_engine(node_outputs);
    if (out.selected_engine >= 0) {
        const PartialOutput& partial = node_outputs.partials[out.selected_engine];
        int destination = partial.block_id / blocks_per_h1;
        Flit flit;
        flit.packet_type = NOC_PARTIAL;
        flit.dest_x = destination % mesh_x_count;
        flit.dest_y = destination / mesh_x_count;
        flit.source_id = node_id;
        flit.epoch = epoch;
        flit.block_id = partial.block_id;
        flit.last = partial.last;
        out.tx = flit;
    }
    return out;
}

void CrossH1Node::tick(const HierarchyOutputs& node_before, const CrossOutputs& before, bool schedule_done,
                       const std::vector<std::optional<DmaCommand>>& commands, const std::vector<bool>& weight_valid,
                       bool tx_ready) {
    int selected = before.selected_engine;
    std::vector<bool> partial_ready(node.engine_count(), false);
    if (selected >= 0) partial_ready[selected] = tx_ready;
    // No state packets reach the cross node in the exact-event model
    // (publication is replayed separately), so state_valid is always false.
    node.tick(node_before, /*iter_start=*/false, schedule_done, commands, weight_valid, partial_ready);
    if (before.tx.has_value() && tx_ready && selected >= 0) {
        const int count = node.engine_count();
        if (output_locked) {
            if (before.tx->last) {
                output_locked = false;
                round_robin_start = (selected + 1) % count;
            }
        } else if (!before.tx->last) {
            output_locked = true;
            locked_engine = selected;
        } else {
            round_robin_start = (selected + 1) % count;
        }
    }
}

InjectionOutputs LocalInjectionArbiter::outputs(const std::optional<Flit>& cross, const std::optional<Flit>& h1,
                                                bool router_ready) const {
    bool select_cross = locked ? cross_selected : cross.has_value();
    InjectionOutputs out;
    out.selected = select_cross ? cross : h1;
    out.cross_ready = select_cross && router_ready;
    out.h1_ready = !select_cross && router_ready;
    return out;
}

void LocalInjectionArbiter::tick(const InjectionOutputs& before, bool router_ready) {
    if (before.selected.has_value() && router_ready) {
        bool select_cross = before.cross_ready;
        if (!locked && !before.selected->last) {
            locked = true;
            cross_selected = select_cross;
        } else if (locked && before.selected->last) {
            locked = false;
        }
    }
}

}  // namespace azilla
