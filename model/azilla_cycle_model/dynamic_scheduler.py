"""Low-cost online placement policies for hybrid interaction execution.

These policies make one irrevocable core/CIR decision as each occupied block
arrives.  They use only bounded resource-availability state and, for the
traffic-aware variant, deduplicated state-publication and hop estimates.  The
module deliberately does not call the calibrated performance model while
making decisions; doing so would turn the policy into an offline oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .scheduler import CROSS, H0, H1, allocate_h1_pairs, mesh_distance
from .workload import Geometry, ScheduledBlock


READY_ONLY = "ready-only"
QUEUE_AWARE = "queue-aware"
TRAFFIC_AWARE = "traffic-aware"
BATCH_BALANCED = "batch-balanced"


@dataclass(frozen=True, slots=True)
class DynamicPlacementResult:
    policy: str
    core_pairs: tuple[tuple[int, int], ...]
    cir_pairs: tuple[tuple[int, int], ...]
    predicted_completion_cycles: int
    core_decisions: int
    cir_decisions: int
    estimated_state_flit_hops: int
    estimated_partial_flit_hops: int


def select_batch_balanced_core_pairs(
    geometry: Geometry,
    records: Iterable[ScheduledBlock | tuple[int, int]],
    *,
    h0_mvm_count: int,
    h1_mvm_count: int,
    cross_mvm_count: int,
    core_block_cycles: int = 67,
    h0_block_cycles: int = 67,
    h1_block_cycles: int = 67,
    cross_block_cycles: int = 67,
    traffic_cycle_weight: int = 0,
    cir_cost_scale: float = 1.0,
) -> DynamicPlacementResult:
    """Globally order interactions, then balance endpoint and CIR loads.

    The complete mapped block set is known before execution.  Interactions
    incident on the most highly demanded cores are placed first.  Each block
    is assigned to the option with the lower projected local finish time,
    including the CIR engine count and an optional communication estimate.
    This is a deterministic software mapping pass, not an offline timing-model
    oracle and not a cycle-by-cycle hardware scheduler.
    """

    if cir_cost_scale <= 0:
        raise ValueError("CIR cost scale must be positive")
    if traffic_cycle_weight < 0:
        raise ValueError("traffic cycle weight must be non-negative")
    counts = {H0: h0_mvm_count, H1: h1_mvm_count, CROSS: cross_mvm_count}
    durations = {H0: h0_block_cycles, H1: h1_block_cycles,
                 CROSS: cross_block_cycles}
    if min(*counts.values(), core_block_cycles, *durations.values()) <= 0:
        raise ValueError("engine counts and service cycles must be positive")
    items = list(records)
    pairs = [_pair(record) for record in items]
    if len(set(pairs)) != len(pairs):
        raise ValueError("batch schedule contains duplicate block pairs")
    if any(not 0 <= a < b < geometry.total_blocks for a, b in pairs):
        raise ValueError("batch schedule contains an invalid block pair")
    owners = allocate_h1_pairs(geometry)
    degree = [0] * geometry.total_blocks
    for a, b in pairs:
        degree[a] += 1; degree[b] += 1
    ordered = sorted(zip(items, pairs), key=lambda item: (
        -max(degree[item[1][0]], degree[item[1][1]]),
        -(degree[item[1][0]] + degree[item[1][1]]), item[1],
    ))
    core_load = [0] * geometry.total_blocks
    cir_load: dict[tuple[str, int], int] = {}
    state_publications: set[tuple[int, int]] = set()
    core_pairs: list[tuple[int, int]] = []
    cir_pairs: list[tuple[int, int]] = []
    state_flit_hops = partial_flit_hops = 0
    for record, pair in ordered:
        a, b = pair
        level, node = _target(geometry, record, owners)
        key = level, node
        core_finish = (max(core_load[a], core_load[b]) + 1) * core_block_cycles
        cir_finish = (
            (cir_load.get(key, 0) + counts[level]) // counts[level]
            * durations[level] * cir_cost_scale
        )
        core_publications = []
        for source, destination in ((b, a), (a, b)):
            destination_h1 = destination // geometry.blocks_per_h1
            publication = source, destination_h1
            if (source // geometry.blocks_per_h1 != destination_h1
                    and publication not in state_publications):
                core_publications.append(publication)
        core_hops = sum(mesh_distance(
            geometry, source // geometry.blocks_per_h1, destination,
        ) for source, destination in core_publications)
        cir_publications = []
        if level == CROSS:
            for block in pair:
                publication = block, node
                if (block // geometry.blocks_per_h1 != node
                        and publication not in state_publications):
                    cir_publications.append(publication)
        cir_state_hops = sum(mesh_distance(
            geometry, block // geometry.blocks_per_h1, destination,
        ) for block, destination in cir_publications)
        cir_partial_hops = 4 * sum(mesh_distance(
            geometry, node, block // geometry.blocks_per_h1,
        ) for block in pair) if level == CROSS else 0
        core_score = core_finish + traffic_cycle_weight * core_hops
        cir_score = cir_finish + traffic_cycle_weight * (
            cir_state_hops + cir_partial_hops
        )
        if core_score <= cir_score:
            core_load[a] += 1; core_load[b] += 1
            core_pairs.append(pair)
            state_publications.update(core_publications)
            state_flit_hops += core_hops
        else:
            cir_load[key] = cir_load.get(key, 0) + 1
            cir_pairs.append(pair)
            state_publications.update(cir_publications)
            state_flit_hops += cir_state_hops
            partial_flit_hops += cir_partial_hops
    predicted = max(
        [max(core_load, default=0) * core_block_cycles,
         *( ((jobs + counts[level] - 1) // counts[level]) * durations[level]
            for (level, _), jobs in cir_load.items()), 0]
    )
    return DynamicPlacementResult(
        BATCH_BALANCED, tuple(core_pairs), tuple(cir_pairs), predicted,
        len(core_pairs), len(cir_pairs), state_flit_hops, partial_flit_hops,
    )


def _pair(record: ScheduledBlock | tuple[int, int]) -> tuple[int, int]:
    if isinstance(record, ScheduledBlock):
        return record.block_a, record.block_b
    return record


def _target(
    geometry: Geometry,
    record: ScheduledBlock | tuple[int, int],
    owners: dict[tuple[int, int], int],
) -> tuple[str, int]:
    a, b = _pair(record)
    h0_a, h0_b = a // geometry.cores_per_h0, b // geometry.cores_per_h0
    if h0_a == h0_b:
        return H0, h0_a
    h1_a, h1_b = a // geometry.blocks_per_h1, b // geometry.blocks_per_h1
    if h1_a == h1_b:
        return H1, h1_a
    requested = record.owner if isinstance(record, ScheduledBlock) else -1
    return CROSS, requested if requested >= 0 else owners[(h1_a, h1_b)]


def select_dynamic_hybrid_core_pairs(
    geometry: Geometry,
    records: Iterable[ScheduledBlock | tuple[int, int]],
    *,
    policy: str,
    h0_mvm_count: int,
    h1_mvm_count: int,
    cross_mvm_count: int,
    core_block_cycles: int = 67,
    h0_block_cycles: int = 67,
    h1_block_cycles: int = 67,
    cross_block_cycles: int = 67,
    traffic_cycle_weight: int = 1,
) -> DynamicPlacementResult:
    """Select core work using bounded online resource state.

    ``ready-only`` implements atomic dual-core reservation with CIR fallback.
    ``queue-aware`` chooses the placement with the lower predicted compute
    finish. ``traffic-aware`` adds incremental flit-hop estimates to that
    comparison.  Ties prefer cores, preserving the core-preferred design.
    """

    if policy not in {READY_ONLY, QUEUE_AWARE, TRAFFIC_AWARE}:
        raise ValueError(f"unknown dynamic placement policy {policy!r}")
    counts = {H0: h0_mvm_count, H1: h1_mvm_count, CROSS: cross_mvm_count}
    durations = {H0: h0_block_cycles, H1: h1_block_cycles,
                 CROSS: cross_block_cycles}
    if min(*counts.values(), core_block_cycles, *durations.values()) <= 0:
        raise ValueError("engine counts and service cycles must be positive")
    if traffic_cycle_weight < 0:
        raise ValueError("traffic cycle weight must be non-negative")

    items = list(records)
    pairs = [_pair(record) for record in items]
    if len(set(pairs)) != len(pairs):
        raise ValueError("dynamic schedule contains duplicate block pairs")
    if any(not 0 <= a < b < geometry.total_blocks for a, b in pairs):
        raise ValueError("dynamic schedule contains an invalid block pair")
    owners = allocate_h1_pairs(geometry)
    core_available = [0] * geometry.total_blocks
    cir_available: dict[tuple[str, int], list[int]] = {}
    for record in items:
        level, node = _target(geometry, record, owners)
        cir_available.setdefault((level, node), [0] * counts[level])

    core_pairs: list[tuple[int, int]] = []
    cir_pairs: list[tuple[int, int]] = []
    state_publications: set[tuple[int, int]] = set()
    state_flit_hops = partial_flit_hops = 0
    now = 0
    for record, pair in zip(items, pairs):
        a, b = pair
        level, node = _target(geometry, record, owners)
        engines = cir_available[(level, node)]
        cir_engine = min(range(len(engines)), key=lambda index: (engines[index], index))
        core_finish = max(core_available[a], core_available[b]) + core_block_cycles
        cir_finish = engines[cir_engine] + durations[level]

        new_core_publications = []
        for source, destination in ((b, a), (a, b)):
            source_h1 = source // geometry.blocks_per_h1
            destination_h1 = destination // geometry.blocks_per_h1
            publication = (source, destination_h1)
            if source_h1 != destination_h1 and publication not in state_publications:
                new_core_publications.append(publication)
        core_traffic = sum(
            mesh_distance(
                geometry, source // geometry.blocks_per_h1, destination_h1,
            )
            for source, destination_h1 in new_core_publications
        )
        new_cir_publications = []
        if level == CROSS:
            for block in pair:
                publication = (block, node)
                if (block // geometry.blocks_per_h1 != node
                        and publication not in state_publications):
                    new_cir_publications.append(publication)
        cir_state_traffic = sum(
            mesh_distance(
                geometry, block // geometry.blocks_per_h1, destination,
            ) for block, destination in new_cir_publications
        )
        cir_traffic = cir_state_traffic
        if level == CROSS:
            cir_traffic = 4 * sum(
                mesh_distance(
                    geometry, node, block // geometry.blocks_per_h1,
                ) for block in pair
            )

        if policy == READY_ONLY:
            while True:
                core_ready = core_available[a] <= now and core_available[b] <= now
                cir_ready = engines[cir_engine] <= now
                if core_ready or cir_ready:
                    choose_core = core_ready
                    break
                now = min(core_available[a], core_available[b], engines[cir_engine])
                # One free core is insufficient for atomic dual-core issue.
                if now < max(core_available[a], core_available[b]) and not cir_ready:
                    now = min(max(core_available[a], core_available[b]),
                              engines[cir_engine])
        else:
            core_score = core_finish
            cir_score = cir_finish
            if policy == TRAFFIC_AWARE:
                core_score += traffic_cycle_weight * core_traffic
                cir_score += traffic_cycle_weight * cir_traffic
            choose_core = core_score <= cir_score

        if choose_core:
            start = max(now, core_available[a], core_available[b])
            core_available[a] = core_available[b] = start + core_block_cycles
            core_pairs.append(pair)
            state_publications.update(new_core_publications)
            state_flit_hops += core_traffic
        else:
            start = max(now, engines[cir_engine])
            engines[cir_engine] = start + durations[level]
            cir_pairs.append(pair)
            state_publications.update(new_cir_publications)
            state_flit_hops += cir_state_traffic
            partial_flit_hops += cir_traffic - cir_state_traffic

    predicted = max(
        [*core_available,
         *(cycle for engines in cir_available.values() for cycle in engines), 0]
    )
    return DynamicPlacementResult(
        policy, tuple(core_pairs), tuple(cir_pairs), predicted,
        len(core_pairs), len(cir_pairs), state_flit_hops, partial_flit_hops,
    )
