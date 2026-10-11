#include "workload.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_set>

namespace azilla {

namespace {

// Mirror of Python ``line.split()`` followed by ``map(int, fields)``.
bool split_ints(const std::string& line, std::vector<long long>& out) {
    out.clear();
    const char* p = line.c_str();
    while (*p) {
        while (*p && std::isspace(static_cast<unsigned char>(*p))) ++p;
        if (!*p) break;
        const char* start = p;
        while (*p && !std::isspace(static_cast<unsigned char>(*p))) ++p;
        std::string field(start, p);
        char* end = nullptr;
        errno = 0;
        long long value = std::strtoll(field.c_str(), &end, 10);
        if (end == field.c_str() || *end != '\0' || errno != 0) return false;
        out.push_back(value);
    }
    return true;
}

}  // namespace

BlockOccupancyDataset BlockOccupancyDataset::load(const std::string& path) {
    std::ifstream source(path);
    if (!source) throw std::runtime_error("cannot open dataset " + path);
    BlockOccupancyDataset dataset;
    std::string line;
    std::vector<long long> fields;
    if (!std::getline(source, line)) throw std::runtime_error("invalid dataset header in " + path);
    if (!split_ints(line, fields) || fields.size() != 2)
        throw std::runtime_error("invalid dataset header in " + path);
    dataset.spin_count = fields[0];
    dataset.known_cut = fields[1];
    const long spin_count = dataset.spin_count;
    const long blocks = (spin_count + 31) / 32;
    std::unordered_set<uint64_t> active;
    long line_number = 1;
    while (std::getline(source, line)) {
        ++line_number;
        if (!split_ints(line, fields))
            throw std::runtime_error("invalid record at " + path + ":" + std::to_string(line_number));
        if (fields.empty()) continue;
        if (fields.size() != 3)
            throw std::runtime_error("invalid record at " + path + ":" + std::to_string(line_number));
        long row = fields[0] - 1;
        long column = fields[1] - 1;
        long long value = fields[2];
        if (!(0 <= row && row < spin_count && 0 <= column && column < spin_count))
            throw std::runtime_error("vertex outside range at " + path + ":" + std::to_string(line_number));
        if (!(-128 <= value && value <= 127))
            throw std::runtime_error("weight outside int8 at " + path + ":" + std::to_string(line_number));
        long block_a = row / 32, block_b = column / 32;
        ++dataset.edge_records;
        if (value != 0 && block_a != block_b) {
            long lo = std::min(block_a, block_b), hi = std::max(block_a, block_b);
            active.insert(uint64_t(lo) * uint64_t(blocks) + uint64_t(hi));
        }
    }
    dataset.active_pairs.reserve(active.size());
    for (uint64_t key : active)
        dataset.active_pairs.emplace_back(int(key / uint64_t(blocks)), int(key % uint64_t(blocks)));
    std::sort(dataset.active_pairs.begin(), dataset.active_pairs.end());
    return dataset;
}

BlockOccupancyDataset BlockOccupancyDataset::dense_all_blocks(const std::string& path) {
    BlockOccupancyDataset dataset = load(path);
    if (dataset.edge_records != 0)
        throw std::runtime_error("--dense-all-blocks requires a header-only dataset; " + path + " carries " +
                                 std::to_string(dataset.edge_records) + " edge records");
    if (dataset.spin_count <= 0 || dataset.spin_count % 32 != 0)
        throw std::runtime_error("--dense-all-blocks requires a positive multiple of 32 spins; got " +
                                 std::to_string(dataset.spin_count));
    const long blocks = dataset.spin_count / 32;
    dataset.active_pairs.clear();
    dataset.active_pairs.reserve(size_t(blocks) * size_t(blocks - 1) / 2);
    for (long a = 0; a < blocks; ++a)
        for (long b = a + 1; b < blocks; ++b) dataset.active_pairs.emplace_back(int(a), int(b));
    // Already sorted ascending and unique: identical to sorting Python's set.
    return dataset;
}

std::vector<ScheduledBlock> load_schedule(const std::string& path) {
    std::ifstream source(path);
    if (!source) throw std::runtime_error("cannot open schedule " + path);
    std::vector<ScheduledBlock> schedule;
    std::string line;
    long line_number = 0;
    while (std::getline(source, line)) {
        ++line_number;
        std::istringstream stream(line);
        std::vector<std::string> fields;
        std::string field;
        while (stream >> field) fields.push_back(field);
        if (fields.empty() || fields[0][0] == '#') continue;
        if (fields.size() != 3)
            throw std::runtime_error("invalid schedule at " + path + ":" + std::to_string(line_number));
        schedule.push_back({std::stoi(fields[0]), std::stoi(fields[1]), std::stoi(fields[2])});
    }
    return schedule;
}

void validate_schedule(const BlockOccupancyDataset& dataset, const Geometry& geometry,
                       const std::vector<ScheduledBlock>& schedule) {
    if (dataset.spin_count != geometry.spin_count())
        throw std::runtime_error("dataset has " + std::to_string(dataset.spin_count) +
                                 " spins; geometry has " + std::to_string(geometry.spin_count()));
    std::set<BlockPair> expected(dataset.active_pairs.begin(), dataset.active_pairs.end());
    std::set<BlockPair> observed;
    for (size_t index = 0; index < schedule.size(); ++index) {
        const auto& item = schedule[index];
        if (!(0 <= item.block_a && item.block_a < item.block_b && item.block_b < geometry.total_blocks()))
            throw std::runtime_error("invalid block pair at schedule record " + std::to_string(index + 1));
        BlockPair pair{item.block_a, item.block_b};
        if (observed.count(pair)) throw std::runtime_error("duplicate block pair");
        observed.insert(pair);
        int h1_a = item.block_a / geometry.blocks_per_h1();
        int h1_b = item.block_b / geometry.blocks_per_h1();
        if (h1_a != h1_b && !(0 <= item.owner && item.owner < geometry.node_count()))
            throw std::runtime_error("cross-H1 pair has invalid owner");
    }
    size_t missing = 0, extra = 0;
    for (const auto& pair : expected) if (!observed.count(pair)) ++missing;
    for (const auto& pair : observed) if (!expected.count(pair)) ++extra;
    if (missing || extra)
        throw std::runtime_error("schedule coverage mismatch: missing=" + std::to_string(missing) +
                                 " extra=" + std::to_string(extra));
}

}  // namespace azilla
