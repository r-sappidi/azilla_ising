#include "core_state_bank.hpp"

#include <string>

namespace azilla {

std::map<int, bool> CoreStateBank::ready(const std::map<int, int>& requests) const {
    std::map<int, bool> selected;
    unsigned used = 0;
    for (const auto& [lane, index] : requests) {
        if (!(0 <= lane && lane < lanes_) || !(0 <= index && index < entries_))
            throw std::runtime_error("operand SRAM lane/index out of range");
        int bank = index % banks_;
        selected[lane] = !(used & (1u << bank));
        used |= 1u << bank;
    }
    return selected;
}

void CoreStateBank::tick(const std::map<int, int>& requests, bool write, int write_index) {
    responses.clear();
    std::map<int, bool> ready_map = ready(requests);
    for (const auto& [lane, index] : requests) {
        if (!ready_map[lane]) {
            stalled_reads += 1;
            continue;
        }
        if (!present_[index])
            throw std::runtime_error("operand " + std::to_string(index) + " read before frozen-state publication");
        responses[lane] = index;
        accepted_reads += 1;
    }
    if (write) {
        if (!(0 <= write_index && write_index < entries_)) throw std::runtime_error("operand SRAM write index out of range");
        present_[write_index] = true;
        writes += 1;
    }
}

}  // namespace azilla
