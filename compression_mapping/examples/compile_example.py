#!/usr/bin/env python3
"""Compile a small synthetic 4K-spin graph and write an Azilla schedule."""

import numpy as np
import torch

from azilla_integration import CompileConfig, compile_mapping, write_schedule


rng = np.random.default_rng(7)
vertices = 4096
edge_count = 20_000
src = rng.integers(0, vertices, edge_count, dtype=np.int64)
dst = rng.integers(0, vertices, edge_count, dtype=np.int64)
keep = src != dst
src, dst = np.minimum(src[keep], dst[keep]), np.maximum(src[keep], dst[keep])
weights = rng.integers(-8, 9, len(src)).astype(np.float32)
weights[weights == 0] = 1

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
mapping = compile_mapping(
    torch.from_numpy(src).to(device),
    torch.from_numpy(dst).to(device),
    torch.from_numpy(weights).to(device),
    vertices,
    CompileConfig(mesh_x=4, mesh_y=4, blocks_per_node=8),
)
write_schedule("mapping.schedule", mapping)
np.save("permutation.npy", mapping.permutation)
print(
    f"wrote {len(mapping.block_pairs)} blocks; "
    f"owner policy={mapping.owner_policy}; "
    f"dropped weight={100*mapping.dropped_weight_fraction:.3f}%"
)
