"""Event-compressed timing model and exact packet-network replay.

This module never drops elapsed time.  It advances ``now`` directly to the
next release only when the mesh and every endpoint are quiescent; while any
flit can contend or backpressure, it executes the same router ``tick`` used by
the full cycle model.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .adapters import NOC_EPOCH_DONE, NOC_PARTIAL, NOC_STATE
from .noc import EAST, LOCAL, NORTH, SOUTH, WEST, Flit, Mesh
from .scheduler import CROSS, H0, H1, CompiledSchedule, compile_schedule
from .workload import BlockOccupancyDataset, Geometry, IsingDataset, ScheduledBlock


@dataclass(order=True, slots=True)
class _Event:
    cycle: int
    sequence: int
    callback: Callable[[int], None] = field(compare=False)


class EventLoop:
    """Minimal integer-cycle discrete-event loop with measured compression."""

    def __init__(self):
        self.now = 0
        self.skipped_cycles = 0
        self.executed_events = 0
        self._sequence = 0
        self._events: list[_Event] = []

    def schedule(self, cycle: int, callback: Callable[[int], None]) -> None:
        if cycle < self.now:
            raise ValueError("cannot schedule an event in the past")
        heapq.heappush(self._events, _Event(cycle, self._sequence, callback))
        self._sequence += 1

    def run(self) -> None:
        while self._events:
            event = heapq.heappop(self._events)
            if event.cycle > self.now:
                self.skipped_cycles += event.cycle - self.now
                self.now = event.cycle
            event.callback(self.now)
            self.executed_events += 1


@dataclass(frozen=True, slots=True)
class PacketRelease:
    cycle: int
    source: int
    destination: int
    packet_type: int
    block_id: int = 0
    flits: int = 1


@dataclass(frozen=True, slots=True)
class NetworkReplayResult:
    start_cycle: int
    end_cycle: int
    elapsed_cycles: int
    active_cycles: int
    skipped_cycles: int
    injected_flits: int
    ejected_flits: int
    physical_link_flits: int
    injection_stalls: int
    link_stalls: int


@dataclass(slots=True)
class _PacketState:
    release: PacketRelease
    remaining: int


class EventCompressedMesh:
    """Exact FlooNoC replay that jumps across globally empty intervals."""

    def __init__(self, width: int, height: int, fifo_depth: int = 4):
        self.width = width
        self.height = height
        self.mesh = Mesh(width, height, fifo_depth)

    def _empty(self, sources: list[list[_PacketState]]) -> bool:
        return (not any(sources) and
                not any(router_fifo.queue for router in self.mesh.routers
                        for router_fifo in router.fifos))

    def replay(self, releases: Iterable[PacketRelease], *,
               start_cycle: int = 0, drain_cycles: int = 0,
               observer: Callable[[int, str, int, str, Flit], None] | None = None,
               ) -> NetworkReplayResult:
        ordered = sorted(releases, key=lambda item: (
            item.cycle, item.source, item.packet_type, item.block_id
        ))
        for release in ordered:
            if release.cycle < start_cycle:
                raise ValueError("packet release precedes replay start")
            if not (0 <= release.source < self.width * self.height and
                    0 <= release.destination < self.width * self.height):
                raise ValueError("packet endpoint lies outside mesh")
            if release.flits <= 0:
                raise ValueError("packet must contain at least one flit")
        self.mesh.reset()
        sources: list[list[_PacketState]] = [
            [] for _ in range(self.width * self.height)
        ]
        now = start_cycle
        release_index = 0
        active_cycles = skipped = injected = ejected = links = 0
        injection_stalls = link_stalls = 0
        quiet = 0

        while release_index < len(ordered) or not self._empty(sources) or quiet < drain_cycles:
            if self._empty(sources) and release_index < len(ordered) and quiet == 0:
                target = ordered[release_index].cycle
                if target > now:
                    skipped += target - now
                    now = target
            while (release_index < len(ordered) and
                   ordered[release_index].cycle <= now):
                release = ordered[release_index]
                sources[release.source].append(_PacketState(release, release.flits))
                release_index += 1

            comb = [router.outputs() for router in self.mesh.routers]
            injections: dict[int, Flit] = {}
            for source, queue in enumerate(sources):
                if not queue:
                    continue
                packet = queue[0]
                destination = packet.release.destination
                injections[source] = Flit(
                    packet_type=packet.release.packet_type,
                    dest_x=destination % self.width,
                    dest_y=destination // self.width,
                    source_id=source,
                    block_id=packet.release.block_id,
                    last=packet.remaining == 1,
                )
                if comb[source].input_ready[LOCAL]:
                    injected += 1
                    if observer is not None:
                        observer(now, "inject", source, "local", injections[source])
                    packet.remaining -= 1
                    if packet.remaining == 0:
                        queue.pop(0)
                else:
                    injection_stalls += 1

            activity = bool(injections)
            port_names = {
                NORTH: "north", SOUTH: "south", EAST: "east", WEST: "west"
            }
            for node, output in enumerate(comb):
                if output.output_flits[LOCAL] is not None:
                    ejected += 1
                    activity = True
                    if observer is not None:
                        observer(
                            now, "eject", node, "local",
                            output.output_flits[LOCAL],
                        )
                router = self.mesh.routers[node]
                for port, nx, ny, neighbor_input in (
                    (NORTH, router.x, router.y - 1, SOUTH),
                    (SOUTH, router.x, router.y + 1, NORTH),
                    (EAST, router.x + 1, router.y, WEST),
                    (WEST, router.x - 1, router.y, EAST),
                ):
                    if output.output_flits[port] is None:
                        continue
                    activity = True
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        neighbor = ny * self.width + nx
                        if comb[neighbor].input_ready[neighbor_input]:
                            links += 1
                            if observer is not None:
                                observer(
                                    now, "link", node, port_names[port],
                                    output.output_flits[port],
                                )
                        else:
                            link_stalls += 1

            self.mesh.tick(injections, {node: True for node in range(len(sources))})
            active_cycles += 1
            now += 1
            if release_index == len(ordered) and self._empty(sources):
                quiet = quiet + 1 if not activity else 0
            else:
                quiet = 0

        return NetworkReplayResult(
            start_cycle, now, now - start_cycle, active_cycles, skipped,
            injected, ejected, links, injection_stalls, link_stalls,
        )


@dataclass(frozen=True, slots=True)
class EventTimingProfile:
    """RTL-derived endpoint constants for timing-only event scheduling.

    ``block_cycles`` is command-to-final-partial availability for an otherwise
    idle engine/memory frontend.  Queueing on an engine and NoC contention are
    computed, not folded into the constant.
    """

    h0_block_cycles: int = 67
    h1_block_cycles: int = 67
    cross_block_cycles: int = 67
    init_fixed_cycles: int = 3
    init_cycles_per_block: int = 33
    iteration_control_cycles: int = 3
    post_compute_cycles: int = 9
    calibrated_name: str = "rtl-g256-ramulator-128x32"


@dataclass(frozen=True, slots=True)
class EventPerformanceConfig:
    h0_mvm_count: int = 1
    h1_mvm_count: int = 1
    cross_mvm_count: int = 1
    fifo_depth: int = 4
    profile: EventTimingProfile = EventTimingProfile()


@dataclass(frozen=True, slots=True)
class EventPerformanceResult:
    accuracy: str
    calibration: str
    initialization_cycles: int
    iteration_cycles: int
    total_cycles: int
    scheduled_h0: int
    scheduled_h1: int
    scheduled_cross: int
    network_active_cycles: int
    skipped_cycles: int
    injected_flits: int
    ejected_flits: int
    physical_link_flits: int


class EventCompressedPerformanceModel:
    """Timing-only hierarchy/resource model plus exact event-compressed NoC.

    The supplied profile is an explicit accuracy contract.  It is suitable for
    mapping and scalability sweeps using the same RTL/memory configuration;
    changing memory timing, streamer widths, or endpoint microarchitecture
    requires recalibration against :class:`RamulatorPerformanceModel`.
    """

    def __init__(self, geometry: Geometry,
                 dataset: IsingDataset | BlockOccupancyDataset,
                 config: EventPerformanceConfig | None = None):
        self.geometry = geometry
        self.dataset = dataset
        self.config = config or EventPerformanceConfig()
        if dataset.spin_count != geometry.spin_count:
            raise ValueError(
                f"dataset has {dataset.spin_count} spins; geometry has "
                f"{geometry.spin_count}"
            )

    @staticmethod
    def _resource_completions(queues, engines: int, duration: int) -> list[int]:
        completions: list[int] = []
        for queue in queues:
            available = [0] * engines
            heapq.heapify(available)
            for _ in queue:
                start = heapq.heappop(available)
                completion = start + duration
                heapq.heappush(available, completion)
                completions.append(completion)
        return completions

    def run(self, *, schedule: list[ScheduledBlock] | None = None,
            sparse: bool = True, iterations: int = 1) -> EventPerformanceResult:
        if iterations <= 0:
            raise ValueError("iteration count must be positive")
        if schedule is None:
            pairs = sorted(self.dataset.active_block_pairs()) if sparse else [
                (a, b) for a in range(self.geometry.total_blocks)
                for b in range(a + 1, self.geometry.total_blocks)
            ]
            schedule = [ScheduledBlock(a, b) for a, b in pairs]
        compiled = compile_schedule(self.geometry, schedule)
        profile = self.config.profile
        initialization = (
            profile.init_fixed_cycles +
            self.geometry.total_blocks * profile.init_cycles_per_block
        )

        h0_done = self._resource_completions(
            compiled.h0, self.config.h0_mvm_count, profile.h0_block_cycles
        )
        h1_done = self._resource_completions(
            compiled.h1, self.config.h1_mvm_count, profile.h1_block_cycles
        )
        cross_done: list[tuple[int, int, int]] = []
        for owner, queue in enumerate(compiled.cross):
            available = [0] * self.config.cross_mvm_count
            heapq.heapify(available)
            for work in queue:
                start = heapq.heappop(available)
                completion = start + profile.cross_block_cycles
                heapq.heappush(available, completion)
                cross_done.append((completion, owner, work.block_a))
                cross_done.append((completion, owner, work.block_b))

        releases: list[PacketRelease] = []
        # Publication tasks are globally serial and contain one empty cycle
        # between packets in the full testbench.
        for index, (block, owner) in enumerate(compiled.publications()):
            releases.append(PacketRelease(
                cycle=index * 2,
                source=block // self.geometry.blocks_per_h1,
                destination=owner, packet_type=NOC_STATE, block_id=block,
            ))
        publication = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y, self.config.fifo_depth
        ).replay(releases, drain_cycles=4)

        partial_releases = [
            PacketRelease(
                cycle=completion, source=owner,
                destination=block // self.geometry.blocks_per_h1,
                packet_type=NOC_PARTIAL, block_id=block, flits=4,
            )
            for completion, owner, block in cross_done
        ]
        partial = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y, self.config.fifo_depth
        ).replay(partial_releases, drain_cycles=4)
        completion_start = max(
            [partial.end_cycle, *h0_done, *h1_done, 0]
        )
        completions = [
            PacketRelease(
                cycle=completion_start, source=node, destination=node,
                packet_type=NOC_EPOCH_DONE,
            )
            for node in range(self.geometry.node_count)
        ]
        done_network = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y, self.config.fifo_depth
        ).replay(completions, start_cycle=completion_start, drain_cycles=4)

        compute_span = done_network.end_cycle
        one_iteration = (
            profile.iteration_control_cycles + publication.elapsed_cycles +
            compute_span + profile.post_compute_cycles
        )
        total = initialization + iterations * one_iteration
        networks = (publication, partial, done_network)
        smoke_pairs = {
            (0, 1), (0, 7), (1, 2), (2, 3),
            (3, 4), (4, 5), (5, 6), (6, 7),
        }
        smoke_schedule = compile_schedule(
            self.geometry,
            [ScheduledBlock(block_a, block_b)
             for block_a, block_b in sorted(smoke_pairs)],
        ) if self.geometry == Geometry(2, 1, 2, 2) else None
        schedule_matches_smoke = (
            smoke_schedule is not None and
            tuple(map(tuple, compiled.h0)) ==
            tuple(map(tuple, smoke_schedule.h0)) and
            tuple(map(tuple, compiled.h1)) ==
            tuple(map(tuple, smoke_schedule.h1)) and
            tuple(map(tuple, compiled.cross)) ==
            tuple(map(tuple, smoke_schedule.cross))
        )
        certified = (
            self.geometry == Geometry(2, 1, 2, 2) and
            self.config.h0_mvm_count == self.config.h1_mvm_count ==
            self.config.cross_mvm_count == 1 and
            profile == EventTimingProfile() and
            set(self.dataset.active_block_pairs()) == smoke_pairs and
            schedule_matches_smoke and
            iterations == 1
        )
        return EventPerformanceResult(
            "rtl-differential" if certified else "calibrated-extrapolation",
            profile.calibrated_name,
            initialization, one_iteration, total,
            *compiled.counts,
            sum(item.active_cycles for item in networks),
            sum(item.skipped_cycles for item in networks),
            sum(item.injected_flits for item in networks),
            sum(item.ejected_flits for item in networks),
            sum(item.physical_link_flits for item in networks),
        )
