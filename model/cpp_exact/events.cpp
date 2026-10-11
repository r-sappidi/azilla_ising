#include "events.hpp"

#include <algorithm>
#include <stdexcept>
#include <tuple>

namespace azilla {

namespace {
struct PacketState {
    PacketRelease release;
    int remaining;
};

const char* port_name(int port) {
    switch (port) {
        case NORTH: return "north";
        case SOUTH: return "south";
        case EAST: return "east";
        default: return "west";
    }
}
}  // namespace

NetworkReplayResult replay_packets(Mesh& mesh, std::vector<PacketRelease> releases, long start_cycle,
                                   int drain_cycles, const TransferObserver& observer,
                                   const ResourceObserver& resource_observer) {
    const int width = mesh.width, height = mesh.height;
    const int node_count = width * height;
    std::stable_sort(releases.begin(), releases.end(), [](const PacketRelease& a, const PacketRelease& b) {
        return std::tie(a.cycle, a.source, a.packet_type, a.block_id) <
               std::tie(b.cycle, b.source, b.packet_type, b.block_id);
    });
    for (const auto& release : releases) {
        if (release.cycle < start_cycle) throw std::runtime_error("packet release precedes replay start");
        if (!(0 <= release.source && release.source < node_count && 0 <= release.destination &&
              release.destination < node_count))
            throw std::runtime_error("packet endpoint lies outside mesh");
        if (release.flits <= 0) throw std::runtime_error("packet must contain at least one flit");
    }
    mesh.reset();
    std::vector<std::vector<PacketState>> sources(node_count);
    long pending_sources = 0;
    auto empty = [&]() { return pending_sources == 0 && !mesh.has_data(); };

    long now = start_cycle;
    size_t release_index = 0;
    NetworkReplayResult result;
    result.start_cycle = start_cycle;
    long quiet = 0;
    std::vector<std::optional<Flit>> injections(node_count);
    std::vector<bool> local_ready(node_count, true);

    while (release_index < releases.size() || !empty() || quiet < drain_cycles) {
        if (empty() && release_index < releases.size() && quiet == 0) {
            long target = releases[release_index].cycle;
            if (target > now) {
                result.skipped_cycles += target - now;
                mesh.advance_idle(target - now);
                now = target;
            }
        }
        while (release_index < releases.size() && releases[release_index].cycle <= now) {
            const auto& release = releases[release_index];
            sources[release.source].push_back({release, release.flits});
            ++pending_sources;
            ++release_index;
        }

        std::vector<RouterOutputs> comb = mesh.all_outputs();
        bool any_injection = false;
        for (int source = 0; source < node_count; ++source) {
            injections[source].reset();
            auto& queue = sources[source];
            if (queue.empty()) continue;
            PacketState& packet = queue.front();
            int destination = packet.release.destination;
            Flit flit;
            flit.packet_type = packet.release.packet_type;
            flit.dest_x = destination % width;
            flit.dest_y = destination / width;
            flit.source_id = source;
            flit.block_id = packet.release.block_id;
            flit.last = packet.remaining == 1;
            injections[source] = flit;
            any_injection = true;
            bool accepted = comb[source].input_ready[LOCAL];
            if (resource_observer) resource_observer(now, "inject", source, "local", flit, accepted);
            if (accepted) {
                result.injected_flits += 1;
                if (observer) observer(now, "inject", source, "local", flit);
                packet.remaining -= 1;
                if (packet.remaining == 0) {
                    queue.erase(queue.begin());
                    --pending_sources;
                }
            } else {
                result.injection_stalls += 1;
            }
        }

        bool activity = any_injection;
        for (int node = 0; node < node_count; ++node) {
            const RouterOutputs& output = comb[node];
            if (output.output_flits[LOCAL].has_value()) {
                result.ejected_flits += 1;
                activity = true;
                if (resource_observer) resource_observer(now, "eject", node, "local", *output.output_flits[LOCAL], true);
                if (observer) observer(now, "eject", node, "local", *output.output_flits[LOCAL]);
            }
            for (const auto& n : Mesh::neighbors(mesh.routers[node])) {
                const auto& flit = output.output_flits[n.out_port];
                if (!flit.has_value()) continue;
                activity = true;
                if (mesh.in_mesh(n.nx, n.ny)) {
                    bool accepted = mesh.link_ready(node, n.out_port, comb);
                    if (resource_observer) resource_observer(now, "link", node, port_name(n.out_port), *flit, accepted);
                    if (accepted) {
                        result.physical_link_flits += 1;
                        if (observer) observer(now, "link", node, port_name(n.out_port), *flit);
                    } else {
                        result.link_stalls += 1;
                    }
                }
            }
        }

        mesh.tick(injections, local_ready, comb);
        result.active_cycles += 1;
        now += 1;
        if (release_index == releases.size() && empty())
            quiet = activity ? 0 : quiet + 1;
        else
            quiet = 0;
    }
    result.end_cycle = now;
    result.elapsed_cycles = now - start_cycle;
    return result;
}

}  // namespace azilla
