// Port of EventCompressedMesh.replay from model/azilla_cycle_model/events.py.
#pragma once

#include <functional>
#include <string>
#include <vector>

#include "noc.hpp"

namespace azilla {

struct PacketRelease {
    long cycle;
    int source;
    int destination;
    int packet_type;
    int block_id = 0;
    int flits = 1;
};

struct NetworkReplayResult {
    long start_cycle = 0;
    long end_cycle = 0;
    long elapsed_cycles = 0;
    long active_cycles = 0;
    long skipped_cycles = 0;
    long injected_flits = 0;
    long ejected_flits = 0;
    long physical_link_flits = 0;
    long injection_stalls = 0;
    long link_stalls = 0;
};

// observer(cycle, scope, node, direction, flit); resource_observer adds accepted.
using TransferObserver = std::function<void(long, const char*, int, const char*, const Flit&)>;
using ResourceObserver = std::function<void(long, const char*, int, const char*, const Flit&, bool)>;

// Replays ``releases`` on ``mesh`` (reset first), returning the result and
// leaving the drained mesh state (arbitration snapshots) intact.
NetworkReplayResult replay_packets(Mesh& mesh, std::vector<PacketRelease> releases, long start_cycle,
                                   int drain_cycles, const TransferObserver& observer,
                                   const ResourceObserver& resource_observer);

}  // namespace azilla
