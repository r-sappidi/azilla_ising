// H0-anchored symmetric ownership (execution mode cir-h0-anchored): every
// off-H0 block is fetched once and evaluated symmetrically at one endpoint
// H0; the partial for the other endpoint leaves the H0 through one uplink
// port, is switched by the H1 child crossbar when the destination is a
// sibling H0, and is injected into the mesh otherwise.  The receive path is
// the existing cross-H1 partial path.  C++ model only; no RTL or Python
// counterpart.
#pragma once

#include <deque>
#include <optional>
#include <utility>
#include <vector>

#include "hierarchy.hpp"
#include "noc.hpp"

namespace azilla {

struct UplinkOutputs {
    // Input side: the engine whose remote partial beat enters the FIFO.
    int selected_engine = -1;
    bool input_ready = false;  // FIFO has room for one beat
    // Output side: the FIFO head.
    bool head_valid = false;
    bool same_h1 = false;      // destination block lives in this H1
    PartialOutput partial;     // as presented to the H1 child crossbar
    std::optional<Flit> tx;    // as presented to the mesh injection arbiter
};

// One 256-bit uplink per H0: round-robin, packet-locking selection among the
// engines whose current partial addresses a block outside this H0 (the
// CrossH1Node selection rule) into two FIFOs of ``fifo_packets`` four-beat
// partials each, one for sibling H0s (H1 crossbar) and one for other H1s
// (mesh).  One beat per cycle leaves the H0: the output side locks on the
// queue of the packet in flight and, between packets, moves to the other
// queue whenever the presented head is not accepted (no head-of-line
// blocking across the two paths).
class H0Uplink {
public:
    // ``accumulate``: remote partials are summed into a per-destination-core
    // accumulator at this H0 (one read-modify-write port, one beat per cycle,
    // the same engine arbitration as the FIFO input) and, once the H0's
    // engines are done, flushed as one four-beat packet per touched remote
    // core through the same FIFOs and output path.  ``total_blocks`` sizes
    // the accumulator index.
    H0Uplink(int first_block, int block_count, int blocks_per_h1, int mesh_x_count, int node_id, int engine_count,
             int fifo_packets, bool accumulate = false, int total_blocks = 0);
    void reset();
    UplinkOutputs outputs(const std::vector<PartialOutput>& partials) const;
    void tick(const UplinkOutputs& before, const std::vector<PartialOutput>& partials, bool output_accepted);
    // Accumulate mode: start flushing (call once the H0 node is DONE).
    void start_flush();
    bool flush_started() const { return flush_started_; }
    // Accumulate mode: every flush packet has left the FIFOs.
    bool flushed() const {
        return flush_started_ && flush_index_ == flush_list_.size() && fifo[0].empty() && fifo[1].empty();
    }
    // No partial is left in this uplink: flushed() in accumulate mode, both
    // FIFOs empty otherwise.  The epoch-done quiet window must not open while
    // a sibling-bound packet is still queued here (it is not NoC activity).
    bool drained() const { return accumulate ? flushed() : fifo[0].empty() && fifo[1].empty(); }
    long touched_remote_blocks() const { return long(flush_list_.size()); }

    int first_block, block_count, blocks_per_h1, mesh_x_count, node_id, engine_count, fifo_packets;
    bool accumulate;
    std::vector<char> touched_;      // accumulate: per global block, a remote partial was summed
    std::vector<int> flush_list_;    // accumulate: touched remote blocks in index order
    size_t flush_index_ = 0;
    int flush_beat_ = 0;
    bool flush_started_ = false;
    bool input_locked = false;
    int locked_engine = 0;
    int round_robin_start = 0;
    std::deque<std::pair<int, bool>> fifo[2];  // [0] sibling, [1] mesh: (block_id, last)
    int out_queue = 0;
    bool out_locked = false;

private:
    bool remote(const PartialOutput& p) const {
        return p.valid && !(first_block <= p.block_id && p.block_id < first_block + block_count);
    }
};

struct UplinkInjectionOutputs {
    int selected_h0 = -1;
    std::optional<Flit> tx;
};

// Per-H1 round-robin, packet-locking arbiter over the H0 uplinks that want
// the mesh this cycle; its output takes the cross node's slot in the local
// injection arbiter (cross nodes carry no jobs in this mode).
class UplinkInjectionArbiter {
public:
    explicit UplinkInjectionArbiter(int h0_count = 1) : h0_count(h0_count) {}
    void reset() { output_locked = false; locked_h0 = 0; round_robin_start = 0; }
    UplinkInjectionOutputs outputs(const std::vector<std::optional<Flit>>& remote_tx) const;
    void tick(const UplinkInjectionOutputs& before, bool accepted);

    int h0_count;
    bool output_locked = false;
    int locked_h0 = 0;
    int round_robin_start = 0;
};

}  // namespace azilla
