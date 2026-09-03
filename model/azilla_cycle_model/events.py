"""Event-compressed timing model and exact packet-network replay.

This module never drops elapsed time.  It advances ``now`` directly to the
next release only when the mesh and every endpoint are quiescent; while any
flit can contend or backpressure, it executes the same router ``tick`` used by
the full cycle model.
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .adapters import NOC_EPOCH_DONE, NOC_PARTIAL, NOC_STATE
from .noc import (
    EAST, LOCAL, NORTH, SOUTH, WEST, Flit, InterconnectConfig, Mesh,
)
from .scheduler import (
    CORES_ONLY, HYBRID, CROSS, H0, H1, CompiledSchedule,
    compile_cores_only_schedule, compile_hybrid_schedule,
    compile_static_hybrid_schedule,
    compile_schedule,
)
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

    def __init__(
        self,
        width: int,
        height: int,
        fifo_depth: int = 4,
        interconnect: InterconnectConfig | None = None,
    ):
        self.width = width
        self.height = height
        self.mesh = Mesh(width, height, fifo_depth, interconnect)

    def _empty(self, sources: list[list[_PacketState]]) -> bool:
        return (not any(sources) and
                not self.mesh.has_link_data and
                not any(router_fifo.queue for router in self.mesh.routers
                        for router_fifo in router.fifos))

    def replay(self, releases: Iterable[PacketRelease], *,
               start_cycle: int = 0, drain_cycles: int = 0,
               observer: Callable[[int, str, int, str, Flit], None] | None = None,
               resource_observer: Callable[
                   [int, str, int, str, Flit, bool], None
               ] | None = None,
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
                    self.mesh.advance_idle(target - now)
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
                accepted = comb[source].input_ready[LOCAL]
                if resource_observer is not None:
                    resource_observer(
                        now, "inject", source, "local",
                        injections[source], accepted,
                    )
                if accepted:
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
                    if resource_observer is not None:
                        resource_observer(
                            now, "eject", node, "local",
                            output.output_flits[LOCAL], True,
                        )
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
                        accepted = self.mesh.link_ready(node, port, comb)
                        if resource_observer is not None:
                            resource_observer(
                                now, "link", node, port_names[port],
                                output.output_flits[port], accepted,
                            )
                        if accepted:
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
    chiplet_link_latency_cycles: int = 0
    interconnect: InterconnectConfig = InterconnectConfig()
    profile: EventTimingProfile = EventTimingProfile()
    execution_mode: str = "cir"

    def __post_init__(self) -> None:
        if self.chiplet_link_latency_cycles < 0:
            raise ValueError("chiplet link latency must be non-negative")
        if self.execution_mode not in {"cir", CORES_ONLY, HYBRID}:
            raise ValueError(f"unknown execution mode {self.execution_mode!r}")


@dataclass(frozen=True, slots=True)
class EventNocResourceStats:
    """Per-resource counters from the exact packet-network replay."""

    scope: str
    node: int
    x: int
    y: int
    direction: str
    offered_cycles: int
    accepted_flits: int
    stall_cycles: int
    packets: int
    state_flits: int
    partial_flits: int
    epoch_done_flits: int
    other_flits: int

    @property
    def acceptance_fraction(self) -> float:
        return (
            self.accepted_flits / self.offered_cycles
            if self.offered_cycles else 0.0
        )

    @property
    def backpressure(self) -> float:
        return (
            self.stall_cycles / self.offered_cycles
            if self.offered_cycles else 0.0
        )


@dataclass(frozen=True, slots=True)
class EventNodeStats:
    node: int
    x: int
    y: int
    h0_jobs: int
    h1_jobs: int
    cross_jobs: int
    total_jobs: int
    state_publications_sent: int
    state_publications_received: int


@dataclass(frozen=True, slots=True)
class EventWorkResourceStats:
    level: str
    node: int
    index: int
    engines: int
    scheduled_jobs: int
    maximum_jobs_per_engine: int
    nominal_block_cycles: int
    nominal_service_cycles: int
    estimated_dram_requests: int


@dataclass(slots=True)
class _EventNocAccumulator:
    offered_cycles: int = 0
    accepted_flits: int = 0
    stall_cycles: int = 0
    packets: int = 0
    type_flits: list[int] = field(default_factory=lambda: [0, 0, 0, 0])


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
    injection_stalls: int
    link_stalls: int
    average_hops: float
    hop_histogram: tuple[tuple[int, int], ...]
    noc_resources: tuple[EventNocResourceStats, ...]
    nodes: tuple[EventNodeStats, ...]
    work_resources: tuple[EventWorkResourceStats, ...]
    chiplet_link_latency_cycles: int
    interconnect: InterconnectConfig
    execution_mode: str = "cir"
    unordered_interaction_blocks: int = 0
    directed_core_jobs: int = 0
    weight_block_reads: int = 0
    logical_weight_blocks_stored: int = 0
    remote_state_packets: int = 0


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

    def _cores_only_completions(self, queues, duration: int) -> list[int]:
        """Completion times with work pinned to its destination spin core."""

        completions: list[int] = []
        for global_h0, queue in enumerate(queues):
            base = global_h0 * self.geometry.cores_per_h0
            available = [0] * self.geometry.cores_per_h0
            for work in queue:
                core = work.block_a - base
                available[core] += duration
                completions.append(available[core])
        return completions

    def run(self, *, schedule: list[ScheduledBlock] | None = None,
            sparse: bool = True, iterations: int = 1,
            hybrid_core_pairs: Iterable[tuple[int, int]] | None = None,
            ) -> EventPerformanceResult:
        if iterations <= 0:
            raise ValueError("iteration count must be positive")
        if schedule is None:
            pairs = sorted(self.dataset.active_block_pairs()) if sparse else [
                (a, b) for a in range(self.geometry.total_blocks)
                for b in range(a + 1, self.geometry.total_blocks)
            ]
            schedule = [ScheduledBlock(a, b) for a, b in pairs]
        compiled = compile_schedule(self.geometry, schedule)
        cores_only = (
            compile_cores_only_schedule(self.geometry, schedule)
            if self.config.execution_mode == CORES_ONLY else None
        )
        if hybrid_core_pairs is not None and self.config.execution_mode != HYBRID:
            raise ValueError("an explicit hybrid partition requires hybrid mode")
        hybrid = (
            compile_hybrid_schedule(
                self.geometry, schedule, core_pairs=hybrid_core_pairs,
            ) if hybrid_core_pairs is not None else
            compile_static_hybrid_schedule(
                self.geometry, schedule,
                h0_mvm_count=self.config.h0_mvm_count,
                h1_mvm_count=self.config.h1_mvm_count,
                cross_mvm_count=self.config.cross_mvm_count,
                core_block_cycles=self.config.profile.h0_block_cycles,
                h0_block_cycles=self.config.profile.h0_block_cycles,
                h1_block_cycles=self.config.profile.h1_block_cycles,
                cross_block_cycles=self.config.profile.cross_block_cycles,
            ) if self.config.execution_mode == HYBRID else None
        )
        if hybrid is not None:
            compiled = hybrid.cir
        profile = self.config.profile
        chiplet_latency = self.config.chiplet_link_latency_cycles
        noc_accumulators: dict[
            tuple[str, int, str], _EventNocAccumulator
        ] = {}
        for node, router in enumerate(
            EventCompressedMesh(
                self.geometry.mesh_x,
                self.geometry.mesh_y,
                self.config.fifo_depth,
                self.config.interconnect,
            ).mesh.routers
        ):
            noc_accumulators[("inject", node, "local")] = _EventNocAccumulator()
            noc_accumulators[("eject", node, "local")] = _EventNocAccumulator()
            for direction, nx, ny in (
                ("north", router.x, router.y - 1),
                ("south", router.x, router.y + 1),
                ("east", router.x + 1, router.y),
                ("west", router.x - 1, router.y),
            ):
                if (
                    0 <= nx < self.geometry.mesh_x
                    and 0 <= ny < self.geometry.mesh_y
                ):
                    noc_accumulators[(
                        "link", node, direction
                    )] = _EventNocAccumulator()

        def observe_resource(
            cycle: int,
            scope: str,
            node: int,
            direction: str,
            flit: Flit,
            accepted: bool,
        ) -> None:
            del cycle
            resource = noc_accumulators.setdefault(
                (scope, node, direction), _EventNocAccumulator()
            )
            resource.offered_cycles += 1
            if not accepted:
                resource.stall_cycles += 1
                return
            resource.accepted_flits += 1
            packet_type = flit.packet_type
            resource.type_flits[
                packet_type if 0 <= packet_type < 3 else 3
            ] += 1
            if flit.last:
                resource.packets += 1
        initialization = (
            profile.init_fixed_cycles +
            self.geometry.total_blocks * profile.init_cycles_per_block
        )

        core_schedule = cores_only if cores_only is not None else (
            hybrid.core if hybrid is not None else None
        )
        h0_queues = core_schedule.h0 if core_schedule is not None else compiled.h0
        # The baseline has one destination-side MVM datapath per spin core.
        h0_engines = (
            self.geometry.cores_per_h0 if core_schedule is not None
            else self.config.h0_mvm_count
        )
        h0_done = (
            self._cores_only_completions(h0_queues, profile.h0_block_cycles)
            if core_schedule is not None else
            self._resource_completions(
                h0_queues, h0_engines, profile.h0_block_cycles
            )
        )
        if hybrid is not None:
            h0_done.extend(self._resource_completions(
                compiled.h0, self.config.h0_mvm_count,
                profile.h0_block_cycles,
            ))
        h1_done = self._resource_completions(
            ([] for _ in compiled.h1) if cores_only is not None else compiled.h1,
            self.config.h1_mvm_count, profile.h1_block_cycles
        )
        cross_done: list[tuple[int, int, int]] = []
        for owner, queue in enumerate(
            ([] for _ in compiled.cross) if cores_only is not None else compiled.cross
        ):
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
        state_publications = (
            cores_only.state_publications if cores_only is not None else (
                tuple(sorted(set(hybrid.core.state_publications)
                             | set(compiled.publications())))
                if hybrid is not None else compiled.publications()
            )
        )
        for index, (block, owner) in enumerate(state_publications):
            releases.append(PacketRelease(
                # H0 state first traverses the fully-pipelined package-local
                # chiplet link to the H1/top endpoint. The launch interval is
                # unchanged because this parameter represents latency only.
                cycle=chiplet_latency + index * 2,
                source=block // self.geometry.blocks_per_h1,
                destination=owner, packet_type=NOC_STATE, block_id=block,
            ))
        publication = EventCompressedMesh(
            self.geometry.mesh_x,
            self.geometry.mesh_y,
            self.config.fifo_depth,
            self.config.interconnect,
        ).replay(
            releases,
            drain_cycles=4,
            resource_observer=observe_resource,
        )

        # H0-to-H1 state snapshotting runs concurrently with publication. Add
        # only the incremental startup wait that is not hidden by that phase;
        # the calibrated zero-latency behavior remains unchanged.
        publication_shift = chiplet_latency if releases else 0
        baseline_compute_start = (
            4 + publication.elapsed_cycles - publication_shift
        )
        projected_compute_start = 4 + publication.elapsed_cycles
        baseline_h1_wait = max(
            0,
            self.geometry.blocks_per_h1 + 2 - baseline_compute_start,
        )
        projected_h1_wait = max(
            0,
            self.geometry.blocks_per_h1 + 2 + chiplet_latency
            - projected_compute_start,
        )
        h1_start_delta = max(0, projected_h1_wait - baseline_h1_wait)
        if h1_start_delta:
            h1_done = [cycle + h1_start_delta for cycle in h1_done]

        partial_releases = [
            PacketRelease(
                cycle=completion, source=owner,
                destination=block // self.geometry.blocks_per_h1,
                packet_type=NOC_PARTIAL, block_id=block, flits=4,
            )
            for completion, owner, block in cross_done
        ]
        partial = EventCompressedMesh(
            self.geometry.mesh_x,
            self.geometry.mesh_y,
            self.config.fifo_depth,
            self.config.interconnect,
        ).replay(
            partial_releases,
            drain_cycles=4,
            resource_observer=observe_resource,
        )
        # H1-local and parent/cross partials share the final H1-to-H0 chiplet
        # crossing. A latency-only link shifts the tail without changing the
        # four-flit stream's initiation interval.
        h1_or_parent_tail = max(
            [partial.end_cycle, *h1_done, 0]
        ) + chiplet_latency
        completion_start = max([h1_or_parent_tail, *h0_done, 0])
        completions = [
            PacketRelease(
                cycle=completion_start, source=node, destination=node,
                packet_type=NOC_EPOCH_DONE,
            )
            for node in range(self.geometry.node_count)
        ]
        done_network = EventCompressedMesh(
            self.geometry.mesh_x,
            self.geometry.mesh_y,
            self.config.fifo_depth,
            self.config.interconnect,
        ).replay(
            completions,
            start_cycle=completion_start,
            drain_cycles=4,
            resource_observer=observe_resource,
        )

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
            tuple(map(tuple, smoke_schedule.cross)) and
            cores_only is None
        )
        certified = (
            self.geometry == Geometry(2, 1, 2, 2) and
            self.config.h0_mvm_count == self.config.h1_mvm_count ==
            self.config.cross_mvm_count == 1 and
            profile == EventTimingProfile() and
            chiplet_latency == 0 and
            self.config.interconnect.is_rtl_direct and
            set(self.dataset.active_block_pairs()) == smoke_pairs and
            schedule_matches_smoke and
            iterations == 1
        )
        if certified:
            accuracy = "rtl-differential"
        elif (chiplet_latency != 0 or
              not self.config.interconnect.is_rtl_direct):
            accuracy = "parameterized-interconnect-projection"
        else:
            accuracy = "calibrated-extrapolation"

        noc_resources = tuple(
            EventNocResourceStats(
                scope=scope,
                node=node,
                x=node % self.geometry.mesh_x,
                y=node // self.geometry.mesh_x,
                direction=direction,
                offered_cycles=values.offered_cycles,
                accepted_flits=values.accepted_flits,
                stall_cycles=values.stall_cycles,
                packets=values.packets,
                state_flits=values.type_flits[0],
                partial_flits=values.type_flits[1],
                epoch_done_flits=values.type_flits[2],
                other_flits=values.type_flits[3],
            )
            for (scope, node, direction), values in sorted(
                noc_accumulators.items()
            )
        )
        hop_counts: Counter[int] = Counter()
        for release in (*releases, *partial_releases, *completions):
            source_x = release.source % self.geometry.mesh_x
            source_y = release.source // self.geometry.mesh_x
            destination_x = release.destination % self.geometry.mesh_x
            destination_y = release.destination // self.geometry.mesh_x
            hops = abs(source_x - destination_x) + abs(
                source_y - destination_y
            )
            hop_counts[hops] += release.flits
        average_hops = (
            sum(hops * flits for hops, flits in hop_counts.items())
            / sum(hop_counts.values())
            if hop_counts else 0.0
        )

        publications = state_publications
        sent = Counter(
            block // self.geometry.blocks_per_h1
            for block, _ in publications
        )
        received = Counter(owner for _, owner in publications)
        nodes = tuple(
            EventNodeStats(
                node=node,
                x=node % self.geometry.mesh_x,
                y=node // self.geometry.mesh_x,
                h0_jobs=(sum(
                    len(h0_queues[node * self.geometry.h0_per_h1 + h0])
                    for h0 in range(self.geometry.h0_per_h1)
                ) + (sum(
                    len(compiled.h0[node * self.geometry.h0_per_h1 + h0])
                    for h0 in range(self.geometry.h0_per_h1)
                ) if hybrid is not None else 0)),
                h1_jobs=(0 if cores_only is not None else len(compiled.h1[node])),
                cross_jobs=(0 if cores_only is not None else len(compiled.cross[node])),
                total_jobs=(
                    sum(
                        len(h0_queues[
                            node * self.geometry.h0_per_h1 + h0
                        ])
                        for h0 in range(self.geometry.h0_per_h1)
                    )
                    + (sum(len(compiled.h0[
                        node * self.geometry.h0_per_h1 + h0
                    ]) for h0 in range(self.geometry.h0_per_h1))
                       if hybrid is not None else 0)
                    + (0 if cores_only is not None else len(compiled.h1[node]))
                    + (0 if cores_only is not None else len(compiled.cross[node]))
                ),
                state_publications_sent=sent[node],
                state_publications_received=received[node],
            )
            for node in range(self.geometry.node_count)
        )

        work_resources: list[EventWorkResourceStats] = []
        for global_h0, queue in enumerate(h0_queues):
            jobs = len(queue)
            engines = h0_engines
            if core_schedule is not None:
                base = global_h0 * self.geometry.cores_per_h0
                jobs_by_core = Counter(work.block_a - base for work in queue)
                maximum = max(jobs_by_core.values(), default=0)
            else:
                maximum = (jobs + engines - 1) // engines
            work_resources.append(EventWorkResourceStats(
                "h0",
                global_h0 // self.geometry.h0_per_h1,
                global_h0 % self.geometry.h0_per_h1,
                engines,
                jobs,
                maximum,
                profile.h0_block_cycles,
                maximum * profile.h0_block_cycles,
                jobs * 32,
            ))
        if hybrid is not None:
            for global_h0, queue in enumerate(compiled.h0):
                jobs = len(queue)
                engines = self.config.h0_mvm_count
                maximum = (jobs + engines - 1) // engines
                work_resources.append(EventWorkResourceStats(
                    "h0-cir", global_h0 // self.geometry.h0_per_h1,
                    global_h0 % self.geometry.h0_per_h1, engines, jobs,
                    maximum, profile.h0_block_cycles,
                    maximum * profile.h0_block_cycles, jobs * 32,
                ))
        hierarchy_resources = () if cores_only is not None else (
            ("h1", compiled.h1, self.config.h1_mvm_count,
             profile.h1_block_cycles),
            ("cross", compiled.cross, self.config.cross_mvm_count,
             profile.cross_block_cycles),
        )
        for level, queues, engines, duration in hierarchy_resources:
            for node, queue in enumerate(queues):
                jobs = len(queue)
                maximum = (jobs + engines - 1) // engines
                work_resources.append(EventWorkResourceStats(
                    level,
                    node,
                    0,
                    engines,
                    jobs,
                    maximum,
                    duration,
                    maximum * duration,
                    jobs * 32,
                ))
        result_counts = (
            (cores_only.directed_jobs, 0, 0) if cores_only is not None else (
                (hybrid.directed_core_jobs + compiled.counts[0],
                 compiled.counts[1], compiled.counts[2])
                if hybrid is not None else compiled.counts
            )
        )
        return EventPerformanceResult(
            accuracy,
            profile.calibrated_name,
            initialization, one_iteration, total,
            *result_counts,
            sum(item.active_cycles for item in networks),
            sum(item.skipped_cycles for item in networks),
            sum(item.injected_flits for item in networks),
            sum(item.ejected_flits for item in networks),
            sum(item.physical_link_flits for item in networks),
            sum(item.injection_stalls for item in networks),
            sum(item.link_stalls for item in networks),
            average_hops,
            tuple(sorted(hop_counts.items())),
            noc_resources,
            nodes,
            tuple(work_resources),
            chiplet_latency,
            self.config.interconnect,
            self.config.execution_mode,
            len(schedule),
            (cores_only.directed_jobs if cores_only is not None else
             hybrid.directed_core_jobs if hybrid is not None else 0),
            (cores_only.directed_jobs if cores_only is not None else
             hybrid.directed_core_jobs + len(hybrid.cir_pairs)
             if hybrid is not None else len(schedule)),
            (2 * len(schedule) if cores_only is not None else
             2 * len(hybrid.core_pairs) + len(hybrid.cir_pairs)
             if hybrid is not None else len(schedule)),
            len(state_publications),
        )
