"""Accelerated timing-only model using the exact Ramulator/RTL components.

The ordinary full-system model instantiates and ticks every spin core, including
arithmetic state that cannot affect timing once ``timing_only`` is selected.
This module keeps the timing-visible path instead: the real Ramulator2 backend,
``DramWeightStreamer``, ``HierarchyNode`` control/double buffers, hierarchy
arbiters, cross-H1 serialization, and the differentially checked FlooNoC model.

Initialization and already-drained network gaps are advanced in bulk. Active
memory, endpoint, and network cycles are still executed one accelerator edge at
a time, so pipeline initiation intervals are consequences of the model rather
than calibrated constants.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from .adapters import (
    H0Adapter, H1ChildAdapter, NOC_EPOCH_DONE, NOC_PARTIAL, RoutedPartial,
)
from .cross import CrossH1Node, LocalInjectionArbiter
from .events import EventCompressedMesh, PacketRelease
from .hierarchy import HierarchyNode
from .memory import DramWeightStreamer, MemoryResponse
from .noc import (
    EAST, LOCAL, NORTH, SOUTH, WEST, Flit, InterconnectConfig, Mesh,
)
from .performance import PerformanceConfig, PerformanceCounters
from .ramulator import RamulatorBackend
from .scheduler import (
    CORES_ONLY, HYBRID, CROSS, H0, H1, ConcurrentDispatcher, DispatchPort,
    WorkTarget, compile_cores_only_schedule, compile_static_hybrid_schedule,
    compile_schedule,
)
from .workload import BlockOccupancyDataset, Geometry, IsingDataset, ScheduledBlock


Transfer = tuple[int, str, int, str, int, int, int, int, int, int, int]


# The ``rtl-differential`` label is evidence about this exact memory timing
# configuration, not merely about a matching accelerator geometry.  A changed
# YAML remains runnable, but must be revalidated before receiving that label.
_CERTIFIED_RAMULATOR_CONFIG_SHA256 = (
    "36d1d3d56b229468a029eae8c324491d51720977a2ab9a08360d5b9e6f130960"
)


def _file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class _Frontend:
    system_id: int
    streamer: DramWeightStreamer
    request_ready: list[bool]
    responses: list[MemoryResponse | None]


@dataclass(slots=True)
class _NocAccumulator:
    offered_cycles: int = 0
    accepted_flits: int = 0
    stall_cycles: int = 0
    packets: int = 0
    type_flits: list[int] = field(default_factory=lambda: [0, 0, 0, 0])


@dataclass(frozen=True, slots=True)
class NocResourceStats:
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
        return self.accepted_flits / self.offered_cycles if self.offered_cycles else 0.0

    @property
    def backpressure(self) -> float:
        return self.stall_cycles / self.offered_cycles if self.offered_cycles else 0.0


@dataclass(frozen=True, slots=True)
class NodePerformanceStats:
    node: int
    x: int
    y: int
    h0_jobs: int
    h1_jobs: int
    cross_jobs: int
    total_jobs: int
    state_publications_sent: int
    state_publications_received: int
    h1_completion_cycle: int
    cross_completion_cycle: int
    completion_cycle: int


@dataclass(frozen=True, slots=True)
class DramPerformanceStats:
    system_id: int
    level: str
    node: int
    index: int
    engines: int
    scheduled_jobs: int
    accepted_requests: int
    rejected_requests: int
    completed_requests: int
    outstanding_requests: int
    average_latency_ticks: float
    maximum_latency_ticks: int
    average_latency_cycles: float
    maximum_latency_cycles: float


@dataclass(frozen=True, slots=True)
class ExactEventResult:
    accuracy: str
    initialization_cycles: int
    iteration_cycles: int
    total_cycles: int
    scheduled_h0: int
    scheduled_h1: int
    scheduled_cross: int
    counters: PerformanceCounters
    average_hops: float
    hop_histogram: tuple[tuple[int, int], ...]
    noc_resources: tuple[NocResourceStats, ...]
    nodes: tuple[NodePerformanceStats, ...]
    dram: tuple[DramPerformanceStats, ...]
    chiplet_link_latency_cycles: int = 0
    interconnect: InterconnectConfig = InterconnectConfig()
    execution_mode: str = "cir"
    unordered_interaction_blocks: int = 0
    directed_core_jobs: int = 0
    weight_block_reads: int = 0
    logical_weight_blocks_stored: int = 0
    remote_state_packets: int = 0
    core_weight_buffers: int = 0


class RamulatorEventPerformanceModel:
    """Timing-only full iteration without per-spin arithmetic objects.

    Memory is not replaced by a latency table: every request is accepted,
    scheduled, and completed by the same Ramulator2 library/configuration used
    by the RTL testbench. The hierarchy nodes and mesh are the existing Python
    cycle models that passed their RTL differential traces.
    """

    def __init__(
        self,
        geometry: Geometry,
        dataset: IsingDataset | BlockOccupancyDataset,
        *,
        dataset_path: str,
        ramulator_library: str,
        ramulator_config: str,
        mem_lanes: int = 16,
        ticks_per_cycle: int = 40,
        idle_tick_modulus: int | None = None,
        config: PerformanceConfig | None = None,
    ):
        self.geometry = geometry
        self.dataset = dataset
        self.dataset_path = dataset_path
        self.mem_lanes = mem_lanes
        self.ticks_per_cycle = ticks_per_cycle
        self.idle_tick_modulus = idle_tick_modulus
        self.ramulator_config_sha256 = _file_sha256(ramulator_config)
        self.config = config or PerformanceConfig(timing_only=True)
        if dataset.spin_count != geometry.spin_count:
            raise ValueError(
                f"dataset has {dataset.spin_count} spins; geometry has "
                f"{geometry.spin_count}"
            )
        if not self.config.timing_only:
            raise ValueError("Ramulator event model is timing-only")

        total_h0 = geometry.node_count * geometry.h0_per_h1
        system_count = total_h0 + 2 * geometry.node_count
        self.backend = RamulatorBackend(
            ramulator_library,
            ramulator_config,
            dataset_path,
            system_count,
            geometry.total_blocks,
            timing_only=True,
        )
        self.frontends: dict[WorkTarget, _Frontend] = {}
        self.local_nodes: dict[WorkTarget, HierarchyNode] = {}
        self.h0_adapters: dict[WorkTarget, H0Adapter] = {}
        self.h1_adapters: list[H1ChildAdapter] = []
        self.cross_nodes: list[CrossH1Node] = []

        for global_h0 in range(total_h0):
            node = global_h0 // geometry.h0_per_h1
            h0 = global_h0 % geometry.h0_per_h1
            target = WorkTarget(H0, node, h0)
            self.frontends[target] = self._frontend(
                global_h0, self.config.h0_mvm_count
            )
            self.local_nodes[target] = HierarchyNode(
                geometry.cores_per_h0,
                self.config.h0_mvm_count,
                timing_only=True,
            )
            self.h0_adapters[target] = H0Adapter(
                geometry.cores_per_h0,
                self.config.h0_mvm_count,
                node * geometry.blocks_per_h1 + h0 * geometry.cores_per_h0,
            )

        for node in range(geometry.node_count):
            h1_target = WorkTarget(H1, node, 0)
            cross_target = WorkTarget(CROSS, node, 0)
            self.frontends[h1_target] = self._frontend(
                total_h0 + node, self.config.h1_mvm_count
            )
            self.local_nodes[h1_target] = HierarchyNode(
                geometry.blocks_per_h1,
                self.config.h1_mvm_count,
                timing_only=True,
            )
            self.h1_adapters.append(H1ChildAdapter(
                geometry.h0_per_h1,
                geometry.cores_per_h0,
                self.config.h1_mvm_count,
                node * geometry.blocks_per_h1,
            ))
            cross = CrossH1Node(
                state_entry_count=geometry.total_blocks,
                mvm_count=self.config.cross_mvm_count,
                blocks_per_h1=geometry.blocks_per_h1,
                mesh_x_count=geometry.mesh_x,
                node_id=node,
                timing_only=True,
            )
            self.cross_nodes.append(cross)
            self.frontends[cross_target] = self._frontend(
                total_h0 + geometry.node_count + node,
                self.config.cross_mvm_count,
            )

        self.mesh = Mesh(
            geometry.mesh_x,
            geometry.mesh_y,
            self.config.fifo_depth,
            self.config.interconnect,
        )
        self.injection_arbiters = [
            LocalInjectionArbiter() for _ in range(geometry.node_count)
        ]
        self.transfers: list[Transfer] = []
        self._noc_resources: dict[
            tuple[str, int, str], _NocAccumulator
        ] = {}
        self._hop_histogram: Counter[int] = Counter()

    def _frontend(self, system_id: int, engines: int) -> _Frontend:
        return _Frontend(
            system_id=system_id,
            streamer=DramWeightStreamer(
                mvm_count=engines,
                total_block_count=self.geometry.total_blocks,
                request_lanes=self.mem_lanes,
                response_lanes=self.mem_lanes,
            ),
            request_ready=[False] * self.mem_lanes,
            responses=[None] * self.mem_lanes,
        )

    def close(self) -> None:
        self.backend.finalize()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def _reset_timing_state(self) -> None:
        self.mesh.reset()
        for arbiter in self.injection_arbiters:
            arbiter.reset()
        for adapter in self.h0_adapters.values():
            adapter.reset()
        for adapter in self.h1_adapters:
            adapter.reset()
        for frontend in self.frontends.values():
            frontend.streamer.reset()
            frontend.request_ready = [False] * self.mem_lanes
            frontend.responses = [None] * self.mem_lanes
        for node in self.local_nodes.values():
            node.reset()
            # The full model has long since left NODE_RESET before an
            # iteration starts. State payloads do not affect timing-only work.
            node.node_state = node.IDLE
        for cross in self.cross_nodes:
            cross.reset()
            cross.node.node_state = cross.node.IDLE
        self._noc_resources = {}
        self._hop_histogram = Counter()
        for node, router in enumerate(self.mesh.routers):
            self._noc_resources[("inject", node, "local")] = _NocAccumulator()
            self._noc_resources[("eject", node, "local")] = _NocAccumulator()
            for direction, nx, ny in (
                ("north", router.x, router.y - 1),
                ("south", router.x, router.y + 1),
                ("east", router.x + 1, router.y),
                ("west", router.x - 1, router.y),
            ):
                if 0 <= nx < self.geometry.mesh_x and 0 <= ny < self.geometry.mesh_y:
                    self._noc_resources[("link", node, direction)] = _NocAccumulator()

    def _observe_noc_resource(
        self,
        cycle: int,
        scope: str,
        node: int,
        direction: str,
        flit: Flit,
        accepted: bool,
    ) -> None:
        del cycle
        resource = self._noc_resources.setdefault(
            (scope, node, direction), _NocAccumulator()
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
        if scope == "inject":
            source_x = node % self.geometry.mesh_x
            source_y = node // self.geometry.mesh_x
            hops = abs(source_x - flit.dest_x) + abs(source_y - flit.dest_y)
            self._hop_histogram[hops] += 1

    def _record_network(
        self,
        comb,
        injections: dict[int, Flit],
        local_ready: dict[int, bool],
        counters: PerformanceCounters,
        absolute_cycle: int,
    ) -> None:
        for node, flit in injections.items():
            accepted = comb[node].input_ready[LOCAL]
            self._observe_noc_resource(
                absolute_cycle, "inject", node, "local", flit, accepted
            )
            if accepted:
                counters.injected_flits += 1
                packet_type = flit.packet_type
                counters.type_flits[
                    packet_type if 0 <= packet_type < 3 else 3
                ] += 1
                self.transfers.append(self._transfer(
                    absolute_cycle, "inject", node, "local", flit
                ))
            else:
                counters.injection_stalls += 1

        for node, output in enumerate(comb):
            if output.output_flits[LOCAL] is not None:
                flit = output.output_flits[LOCAL]
                accepted = local_ready[node]
                self._observe_noc_resource(
                    absolute_cycle, "eject", node, "local", flit, accepted
                )
                if accepted:
                    counters.ejected_flits += 1
                    self.transfers.append(self._transfer(
                        absolute_cycle, "eject", node, "local",
                        flit,
                    ))
                else:
                    counters.ejection_stalls += 1
            router = self.mesh.routers[node]
            for port, nx, ny, neighbor_input in (
                (NORTH, router.x, router.y - 1, SOUTH),
                (SOUTH, router.x, router.y + 1, NORTH),
                (EAST, router.x + 1, router.y, WEST),
                (WEST, router.x - 1, router.y, EAST),
            ):
                flit = output.output_flits[port]
                if flit is None:
                    continue
                ready = self.mesh.link_ready(node, port, comb)
                direction = {
                    NORTH: "north", SOUTH: "south",
                    EAST: "east", WEST: "west",
                }[port]
                self._observe_noc_resource(
                    absolute_cycle, "link", node, direction, flit, ready
                )
                if ready:
                    counters.physical_link_flits += 1
                    self.transfers.append(self._transfer(
                        absolute_cycle,
                        "link",
                        node,
                        direction,
                        flit,
                    ))
                else:
                    counters.link_stalls += 1

    @staticmethod
    def _transfer(cycle: int, scope: str, node: int, direction: str,
                  flit: Flit) -> Transfer:
        return (
            cycle, scope, node, direction, flit.packet_type,
            flit.source_id, flit.epoch, flit.block_id,
            flit.dest_x, flit.dest_y, int(flit.last),
        )

    def _memory_edge(self) -> None:
        self.backend.tick(self.ticks_per_cycle)
        for frontend in self.frontends.values():
            outputs = frontend.streamer.outputs()
            frontend.request_ready = [
                request is not None and self.backend.send(
                    frontend.system_id, request.address, request.tag
                )
                for request in outputs.memory_requests
            ]
            frontend.responses = [
                self.backend.pop(frontend.system_id)
                for _ in range(self.mem_lanes)
            ]

    def _noc_stats(self) -> tuple[NocResourceStats, ...]:
        rows = []
        for (scope, node, direction), values in sorted(
            self._noc_resources.items()
        ):
            rows.append(NocResourceStats(
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
            ))
        return tuple(rows)

    def _dram_stats(
        self, compiled
    ) -> tuple[DramPerformanceStats, ...]:
        rows = []
        for target, frontend in sorted(
            self.frontends.items(), key=lambda item: item[1].system_id
        ):
            if target.level == H0:
                queue = compiled.h0[
                    target.node * self.geometry.h0_per_h1 + target.index
                ]
            elif target.level == H1:
                queue = compiled.h1[target.node]
            else:
                queue = compiled.cross[target.node]
            raw = self.backend.stats(frontend.system_id)
            expected_requests = len(queue) * 32
            if (
                raw.accepted != expected_requests
                or raw.completed != expected_requests
                or raw.outstanding != 0
            ):
                raise RuntimeError(
                    f"DRAM system {frontend.system_id} did not drain: "
                    f"expected={expected_requests} accepted={raw.accepted} "
                    f"completed={raw.completed} outstanding={raw.outstanding}"
                )
            rows.append(DramPerformanceStats(
                system_id=frontend.system_id,
                level=target.level,
                node=target.node,
                index=target.index,
                engines=len(frontend.streamer.slots),
                scheduled_jobs=len(queue),
                accepted_requests=raw.accepted,
                rejected_requests=raw.rejected,
                completed_requests=raw.completed,
                outstanding_requests=raw.outstanding,
                average_latency_ticks=raw.average_latency_ticks,
                maximum_latency_ticks=raw.latency_max_ticks,
                average_latency_cycles=(
                    raw.average_latency_ticks / self.ticks_per_cycle
                ),
                maximum_latency_cycles=(
                    raw.latency_max_ticks / self.ticks_per_cycle
                ),
            ))
        return tuple(rows)

    @staticmethod
    def _validate_metric_totals(
        counters: PerformanceCounters,
        resources: tuple[NocResourceStats, ...],
        hop_histogram: tuple[tuple[int, int], ...],
    ) -> None:
        accepted = {
            scope: sum(
                resource.accepted_flits
                for resource in resources if resource.scope == scope
            )
            for scope in ("inject", "eject", "link")
        }
        stalls = {
            scope: sum(
                resource.stall_cycles
                for resource in resources if resource.scope == scope
            )
            for scope in ("inject", "eject", "link")
        }
        expected_accepted = {
            "inject": counters.injected_flits,
            "eject": counters.ejected_flits,
            "link": counters.physical_link_flits,
        }
        expected_stalls = {
            "inject": counters.injection_stalls,
            "eject": counters.ejection_stalls,
            "link": counters.link_stalls,
        }
        if accepted != expected_accepted or stalls != expected_stalls:
            raise RuntimeError(
                "NoC resource counters do not match aggregate counters: "
                f"accepted={accepted}/{expected_accepted} "
                f"stalls={stalls}/{expected_stalls}"
            )
        hop_flits = sum(hops * flits for hops, flits in hop_histogram)
        if hop_flits != counters.physical_link_flits:
            raise RuntimeError(
                "hop histogram does not match physical-link traffic: "
                f"hops={hop_flits} links={counters.physical_link_flits}"
            )

    @staticmethod
    def _validate_cores_only_dram(system_id: int, expected: int, raw) -> None:
        if raw.accepted != expected or raw.completed != expected or raw.outstanding:
            raise RuntimeError(
                f"cores-only DRAM conservation failed for H0 {system_id}: "
                f"expected={expected} accepted={raw.accepted} "
                f"completed={raw.completed} outstanding={raw.outstanding}"
            )

    @staticmethod
    def _advance_cores_only_core(slots: list[dict[str, object]]) -> bool:
        """Advance the ordered compute head; return whether it completed."""

        state = slots[0]
        if int(state["received"]) == 32 and int(state["compute"]) < 0:
            state["compute"] = 32
        remaining = int(state["compute"])
        if remaining <= 0:
            return False
        remaining -= 1
        state["compute"] = remaining
        return remaining == 0

    def _run_cores_only(
        self, schedule_records: list[ScheduledBlock], initialization: int,
    ) -> ExactEventResult:
        """Exact DRAM timing for destination-stationary core computation.

        Each H0 retains one Ramulator system. Its cores independently serialize
        their directed jobs, while all request streams contend at that shared
        endpoint. A core starts arithmetic only after all 32 weights arrive;
        arithmetic occupies that core for 32 cycles. Remote states are cached
        once per source block and consuming H1 before computation starts.
        """

        compiled = compile_cores_only_schedule(self.geometry, schedule_records)
        chiplet_latency = self.config.chiplet_link_latency_cycles
        publications = [
            PacketRelease(
                cycle=chiplet_latency + index * 2,
                source=block // self.geometry.blocks_per_h1,
                destination=destination,
                packet_type=0,
                block_id=block,
            )
            for index, (block, destination) in enumerate(
                compiled.state_publications
            )
        ]

        trace_offset = initialization + 2

        def observe(cycle, scope, node, direction, flit):
            self.transfers.append(self._transfer(
                trace_offset + cycle, scope, node, direction, flit,
            ))

        def observe_resource(cycle, scope, node, direction, flit, accepted):
            self._observe_noc_resource(
                initialization + 2 + cycle,
                scope, node, direction, flit, accepted,
            )

        publication = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.config.fifo_depth, self.config.interconnect,
        ).replay(
            publications, drain_cycles=4, observer=observe,
            resource_observer=observe_resource,
        )
        counters = PerformanceCounters(
            injected_flits=publication.injected_flits,
            ejected_flits=publication.ejected_flits,
            physical_link_flits=publication.physical_link_flits,
            injection_stalls=publication.injection_stalls,
            link_stalls=publication.link_stalls,
        )
        counters.type_flits[0] = publication.injected_flits
        compute_start = 2 + publication.elapsed_cycles + 2
        self.backend.tick((initialization + compute_start) * self.ticks_per_cycle)

        # One FIFO per destination core.  Every H0 shares one memory system.
        core_queues: list[list[deque]] = []
        for global_h0, queue in enumerate(compiled.h0):
            base = global_h0 * self.geometry.cores_per_h0
            per_core = [deque() for _ in range(self.geometry.cores_per_h0)]
            for work in queue:
                per_core[work.block_a - base].append(work)
            core_queues.append(per_core)
        # Two slots per core mirror the CIR streamer's double buffering.  The
        # tail may fetch while the head computes, but retirement remains FIFO.
        active: dict[tuple[int, int], list[dict[str, object]]] = {}
        next_tag = 1
        tags: dict[int, dict[str, object]] = {}
        cycle = 0
        while True:
            for global_h0, per_core in enumerate(core_queues):
                for core, queue in enumerate(per_core):
                    key = (global_h0, core)
                    slots = active.setdefault(key, [])
                    while len(slots) < 2 and queue:
                        slots.append({
                            "work": queue.popleft(), "sent": 0,
                            "received": 0, "compute": -1,
                        })
                    if not slots:
                        active.pop(key, None)

            # Match the RTL frontend width: at most mem_lanes offers per H0
            # and cycle. Rejected offers remain at the same beat for retry.
            for global_h0 in range(len(core_queues)):
                offers = 0
                for core in range(self.geometry.cores_per_h0):
                    if offers >= self.mem_lanes:
                        break
                    for state in active.get((global_h0, core), []):
                        if offers >= self.mem_lanes:
                            break
                        beat = int(state["sent"])
                        if beat >= 32:
                            continue
                        work = state["work"]
                        address = (
                            (work.block_a * self.geometry.total_blocks
                             + work.block_b) * 1024
                            + beat * 32
                        )
                        tag = next_tag
                        offers += 1
                        if self.backend.send(global_h0, address, tag):
                            tags[tag] = state
                            next_tag += 1
                            state["sent"] = beat + 1

            self.backend.tick(self.ticks_per_cycle)
            for global_h0 in range(len(core_queues)):
                for _ in range(self.mem_lanes):
                    response = self.backend.pop(global_h0)
                    if response is None:
                        break
                    state = tags.pop(response.tag)
                    state["received"] = int(state["received"]) + 1

            completed = []
            for key, slots in active.items():
                if self._advance_cores_only_core(slots):
                    completed.append(key)
            for key in completed:
                active[key].pop(0)
                if not active[key]:
                    del active[key]
            cycle += 1
            if cycle > self.config.max_cycles:
                raise TimeoutError("cores-only exact-event simulation timed out")
            if not active and all(
                not queue for per_core in core_queues for queue in per_core
            ) and not tags:
                break

        completion_start = cycle
        done_packets = [
            PacketRelease(
                cycle=completion_start, source=node, destination=node,
                packet_type=NOC_EPOCH_DONE,
            )
            for node in range(self.geometry.node_count)
        ]
        done = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.config.fifo_depth, self.config.interconnect,
        ).replay(
            done_packets, start_cycle=completion_start, drain_cycles=4,
            observer=observe, resource_observer=observe_resource,
        )
        counters.injected_flits += done.injected_flits
        counters.ejected_flits += done.ejected_flits
        counters.physical_link_flits += done.physical_link_flits
        counters.injection_stalls += done.injection_stalls
        counters.link_stalls += done.link_stalls
        counters.type_flits[2] += done.injected_flits
        iteration_cycles = compute_start + done.end_cycle + 9
        counters.cycles = iteration_cycles

        dram_rows = []
        for global_h0, queue in enumerate(compiled.h0):
            raw = self.backend.stats(global_h0)
            expected = len(queue) * 32
            self._validate_cores_only_dram(global_h0, expected, raw)
            dram_rows.append(DramPerformanceStats(
                system_id=global_h0, level=H0,
                node=global_h0 // self.geometry.h0_per_h1,
                index=global_h0 % self.geometry.h0_per_h1,
                engines=self.geometry.cores_per_h0,
                scheduled_jobs=len(queue), accepted_requests=raw.accepted,
                rejected_requests=raw.rejected,
                completed_requests=raw.completed,
                outstanding_requests=raw.outstanding,
                average_latency_ticks=raw.average_latency_ticks,
                maximum_latency_ticks=raw.latency_max_ticks,
                average_latency_cycles=raw.average_latency_ticks / self.ticks_per_cycle,
                maximum_latency_cycles=raw.latency_max_ticks / self.ticks_per_cycle,
            ))
        sent = Counter(
            block // self.geometry.blocks_per_h1
            for block, _ in compiled.state_publications
        )
        received = Counter(destination for _, destination in compiled.state_publications)
        nodes = tuple(NodePerformanceStats(
            node=node, x=node % self.geometry.mesh_x,
            y=node // self.geometry.mesh_x,
            h0_jobs=sum(len(compiled.h0[
                node * self.geometry.h0_per_h1 + h0
            ]) for h0 in range(self.geometry.h0_per_h1)),
            h1_jobs=0, cross_jobs=0,
            total_jobs=sum(len(compiled.h0[
                node * self.geometry.h0_per_h1 + h0
            ]) for h0 in range(self.geometry.h0_per_h1)),
            state_publications_sent=sent[node],
            state_publications_received=received[node],
            h1_completion_cycle=0, cross_completion_cycle=0,
            completion_cycle=initialization + iteration_cycles,
        ) for node in range(self.geometry.node_count))
        resources = self._noc_stats()
        hop_histogram = tuple(sorted(self._hop_histogram.items()))
        self._validate_metric_totals(counters, resources, hop_histogram)
        return ExactEventResult(
            accuracy=("parameterized-interconnect-projection" if (
                chiplet_latency or not self.config.interconnect.is_rtl_direct
            ) else "cycle-structured-unverified"),
            initialization_cycles=initialization,
            iteration_cycles=iteration_cycles,
            total_cycles=initialization + iteration_cycles,
            scheduled_h0=compiled.directed_jobs,
            scheduled_h1=0, scheduled_cross=0, counters=counters,
            average_hops=(counters.physical_link_flits / counters.injected_flits
                          if counters.injected_flits else 0.0),
            hop_histogram=hop_histogram, noc_resources=resources,
            nodes=nodes, dram=tuple(dram_rows),
            chiplet_link_latency_cycles=chiplet_latency,
            interconnect=self.config.interconnect,
            execution_mode=CORES_ONLY,
            unordered_interaction_blocks=len(schedule_records),
            directed_core_jobs=compiled.directed_jobs,
            weight_block_reads=compiled.directed_jobs,
            logical_weight_blocks_stored=2 * len(schedule_records),
            remote_state_packets=len(compiled.state_publications),
            core_weight_buffers=2,
        )

    def _run_hybrid(
        self, schedule_records: list[ScheduledBlock], initialization: int,
    ) -> ExactEventResult:
        """Run static hybrid work with shared H0 Ramulator contention.

        Core-local and H0-CIR engines share the same request lanes and memory
        system. H1 and cross-H1 engines retain their existing distinct memory
        endpoints. Every engine has two in-order weight buffers. This path is
        timing-only and is deliberately labeled unverified until matched to
        the integrated RTL hybrid datapath.
        """

        hybrid = compile_static_hybrid_schedule(
            self.geometry, schedule_records,
            h0_mvm_count=self.config.h0_mvm_count,
            h1_mvm_count=self.config.h1_mvm_count,
            cross_mvm_count=self.config.cross_mvm_count,
        )
        cir, core = hybrid.cir, hybrid.core
        publications = tuple(sorted(
            set(core.state_publications) | set(cir.publications())
        ))
        releases = [PacketRelease(
            cycle=self.config.chiplet_link_latency_cycles + index * 2,
            source=block // self.geometry.blocks_per_h1,
            destination=destination, packet_type=0, block_id=block,
        ) for index, (block, destination) in enumerate(publications)]
        trace_offset = initialization + 2

        def observe(cycle, scope, node, direction, flit):
            self.transfers.append(self._transfer(
                trace_offset + cycle, scope, node, direction, flit,
            ))

        def observe_resource(cycle, scope, node, direction, flit, accepted):
            self._observe_noc_resource(
                trace_offset + cycle, scope, node, direction,
                flit, accepted,
            )

        publication = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.config.fifo_depth, self.config.interconnect,
        ).replay(releases, drain_cycles=4, observer=observe,
                 resource_observer=observe_resource)
        compute_start = 2 + publication.elapsed_cycles + 2
        self.backend.tick((initialization + compute_start) * self.ticks_per_cycle)

        # A resource is (system id, level, node, index, engine queues).
        resources = []
        total_h0 = self.geometry.node_count * self.geometry.h0_per_h1
        for global_h0 in range(total_h0):
            base = global_h0 * self.geometry.cores_per_h0
            engines = [deque() for _ in range(self.geometry.cores_per_h0)]
            for work in core.h0[global_h0]:
                engines[work.block_a - base].append(work)
            cir_engines = [deque() for _ in range(self.config.h0_mvm_count)]
            for index, work in enumerate(cir.h0[global_h0]):
                cir_engines[index % len(cir_engines)].append(work)
            resources.append((global_h0, H0,
                global_h0 // self.geometry.h0_per_h1,
                global_h0 % self.geometry.h0_per_h1,
                engines + cir_engines, len(core.h0[global_h0]),
                len(cir.h0[global_h0])))
        for node in range(self.geometry.node_count):
            for level, queues, count, system_id in (
                (H1, cir.h1, self.config.h1_mvm_count, total_h0 + node),
                (CROSS, cir.cross, self.config.cross_mvm_count,
                 total_h0 + self.geometry.node_count + node),
            ):
                engines = [deque() for _ in range(count)]
                for index, work in enumerate(queues[node]):
                    engines[index % count].append(work)
                resources.append((system_id, level, node, 0, engines, 0,
                                  len(queues[node])))

        active: dict[tuple[int, int], list[dict[str, object]]] = {}
        tags: dict[int, dict[str, object]] = {}
        # Ramulator DPI tags are 32-bit; this standalone hybrid loop does not
        # concurrently use DramWeightStreamer tags, so a compact sequence is
        # both sufficient and portable across the C boundary.
        next_tag = 1
        completion: dict[tuple[int, int], int] = {}
        cycle = 0
        while True:
            for system_id, _level, _node, _index, engines, _core_n, _cir_n in resources:
                for engine, queue in enumerate(engines):
                    slots = active.setdefault((system_id, engine), [])
                    while len(slots) < 2 and queue:
                        slots.append({"work": queue.popleft(), "sent": 0,
                                      "received": 0, "compute": -1})
                    if not slots:
                        active.pop((system_id, engine), None)

            for system_id, _level, _node, _index, engines, _core_n, _cir_n in resources:
                offers = 0
                for engine in range(len(engines)):
                    for state in active.get((system_id, engine), []):
                        if offers >= self.mem_lanes:
                            break
                        beat = int(state["sent"])
                        if beat >= 32:
                            continue
                        work = state["work"]
                        address = ((work.block_a * self.geometry.total_blocks
                                    + work.block_b) * 1024 + beat * 32)
                        tag = next_tag
                        offers += 1
                        if self.backend.send(system_id, address, tag):
                            tags[tag] = state; next_tag += 1
                            state["sent"] = beat + 1
                    if offers >= self.mem_lanes:
                        break
            self.backend.tick(self.ticks_per_cycle)
            for system_id, *_ in resources:
                for _ in range(self.mem_lanes):
                    response = self.backend.pop(system_id)
                    if response is None:
                        break
                    state = tags.pop(response.tag)
                    state["received"] = int(state["received"]) + 1
            completed = []
            for key, slots in active.items():
                if self._advance_cores_only_core(slots):
                    completion[(key[0], id(slots[0]["work"]))] = cycle + 1
                    completed.append(key)
            for key in completed:
                active[key].pop(0)
                if not active[key]:
                    del active[key]
            cycle += 1
            if cycle > self.config.max_cycles:
                raise TimeoutError("hybrid exact-event simulation timed out")
            if not active and all(not q for *_, engines, _a, _b in resources
                                  for q in engines) and not tags:
                break

        # Cross-CIR results are the only top-level partial packets.
        partials = []
        for node, queue in enumerate(cir.cross):
            system_id = total_h0 + self.geometry.node_count + node
            for work in queue:
                done = completion[(system_id, id(work))]
                for block in (work.block_a, work.block_b):
                    partials.append(PacketRelease(
                        cycle=done, source=node,
                        destination=block // self.geometry.blocks_per_h1,
                        packet_type=NOC_PARTIAL, block_id=block, flits=4,
                    ))
        trace_offset = initialization + compute_start
        partial_net = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.config.fifo_depth, self.config.interconnect,
        ).replay(partials, drain_cycles=4, observer=observe,
                 resource_observer=observe_resource)
        completion_start = max(cycle, partial_net.end_cycle)
        done_net = EventCompressedMesh(
            self.geometry.mesh_x, self.geometry.mesh_y,
            self.config.fifo_depth, self.config.interconnect,
        ).replay([PacketRelease(completion_start, node, node, NOC_EPOCH_DONE)
                  for node in range(self.geometry.node_count)],
                 start_cycle=completion_start, drain_cycles=4,
                 observer=observe, resource_observer=observe_resource)
        iteration_cycles = compute_start + done_net.end_cycle + 9
        counters = PerformanceCounters(
            cycles=iteration_cycles,
            injected_flits=publication.injected_flits + partial_net.injected_flits
                           + done_net.injected_flits,
            ejected_flits=publication.ejected_flits + partial_net.ejected_flits
                          + done_net.ejected_flits,
            physical_link_flits=publication.physical_link_flits
                                + partial_net.physical_link_flits
                                + done_net.physical_link_flits,
            injection_stalls=publication.injection_stalls
                             + partial_net.injection_stalls
                             + done_net.injection_stalls,
            link_stalls=publication.link_stalls + partial_net.link_stalls
                        + done_net.link_stalls,
        )
        counters.type_flits[0] = publication.injected_flits
        counters.type_flits[1] = partial_net.injected_flits
        counters.type_flits[2] = done_net.injected_flits
        dram_rows = []
        for system_id, level, node, index, engines, core_n, cir_n in resources:
            raw = self.backend.stats(system_id)
            expected = (core_n + cir_n) * 32
            self._validate_cores_only_dram(system_id, expected, raw)
            dram_rows.append(DramPerformanceStats(
                system_id, level, node, index, len(engines), core_n + cir_n,
                raw.accepted, raw.rejected, raw.completed, raw.outstanding,
                raw.average_latency_ticks, raw.latency_max_ticks,
                raw.average_latency_ticks / self.ticks_per_cycle,
                raw.latency_max_ticks / self.ticks_per_cycle,
            ))
        noc = self._noc_stats(); hops = tuple(sorted(self._hop_histogram.items()))
        self._validate_metric_totals(counters, noc, hops)
        sent = Counter(b // self.geometry.blocks_per_h1 for b, _ in publications)
        received = Counter(d for _, d in publications)
        nodes = tuple(NodePerformanceStats(
            node, node % self.geometry.mesh_x, node // self.geometry.mesh_x,
            sum(len(core.h0[node*self.geometry.h0_per_h1+h])
                + len(cir.h0[node*self.geometry.h0_per_h1+h])
                for h in range(self.geometry.h0_per_h1)),
            len(cir.h1[node]), len(cir.cross[node]),
            sum(len(core.h0[node*self.geometry.h0_per_h1+h])
                + len(cir.h0[node*self.geometry.h0_per_h1+h])
                for h in range(self.geometry.h0_per_h1))
            + len(cir.h1[node]) + len(cir.cross[node]),
            sent[node], received[node], 0, 0,
            initialization + iteration_cycles,
        ) for node in range(self.geometry.node_count))
        accuracy = (
            "parameterized-interconnect-projection"
            if (self.config.chiplet_link_latency_cycles
                or not self.config.interconnect.is_rtl_direct)
            else "cycle-structured-unverified"
        )
        return ExactEventResult(
            accuracy, initialization, iteration_cycles,
            initialization + iteration_cycles,
            core.directed_jobs + cir.counts[0], cir.counts[1], cir.counts[2],
            counters, (counters.physical_link_flits / counters.injected_flits
                       if counters.injected_flits else 0.0), hops, noc, nodes,
            tuple(dram_rows), self.config.chiplet_link_latency_cycles,
            self.config.interconnect, HYBRID, len(schedule_records),
            core.directed_jobs, core.directed_jobs + len(hybrid.cir_pairs),
            2 * len(hybrid.core_pairs) + len(hybrid.cir_pairs),
            len(publications), 2,
        )

    def run(
        self,
        *,
        schedule: Iterable[ScheduledBlock] | None = None,
        sparse: bool = True,
    ) -> ExactEventResult:
        self._reset_timing_state()
        self.transfers = []
        if schedule is None:
            pairs = sorted(self.dataset.active_block_pairs()) if sparse else [
                (a, b) for a in range(self.geometry.total_blocks)
                for b in range(a + 1, self.geometry.total_blocks)
            ]
            schedule_records = [ScheduledBlock(a, b) for a, b in pairs]
        else:
            schedule_records = list(schedule)
        initialization = 3 + self.geometry.total_blocks * 33
        if self.config.execution_mode == CORES_ONLY:
            return self._run_cores_only(schedule_records, initialization)
        if self.config.execution_mode == HYBRID:
            return self._run_hybrid(schedule_records, initialization)
        compiled = compile_schedule(self.geometry, schedule_records)
        chiplet_latency = self.config.chiplet_link_latency_cycles

        publications = [
            PacketRelease(
                # Fixed, fully-pipelined H0-chiplet to top-endpoint latency.
                # Publication issue remains one packet every two RTL cycles.
                cycle=chiplet_latency + index * 2,
                source=block // self.geometry.blocks_per_h1,
                destination=owner,
                packet_type=0,
                block_id=block,
            )
            for index, (block, owner) in enumerate(compiled.publications())
        ]
        def observe_publication(cycle, scope, node, direction, flit):
            self.transfers.append(self._transfer(
                initialization + 2 + cycle,
                scope, node, direction, flit,
            ))

        def observe_publication_resource(
            cycle, scope, node, direction, flit, accepted
        ):
            self._observe_noc_resource(
                initialization + 2 + cycle,
                scope, node, direction, flit, accepted,
            )

        publication = EventCompressedMesh(
            self.geometry.mesh_x,
            self.geometry.mesh_y,
            self.config.fifo_depth,
            self.config.interconnect,
        ).replay(
            publications,
            drain_cycles=4,
            observer=observe_publication,
            resource_observer=observe_publication_resource,
        )

        counters = PerformanceCounters(
            injected_flits=publication.injected_flits,
            ejected_flits=publication.ejected_flits,
            physical_link_flits=publication.physical_link_flits,
            injection_stalls=publication.injection_stalls,
            link_stalls=publication.link_stalls,
        )
        counters.type_flits[0] = publication.injected_flits

        # Two leading iteration-control edges, followed by publication/drain,
        # then cross-node start plus the testbench's following empty edge.
        compute_start = 2 + publication.elapsed_cycles + 2

        # Ramulator is alive during initialization and publication in the RTL.
        # Advance that idle time in C++ without constructing/ticking spin cores.
        idle_ticks = (initialization + compute_start) * self.ticks_per_cycle
        if self.idle_tick_modulus is not None:
            if self.idle_tick_modulus <= 0:
                raise ValueError("idle_tick_modulus must be positive")
            idle_ticks %= self.idle_tick_modulus
        self.backend.tick(idle_ticks)

        for cross in self.cross_nodes:
            cross.node.node_state = cross.node.RUN

        local_start_pulse: dict[WorkTarget, int] = {}
        for target, node in self.local_nodes.items():
            ready_global = (
                self.geometry.cores_per_h0 + 2
                if target.level == H0 else
                self.geometry.blocks_per_h1 + 2 + chiplet_latency
            )
            ready_local = ready_global - compute_start
            if ready_local <= 0:
                node.node_state = node.RUN
            else:
                node.node_state = node.IDLE
                local_start_pulse[target] = ready_local - 1

        dispatcher = ConcurrentDispatcher(
            compiled,
            h0_mvm_count=self.config.h0_mvm_count,
            h1_mvm_count=self.config.h1_mvm_count,
            cross_mvm_count=self.config.cross_mvm_count,
        )
        phase = "blank" if dispatcher.done else "dispatch"
        quiet = 0
        done_pending: set[int] = set()
        done_received: dict[int, int] = {}
        local_done: dict[WorkTarget, int] = {}
        cross_done: dict[int, int] = {}
        done_quiet_end: int | None = None
        node_completion: dict[int, int] = {}
        cycle = 0

        while True:
            if cycle > self.config.max_cycles:
                raise TimeoutError(
                    f"event Ramulator simulation exceeded {self.config.max_cycles} cycles"
                )

            if phase == "run" and all(
                cross.outputs(0).iter_done for cross in self.cross_nodes
            ):
                phase = "cross_quiet"
                quiet = 0

            schedule_done = False
            if phase == "drain" and all(
                frontend.streamer.outputs().idle
                for frontend in self.frontends.values()
            ):
                schedule_done = True
                phase = "run"

            comb = [router.outputs() for router in self.mesh.routers]
            old_node_outputs = {
                target: (
                    self.cross_nodes[target.node].node.outputs()
                    if target.level == CROSS else self.local_nodes[target].outputs()
                )
                for target in self.frontends
            }
            parents: list[RoutedPartial] = []
            h1_adapter_outputs = []
            local_ready: dict[int, bool] = {}
            for node in range(self.geometry.node_count):
                rx = comb[node].output_flits[LOCAL]
                parent = RoutedPartial(
                    valid=bool(rx is not None and rx.packet_type == NOC_PARTIAL),
                    data=0 if rx is None else rx.data,
                    block_id=0 if rx is None else rx.block_id,
                )
                parents.append(parent)
                h1_target = WorkTarget(H1, node, 0)
                outputs = self.h1_adapters[node].outputs(
                    self.local_nodes[h1_target].outputs().partials,
                    parent,
                    [True] * self.geometry.h0_per_h1,
                )
                h1_adapter_outputs.append(outputs)
                local_ready[node] = (
                    outputs.parent_ready
                    if rx is not None and rx.packet_type == NOC_PARTIAL
                    else True
                )

            h0_adapter_outputs = {
                target: adapter.outputs(
                    self.local_nodes[target].outputs().partials,
                    [True] * self.geometry.cores_per_h0,
                )
                for target, adapter in self.h0_adapters.items()
            }
            old_local_partials = {
                target: node.outputs().partials
                for target, node in self.local_nodes.items()
            }

            h1_packets: list[Flit | None] = [None] * self.geometry.node_count
            if phase == "done_inject":
                for node in done_pending:
                    h1_packets[node] = Flit(
                        packet_type=NOC_EPOCH_DONE,
                        dest_x=node % self.geometry.mesh_x,
                        dest_y=node // self.geometry.mesh_x,
                        source_id=node,
                        last=True,
                    )

            injections: dict[int, Flit] = {}
            arbiter_outputs = []
            cross_txs: list[Flit | None] = []
            for node in range(self.geometry.node_count):
                cross_tx = self.cross_nodes[node].outputs(0).tx
                cross_txs.append(cross_tx)
                arb = self.injection_arbiters[node].outputs(
                    cross_tx, h1_packets[node], comb[node].input_ready[LOCAL]
                )
                arbiter_outputs.append(arb)
                if arb.selected is not None:
                    injections[node] = arb.selected

            activity = self.mesh.has_link_data or bool(injections) or any(
                flit is not None for output in comb for flit in output.output_flits
            )
            self._record_network(
                comb,
                injections,
                local_ready,
                counters,
                initialization + compute_start + cycle,
            )

            scheduler_commands = {}
            dispatch_finished = False
            if phase == "dispatch":
                ready = {
                    DispatchPort(target, engine): is_ready
                    for target, frontend in self.frontends.items()
                    for engine, is_ready in enumerate(
                        frontend.streamer.outputs().scheduler_ready
                    )
                }
                scheduler_commands = dispatcher.drive(ready)
                dispatch_finished = dispatcher.done

            self._memory_edge()

            for target, node in self.local_nodes.items():
                stream = self.frontends[target].streamer.outputs()
                if target.level == H0:
                    partial_ready = h0_adapter_outputs[target].engine_ready
                else:
                    partial_ready = h1_adapter_outputs[target.node].node_ready
                node.tick(
                    iter_start=local_start_pulse.get(target) == cycle,
                    schedule_done=schedule_done,
                    commands=[
                        output.command if output.command_valid else None
                        for output in stream.node
                    ],
                    weight_valid=[output.weight_valid for output in stream.node],
                    weight_data=[output.weight_data for output in stream.node],
                    partial_ready=partial_ready,
                )

            for node, cross in enumerate(self.cross_nodes):
                target = WorkTarget(CROSS, node, 0)
                stream = self.frontends[target].streamer.outputs()
                cross.tick(
                    epoch=0,
                    schedule_done=schedule_done,
                    commands=[
                        output.command if output.command_valid else None
                        for output in stream.node
                    ],
                    weight_valid=[output.weight_valid for output in stream.node],
                    weight_data=[output.weight_data for output in stream.node],
                    tx_ready=arbiter_outputs[node].cross_ready,
                )

            for target, adapter in self.h0_adapters.items():
                adapter.tick(
                    old_local_partials[target],
                    [True] * self.geometry.cores_per_h0,
                )
            for node, adapter in enumerate(self.h1_adapters):
                target = WorkTarget(H1, node, 0)
                adapter.tick(
                    old_local_partials[target],
                    parents[node],
                    [True] * self.geometry.h0_per_h1,
                )

            self.mesh.tick(injections, local_ready)
            for node, arbiter in enumerate(self.injection_arbiters):
                arbiter.tick(
                    cross_txs[node],
                    h1_packets[node],
                    comb[node].input_ready[LOCAL],
                )

            for node, output in enumerate(comb):
                rx = output.output_flits[LOCAL]
                if (
                    rx is not None
                    and local_ready[node]
                    and rx.packet_type == NOC_EPOCH_DONE
                ):
                    done_received.setdefault(node, cycle)

            for target, frontend in self.frontends.items():
                node_outputs = old_node_outputs[target]
                commands = [None] * len(frontend.streamer.slots)
                for engine in range(len(commands)):
                    commands[engine] = scheduler_commands.get(
                        DispatchPort(target, engine)
                    )
                frontend.streamer.tick(
                    scheduler_commands=commands,
                    node_command_ready=node_outputs.command_ready,
                    node_weight_ready=node_outputs.weight_ready,
                    memory_request_ready=frontend.request_ready,
                    memory_responses=frontend.responses,
                )

            for target, node in self.local_nodes.items():
                if node.outputs().iter_done:
                    local_done.setdefault(target, cycle + 1)
            for node, cross in enumerate(self.cross_nodes):
                if cross.outputs(0).iter_done:
                    cross_done.setdefault(node, cycle + 1)

            if phase == "dispatch" and dispatch_finished:
                phase = "blank"
            elif phase == "blank":
                phase = "drain"
            elif phase == "cross_quiet":
                quiet = 0 if activity else quiet + 1
                if quiet == 4:
                    phase = "done_inject"
                    done_pending = set(range(self.geometry.node_count))
            elif phase == "done_inject":
                for node in tuple(done_pending):
                    if h1_packets[node] is not None and arbiter_outputs[node].h1_ready:
                        done_pending.remove(node)
                if not done_pending:
                    phase = "done_quiet"
                    quiet = 0
            elif phase == "done_quiet":
                quiet = 0 if activity else quiet + 1
                if quiet == 4:
                    done_quiet_end = cycle + 1
                    phase = "tail"

            if (
                phase == "tail"
                and len(local_done) == len(self.local_nodes)
            ):
                if len(done_received) != self.geometry.node_count:
                    raise RuntimeError("completion packets did not reach every H1")
                if done_quiet_end is None:
                    raise RuntimeError("completion quiet window was not recorded")
                tile_done = 0
                for node in range(self.geometry.node_count):
                    h1_done = local_done[WorkTarget(H1, node, 0)]
                    # Both H1-local and cross-H1 partial streams traverse the
                    # same final package-internal H1-to-H0 chiplet boundary.
                    # The link is latency-only, so its tail is shifted once.
                    h1_to_h0 = (
                        max(done_received[node] + 2, h1_done)
                        + chiplet_latency
                    )
                    node_core_done = 0
                    for h0 in range(self.geometry.h0_per_h1):
                        h0_done = local_done[WorkTarget(H0, node, h0)]
                        core_done = max(h1_to_h0 + 1, h0_done) + 3
                        node_core_done = max(node_core_done, core_done)
                        tile_done = max(tile_done, core_done)
                    node_completion[node] = (
                        compute_start
                        + max(done_quiet_end, node_core_done) + 2
                    )
                local_end = max(done_quiet_end, tile_done) + 2
                break

            counters.cycles += 1
            cycle += 1

        iteration_cycles = compute_start + local_end
        counters.cycles = iteration_cycles
        resources = self._noc_stats()
        hop_histogram = tuple(sorted(self._hop_histogram.items()))
        self._validate_metric_totals(counters, resources, hop_histogram)
        publications_sent = Counter(
            block // self.geometry.blocks_per_h1
            for block, _ in compiled.publications()
        )
        publications_received = Counter(
            owner for _, owner in compiled.publications()
        )
        nodes = tuple(
            NodePerformanceStats(
                node=node,
                x=node % self.geometry.mesh_x,
                y=node // self.geometry.mesh_x,
                h0_jobs=sum(
                    len(compiled.h0[
                        node * self.geometry.h0_per_h1 + h0
                    ])
                    for h0 in range(self.geometry.h0_per_h1)
                ),
                h1_jobs=len(compiled.h1[node]),
                cross_jobs=len(compiled.cross[node]),
                total_jobs=(
                    sum(
                        len(compiled.h0[
                            node * self.geometry.h0_per_h1 + h0
                        ])
                        for h0 in range(self.geometry.h0_per_h1)
                    )
                    + len(compiled.h1[node])
                    + len(compiled.cross[node])
                ),
                state_publications_sent=publications_sent[node],
                state_publications_received=publications_received[node],
                h1_completion_cycle=(
                    compute_start + local_done[WorkTarget(H1, node, 0)]
                ),
                cross_completion_cycle=compute_start + cross_done[node],
                completion_cycle=node_completion[node],
            )
            for node in range(self.geometry.node_count)
        )
        dram = self._dram_stats(compiled)
        default_records = [
            ScheduledBlock(a, b)
            for a, b in sorted(self.dataset.active_block_pairs())
        ]
        default_compiled = compile_schedule(self.geometry, default_records)
        schedule_matches_default = all(
            tuple(map(tuple, getattr(compiled, level))) ==
            tuple(map(tuple, getattr(default_compiled, level)))
            for level in ("h0", "h1", "cross")
        )
        certified_geometry = (
            self.geometry == Geometry(2, 1, 2, 2)
            and self.config.h0_mvm_count == 1
            and self.config.h1_mvm_count == 1
            and self.config.cross_mvm_count == 1
        ) or (
            self.geometry == Geometry(4, 4, 2, 16)
            and self.config.h0_mvm_count == 1
            and self.config.h1_mvm_count == 1
            and self.config.cross_mvm_count == 16
        )
        certified = (
            certified_geometry
            and schedule_matches_default
            and self.config.fifo_depth == 4
            and chiplet_latency == 0
            and self.config.interconnect.is_rtl_direct
            and self.mem_lanes == 16
            and self.ticks_per_cycle == 40
            and self.idle_tick_modulus in (None, 7600)
            and self.ramulator_config_sha256
            == _CERTIFIED_RAMULATOR_CONFIG_SHA256
        )
        if certified:
            accuracy = "rtl-differential"
        elif (chiplet_latency != 0 or
              not self.config.interconnect.is_rtl_direct):
            accuracy = "parameterized-interconnect-projection"
        else:
            accuracy = "cycle-structured-unverified"
        return ExactEventResult(
            accuracy=accuracy,
            initialization_cycles=initialization,
            iteration_cycles=iteration_cycles,
            total_cycles=initialization + iteration_cycles,
            scheduled_h0=compiled.counts[0],
            scheduled_h1=compiled.counts[1],
            scheduled_cross=compiled.counts[2],
            counters=counters,
            average_hops=(
                counters.physical_link_flits / counters.injected_flits
                if counters.injected_flits else 0.0
            ),
            hop_histogram=hop_histogram,
            noc_resources=resources,
            nodes=nodes,
            dram=dram,
            chiplet_link_latency_cycles=chiplet_latency,
            interconnect=self.config.interconnect,
            execution_mode="cir",
            unordered_interaction_blocks=sum(compiled.counts),
            directed_core_jobs=0,
            weight_block_reads=sum(compiled.counts),
            logical_weight_blocks_stored=sum(compiled.counts),
            remote_state_packets=len(compiled.publications()),
            core_weight_buffers=2,
        )
