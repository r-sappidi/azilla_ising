#include "memory.hpp"

#include <algorithm>
#include <stdexcept>

namespace azilla {

DramWeightStreamer::DramWeightStreamer(int mvm_count_, long total_block_count_, int request_lanes_,
                                       int response_lanes_, int max_outstanding_)
    : mvm_count(mvm_count_), total_block_count(total_block_count_), request_lanes(request_lanes_),
      response_lanes(response_lanes_ > 0 ? response_lanes_ : request_lanes_), max_outstanding(max_outstanding_) {
    if (mvm_count <= 0 || total_block_count <= 0 || request_lanes <= 0)
        throw std::runtime_error("streamer geometry must be positive");
    slots.resize(mvm_count);
    head.assign(mvm_count, 0);
    tail.assign(mvm_count, 0);
}

void DramWeightStreamer::reset() {
    for (auto& engine : slots)
        for (auto& slot : engine) slot.clear();
    std::fill(head.begin(), head.end(), 0);
    std::fill(tail.begin(), tail.end(), 0);
}

int DramWeightStreamer::encode_tag(int engine, int buffer, int beat) const {
    return (engine << (beat_width + 1)) | (buffer << beat_width) | beat;
}

void DramWeightStreamer::decode_tag(int tag, int& engine, int& buffer, int& beat) const {
    int beat_mask = (1 << beat_width) - 1;
    engine = tag >> (beat_width + 1);
    buffer = (tag >> beat_width) & 1;
    beat = tag & beat_mask;
}

uint64_t DramWeightStreamer::block_address(int block_a, int block_b, int beat) const {
    uint64_t low = uint64_t(std::min(block_a, block_b)), high = uint64_t(std::max(block_a, block_b));
    const uint64_t block_bytes = 32 * 32 * 8 / 8;  // 1024
    const uint64_t tx_bytes = 256 / 8;             // 32
    return (low * uint64_t(total_block_count) + high) * block_bytes + uint64_t(beat) * tx_bytes;
}

int DramWeightStreamer::outstanding() const {
    int total = 0;
    for (const auto& engine : slots)
        for (const auto& slot : engine) total += slot.request_count - slot.responses();
    return total;
}

StreamerOutputs DramWeightStreamer::outputs() const {
    StreamerOutputs out;
    out.scheduler_ready.resize(mvm_count);
    out.node.resize(mvm_count);
    for (int engine = 0; engine < mvm_count; ++engine) {
        out.scheduler_ready[engine] = !slots[engine][tail[engine]].allocated;
        const Slot& slot = slots[engine][head[engine]];
        bool complete = slot.complete();
        out.node[engine].command_valid = complete && !slot.command_sent;
        out.node[engine].command = slot.command;
        out.node[engine].weight_valid = complete && slot.command_sent;
    }
    out.memory_requests.assign(request_lanes, std::nullopt);
    int lane = 0;
    out.outstanding = outstanding();
    out.idle = true;
    for (int engine = 0; engine < mvm_count; ++engine) {
        for (int buffer = 0; buffer < 2; ++buffer) {
            const Slot& slot = slots[engine][buffer];
            if (slot.allocated) out.idle = false;
            if (lane < request_lanes && lane < max_outstanding - out.outstanding && slot.allocated &&
                !slot.requests_done) {
                int beat = slot.request_count;
                if (!slot.command) throw std::runtime_error("allocated streamer slot lacks metadata");
                out.memory_requests[lane] = MemoryRequest{
                    block_address(slot.command->block_a, slot.command->block_b, beat),
                    encode_tag(engine, buffer, beat)};
                ++lane;
            }
        }
    }
    return out;
}

void DramWeightStreamer::tick(const StreamerOutputs& before,
                              const std::vector<std::optional<DmaCommand>>& scheduler_commands,
                              const std::vector<bool>& node_command_ready, const std::vector<bool>& node_weight_ready,
                              const std::vector<bool>& memory_request_ready,
                              const std::vector<std::optional<MemoryResponse>>& memory_responses) {
    if (int(scheduler_commands.size()) != mvm_count) throw std::runtime_error("one scheduler command is required per engine");
    if (int(node_command_ready.size()) != mvm_count || int(node_weight_ready.size()) != mvm_count)
        throw std::runtime_error("one node ready is required per engine");
    if (int(memory_request_ready.size()) != request_lanes) throw std::runtime_error("request-ready lane count mismatch");
    if (int(memory_responses.size()) != response_lanes) throw std::runtime_error("response lane count mismatch");

    std::vector<int> old_heads = head;

    for (int engine = 0; engine < mvm_count; ++engine) {
        if (scheduler_commands[engine].has_value() && before.scheduler_ready[engine]) {
            int buffer = tail[engine];
            Slot& slot = slots[engine][buffer];
            slot.allocated = true;
            slot.requests_done = false;
            slot.command_sent = false;
            slot.command = scheduler_commands[engine];
            slot.request_count = 0;
            slot.response_valid = 0;
            slot.send_beat = 0;
            tail[engine] ^= 1;
        }
    }
    for (int lane = 0; lane < request_lanes; ++lane) {
        const auto& request = before.memory_requests[lane];
        if (request.has_value() && memory_request_ready[lane]) {
            int engine, buffer, beat;
            decode_tag(request->tag, engine, buffer, beat);
            Slot& slot = slots[engine][buffer];
            if (beat == SPIN_COUNT - 1) {
                slot.requests_done = true;
                slot.request_count = SPIN_COUNT;
            } else {
                slot.request_count += 1;
            }
        }
    }
    for (const auto& response : memory_responses) {
        if (!response) continue;
        int engine, buffer, beat;
        decode_tag(response->tag, engine, buffer, beat);
        if (!(0 <= engine && engine < mvm_count && 0 <= beat && beat < SPIN_COUNT))
            throw std::runtime_error("invalid response tag");
        Slot& slot = slots[engine][buffer];
        if (!slot.allocated || (slot.response_valid & (1u << beat)))
            throw std::runtime_error("stale or duplicate response tag");
        slot.response_valid |= 1u << beat;
    }
    for (int engine = 0; engine < mvm_count; ++engine) {
        int buffer = old_heads[engine];
        Slot& slot = slots[engine][buffer];
        const auto& node_output = before.node[engine];
        if (node_output.command_valid && node_command_ready[engine]) slot.command_sent = true;
        if (node_output.weight_valid && node_weight_ready[engine]) {
            if (slot.send_beat == SPIN_COUNT - 1) {
                slot.clear();
                head[engine] ^= 1;
            } else {
                slot.send_beat += 1;
            }
        }
    }
}

}  // namespace azilla
