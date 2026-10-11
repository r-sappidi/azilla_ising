// Port of model/azilla_cycle_model/shared_fetch.py (PairReplay,
// SharedFetchStreamer) and shared_fetch_double.py (DoublePairReplay,
// DoubleFetchStreamer): fetch a canonical block once, replay it to both
// endpoints through two slots (fetch_policy single_fetch_two_slot_cir_routes_v3).
// Weight payload rows are not stored (timing-only).
#pragma once

#include <memory>
#include <optional>
#include <vector>

#include "memory.hpp"

namespace azilla {

struct PairReplay {
    enum State { IDLE, FILL, COMMAND, SEND };
    State state = IDLE;
    std::optional<DmaCommand> command;
    int row = 0;
    int direction = 0;

    void reset() {
        state = IDLE;
        command.reset();
        row = 0;
        direction = 0;
    }
    StreamerNodeOutput outputs() const;
    void tick(const std::optional<DmaCommand>& command_in, bool weight_valid, bool command_ready, bool weight_ready);
};

struct DoublePairReplay {
    PairReplay slots[2];
    int fill_slot = 0;
    int drain_slot = 0;
    int fill_row = 0;
    int drain_beat = 0;

    void reset() {
        slots[0].reset();
        slots[1].reset();
        fill_slot = drain_slot = fill_row = drain_beat = 0;
    }
    // SharedFetchStreamer uses ``state`` only for its input handshake/idle.
    PairReplay::State state() const { return slots[fill_slot].state; }
    bool idle() const { return slots[0].state == PairReplay::IDLE && slots[1].state == PairReplay::IDLE; }
    StreamerNodeOutput outputs() const { return slots[drain_slot].outputs(); }
    void tick(const std::optional<DmaCommand>& command, bool weight_valid, bool command_ready, bool weight_ready);
};

// DoubleFetchStreamer: DramWeightStreamer base plus one DoublePairReplay per engine.
class DoubleFetchStreamer {
public:
    DoubleFetchStreamer(int mvm_count, long total_block_count, int request_lanes, int response_lanes)
        : base(mvm_count, total_block_count, request_lanes, response_lanes), replay(mvm_count) {}

    void reset() {
        base.reset();
        for (auto& r : replay) r.reset();
    }
    int outstanding() const { return base.outstanding(); }
    int mvm_count() const { return base.mvm_count; }
    // Combined outputs: scheduler_ready/memory_requests/outstanding from the
    // base, node outputs from the replay drain slots, idle = both idle.
    StreamerOutputs outputs() const { return outputs_from_base(base.outputs()); }
    // Same, from an already computed base.outputs() (avoids recomputation).
    StreamerOutputs outputs_from_base(const StreamerOutputs& base_out) const;
    // ``base_before`` must be base.outputs() for the current state.
    void tick(const StreamerOutputs& base_before, const std::vector<std::optional<DmaCommand>>& scheduler_commands,
              const std::vector<bool>& node_command_ready, const std::vector<bool>& node_weight_ready,
              const std::vector<bool>& memory_request_ready,
              const std::vector<std::optional<MemoryResponse>>& memory_responses);

    DramWeightStreamer base;
    std::vector<DoublePairReplay> replay;
};

}  // namespace azilla
