// Port of model/azilla_cycle_model/adapters.py: H0Adapter, H1ChildAdapter.
#pragma once

#include <vector>

#include "hierarchy.hpp"

namespace azilla {

struct RoutedPartial {
    bool valid = false;
    int block_id = 0;
};

struct H0AdapterOutputs {
    std::vector<bool> engine_ready;
    // Only cores that may select an engine are materialized; every other
    // core's RoutedPartial is invalid and has no effect on tick().
    std::vector<std::pair<int, RoutedPartial>> core_partials;
};

// Fixed-priority, packet-locking mirror of rtl/h0_adapter.sv.  The Python
// model scans every core each cycle; this port visits only the cores that
// are locked or whose block id matches a valid partial, which is the exact
// set of cores for which the Python ``selected`` is not None.
class H0Adapter {
public:
    H0Adapter(int core_count, int mvm_count, int base_block_id);
    void reset();
    H0AdapterOutputs outputs(const std::vector<PartialOutput>& partials) const;
    void tick(const H0AdapterOutputs& before, const std::vector<PartialOutput>& partials);

    int core_count;
    int mvm_count;
    int base_block_id;
    std::vector<int> locked;  // -1 = None
};

struct H1ChildOutputs {
    std::vector<bool> node_ready;
    bool parent_ready = false;
    std::vector<RoutedPartial> children;
};

class H1ChildAdapter {
public:
    H1ChildAdapter(int child_count, int blocks_per_child, int mvm_count, int base_block_id);
    void reset();
    bool belongs(int child, int block) const;
    int select(int child, const std::vector<PartialOutput>& partials, const RoutedPartial& parent) const;
    H1ChildOutputs outputs(const std::vector<PartialOutput>& partials, const RoutedPartial& parent) const;
    void tick(const H1ChildOutputs& before, const std::vector<PartialOutput>& partials, const RoutedPartial& parent);

    int child_count;
    int blocks_per_child;
    int mvm_count;
    int base_block_id;
    std::vector<int> locked_source;  // -1 = None
    int parent_beat = 0;
};

}  // namespace azilla
