"""Hierarchical graph partitioning, 32x32 tiling, and mesh scheduling."""

from .tile_compressor import (
    SparseBlockGraph,
    TiledGraph,
    extract_upper_tiles_from_edges,
    extract_block_coords_from_edges,
    hierarchical_vertex_permutation,
    partition_vertices,
    permutation_from_partitions,
    prune_low_weight_blocks,
    reorder_and_extract_block_coords_from_edges,
    reorder_and_tile_upper_adjacency,
)
from .tile_scheduler import TileSchedule, schedule_tiles_on_mesh

__all__ = [
    "SparseBlockGraph",
    "TileSchedule",
    "TiledGraph",
    "extract_upper_tiles_from_edges",
    "extract_block_coords_from_edges",
    "hierarchical_vertex_permutation",
    "partition_vertices",
    "permutation_from_partitions",
    "prune_low_weight_blocks",
    "reorder_and_extract_block_coords_from_edges",
    "reorder_and_tile_upper_adjacency",
    "schedule_tiles_on_mesh",
]
