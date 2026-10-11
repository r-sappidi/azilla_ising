#include "shared_fetch.hpp"

#include <stdexcept>

namespace azilla {

namespace {
constexpr int SPINS = 32;
}

StreamerNodeOutput PairReplay::outputs() const {
    StreamerNodeOutput out;
    out.command_valid = state == COMMAND;
    out.command = command;
    if (command.has_value() && direction) {
        // Reverse direction: DmaCommand(block_b, block_a, block_b, block_a).
        out.command = DmaCommand{command->block_b, command->block_a, command->block_b, command->block_a};
    }
    out.weight_valid = state == SEND;
    return out;
}

void PairReplay::tick(const std::optional<DmaCommand>& command_in, bool weight_valid, bool command_ready,
                      bool weight_ready) {
    if (state == IDLE && command_in.has_value()) {
        if (command_in->block_a >= command_in->block_b)
            throw std::runtime_error("shared fetch requires canonical unordered off-diagonal jobs");
        command = command_in;
        row = direction = 0;
        state = FILL;
    } else if (state == FILL && weight_valid) {
        if (row == SPINS - 1) {
            row = 0;
            state = COMMAND;
        } else {
            row += 1;
        }
    } else if (state == COMMAND && command_ready) {
        state = SEND;
    } else if (state == SEND && weight_ready) {
        if (row < SPINS - 1) {
            row += 1;
        } else if (!direction) {
            direction = 1;
            row = 0;
            state = COMMAND;
        } else {
            state = IDLE;
            row = 0;
        }
    }
}

void DoublePairReplay::tick(const std::optional<DmaCommand>& command, bool weight_valid, bool command_ready,
                            bool weight_ready) {
    const int fill = fill_slot;
    const int drain = drain_slot;
    const bool fill_accept = weight_valid && slots[fill].state == PairReplay::FILL;
    const bool drain_accept = weight_ready && slots[drain].state == PairReplay::SEND;
    for (int i = 0; i < 2; ++i) {
        slots[i].tick(i == fill ? command : std::nullopt, weight_valid && i == fill, command_ready && i == drain,
                      weight_ready && i == drain);
    }
    if (fill_accept) {
        fill_row = (fill_row + 1) % SPINS;
        if (!fill_row) fill_slot ^= 1;
    }
    if (drain_accept) {
        drain_beat = (drain_beat + 1) % (2 * SPINS);
        if (!drain_beat) drain_slot ^= 1;
    }
}

StreamerOutputs DoubleFetchStreamer::outputs_from_base(const StreamerOutputs& base_out) const {
    StreamerOutputs o = base_out;
    bool all_idle = true;
    for (size_t e = 0; e < replay.size(); ++e) {
        o.node[e] = replay[e].outputs();
        if (!replay[e].idle()) all_idle = false;
    }
    o.idle = o.idle && all_idle;
    return o;
}

void DoubleFetchStreamer::tick(const StreamerOutputs& base_before,
                               const std::vector<std::optional<DmaCommand>>& scheduler_commands,
                               const std::vector<bool>& node_command_ready, const std::vector<bool>& node_weight_ready,
                               const std::vector<bool>& memory_request_ready,
                               const std::vector<std::optional<MemoryResponse>>& memory_responses) {
    const int n = base.mvm_count;
    std::vector<bool> cr(n), wr(n);
    for (int e = 0; e < n; ++e) {
        cr[e] = replay[e].state() == PairReplay::IDLE;
        wr[e] = replay[e].state() == PairReplay::FILL;
    }
    for (int e = 0; e < n; ++e) {
        const StreamerNodeOutput& node = base_before.node[e];
        replay[e].tick(node.command_valid && cr[e] ? node.command : std::nullopt, node.weight_valid && wr[e],
                       node_command_ready[e], node_weight_ready[e]);
    }
    base.tick(base_before, scheduler_commands, cr, wr, memory_request_ready, memory_responses);
}

}  // namespace azilla
