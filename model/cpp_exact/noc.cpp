#include "noc.hpp"

namespace azilla {

// ---------------------------------------------------------------- DirectedLink

void DirectedLink::reset() {
    cycle = 0;
    next_launch_cycle = 0;
    credits_in_use = 0;
    inflight.clear();
    arrived.clear();
    credit_returns.clear();
}

void DirectedLink::advance_control(long new_cycle) {
    cycle = new_cycle;
    while (!credit_returns.empty() && credit_returns.front() <= cycle) {
        credit_returns.pop_front();
        credits_in_use -= 1;
    }
    while (!inflight.empty() && inflight.front().first <= cycle) {
        arrived.push_back(inflight.front().second);
        inflight.pop_front();
    }
    if (credits_in_use < 0) throw std::runtime_error("link returned more credits than it consumed");
}

void DirectedLink::tick(const std::optional<Flit>& source_flit, bool launch_accepted, bool delivery_accepted) {
    if (delivery_accepted) {
        if (config.forward_latency_cycles == 0) {
            if (!launch_accepted || !source_flit) throw std::runtime_error("direct-link delivery lacks a launch");
        } else {
            if (arrived.empty()) throw std::runtime_error("link delivered without an arrived flit");
            arrived.pop_front();
        }
        credit_returns.push_back(cycle + config.credit_return_latency_cycles + 1);
    }
    if (launch_accepted) {
        if (!source_flit || !can_launch()) throw std::runtime_error("link launch violated ready/valid");
        credits_in_use += 1;
        next_launch_cycle = cycle + config.flit_interval_cycles;
        if (config.forward_latency_cycles > 0)
            inflight.emplace_back(cycle + config.forward_latency_cycles, *source_flit);
    }
    advance_control(cycle + 1);
}

void DirectedLink::advance_idle(long cycles) {
    if (cycles < 0) throw std::runtime_error("cannot advance a link backward");
    if (has_data()) throw std::runtime_error("cannot skip a link containing flit data");
    advance_control(cycle + cycles);
}

// ------------------------------------------------------------------ FlooRouter

FlooRouter::FlooRouter(int x_, int y_, int depth) : x(x_), y(y_), fifo_depth(depth) {
    if (depth < 2) throw std::runtime_error("RTL FIFO depth must be at least two");
    reset();
}

void FlooRouter::reset() {
    for (auto& fifo : fifos) fifo.clear();
    rr_priority.fill(0);
    request_snapshot.fill(0);
    last_handshake.fill(false);
    arbiter_locked.fill(false);
    locked_requests.fill(0);
}

int FlooRouter::route(const Flit& flit) const {
    if (flit.dest_x == x && flit.dest_y == y) return LOCAL;
    if (flit.dest_x < x) return WEST;
    if (flit.dest_x > x) return EAST;
    if (flit.dest_y < y) return NORTH;
    return SOUTH;
}

int FlooRouter::select_rr(uint8_t requests, int priority) {
    if (!requests) return -1;
    // cc_rr_arb_tree: minimize index ^ priority.
    int best = -1, best_key = 0;
    for (int index = 0; index < PORT_COUNT; ++index) {
        if (!(requests & (1 << index))) continue;
        int key = index ^ priority;
        if (best < 0 || key < best_key) {
            best = index;
            best_key = key;
        }
    }
    return best;
}

int FlooRouter::fair_next(uint8_t requests, int old_priority) {
    if (!requests) return old_priority;
    for (int index = old_priority + 1; index < PORT_COUNT; ++index)
        if (requests & (1 << index)) return index;
    for (int index = 0; index < PORT_COUNT; ++index)
        if (requests & (1 << index)) return index;
    return old_priority;
}

void FlooRouter::request_vectors(std::array<uint8_t, PORT_COUNT>& current,
                                 std::array<uint8_t, PORT_COUNT>& visible) const {
    current.fill(0);
    for (int input_port = 0; input_port < PORT_COUNT; ++input_port)
        if (!fifos[input_port].empty())
            current[route(fifos[input_port].front())] |= uint8_t(1 << AZ_TO_FLOO[input_port]);
    for (int output_port = 0; output_port < PORT_COUNT; ++output_port) {
        uint8_t snapshot_next = (request_snapshot[output_port] == 0 || last_handshake[output_port])
                                    ? current[output_port]
                                    : request_snapshot[output_port];
        visible[output_port] = arbiter_locked[output_port] ? locked_requests[output_port] : snapshot_next;
    }
}

RouterOutputs FlooRouter::outputs() const {
    std::array<uint8_t, PORT_COUNT> current, visible;
    request_vectors(current, visible);
    RouterOutputs result;
    result.selected_inputs.fill(-1);
    for (int output_port = 0; output_port < PORT_COUNT; ++output_port) {
        int floo_choice = select_rr(visible[output_port], rr_priority[output_port]);
        if (floo_choice < 0) continue;
        int choice = FLOO_TO_AZ[floo_choice];
        const auto& fifo = fifos[choice];
        if ((current[output_port] & (1 << floo_choice)) && !fifo.empty() &&
            route(fifo.front()) == output_port) {
            result.selected_inputs[output_port] = choice;
            result.output_flits[output_port] = fifo.front();
        }
    }
    for (int port = 0; port < PORT_COUNT; ++port)
        result.input_ready[port] = int(fifos[port].size()) < fifo_depth;
    return result;
}

void FlooRouter::tick(const std::array<std::optional<Flit>, PORT_COUNT>& inputs,
                      const std::array<bool, PORT_COUNT>& output_ready, const RouterOutputs& before) {
    std::array<uint8_t, PORT_COUNT> current, visible;
    request_vectors(current, visible);
    std::array<bool, PORT_COUNT> popped{};
    for (int output_port = 0; output_port < PORT_COUNT; ++output_port) {
        const auto& flit = before.output_flits[output_port];
        bool is_ready = output_ready[output_port];
        int input_port = before.selected_inputs[output_port];
        uint8_t snapshot_next = (request_snapshot[output_port] == 0 || last_handshake[output_port])
                                    ? current[output_port]
                                    : request_snapshot[output_port];
        uint8_t requests = visible[output_port];
        bool request_valid = requests != 0;
        bool last_transfer = flit.has_value() && is_ready && input_port >= 0 && flit->last;
        if (last_transfer) rr_priority[output_port] = fair_next(requests, rr_priority[output_port]);
        request_snapshot[output_port] = snapshot_next;
        last_handshake[output_port] = last_transfer;
        arbiter_locked[output_port] = request_valid && !last_transfer;
        locked_requests[output_port] = requests;
        if (flit.has_value() && is_ready && input_port >= 0) popped[input_port] = true;
    }
    for (int input_port = 0; input_port < PORT_COUNT; ++input_port) {
        bool push = inputs[input_port].has_value() && before.input_ready[input_port];
        if (popped[input_port]) fifos[input_port].pop_front();
        if (push) fifos[input_port].push_back(*inputs[input_port]);
    }
}

// ------------------------------------------------------------------------ Mesh

Mesh::Mesh(int width_, int height_, int fifo_depth, const InterconnectConfig& interconnect_)
    : width(width_), height(height_), interconnect(interconnect_) {
    if (width <= 0 || height <= 0) throw std::runtime_error("mesh dimensions must be positive");
    for (int y = 0; y < height; ++y)
        for (int x = 0; x < width; ++x) routers.emplace_back(x, y, fifo_depth);
    links.resize(routers.size());
    for (size_t node = 0; node < routers.size(); ++node)
        for (const auto& n : neighbors(routers[node]))
            if (in_mesh(n.nx, n.ny)) {
                links[node][n.out_port].exists = true;
                links[node][n.out_port].link.config = interconnect;
            }
}

std::array<Neighbor, 4> Mesh::neighbors(const FlooRouter& router) {
    return {{{NORTH, router.x, router.y - 1, SOUTH},
             {SOUTH, router.x, router.y + 1, NORTH},
             {EAST, router.x + 1, router.y, WEST},
             {WEST, router.x - 1, router.y, EAST}}};
}

std::vector<RouterOutputs> Mesh::all_outputs() const {
    std::vector<RouterOutputs> comb;
    comb.reserve(routers.size());
    for (const auto& router : routers) comb.push_back(router.outputs());
    return comb;
}

bool Mesh::link_ready(int node, int output_port, const std::vector<RouterOutputs>& comb) const {
    const auto& slot = links[node][output_port];
    if (!slot.exists || !slot.link.can_launch()) return false;
    if (interconnect.forward_latency_cycles > 0) return true;
    for (const auto& n : neighbors(routers[node]))
        if (n.out_port == output_port) return comb[index(n.nx, n.ny)].input_ready[n.neighbor_in];
    return false;
}

bool Mesh::has_link_data() const {
    for (const auto& row : links)
        for (const auto& slot : row)
            if (slot.exists && slot.link.has_data()) return true;
    return false;
}

bool Mesh::has_data() const {
    if (has_link_data()) return true;
    for (const auto& router : routers)
        for (const auto& fifo : router.fifos)
            if (!fifo.empty()) return true;
    return false;
}

void Mesh::advance_idle(long cycles) {
    if (has_data()) throw std::runtime_error("cannot skip a non-empty mesh");
    for (auto& row : links)
        for (auto& slot : row)
            if (slot.exists) slot.link.advance_idle(cycles);
    cycle += cycles;
}

void Mesh::reset() {
    for (auto& router : routers) router.reset();
    for (auto& row : links)
        for (auto& slot : row)
            if (slot.exists) slot.link.reset();
    cycle = 0;
}

void Mesh::tick(const std::vector<std::optional<Flit>>& local_injections, const std::vector<bool>& local_ready,
                const std::vector<RouterOutputs>& comb) {
    const size_t count = routers.size();
    std::vector<std::array<std::optional<Flit>, PORT_COUNT>> inputs(count);
    std::vector<std::array<bool, PORT_COUNT>> output_ready(count);
    for (size_t node = 0; node < count; ++node) {
        output_ready[node].fill(false);
        inputs[node][LOCAL] = local_injections[node];
        output_ready[node][LOCAL] = local_ready[node];
        for (const auto& n : neighbors(routers[node])) {
            if (!in_mesh(n.nx, n.ny)) {
                output_ready[node][n.out_port] = false;
                continue;
            }
            int neighbor = index(n.nx, n.ny);
            const auto& link = links[node][n.out_port].link;
            if (interconnect.forward_latency_cycles == 0) {
                if (link.can_launch()) inputs[neighbor][n.neighbor_in] = comb[node].output_flits[n.out_port];
                output_ready[node][n.out_port] = link.can_launch() && comb[neighbor].input_ready[n.neighbor_in];
            } else {
                inputs[neighbor][n.neighbor_in] = link.output();
                output_ready[node][n.out_port] = link.can_launch();
            }
        }
    }
    for (size_t node = 0; node < count; ++node) routers[node].tick(inputs[node], output_ready[node], comb[node]);
    for (size_t node = 0; node < count; ++node) {
        for (const auto& n : neighbors(routers[node])) {
            if (!in_mesh(n.nx, n.ny)) continue;
            int neighbor = index(n.nx, n.ny);
            auto& link = links[node][n.out_port].link;
            const auto& source_flit = comb[node].output_flits[n.out_port];
            bool launch_accepted = source_flit.has_value() && output_ready[node][n.out_port];
            bool delivery_accepted = interconnect.forward_latency_cycles == 0
                                         ? launch_accepted
                                         : (link.output().has_value() && comb[neighbor].input_ready[n.neighbor_in]);
            link.tick(source_flit, launch_accepted, delivery_accepted);
        }
    }
    cycle += 1;
}

}  // namespace azilla
