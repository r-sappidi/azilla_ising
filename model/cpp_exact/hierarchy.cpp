#include "hierarchy.hpp"

#include <stdexcept>

namespace azilla {

void Engine::reset() {
    state = IDLE;
    output_state = OUTPUT_IDLE;
    slot_valid = {false, false};
    load_active = false;
    load_slot = 0;
    weight_beat = 0;
    active_slot = 0;
    slot_commands = {std::nullopt, std::nullopt};
    result_valid = {false, false};
    result_reserved = {false, false};
    result_write_slot = 0;
    result_read_slot = 0;
    result_blocks = {std::make_pair(0, 0), std::make_pair(0, 0)};
    partial_beat = 0;
    state_a_valid = state_b_valid = false;
    state_a_pending = state_b_pending = false;
    mvm.reset();
}

bool Engine::work_done() const {
    return state == IDLE && !(slot_valid[0] || slot_valid[1]) && !load_active && output_state == OUTPUT_IDLE &&
           !(result_valid[0] || result_valid[1]) && !(result_reserved[0] || result_reserved[1]);
}

PartialOutput Engine::partial() const {
    if (output_state != SEND_A && output_state != SEND_B) return PartialOutput{};
    int slot = result_read_slot;
    int block = output_state == SEND_A ? result_blocks[slot].first : result_blocks[slot].second;
    return PartialOutput{true, block, partial_beat == PARTIAL_BEATS - 1};
}

HierarchyNode::HierarchyNode(int state_entry_count_, int mvm_count, bool directed)
    : state_entry_count(state_entry_count_) {
    if (state_entry_count <= 0 || mvm_count <= 0) throw std::runtime_error("state and engine counts must be positive");
    engines.resize(mvm_count);
    for (auto& engine : engines) engine.directed = directed;
    reset();
}

void HierarchyNode::reset() {
    node_state = RESET;
    schedule_done_pending = false;
    state_responses_.clear();
    for (auto& engine : engines) engine.reset();
}

HierarchyOutputs HierarchyNode::outputs() const {
    HierarchyOutputs out;
    out.iter_done = node_state == DONE;
    out.state_ready = node_state != RUN;
    out.command_ready.reserve(engines.size());
    out.weight_ready.reserve(engines.size());
    out.partials.reserve(engines.size());
    for (const auto& engine : engines) {
        out.command_ready.push_back(node_state == RUN && !engine.load_active &&
                                    !(engine.slot_valid[0] && engine.slot_valid[1]));
        out.weight_ready.push_back(node_state == RUN && engine.load_active);
        out.partials.push_back(engine.partial());
    }
    return out;
}

HierarchyNode::NodeState HierarchyNode::node_next(bool iter_start) const {
    if (node_state == RESET) return IDLE;
    if (node_state == IDLE) return iter_start ? RUN : IDLE;
    if (node_state == RUN) {
        bool work_done = schedule_done_pending;
        if (work_done)
            for (const auto& engine : engines)
                if (!engine.work_done()) { work_done = false; break; }
        return work_done ? DONE : RUN;
    }
    return iter_start ? RUN : DONE;
}

Engine::State HierarchyNode::engine_next(const Engine& engine) {
    if (engine.state == Engine::IDLE) {
        bool busy0 = engine.result_valid[0] || engine.result_reserved[0];
        bool busy1 = engine.result_valid[1] || engine.result_reserved[1];
        if ((engine.slot_valid[0] || engine.slot_valid[1]) && !(busy0 && busy1)) return Engine::FETCH;
    } else if (engine.state == Engine::FETCH) {
        if (engine.state_a_valid && engine.state_b_valid) return Engine::START;
    } else if (engine.state == Engine::START) {
        return Engine::COMPUTE;
    } else if (engine.state == Engine::COMPUTE && engine.mvm.done) {
        return Engine::IDLE;
    }
    return engine.state;
}

Engine::OutputState HierarchyNode::output_next(const Engine& engine, bool accepted) {
    if (engine.output_state == Engine::OUTPUT_IDLE)
        return (engine.result_valid[0] || engine.result_valid[1]) ? Engine::SEND_A : Engine::OUTPUT_IDLE;
    if (engine.output_state == Engine::SEND_A && accepted && engine.partial_beat == PARTIAL_BEATS - 1)
        return engine.directed ? Engine::OUTPUT_IDLE : Engine::SEND_B;
    if (engine.output_state == Engine::SEND_B && accepted && engine.partial_beat == PARTIAL_BEATS - 1)
        return Engine::OUTPUT_IDLE;
    return engine.output_state;
}

void HierarchyNode::tick(const HierarchyOutputs& before, bool iter_start, bool schedule_done,
                         const std::vector<std::optional<DmaCommand>>& commands,
                         const std::vector<bool>& weight_valid, const std::vector<bool>& partial_ready) {
    const int count = int(engines.size());
    if (int(commands.size()) != count || int(weight_valid.size()) != count || int(partial_ready.size()) != count)
        throw std::runtime_error("one DMA/partial value is required per engine");

    NodeState next_node = node_next(iter_start);
    std::vector<Engine::State> next_engine(count);
    std::vector<bool> accepted(count);
    std::vector<Engine::OutputState> next_output(count);
    for (int index = 0; index < count; ++index) {
        next_engine[index] = engine_next(engines[index]);
        accepted[index] = before.partials[index].valid && partial_ready[index];
        next_output[index] = output_next(engines[index], accepted[index]);
    }

    // State-bank reads: accepted reads return one cycle later.  Same-bank
    // reads serialize in lane order (engine-major, operand A before B).
    std::vector<int> old_state_responses = std::move(state_responses_);
    std::vector<int> next_state_responses;
    unsigned used_banks = 0;
    for (int lane = 0; lane < 2 * count; ++lane) {
        Engine& engine = engines[lane / 2];
        bool operand_b = lane & 1;
        bool valid = engine.state == Engine::FETCH &&
                     (operand_b ? (!engine.state_b_valid && !engine.state_b_pending)
                                : (!engine.state_a_valid && !engine.state_a_pending));
        if (!valid) continue;
        const auto& command = engine.slot_commands[engine.active_slot];
        if (!command) throw std::runtime_error("state fetch has no command metadata");
        int state_index = operand_b ? command->state_b_index : command->state_a_index;
        int bank = state_index % STATE_BANK_COUNT;
        if (used_banks & (1u << bank)) continue;
        used_banks |= 1u << bank;
        next_state_responses.push_back(lane);
        if (operand_b) engine.state_b_pending = true;
        else engine.state_a_pending = true;
    }

    node_state = next_node;
    if (iter_start) schedule_done_pending = false;
    else if (schedule_done) schedule_done_pending = true;

    for (int index = 0; index < count; ++index) {
        Engine& engine = engines[index];
        Engine::State old_state = engine.state;
        Engine::OutputState old_output_state = engine.output_state;
        bool old_done = engine.mvm.done;
        int old_active_slot = engine.active_slot;
        int old_load_slot = engine.load_slot;
        int old_weight_beat = engine.weight_beat;
        int old_partial_beat = engine.partial_beat;
        int old_result_read_slot = engine.result_read_slot;
        int old_result_write_slot = engine.result_write_slot;
        std::array<bool, 2> old_slot_valid = engine.slot_valid;
        std::array<bool, 2> old_result_valid = engine.result_valid;
        std::array<bool, 2> old_result_reserved = engine.result_reserved;

        bool command_accepted = commands[index].has_value() && before.command_ready[index];
        bool weight_accepted = weight_valid[index] && before.weight_ready[index];

        engine.mvm.tick(old_state == Engine::START);

        engine.state = next_engine[index];
        engine.output_state = next_output[index];

        if (command_accepted) {
            int slot = !engine.slot_valid[0] ? 0 : 1;
            engine.load_slot = slot;
            engine.slot_commands[slot] = commands[index];
            engine.load_active = true;
            engine.weight_beat = 0;
        }
        if (weight_accepted) {
            if (old_weight_beat == WEIGHT_BEATS - 1) {
                engine.slot_valid[old_load_slot] = true;
                engine.load_active = false;
                engine.weight_beat = 0;
            } else {
                engine.weight_beat = old_weight_beat + 1;
            }
        }
        if (old_state == Engine::IDLE && next_engine[index] == Engine::FETCH) {
            int selected_j = old_slot_valid[0] ? 0 : 1;
            int selected_result = (old_result_valid[0] || old_result_reserved[0]) ? 1 : 0;
            engine.active_slot = selected_j;
            engine.result_write_slot = selected_result;
            engine.result_reserved[selected_result] = true;
            const auto& command = engine.slot_commands[selected_j];
            if (!command) throw std::runtime_error("valid J slot has no command metadata");
            engine.result_blocks[selected_result] = {command->block_a, command->block_b};
            // A directed job reads only the source (operand B) states.
            engine.state_a_valid = engine.directed;
            engine.state_b_valid = false;
            engine.state_a_pending = engine.state_b_pending = false;
        }
        if (old_state == Engine::COMPUTE && old_done) {
            engine.result_reserved[old_result_write_slot] = false;
            engine.result_valid[old_result_write_slot] = true;
            engine.slot_valid[old_active_slot] = false;
        }
        if (old_output_state == Engine::OUTPUT_IDLE && next_output[index] == Engine::SEND_A)
            engine.result_read_slot = old_result_valid[0] ? 0 : 1;
        if (old_output_state == Engine::SEND_A || old_output_state == Engine::SEND_B) {
            if (accepted[index]) engine.partial_beat = (old_partial_beat + 1) % PARTIAL_BEATS;
        } else {
            engine.partial_beat = 0;
        }
        if (old_output_state == (engine.directed ? Engine::SEND_A : Engine::SEND_B) && accepted[index] &&
            old_partial_beat == PARTIAL_BEATS - 1)
            engine.result_valid[old_result_read_slot] = false;
    }

    for (int lane : old_state_responses) {
        Engine& engine = engines[lane / 2];
        if (lane & 1) {
            engine.state_b_valid = true;
            engine.state_b_pending = false;
        } else {
            engine.state_a_valid = true;
            engine.state_a_pending = false;
        }
    }
    state_responses_ = std::move(next_state_responses);
}

}  // namespace azilla
