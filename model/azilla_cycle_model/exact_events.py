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

from dataclasses import dataclass
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
from .noc import EAST, LOCAL, NORTH, SOUTH, WEST, Flit, Mesh
from .performance import PerformanceConfig, PerformanceCounters
from .ramulator import RamulatorBackend
from .scheduler import CROSS, H0, H1, ConcurrentDispatcher, DispatchPort, WorkTarget, compile_schedule
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
            geometry.mesh_x, geometry.mesh_y, self.config.fifo_depth
        )
        self.injection_arbiters = [
            LocalInjectionArbiter() for _ in range(geometry.node_count)
        ]
        self.transfers: list[Transfer] = []

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

    def _record_network(
        self,
        comb,
        injections: dict[int, Flit],
        local_ready: dict[int, bool],
        counters: PerformanceCounters,
        absolute_cycle: int,
    ) -> None:
        for node, flit in injections.items():
            if comb[node].input_ready[LOCAL]:
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
                if local_ready[node]:
                    counters.ejected_flits += 1
                    self.transfers.append(self._transfer(
                        absolute_cycle, "eject", node, "local",
                        output.output_flits[LOCAL],
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
                ready = False
                if 0 <= nx < self.geometry.mesh_x and 0 <= ny < self.geometry.mesh_y:
                    neighbor = ny * self.geometry.mesh_x + nx
                    ready = comb[neighbor].input_ready[neighbor_input]
                if ready:
                    counters.physical_link_flits += 1
                    self.transfers.append(self._transfer(
                        absolute_cycle,
                        "link",
                        node,
                        {NORTH: "north", SOUTH: "south", EAST: "east", WEST: "west"}[port],
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
        compiled = compile_schedule(self.geometry, schedule_records)

        initialization = 3 + self.geometry.total_blocks * 33
        publications = [
            PacketRelease(
                cycle=index * 2,
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

        publication = EventCompressedMesh(
            self.geometry.mesh_x,
            self.geometry.mesh_y,
            self.config.fifo_depth,
        ).replay(
            publications, drain_cycles=4, observer=observe_publication
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
                if target.level == H0 else self.geometry.blocks_per_h1 + 2
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
        done_quiet_end: int | None = None
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

            activity = bool(injections) or any(
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
                    h1_to_h0 = max(done_received[node] + 2, h1_done)
                    for h0 in range(self.geometry.h0_per_h1):
                        h0_done = local_done[WorkTarget(H0, node, h0)]
                        core_done = max(h1_to_h0 + 1, h0_done) + 3
                        tile_done = max(tile_done, core_done)
                local_end = max(done_quiet_end, tile_done) + 2
                break

            counters.cycles += 1
            cycle += 1

        iteration_cycles = compute_start + local_end
        counters.cycles = iteration_cycles
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
            and self.mem_lanes == 16
            and self.ticks_per_cycle == 40
            and self.idle_tick_modulus in (None, 7600)
            and self.ramulator_config_sha256
            == _CERTIFIED_RAMULATOR_CONFIG_SHA256
        )
        return ExactEventResult(
            accuracy=(
                "rtl-differential" if certified
                else "cycle-structured-unverified"
            ),
            initialization_cycles=initialization,
            iteration_cycles=iteration_cycles,
            total_cycles=initialization + iteration_cycles,
            scheduled_h0=compiled.counts[0],
            scheduled_h1=compiled.counts[1],
            scheduled_cross=compiled.counts[2],
            counters=counters,
        )
