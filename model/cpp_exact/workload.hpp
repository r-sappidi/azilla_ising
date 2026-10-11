// Port of model/azilla_cycle_model/workload.py (dataset, geometry, schedule).
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace azilla {

struct Geometry {
    int mesh_x = 1;
    int mesh_y = 1;
    int h0_per_h1 = 1;
    int cores_per_h0 = 1;

    int node_count() const { return mesh_x * mesh_y; }
    int blocks_per_h1() const { return h0_per_h1 * cores_per_h0; }
    int total_blocks() const { return node_count() * blocks_per_h1(); }
    long spin_count() const { return long(total_blocks()) * 32; }
    bool operator==(const Geometry& other) const {
        return mesh_x == other.mesh_x && mesh_y == other.mesh_y &&
               h0_per_h1 == other.h0_per_h1 && cores_per_h0 == other.cores_per_h0;
    }
};

struct ScheduledBlock {
    int block_a;
    int block_b;
    int owner = -1;
};

using BlockPair = std::pair<int, int>;

// BlockOccupancyDataset: unordered nonzero off-diagonal 32x32 block occupancy.
struct BlockOccupancyDataset {
    long spin_count = 0;
    long known_cut = 0;
    long edge_records = 0;                // edge lines read from the file (any weight)
    std::vector<BlockPair> active_pairs;  // sorted ascending, unique

    static BlockOccupancyDataset load(const std::string& path);
    // Mirror of the Python campaigns' implicit dense occupancy
    // ``BlockOccupancyDataset(n, 0, {(a, b) for a in range(n//32) for b in range(a+1, n//32)})``:
    // every unordered off-diagonal block pair is occupied; the file supplies only
    // the header.  Refuses files that carry edge records (fail closed).
    static BlockOccupancyDataset dense_all_blocks(const std::string& path);
};

std::vector<ScheduledBlock> load_schedule(const std::string& path);
void validate_schedule(const BlockOccupancyDataset& dataset, const Geometry& geometry,
                       const std::vector<ScheduledBlock>& schedule);

}  // namespace azilla
