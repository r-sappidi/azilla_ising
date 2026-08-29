"""Dataset, schedule, and RTL-compatible initialization utilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .fixed import pack_lanes, unsigned


@dataclass(frozen=True, slots=True)
class ScheduledBlock:
    block_a: int
    block_b: int
    owner: int = -1


@dataclass(frozen=True, slots=True)
class Geometry:
    mesh_x: int
    mesh_y: int
    h0_per_h1: int
    cores_per_h0: int

    @property
    def node_count(self) -> int:
        return self.mesh_x * self.mesh_y

    @property
    def blocks_per_h1(self) -> int:
        return self.h0_per_h1 * self.cores_per_h0

    @property
    def total_blocks(self) -> int:
        return self.node_count * self.blocks_per_h1

    @property
    def spin_count(self) -> int:
        return self.total_blocks * 32


class IsingDataset:
    def __init__(self, spin_count: int, known_cut: int,
                 weights: dict[tuple[int, int], int]):
        self.spin_count = spin_count
        self.known_cut = known_cut
        self.weights = weights

    @classmethod
    def load(cls, path: str | Path) -> "IsingDataset":
        path = Path(path)
        with path.open() as source:
            header = source.readline().split()
            if len(header) != 2:
                raise ValueError(f"invalid dataset header in {path}")
            spin_count, known_cut = map(int, header)
            weights: dict[tuple[int, int], int] = {}
            for line_number, line in enumerate(source, 2):
                fields = line.split()
                if not fields:
                    continue
                if len(fields) != 3:
                    raise ValueError(f"invalid record at {path}:{line_number}")
                row, column, value = map(int, fields)
                row -= 1
                column -= 1
                if not (0 <= row < spin_count and 0 <= column < spin_count):
                    raise ValueError(f"vertex outside range at {path}:{line_number}")
                if not -128 <= value <= 127:
                    raise ValueError(f"weight outside int8 at {path}:{line_number}")
                weights[(row, column)] = value
        return cls(spin_count, known_cut, weights)

    def weight(self, row: int, column: int) -> int:
        return self.weights.get((row, column), 0)

    def weight_row(self, block_a: int, block_b: int, row: int) -> int:
        return pack_lanes(
            [self.weight(block_a * 32 + row, block_b * 32 + column)
             for column in range(32)], 8
        )

    def active_block_pairs(self) -> set[tuple[int, int]]:
        active = set()
        for row, column in self.weights:
            a, b = row // 32, column // 32
            if a != b:
                active.add(tuple(sorted((a, b))))
        return active


class BlockOccupancyDataset:
    """Memory-efficient dataset view for timing-only scalability studies.

    It retains only unordered nonzero 32x32 block occupancy, not individual
    edge weights. Consequently it cannot be used by the arithmetic model.
    """

    def __init__(self, spin_count: int, known_cut: int,
                 active_pairs: set[tuple[int, int]]):
        self.spin_count = spin_count
        self.known_cut = known_cut
        self._active_pairs = active_pairs

    @classmethod
    def load(cls, path: str | Path) -> "BlockOccupancyDataset":
        path = Path(path)
        with path.open() as source:
            header = source.readline().split()
            if len(header) != 2:
                raise ValueError(f"invalid dataset header in {path}")
            spin_count, known_cut = map(int, header)
            active: set[tuple[int, int]] = set()
            for line_number, line in enumerate(source, 2):
                fields = line.split()
                if not fields:
                    continue
                if len(fields) != 3:
                    raise ValueError(f"invalid record at {path}:{line_number}")
                row, column, value = map(int, fields)
                row -= 1
                column -= 1
                if not (0 <= row < spin_count and 0 <= column < spin_count):
                    raise ValueError(f"vertex outside range at {path}:{line_number}")
                if not -128 <= value <= 127:
                    raise ValueError(f"weight outside int8 at {path}:{line_number}")
                block_a, block_b = row // 32, column // 32
                if value != 0 and block_a != block_b:
                    active.add(tuple(sorted((block_a, block_b))))
        return cls(spin_count, known_cut, active)

    def active_block_pairs(self) -> set[tuple[int, int]]:
        return self._active_pairs


def load_schedule(path: str | Path) -> list[ScheduledBlock]:
    path = Path(path)
    schedule = []
    with path.open() as source:
        for line_number, line in enumerate(source, 1):
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            if len(fields) != 3:
                raise ValueError(f"invalid schedule at {path}:{line_number}")
            schedule.append(ScheduledBlock(*map(int, fields)))
    return schedule


def validate_schedule(dataset: IsingDataset | BlockOccupancyDataset,
                      geometry: Geometry,
                      schedule: list[ScheduledBlock]) -> None:
    if dataset.spin_count != geometry.spin_count:
        raise ValueError(
            f"dataset has {dataset.spin_count} spins; geometry has {geometry.spin_count}"
        )
    expected = dataset.active_block_pairs()
    observed: set[tuple[int, int]] = set()
    for index, item in enumerate(schedule):
        if not (0 <= item.block_a < item.block_b < geometry.total_blocks):
            raise ValueError(f"invalid block pair at schedule record {index + 1}")
        pair = (item.block_a, item.block_b)
        if pair in observed:
            raise ValueError(f"duplicate block pair {pair}")
        observed.add(pair)
        h1_a = item.block_a // geometry.blocks_per_h1
        h1_b = item.block_b // geometry.blocks_per_h1
        if h1_a != h1_b and not (0 <= item.owner < geometry.node_count):
            raise ValueError(f"cross-H1 pair {pair} has invalid owner {item.owner}")
    missing = expected - observed
    extra = observed - expected
    if missing or extra:
        raise ValueError(
            f"schedule coverage mismatch: missing={len(missing)} extra={len(extra)}"
        )


def initial_state_for_block(block_id: int) -> int:
    value = unsigned(0x9E3779B9 ^ unsigned(block_id * 0x85EBCA6B, 32), 32)
    value ^= unsigned(value << 13, 32)
    value ^= value >> 17
    value ^= unsigned(value << 5, 32)
    return unsigned(value, 32)


def noise_seed_for_block(block_id: int) -> int:
    value = unsigned(0xD1B54A35 ^ unsigned(block_id * 0x27D4EB2D, 32), 32)
    return 1 if value == 0 else value
