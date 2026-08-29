"""RTL-testbench-compatible sparse mapping and command dispatch.

The synthesizable design deliberately leaves mapping policy outside the RTL.
This module mirrors the controller behavior in ``tb/ising_mesh_tb.sv`` so a
mapping algorithm can be changed without changing the cycle model itself.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Sequence

from .hierarchy import DmaCommand
from .workload import Geometry, ScheduledBlock


H0 = "h0"
H1 = "h1"
CROSS = "cross"


def mesh_distance(geometry: Geometry, node_a: int, node_b: int) -> int:
    ax, ay = node_a % geometry.mesh_x, node_a // geometry.mesh_x
    bx, by = node_b % geometry.mesh_x, node_b // geometry.mesh_x
    return abs(ax - bx) + abs(ay - by)


def allocate_h1_pairs(geometry: Geometry) -> dict[tuple[int, int], int]:
    """Reproduce ``allocate_h1_pairs`` from the full-system RTL testbench."""

    count = geometry.node_count
    pair_count = count * (count - 1) // 2
    base_capacity, extra_capacity = divmod(pair_count, count)
    capacity = [base_capacity] * count
    load = [0] * count

    # Nodes with the smallest total distance receive the remainder capacity.
    extra_selected: set[int] = set()
    for _ in range(extra_capacity):
        node = min(
            (candidate for candidate in range(count)
             if candidate not in extra_selected),
            key=lambda candidate: (
                sum(mesh_distance(geometry, candidate, other)
                    for other in range(count)),
                candidate,
            ),
        )
        extra_selected.add(node)
        capacity[node] += 1

    unassigned = {(a, b) for a in range(count) for b in range(a + 1, count)}
    owners: dict[tuple[int, int], int] = {}
    while unassigned:
        # The SV loop keeps the first lexicographic pair at a tied distance.
        endpoint_a, endpoint_b = min(
            unassigned,
            key=lambda pair: (-mesh_distance(geometry, *pair), pair[0], pair[1]),
        )
        eligible = [node for node in range(count) if load[node] < capacity[node]]

        def owner_key(owner: int) -> tuple[int, int, Fraction, int]:
            da = mesh_distance(geometry, owner, endpoint_a)
            db = mesh_distance(geometry, owner, endpoint_b)
            normalized_load = Fraction(load[owner], capacity[owner])
            return max(da, db), da + db, normalized_load, owner

        owner = min(eligible, key=owner_key)
        owners[(endpoint_a, endpoint_b)] = owner
        owners[(endpoint_b, endpoint_a)] = owner
        load[owner] += 1
        unassigned.remove((endpoint_a, endpoint_b))
    return owners


@dataclass(frozen=True, slots=True)
class WorkItem:
    block_a: int
    block_b: int


@dataclass(frozen=True, slots=True)
class WorkTarget:
    level: str
    node: int
    index: int


@dataclass(slots=True)
class CompiledSchedule:
    geometry: Geometry
    h0: list[deque[WorkItem]]
    h1: list[deque[WorkItem]]
    cross: list[deque[WorkItem]]
    owners: dict[tuple[int, int], int]

    @property
    def counts(self) -> tuple[int, int, int]:
        return (
            sum(map(len, self.h0)),
            sum(map(len, self.h1)),
            sum(map(len, self.cross)),
        )

    def publications(self) -> tuple[tuple[int, int], ...]:
        """Unique ``(block, owner)`` state packets in SV foreach order."""

        needed = {
            (block, owner)
            for owner, queue in enumerate(self.cross)
            for work in queue
            for block in (work.block_a, work.block_b)
        }
        return tuple(sorted(needed))


def compile_schedule(
    geometry: Geometry,
    pairs: Iterable[ScheduledBlock | tuple[int, int]],
    *,
    owners: dict[tuple[int, int], int] | None = None,
) -> CompiledSchedule:
    """Classify unordered blocks into the H0, H1, and cross-H1 queues.

    A non-negative owner in :class:`ScheduledBlock` overrides the allocator
    only for cross-H1 work, exactly like ``enqueue_external_pair``.
    """

    owners = owners or allocate_h1_pairs(geometry)
    total_h0 = geometry.node_count * geometry.h0_per_h1
    h0 = [deque() for _ in range(total_h0)]
    h1 = [deque() for _ in range(geometry.node_count)]
    cross = [deque() for _ in range(geometry.node_count)]
    observed: set[tuple[int, int]] = set()

    for record in pairs:
        if isinstance(record, ScheduledBlock):
            block_a, block_b, requested_owner = (
                record.block_a, record.block_b, record.owner
            )
        else:
            block_a, block_b = record
            requested_owner = -1
        pair = (block_a, block_b)
        if not (0 <= block_a < block_b < geometry.total_blocks):
            raise ValueError(f"invalid scheduled block pair {pair}")
        if pair in observed:
            raise ValueError(f"duplicate scheduled block pair {pair}")
        observed.add(pair)
        work = WorkItem(block_a, block_b)
        h0_a = block_a // geometry.cores_per_h0
        h0_b = block_b // geometry.cores_per_h0
        h1_a = block_a // geometry.blocks_per_h1
        h1_b = block_b // geometry.blocks_per_h1
        if h0_a == h0_b:
            h0[h0_a].append(work)
        elif h1_a == h1_b:
            h1[h1_a].append(work)
        else:
            owner = requested_owner if requested_owner >= 0 else owners[(h1_a, h1_b)]
            if not 0 <= owner < geometry.node_count:
                raise ValueError(f"cross owner {owner} out of range for {pair}")
            cross[owner].append(work)
    return CompiledSchedule(geometry, h0, h1, cross, owners)


def command_for(target: WorkTarget, work: WorkItem,
                geometry: Geometry) -> DmaCommand:
    if target.level == H0:
        state_count = geometry.cores_per_h0
    elif target.level == H1:
        state_count = geometry.blocks_per_h1
    elif target.level == CROSS:
        state_count = geometry.total_blocks
    else:
        raise ValueError(f"unknown hierarchy level {target.level!r}")
    return DmaCommand(
        state_a_index=work.block_a % state_count,
        state_b_index=work.block_b % state_count,
        block_a=work.block_a,
        block_b=work.block_b,
    )


@dataclass(frozen=True, slots=True)
class DispatchPort:
    target: WorkTarget
    engine: int


class ConcurrentDispatcher:
    """Ready-aware, per-port command source used by Ramulator mode.

    Pending valid and metadata remain stable until a rising-edge handshake.
    Newly free engines are visited from the per-node round-robin pointer,
    matching ``dispatch_ramulator_schedule``.
    """

    def __init__(self, schedule: CompiledSchedule, *, h0_mvm_count: int,
                 h1_mvm_count: int, cross_mvm_count: int):
        self.schedule = schedule
        self.engine_counts = {
            H0: h0_mvm_count,
            H1: h1_mvm_count,
            CROSS: cross_mvm_count,
        }
        self.queues: dict[WorkTarget, deque[WorkItem]] = {}
        for global_h0, queue in enumerate(schedule.h0):
            self.queues[WorkTarget(H0, global_h0 // schedule.geometry.h0_per_h1,
                                   global_h0 % schedule.geometry.h0_per_h1)] = deque(queue)
        for node, queue in enumerate(schedule.h1):
            self.queues[WorkTarget(H1, node, 0)] = deque(queue)
        for node, queue in enumerate(schedule.cross):
            self.queues[WorkTarget(CROSS, node, 0)] = deque(queue)
        self.next_engine = {target: 0 for target in self.queues}
        self.pending: dict[DispatchPort, DmaCommand] = {}
        self.accepted: set[DispatchPort] = set()
        self.total = sum(len(queue) for queue in self.queues.values())
        self.retired = 0

    @property
    def done(self) -> bool:
        return self.retired == self.total and not self.pending

    def drive(self, ready: dict[DispatchPort, bool]) -> dict[DispatchPort, DmaCommand]:
        # Retire transfers sampled on the preceding rising edge.
        # Keep the accepted mask through this dispatch step.  The SV
        # testbench clears cmd_pending for an accepted port and then tests
        # !cmd_accepted before refilling it, so that port cannot receive a new
        # command until the following negedge.
        previously_accepted = self.accepted
        for port in previously_accepted:
            self.pending.pop(port)
            self.retired += 1

        for target, queue in self.queues.items():
            count = self.engine_counts[target.level]
            base = self.next_engine[target]
            for offset in range(count):
                engine = (base + offset) % count
                port = DispatchPort(target, engine)
                if (queue and port not in self.pending and
                        port not in previously_accepted and
                        ready.get(port, False)):
                    self.pending[port] = command_for(target, queue.popleft(),
                                                     self.schedule.geometry)
                    self.next_engine[target] = (engine + 1) % count

        # These are the transfers that the upcoming rising edge will sample.
        self.accepted = {
            port for port in self.pending if ready.get(port, False)
        }
        return dict(self.pending)


class DirectDispatcher:
    """The serial command/32-weight-beat task ordering of direct TB mode."""

    def __init__(self, schedule: CompiledSchedule, *, h0_mvm_count: int,
                 h1_mvm_count: int, cross_mvm_count: int):
        geometry = schedule.geometry
        ordered: list[tuple[DispatchPort, DmaCommand]] = []

        def add_level(level: str, queues: Sequence[deque[WorkItem]], count: int):
            for index, source in enumerate(queues):
                target = WorkTarget(
                    level,
                    index // geometry.h0_per_h1 if level == H0 else index,
                    index % geometry.h0_per_h1 if level == H0 else 0,
                )
                engine_queues = [deque() for _ in range(count)]
                for work_index, work in enumerate(source):
                    engine_queues[work_index % count].append(work)
                for engine, queue in enumerate(engine_queues):
                    for work in queue:
                        ordered.append((DispatchPort(target, engine),
                                        command_for(target, work, geometry)))

        add_level(H0, schedule.h0, h0_mvm_count)
        # The direct SV dispatcher nests H1 and cross work inside the node
        # loop; cross work owned by node N therefore precedes H1 work at N+1.
        for node in range(geometry.node_count):
            for level, source, count in (
                (H1, schedule.h1[node], h1_mvm_count),
                (CROSS, schedule.cross[node], cross_mvm_count),
            ):
                target = WorkTarget(level, node, 0)
                engine_queues = [deque() for _ in range(count)]
                for work_index, work in enumerate(source):
                    engine_queues[work_index % count].append(work)
                for engine, queue in enumerate(engine_queues):
                    for work in queue:
                        ordered.append((DispatchPort(target, engine),
                                        command_for(target, work, geometry)))
        self.ordered = deque(ordered)
        self.active: tuple[DispatchPort, DmaCommand] | None = None
        self.last_driven_command: DmaCommand | None = None
        self.phase = "idle"
        self.beat = 0
        self.cooldown = False

    @property
    def done(self) -> bool:
        return not self.ordered and self.active is None

    def drive(self, command_ready: dict[DispatchPort, bool],
              weight_ready: dict[DispatchPort, bool]) -> tuple[
                  dict[DispatchPort, DmaCommand], dict[DispatchPort, int]]:
        """Return command valids and weight beat indices for the next edge."""

        if self.active is None and self.ordered and self.cooldown:
            self.cooldown = False
            self.last_driven_command = None
            return {}, {}
        if self.active is None and self.ordered:
            self.active = self.ordered.popleft()
            self.phase = "command"
        if self.active is None:
            self.last_driven_command = None
            return {}, {}
        port, command = self.active
        self.last_driven_command = command
        if self.phase == "command":
            outputs = ({port: command}, {})
            if command_ready.get(port, False):
                self.phase = "weight"
                self.beat = 0
            return outputs
        outputs = ({}, {port: self.beat})
        if weight_ready.get(port, False):
            if self.beat == 31:
                self.active = None
                self.phase = "idle"
                self.cooldown = True
            else:
                self.beat += 1
        return outputs
