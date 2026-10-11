// Port of model/azilla_cycle_model/core_state_bank.py (CoreStateBank):
// eight interleaved synchronous banks, fixed lane priority within a bank,
// one read per bank per edge, one-cycle tagged responses, read-before-write.
// State words are zero in timing-only runs, so only presence is tracked.
#pragma once

#include <map>
#include <stdexcept>
#include <vector>

namespace azilla {

class CoreStateBank {
public:
    CoreStateBank(int entries, int lanes, int banks = 8)
        : entries_(entries), banks_(banks), lanes_(lanes), present_(entries, false) {
        if (entries <= 0 || lanes <= 0 || banks <= 0 || (banks & (banks - 1)))
            throw std::runtime_error("positive entries/lanes and power-of-two banks required");
    }

    // requests: lane -> index (std::map iterates in lane order == Python sorted()).
    std::map<int, bool> ready(const std::map<int, int>& requests) const;
    // Registers this edge's accepted reads; the caller snapshots ``responses``
    // before calling (Python returns the pre-edge responses).
    void tick(const std::map<int, int>& requests, bool write, int write_index);

    std::map<int, int> responses;  // lane -> index read (data is always zero)
    long accepted_reads = 0;
    long stalled_reads = 0;
    long writes = 0;

private:
    int entries_, banks_, lanes_;
    std::vector<bool> present_;
};

}  // namespace azilla
