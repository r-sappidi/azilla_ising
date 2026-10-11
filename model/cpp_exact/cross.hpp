// Port of model/azilla_cycle_model/cross.py: CrossH1Node, LocalInjectionArbiter.
#pragma once

#include <optional>

#include "hierarchy.hpp"
#include "noc.hpp"

namespace azilla {

struct CrossOutputs {
    bool iter_done = false;
    std::optional<Flit> tx;
    int selected_engine = -1;  // _selected_engine() for the same state
};

class CrossH1Node {
public:
    CrossH1Node(int state_entry_count, int mvm_count, int blocks_per_h1, int mesh_x_count, int node_id);
    void reset();
    int selected_engine(const HierarchyOutputs& node_outputs) const;
    CrossOutputs outputs(const HierarchyOutputs& node_outputs, int epoch) const;
    // ``node_before`` / ``before`` must be computed for the current state.
    void tick(const HierarchyOutputs& node_before, const CrossOutputs& before, bool schedule_done,
              const std::vector<std::optional<DmaCommand>>& commands, const std::vector<bool>& weight_valid,
              bool tx_ready);

    HierarchyNode node;
    int blocks_per_h1;
    int mesh_x_count;
    int node_id;
    bool output_locked = false;
    int locked_engine = 0;
    int round_robin_start = 0;
};

struct InjectionOutputs {
    std::optional<Flit> selected;
    bool cross_ready = false;
    bool h1_ready = false;
};

class LocalInjectionArbiter {
public:
    void reset() { locked = false; cross_selected = false; }
    InjectionOutputs outputs(const std::optional<Flit>& cross, const std::optional<Flit>& h1,
                             bool router_ready) const;
    void tick(const InjectionOutputs& before, bool router_ready);

    bool locked = false;
    bool cross_selected = false;
};

}  // namespace azilla
