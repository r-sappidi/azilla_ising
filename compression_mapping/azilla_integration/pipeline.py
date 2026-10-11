"""End-to-end graph compression and mapping pipeline for Azilla."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import torch

from graph_mapping.graph_compression import (
    extract_block_coords_from_edges,
    partition_vertices,
    permutation_from_partitions,
    prune_low_weight_blocks,
)


@dataclass(frozen=True)
class CompileConfig:
    mesh_x: int = 4
    mesh_y: int = 4
    tile_size: int = 32
    blocks_per_node: int = 32
    lossy_weight_budget: float = 0.01
    macro_iterations: int = 8
    micro_iterations: int = 6
    candidate_swaps: int = 64
    seed: int = 1

    @property
    def node_count(self) -> int:
        return self.mesh_x * self.mesh_y


@dataclass(frozen=True)
class CompiledMapping:
    permutation: np.ndarray
    inverse_permutation: np.ndarray
    partition_labels: np.ndarray
    block_pairs: np.ndarray
    owners: np.ndarray
    owner_policy: str
    dropped_weight_fraction: float
    dropped_blocks: int


OwnerObjective = Callable[[np.ndarray, np.ndarray, CompileConfig], float]


def _distance(config: CompileConfig, a: int, b: int) -> int:
    return abs(a % config.mesh_x - b % config.mesh_x) + abs(
        a // config.mesh_x - b // config.mesh_x
    )


def endpoint_owners(pairs: np.ndarray, config: CompileConfig) -> np.ndarray:
    owners = np.full(len(pairs), -1, dtype=np.int32)
    for i, (a, b) in enumerate(pairs):
        node_a = int(a) // config.blocks_per_node
        node_b = int(b) // config.blocks_per_node
        if node_a != node_b:
            owners[i] = node_a
    return owners


def balanced_owners(pairs: np.ndarray, config: CompileConfig) -> np.ndarray:
    """Greedily balance cross-node blocks while favoring short routes."""
    owners = np.full(len(pairs), -1, dtype=np.int32)
    cross = [
        i for i, (a, b) in enumerate(pairs)
        if int(a) // config.blocks_per_node != int(b) // config.blocks_per_node
    ]
    if not cross:
        return owners
    base, extra = divmod(len(cross), config.node_count)
    capacity = [base + (node < extra) for node in range(config.node_count)]
    load = [0] * config.node_count
    for i in sorted(cross, key=lambda j: (int(pairs[j, 0]), int(pairs[j, 1]))):
        a, b = map(int, pairs[i])
        node_a, node_b = a // config.blocks_per_node, b // config.blocks_per_node
        candidates = [n for n in range(config.node_count) if load[n] < capacity[n]]
        owner = min(
            candidates,
            key=lambda n: (
                _distance(config, n, node_a) + _distance(config, n, node_b),
                load[n], n,
            ),
        )
        owners[i] = owner
        load[owner] += 1
    return owners


def traffic_objective(
    pairs: np.ndarray, owners: np.ndarray, config: CompileConfig
) -> float:
    """Minimize endpoint-to-owner link traversals; lower is better."""
    total = 0
    for (a, b), owner in zip(pairs, owners):
        if owner < 0:
            continue
        node_a = int(a) // config.blocks_per_node
        node_b = int(b) // config.blocks_per_node
        total += _distance(config, int(owner), node_a)
        total += _distance(config, int(owner), node_b)
    return float(total)


def compile_mapping(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    num_vertices: int,
    config: CompileConfig = CompileConfig(),
    *,
    owner_objective: OwnerObjective = traffic_objective,
) -> CompiledMapping:
    """Compile an upper-triangular edge list into an Azilla block schedule.

    ``owner_objective`` scores a complete owner array. The endpoint-local
    schedule is always a candidate, so final scheduling cannot regress under
    that objective. Ties remain endpoint-local.
    """
    if num_vertices != config.node_count * config.blocks_per_node * config.tile_size:
        raise ValueError(
            "num_vertices must equal mesh nodes * blocks_per_node * tile_size"
        )
    if not (src.device == dst.device == weight.device):
        raise ValueError("src, dst, and weight must be on the same device")
    score_weight = weight.float().abs()
    labels = partition_vertices(
        src, dst, score_weight, num_vertices, config.node_count,
        iterations=config.macro_iterations,
        candidate_swaps=config.candidate_swaps,
        seed=config.seed,
    )
    permutation = permutation_from_partitions(
        src, dst, score_weight, labels, num_vertices, config.node_count,
        config.tile_size, refine_tiles=True,
        micro_iterations=config.micro_iterations,
        candidate_swaps=config.candidate_swaps,
        seed=config.seed,
    )
    coords, inverse = extract_block_coords_from_edges(
        src, dst, permutation, config.tile_size, num_vertices
    )
    coords, dropped_fraction, dropped_blocks = prune_low_weight_blocks(
        src, dst, weight, inverse, coords, config.tile_size, num_vertices,
        config.lossy_weight_budget,
    )
    pairs = coords[coords[:, 0] != coords[:, 1]].detach().cpu().numpy()
    local = endpoint_owners(pairs, config)
    balanced = balanced_owners(pairs, config)
    local_score = owner_objective(pairs, local, config)
    balanced_score = owner_objective(pairs, balanced, config)
    if balanced_score < local_score:
        owners, policy = balanced, "balanced"
    else:
        owners, policy = local, "endpoint-local"
    return CompiledMapping(
        permutation.detach().cpu().numpy(),
        inverse.detach().cpu().numpy(),
        labels.detach().cpu().numpy(),
        pairs, owners, policy, dropped_fraction, dropped_blocks,
    )


def write_schedule(path: str | Path, mapping: CompiledMapping) -> None:
    """Write the schedule consumed by Azilla workload/replay scripts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as output:
        output.write("# block_a block_b owner\n")
        for (a, b), owner in zip(mapping.block_pairs, mapping.owners):
            output.write(f"{int(a)} {int(b)} {int(owner)}\n")
