// Port of model/azilla_cycle_model/noc.py: one-VC FlooNoC router mesh.
#pragma once

#include <array>
#include <cstdint>
#include <deque>
#include <optional>
#include <stdexcept>
#include <vector>

namespace azilla {

// Azilla wrapper port order.
enum Port : int { LOCAL = 0, NORTH = 1, SOUTH = 2, EAST = 3, WEST = 4 };
constexpr int PORT_COUNT = 5;
constexpr int AZ_TO_FLOO[PORT_COUNT] = {4, 2, 0, 1, 3};
constexpr int FLOO_TO_AZ[PORT_COUNT] = {2, 3, 1, 4, 0};

constexpr int NOC_STATE = 0;
constexpr int NOC_PARTIAL = 1;
constexpr int NOC_EPOCH_DONE = 2;

struct Flit {
    int packet_type = 0;
    int dest_x = 0;
    int dest_y = 0;
    int source_id = 0;
    int epoch = 0;
    int block_id = 0;
    bool last = true;
    // ``data`` is always zero in timing-only runs and is omitted.
};

struct InterconnectConfig {
    int forward_latency_cycles = 0;
    int flit_interval_cycles = 1;
    int max_inflight_flits = 4;
    int credit_return_latency_cycles = 0;

    void validate() const {
        if (forward_latency_cycles < 0) throw std::runtime_error("link forward latency cannot be negative");
        if (flit_interval_cycles <= 0) throw std::runtime_error("link flit interval must be positive");
        if (max_inflight_flits <= 0) throw std::runtime_error("link credit capacity must be positive");
        if (credit_return_latency_cycles < 0)
            throw std::runtime_error("link credit-return latency cannot be negative");
    }
    bool is_rtl_direct() const {
        return forward_latency_cycles == 0 && flit_interval_cycles == 1 && credit_return_latency_cycles == 0;
    }
};

struct DirectedLink {
    InterconnectConfig config;
    long cycle = 0;
    long next_launch_cycle = 0;
    int credits_in_use = 0;
    std::deque<std::pair<long, Flit>> inflight;
    std::deque<Flit> arrived;
    std::deque<long> credit_returns;

    bool can_launch() const {
        return cycle >= next_launch_cycle && credits_in_use < config.max_inflight_flits;
    }
    std::optional<Flit> output() const {
        if (arrived.empty()) return std::nullopt;
        return arrived.front();
    }
    bool has_data() const { return !inflight.empty() || !arrived.empty(); }
    void reset();
    void advance_control(long new_cycle);
    void tick(const std::optional<Flit>& source_flit, bool launch_accepted, bool delivery_accepted);
    void advance_idle(long cycles);
};

struct RouterOutputs {
    std::array<bool, PORT_COUNT> input_ready{};
    std::array<std::optional<Flit>, PORT_COUNT> output_flits{};
    std::array<int, PORT_COUNT> selected_inputs{};  // -1 = none
};

class FlooRouter {
public:
    int x, y;
    int fifo_depth;
    std::array<std::deque<Flit>, PORT_COUNT> fifos;
    std::array<int, PORT_COUNT> rr_priority{};
    std::array<uint8_t, PORT_COUNT> request_snapshot{};
    std::array<bool, PORT_COUNT> last_handshake{};
    std::array<bool, PORT_COUNT> arbiter_locked{};
    std::array<uint8_t, PORT_COUNT> locked_requests{};

    FlooRouter(int x_, int y_, int depth);
    void reset();
    int route(const Flit& flit) const;
    RouterOutputs outputs() const;
    // ``before`` must be this router's outputs() for the current state.
    void tick(const std::array<std::optional<Flit>, PORT_COUNT>& inputs,
              const std::array<bool, PORT_COUNT>& output_ready, const RouterOutputs& before);

private:
    static int select_rr(uint8_t requests, int priority);
    static int fair_next(uint8_t requests, int old_priority);
    void request_vectors(std::array<uint8_t, PORT_COUNT>& current,
                         std::array<uint8_t, PORT_COUNT>& visible) const;
};

struct Neighbor {
    int out_port;
    int nx, ny;
    int neighbor_in;
};

class Mesh {
public:
    int width, height;
    InterconnectConfig interconnect;
    std::vector<FlooRouter> routers;
    // links[node][out_port]; ``exists`` false where the mesh has no neighbor.
    struct LinkSlot {
        bool exists = false;
        DirectedLink link;
    };
    std::vector<std::array<LinkSlot, PORT_COUNT>> links;
    long cycle = 0;

    Mesh(int width_, int height_, int fifo_depth, const InterconnectConfig& interconnect_);
    int index(int x, int y) const { return y * width + x; }
    bool in_mesh(int x, int y) const { return 0 <= x && x < width && 0 <= y && y < height; }
    static std::array<Neighbor, 4> neighbors(const FlooRouter& router);
    std::vector<RouterOutputs> all_outputs() const;
    bool link_ready(int node, int output_port, const std::vector<RouterOutputs>& comb) const;
    bool has_link_data() const;
    bool has_data() const;
    void advance_idle(long cycles);
    void reset();
    // ``comb`` must equal all_outputs() for the current state.
    void tick(const std::vector<std::optional<Flit>>& local_injections, const std::vector<bool>& local_ready,
              const std::vector<RouterOutputs>& comb);
};

}  // namespace azilla
