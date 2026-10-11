// Port of model/azilla_cycle_model/hierarchy.py (timing-only) and the
// SymmetricMVM state machine from compute.py.
#pragma once

#include <array>
#include <optional>
#include <utility>
#include <vector>

#include "scheduler.hpp"

namespace azilla {

constexpr int SPIN_COUNT = 32;
constexpr int PARTIAL_LANES = 8;    // 256 / 32
constexpr int PARTIAL_BEATS = 4;    // 32 / 8
constexpr int WEIGHT_BEATS = 32;
constexpr int STATE_BANK_COUNT = 8;

struct PartialOutput {
    bool valid = false;
    int block_id = 0;
    bool last = false;
};

// Timing-only mirror of rtl/symmetric_mvm.sv (compute.SymmetricMVM).
struct SymmetricMVM {
    bool active = false;
    int process_row = 0;
    int request_row = 0;
    bool read_valid = false;
    bool done = false;

    void reset() { active = false; process_row = 0; request_row = 0; read_valid = false; done = false; }
    void tick(bool start) {
        done = false;  // symmetric_mvm pulses done for one cycle
        if (start && !active) {
            active = true;
            process_row = 0;
            request_row = 1 % SPIN_COUNT;
            read_valid = true;
            return;
        }
        if (!(active && read_valid)) return;
        int row = process_row;
        if (row == SPIN_COUNT - 1) {
            active = false;
            process_row = 0;
            request_row = 0;
            read_valid = false;
            done = true;
        } else {
            process_row += 1;
            if (request_row != SPIN_COUNT - 1) request_row += 1;
        }
    }
};

struct Engine {
    enum State { IDLE, FETCH, START, COMPUTE };
    enum OutputState { OUTPUT_IDLE, SEND_A, SEND_B };

    State state = IDLE;
    OutputState output_state = OUTPUT_IDLE;
    std::array<bool, 2> slot_valid{};
    bool load_active = false;
    int load_slot = 0;
    int weight_beat = 0;
    int active_slot = 0;
    std::array<std::optional<DmaCommand>, 2> slot_commands{};
    std::array<bool, 2> result_valid{};
    std::array<bool, 2> result_reserved{};
    int result_write_slot = 0;
    int result_read_slot = 0;
    std::array<std::pair<int, int>, 2> result_blocks{};
    int partial_beat = 0;
    bool state_a_valid = false;
    bool state_b_valid = false;
    bool state_a_pending = false;
    bool state_b_pending = false;
    SymmetricMVM mvm;

    void reset();
    bool work_done() const;
    PartialOutput partial() const;
};

struct HierarchyOutputs {
    bool iter_done = false;
    bool state_ready = false;
    std::vector<bool> command_ready;
    std::vector<bool> weight_ready;
    std::vector<PartialOutput> partials;
};

class HierarchyNode {
public:
    enum NodeState { RESET, IDLE, RUN, DONE };

    HierarchyNode(int state_entry_count, int mvm_count);
    void reset();
    HierarchyOutputs outputs() const;
    int engine_count() const { return int(engines.size()); }

    // ``before`` must be outputs() for the current state.
    void tick(const HierarchyOutputs& before, bool iter_start, bool schedule_done,
              const std::vector<std::optional<DmaCommand>>& commands, const std::vector<bool>& weight_valid,
              const std::vector<bool>& partial_ready);

    NodeState node_state = RESET;
    bool schedule_done_pending = false;
    int state_entry_count;
    std::vector<Engine> engines;

private:
    NodeState node_next(bool iter_start) const;
    static Engine::State engine_next(const Engine& engine);
    static Engine::OutputState output_next(const Engine& engine, bool accepted);
    std::vector<int> state_responses_;  // lanes whose read response is latched
};

}  // namespace azilla
