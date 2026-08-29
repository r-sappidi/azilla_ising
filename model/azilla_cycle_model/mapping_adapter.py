"""Bridge the sparse graph mapper to Azilla schedules and timing artifacts.

The graph mapper owns vertex permutation and occupied 32x32 block discovery.
This adapter preserves the hardware's fixed hierarchy: contiguous macro
partitions map one-to-one to H1 nodes, while each block's H0 and spin-core
placement follows its block index. Only cross-H1 work ownership is selected.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from .scheduler import mesh_distance
from .workload import BlockOccupancyDataset, Geometry, ScheduledBlock


ARTIFACT_SCHEMA = "azilla-sparse-mapping-v1"
RTL_TILE_SIZE = 32
OwnerAssigner = Callable[[Geometry, np.ndarray], np.ndarray]


def _geometry_dict(geometry: Geometry) -> dict[str, int]:
    return {
        "mesh_x": geometry.mesh_x,
        "mesh_y": geometry.mesh_y,
        "h0_per_h1": geometry.h0_per_h1,
        "cores_per_h0": geometry.cores_per_h0,
    }


def _central_capacity_order(geometry: Geometry) -> list[int]:
    return sorted(
        range(geometry.node_count),
        key=lambda node: (
            sum(mesh_distance(geometry, node, other)
                for other in range(geometry.node_count)),
            node,
        ),
    )


def assign_cross_owners(
    geometry: Geometry,
    block_pairs: np.ndarray | Iterable[tuple[int, int]],
) -> np.ndarray:
    """Assign cross-H1 blocks with strict balance, locality, and reuse.

    Local H0/H1 records receive owner -1 because their destination is fixed by
    the RTL hierarchy. Cross-H1 records are balanced across all mesh nodes;
    capacities differ by at most one. Within that constraint, the greedy key
    favors reuse of blocks already published to an owner, then short endpoint
    distance.
    """
    pairs = np.asarray(
        block_pairs if isinstance(block_pairs, np.ndarray)
        else list(block_pairs),
        dtype=np.int64,
    )
    if pairs.size == 0:
        return np.empty(0, dtype=np.int32)
    if pairs.ndim != 2 or pairs.shape[1] != 2:
        raise ValueError("block_pairs must have shape [count, 2]")

    owners = np.full(pairs.shape[0], -1, dtype=np.int32)
    blocks_per_h1 = geometry.blocks_per_h1
    cross_indices = [
        index for index, (block_a, block_b) in enumerate(pairs.tolist())
        if block_a // blocks_per_h1 != block_b // blocks_per_h1
    ]
    if not cross_indices:
        return owners

    base, extra = divmod(len(cross_indices), geometry.node_count)
    capacity = [base] * geometry.node_count
    for node in _central_capacity_order(geometry)[:extra]:
        capacity[node] += 1
    load = [0] * geometry.node_count
    resident: list[Counter[int]] = [
        Counter() for _ in range(geometry.node_count)
    ]

    endpoint_frequency: Counter[int] = Counter()
    for index in cross_indices:
        block_a, block_b = map(int, pairs[index])
        endpoint_frequency[block_a] += 1
        endpoint_frequency[block_b] += 1

    processing_order = sorted(
        cross_indices,
        key=lambda index: (
            -endpoint_frequency[int(pairs[index, 0])],
            -endpoint_frequency[int(pairs[index, 1])],
            -mesh_distance(
                geometry,
                int(pairs[index, 0]) // blocks_per_h1,
                int(pairs[index, 1]) // blocks_per_h1,
            ),
            int(pairs[index, 0]),
            int(pairs[index, 1]),
        ),
    )

    for index in processing_order:
        block_a, block_b = map(int, pairs[index])
        h1_a = block_a // blocks_per_h1
        h1_b = block_b // blocks_per_h1
        candidates = [
            node for node in range(geometry.node_count)
            if load[node] < capacity[node]
        ]
        if not candidates:
            raise RuntimeError("internal error: no cross-owner capacity")

        def key(owner: int) -> tuple[int, int, int, Fraction, int]:
            reuse = resident[owner][block_a] + resident[owner][block_b]
            distance_a = mesh_distance(geometry, owner, h1_a)
            distance_b = mesh_distance(geometry, owner, h1_b)
            return (
                -reuse,
                max(distance_a, distance_b),
                distance_a + distance_b,
                Fraction(load[owner], capacity[owner]),
                owner,
            )

        owner = min(candidates, key=key)
        owners[index] = owner
        load[owner] += 1
        resident[owner][block_a] += 1
        resident[owner][block_b] += 1

    return owners


@dataclass(frozen=True, slots=True)
class MappingArtifact:
    """Portable mapper output consumed directly by performance models."""

    geometry: Geometry
    permutation: np.ndarray
    inverse_permutation: np.ndarray
    block_pairs: np.ndarray
    owners: np.ndarray
    known_cut: int = 0
    tile_size: int = RTL_TILE_SIZE
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        permutation = np.asarray(self.permutation, dtype=np.int64)
        inverse = np.asarray(self.inverse_permutation, dtype=np.int64)
        pairs = np.asarray(self.block_pairs, dtype=np.int64)
        owners = np.asarray(self.owners, dtype=np.int32)
        object.__setattr__(self, "permutation", permutation)
        object.__setattr__(self, "inverse_permutation", inverse)
        object.__setattr__(self, "block_pairs", pairs)
        object.__setattr__(self, "owners", owners)

        if self.tile_size != RTL_TILE_SIZE:
            raise ValueError(
                f"Azilla RTL requires {RTL_TILE_SIZE}-spin blocks"
            )
        if any(value <= 0 for value in (
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.geometry.h0_per_h1, self.geometry.cores_per_h0,
        )):
            raise ValueError("all geometry dimensions must be positive")
        if permutation.shape != (self.geometry.spin_count,):
            raise ValueError("permutation length does not match geometry")
        if inverse.shape != permutation.shape:
            raise ValueError("inverse permutation has wrong shape")
        if not np.array_equal(
            np.sort(permutation), np.arange(self.geometry.spin_count)
        ):
            raise ValueError("permutation is not a vertex bijection")
        if not np.array_equal(
            inverse[permutation], np.arange(self.geometry.spin_count)
        ):
            raise ValueError("inverse_permutation is inconsistent")
        if pairs.size == 0:
            pairs = np.empty((0, 2), dtype=np.int64)
            object.__setattr__(self, "block_pairs", pairs)
        if pairs.ndim != 2 or pairs.shape[1] != 2:
            raise ValueError("block_pairs must have shape [count, 2]")
        if owners.shape != (pairs.shape[0],):
            raise ValueError("owners must align one-to-one with block_pairs")
        if pairs.shape[0]:
            if np.any(pairs[:, 0] < 0) or np.any(
                pairs[:, 1] >= self.geometry.total_blocks
            ):
                raise ValueError("block pair outside geometry")
            if np.any(pairs[:, 0] >= pairs[:, 1]):
                raise ValueError("block pairs must be strictly upper triangular")
            if np.unique(pairs, axis=0).shape[0] != pairs.shape[0]:
                raise ValueError("duplicate block pair")
        for index, (block_a, block_b) in enumerate(pairs.tolist()):
            cross = (
                block_a // self.geometry.blocks_per_h1 !=
                block_b // self.geometry.blocks_per_h1
            )
            owner = int(owners[index])
            if cross and not 0 <= owner < self.geometry.node_count:
                raise ValueError(f"cross-H1 pair {(block_a, block_b)} has no owner")
            if not cross and owner != -1:
                raise ValueError(f"local pair {(block_a, block_b)} cannot move")

    def schedule(self) -> list[ScheduledBlock]:
        return [
            ScheduledBlock(int(pair[0]), int(pair[1]), int(owner))
            for pair, owner in zip(self.block_pairs, self.owners)
        ]

    def occupancy_dataset(self) -> BlockOccupancyDataset:
        return BlockOccupancyDataset(
            self.geometry.spin_count,
            self.known_cut,
            {tuple(map(int, pair)) for pair in self.block_pairs.tolist()},
        )

    def save(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "permutation.npy", self.permutation,
                allow_pickle=False)
        np.save(directory / "inverse_permutation.npy",
                self.inverse_permutation, allow_pickle=False)
        np.save(directory / "block_pairs.npy", self.block_pairs,
                allow_pickle=False)
        np.save(directory / "owners.npy", self.owners, allow_pickle=False)
        manifest = {
            "schema": ARTIFACT_SCHEMA,
            "geometry": _geometry_dict(self.geometry),
            "tile_size": self.tile_size,
            "spin_count": self.geometry.spin_count,
            "known_cut": self.known_cut,
            "scheduled_blocks": int(self.block_pairs.shape[0]),
            "permutation_convention": "permutation[new_vertex] = old_vertex",
            "inverse_convention": "inverse_permutation[old_vertex] = new_vertex",
            "files": {
                "permutation": "permutation.npy",
                "inverse_permutation": "inverse_permutation.npy",
                "block_pairs": "block_pairs.npy",
                "owners": "owners.npy",
                "text_schedule": "schedule.txt",
            },
            "metadata": self.metadata,
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        with (directory / "schedule.txt").open("w") as output:
            output.write("# block_a block_b cross_owner; -1 means fixed local\n")
            for record in self.schedule():
                output.write(
                    f"{record.block_a} {record.block_b} {record.owner}\n"
                )
        return directory

    @classmethod
    def load(cls, directory: str | Path) -> "MappingArtifact":
        directory = Path(directory)
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("schema") != ARTIFACT_SCHEMA:
            raise ValueError("unsupported mapping artifact schema")
        geometry = Geometry(**manifest["geometry"])
        files = manifest["files"]
        return cls(
            geometry=geometry,
            permutation=np.load(directory / files["permutation"],
                                allow_pickle=False),
            inverse_permutation=np.load(
                directory / files["inverse_permutation"], allow_pickle=False
            ),
            block_pairs=np.load(directory / files["block_pairs"],
                                allow_pickle=False),
            owners=np.load(directory / files["owners"], allow_pickle=False),
            known_cut=int(manifest.get("known_cut", 0)),
            tile_size=int(manifest["tile_size"]),
            metadata=dict(manifest.get("metadata", {})),
        )


def artifact_from_mapping(
    geometry: Geometry,
    permutation: np.ndarray,
    inverse_permutation: np.ndarray,
    occupied_coords: np.ndarray,
    *,
    known_cut: int = 0,
    metadata: dict[str, Any] | None = None,
    owner_assigner: OwnerAssigner = assign_cross_owners,
) -> MappingArtifact:
    """Convert any mapper's output into an RTL-valid artifact.

    The callable owner_assigner is the experiment interface for comparing
    cross-H1 scheduling algorithms without changing the timing model.
    """
    coords = np.asarray(occupied_coords, dtype=np.int64)
    if coords.size == 0:
        pairs = np.empty((0, 2), dtype=np.int64)
    else:
        if coords.ndim != 2 or coords.shape[1] != 2:
            raise ValueError("occupied_coords must have shape [count, 2]")
        coords = np.sort(coords, axis=1)
        pairs = np.unique(coords[coords[:, 0] != coords[:, 1]], axis=0)
    owners = owner_assigner(geometry, pairs)
    return MappingArtifact(
        geometry=geometry,
        permutation=permutation,
        inverse_permutation=inverse_permutation,
        block_pairs=pairs,
        owners=owners,
        known_cut=known_cut,
        metadata=metadata or {},
    )


def load_sparse_dataset_arrays(
    path: str | Path,
) -> tuple[int, int, np.ndarray, np.ndarray, np.ndarray]:
    """Load the text Ising format into compact NumPy edge arrays."""
    path = Path(path)
    values = np.fromfile(path, dtype=np.int64, sep=" ")
    if values.size < 2:
        raise ValueError(f"invalid dataset header in {path}")
    spin_count, known_cut = map(int, values[:2])
    payload = values[2:]
    if payload.size % 3:
        raise ValueError(f"invalid edge record in {path}")
    records = payload.reshape((-1, 3))
    if records.shape[0] == 0:
        return (
            spin_count, known_cut,
            np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.float32),
        )
    src = records[:, 0] - 1
    dst = records[:, 1] - 1
    raw_weight = records[:, 2]
    if (
        np.any(src < 0) or np.any(src >= spin_count) or
        np.any(dst < 0) or np.any(dst >= spin_count)
    ):
        raise ValueError(f"vertex outside range in {path}")
    if np.any(raw_weight < -128) or np.any(raw_weight > 127):
        raise ValueError(f"weight outside int8 in {path}")
    keep = raw_weight != 0
    return (
        spin_count,
        known_cut,
        np.asarray(src[keep], dtype=np.int64),
        np.asarray(dst[keep], dtype=np.int64),
        np.asarray(np.abs(raw_weight[keep]), dtype=np.float32),
    )


def map_sparse_dataset(
    path: str | Path,
    geometry: Geometry,
    *,
    device: str = "auto",
    macro_iterations: int = 8,
    micro_iterations: int = 6,
    candidate_swaps: int = 64,
    seed: int | None = None,
    require_cuda: bool = False,
) -> MappingArtifact:
    """Run the vendored hierarchical mapper without a dense adjacency matrix."""
    try:
        import torch
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "map-graph requires PyTorch; install a CUDA-enabled build for "
            "million-spin mapping"
        ) from exc
    from graph_mapping.graph_compression.tile_compressor import (
        reorder_and_extract_block_coords_from_edges,
    )

    spin_count, known_cut, src, dst, weight = load_sparse_dataset_arrays(path)
    if spin_count != geometry.spin_count:
        raise ValueError(
            f"dataset has {spin_count} spins; geometry has {geometry.spin_count}"
        )
    if device == "auto":
        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        selected_device = device
    if require_cuda and not str(selected_device).startswith("cuda"):
        raise ValueError("--require-cuda requested but CUDA was not selected")
    torch_device = torch.device(selected_device)
    src_tensor = torch.from_numpy(src).to(torch_device)
    dst_tensor = torch.from_numpy(dst).to(torch_device)
    weight_tensor = torch.from_numpy(weight).to(torch_device)
    mapped = reorder_and_extract_block_coords_from_edges(
        src_tensor,
        dst_tensor,
        weight_tensor,
        num_vertices=spin_count,
        num_partitions=geometry.node_count,
        tile_size=RTL_TILE_SIZE,
        macro_iterations=macro_iterations,
        micro_iterations=micro_iterations,
        candidate_swaps=candidate_swaps,
        seed=seed,
        score_by_magnitude=True,
        require_cuda=require_cuda,
    )
    return artifact_from_mapping(
        geometry,
        mapped.perm.detach().cpu().numpy(),
        mapped.inverse_perm.detach().cpu().numpy(),
        mapped.coords.detach().cpu().numpy(),
        known_cut=known_cut,
        metadata={
            "source_dataset": str(Path(path)),
            "device": str(torch_device),
            "edge_records": int(src.shape[0]),
            "macro_iterations": macro_iterations,
            "micro_iterations": micro_iterations,
            "candidate_swaps": candidate_swaps,
            "seed": seed,
            "partition_score": "absolute-coupling-magnitude",
            "cross_owner_policy": "strict-balance-block-reuse-distance",
        },
    )


def write_permuted_dataset(
    source_path: str | Path,
    destination_path: str | Path,
    artifact: MappingArtifact,
) -> Path:
    """Stream an exact dataset into the mapper's new vertex numbering."""
    source_path = Path(source_path)
    destination_path = Path(destination_path)
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("source and destination dataset paths must differ")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    inverse = artifact.inverse_permutation
    with source_path.open() as source, destination_path.open("w") as output:
        header = source.readline().split()
        if len(header) != 2:
            raise ValueError(f"invalid dataset header in {source_path}")
        spin_count, known_cut = map(int, header)
        if spin_count != artifact.geometry.spin_count:
            raise ValueError("dataset and mapping artifact spin counts differ")
        output.write(f"{spin_count} {known_cut}\n")
        for line_number, line in enumerate(source, 2):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 3:
                raise ValueError(
                    f"invalid record at {source_path}:{line_number}"
                )
            old_row, old_column, value = map(int, fields)
            if not (
                1 <= old_row <= spin_count and
                1 <= old_column <= spin_count
            ):
                raise ValueError(
                    f"vertex outside range at {source_path}:{line_number}"
                )
            output.write(
                f"{int(inverse[old_row - 1]) + 1} "
                f"{int(inverse[old_column - 1]) + 1} {value}\n"
            )
    return destination_path
