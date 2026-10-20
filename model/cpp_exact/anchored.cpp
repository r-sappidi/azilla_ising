#include "anchored.hpp"

#include <algorithm>
#include <stdexcept>

namespace azilla {

H0Uplink::H0Uplink(int first_block_, int block_count_, int blocks_per_h1_, int mesh_x_count_, int node_id_,
                   int engine_count_, int fifo_packets_, bool accumulate_, int total_blocks)
    : first_block(first_block_), block_count(block_count_), blocks_per_h1(blocks_per_h1_),
      mesh_x_count(mesh_x_count_), node_id(node_id_), engine_count(engine_count_), fifo_packets(fifo_packets_),
      accumulate(accumulate_) {
    if (fifo_packets < 1) throw std::runtime_error("uplink FIFO needs at least one packet");
    if (accumulate && total_blocks <= 0) throw std::runtime_error("accumulate mode needs the block count");
    if (accumulate) touched_.assign(size_t(total_blocks), 0);
}

void H0Uplink::reset() {
    input_locked = false;
    locked_engine = 0;
    round_robin_start = 0;
    fifo[0].clear();
    fifo[1].clear();
    out_queue = 0;
    out_locked = false;
    std::fill(touched_.begin(), touched_.end(), 0);
    flush_list_.clear();
    flush_index_ = 0;
    flush_beat_ = 0;
    flush_started_ = false;
}

void H0Uplink::start_flush() {
    if (!accumulate || flush_started_) return;
    flush_started_ = true;
    for (size_t block = 0; block < touched_.size(); ++block)
        if (touched_[block]) flush_list_.push_back(int(block));
    flush_index_ = 0;
    flush_beat_ = 0;
}

UplinkOutputs H0Uplink::outputs(const std::vector<PartialOutput>& partials) const {
    UplinkOutputs out;
    const int count = int(partials.size());
    if (input_locked) {
        if (remote(partials[locked_engine])) out.selected_engine = locked_engine;
    } else {
        for (int offset = 0; offset < count; ++offset) {
            int candidate = (round_robin_start + offset) % count;
            if (remote(partials[candidate])) { out.selected_engine = candidate; break; }
        }
    }
    if (out.selected_engine >= 0) {
        int queue = partials[out.selected_engine].block_id / blocks_per_h1 == node_id ? 0 : 1;
        // Accumulate: the RMW port takes one beat per cycle, never blocks.
        out.input_ready = accumulate || int(fifo[queue].size()) < fifo_packets * PARTIAL_BEATS;
    }
    int queue = out_queue;
    if (!out_locked && fifo[queue].empty()) queue = 1 - queue;
    if (fifo[queue].empty()) return out;
    const auto& [block_id, last] = fifo[queue].front();
    out.head_valid = true;
    int destination = block_id / blocks_per_h1;
    out.same_h1 = queue == 0;
    (void)destination;
    if (out.same_h1) {
        out.partial = PartialOutput{true, block_id, last};
    } else {
        Flit flit;
        flit.packet_type = NOC_PARTIAL;
        flit.dest_x = destination % mesh_x_count;
        flit.dest_y = destination / mesh_x_count;
        flit.source_id = node_id;
        flit.epoch = 0;
        flit.block_id = block_id;
        flit.last = last;
        out.tx = flit;
    }
    return out;
}

void H0Uplink::tick(const UplinkOutputs& before, const std::vector<PartialOutput>& partials, bool output_accepted) {
    if (before.head_valid) {
        int queue = before.same_h1 ? 0 : 1;
        if (output_accepted) {
            bool last = fifo[queue].front().second;
            fifo[queue].pop_front();
            out_queue = queue;
            if (last) {
                out_locked = false;
                out_queue = 1 - queue;  // give the other path the next turn
            } else {
                out_locked = true;
            }
        } else if (!out_locked) {
            out_queue = 1 - queue;  // head blocked between packets: try the other path
        }
    }
    // Accumulate: flush one beat per cycle into the destination FIFO.
    if (flush_started_ && flush_index_ < flush_list_.size()) {
        int block = flush_list_[flush_index_];
        int queue = block / blocks_per_h1 == node_id ? 0 : 1;
        if (int(fifo[queue].size()) < fifo_packets * PARTIAL_BEATS) {
            fifo[queue].emplace_back(block, flush_beat_ == PARTIAL_BEATS - 1);
            if (++flush_beat_ == PARTIAL_BEATS) {
                flush_beat_ = 0;
                ++flush_index_;
            }
        }
    }
    if (before.selected_engine >= 0 && before.input_ready) {
        const PartialOutput& partial = partials[before.selected_engine];
        int queue = partial.block_id / blocks_per_h1 == node_id ? 0 : 1;
        if (accumulate) touched_[size_t(partial.block_id)] = 1;
        else fifo[queue].emplace_back(partial.block_id, partial.last);
        if (input_locked) {
            if (partial.last) {
                input_locked = false;
                round_robin_start = (before.selected_engine + 1) % engine_count;
            }
        } else if (!partial.last) {
            input_locked = true;
            locked_engine = before.selected_engine;
        } else {
            round_robin_start = (before.selected_engine + 1) % engine_count;
        }
    }
}

UplinkInjectionOutputs UplinkInjectionArbiter::outputs(const std::vector<std::optional<Flit>>& remote_tx) const {
    UplinkInjectionOutputs out;
    if (output_locked) {
        if (remote_tx[locked_h0].has_value()) out.selected_h0 = locked_h0;
    } else {
        for (int offset = 0; offset < h0_count; ++offset) {
            int candidate = (round_robin_start + offset) % h0_count;
            if (remote_tx[candidate].has_value()) { out.selected_h0 = candidate; break; }
        }
    }
    if (out.selected_h0 >= 0) out.tx = remote_tx[out.selected_h0];
    return out;
}

void UplinkInjectionArbiter::tick(const UplinkInjectionOutputs& before, bool accepted) {
    if (before.selected_h0 < 0 || !accepted) return;
    if (output_locked) {
        if (before.tx->last) {
            output_locked = false;
            round_robin_start = (before.selected_h0 + 1) % h0_count;
        }
    } else if (!before.tx->last) {
        output_locked = true;
        locked_h0 = before.selected_h0;
    } else {
        round_robin_start = (before.selected_h0 + 1) % h0_count;
    }
}

}  // namespace azilla
