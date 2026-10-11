// Port of model/azilla_cycle_model/memory.py: DramWeightStreamer.
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

#include "hierarchy.hpp"

namespace azilla {

struct MemoryRequest {
    uint64_t address;
    int tag;
};

struct MemoryResponse {
    int tag;
};

struct StreamerNodeOutput {
    bool command_valid = false;
    std::optional<DmaCommand> command;
    bool weight_valid = false;
};

struct StreamerOutputs {
    std::vector<bool> scheduler_ready;
    std::vector<StreamerNodeOutput> node;
    std::vector<std::optional<MemoryRequest>> memory_requests;
    int outstanding = 0;
    bool idle = true;
};

class DramWeightStreamer {
public:
    struct Slot {
        bool allocated = false;
        bool requests_done = false;
        bool command_sent = false;
        std::optional<DmaCommand> command;
        int request_count = 0;
        uint32_t response_valid = 0;  // bit per beat (32 beats)
        int send_beat = 0;
        void clear() {
            allocated = requests_done = command_sent = false;
            command.reset();
            request_count = 0;
            response_valid = 0;
            send_beat = 0;
        }
        int responses() const { return __builtin_popcount(response_valid); }
        bool complete() const { return allocated && response_valid == 0xFFFFFFFFu; }
    };

    DramWeightStreamer(int mvm_count, long total_block_count, int request_lanes, int response_lanes,
                       int max_outstanding = 64);
    void reset();
    int encode_tag(int engine, int buffer, int beat) const;
    void decode_tag(int tag, int& engine, int& buffer, int& beat) const;
    uint64_t block_address(int block_a, int block_b, int beat) const;
    int outstanding() const;
    StreamerOutputs outputs() const;
    // ``before`` must be outputs() for the current state.
    void tick(const StreamerOutputs& before, const std::vector<std::optional<DmaCommand>>& scheduler_commands,
              const std::vector<bool>& node_command_ready, const std::vector<bool>& node_weight_ready,
              const std::vector<bool>& memory_request_ready,
              const std::vector<std::optional<MemoryResponse>>& memory_responses);

    int mvm_count;
    long total_block_count;
    int request_lanes;
    int response_lanes;
    int max_outstanding;
    int beat_width = 5;
    std::vector<std::array<Slot, 2>> slots;
    std::vector<int> head;
    std::vector<int> tail;
};

}  // namespace azilla
