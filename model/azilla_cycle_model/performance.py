"""Full-system cycle driver for the direct RTL testbench workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .noc import Flit, EAST, LOCAL, NORTH, SOUTH, WEST, InterconnectConfig
from .adapters import NOC_PARTIAL, NOC_STATE, RoutedPartial
from .scheduler import (
    CROSS, H0, H1, ConcurrentDispatcher, DirectDispatcher, DispatchPort, WorkTarget,
    compile_schedule,
)
from .memory import DramWeightStreamer, MemoryResponse
from .ramulator import RamulatorBackend
from .system import IsingMeshSystem, SystemCycleOutputs
from .workload import (
    Geometry, IsingDataset, ScheduledBlock, initial_state_for_block,
    noise_seed_for_block,
)


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    h0_mvm_count: int = 1
    h1_mvm_count: int = 1
    cross_mvm_count: int = 1
    fifo_depth: int = 4
    coeff_a: int = 0
    coeff_b: int = 1
    coeff_c: int = 0
    noise_amplitude: int = 0
    noise_decay: int = 0
    max_cycles: int = 1_000_000_000
    timing_only: bool = False
    chiplet_link_latency_cycles: int = 0
    interconnect: InterconnectConfig = InterconnectConfig()
    execution_mode: str = "cir"

    def __post_init__(self) -> None:
        if self.chiplet_link_latency_cycles < 0:
            raise ValueError("chiplet link latency must be non-negative")
        if self.execution_mode not in {"cir", "cores-only", "hybrid"}:
            raise ValueError(f"unknown execution mode {self.execution_mode!r}")


@dataclass(slots=True)
class PerformanceCounters:
    cycles: int = 0
    injected_flits: int = 0
    ejected_flits: int = 0
    physical_link_flits: int = 0
    injection_stalls: int = 0
    ejection_stalls: int = 0
    link_stalls: int = 0
    type_flits: list[int] = field(default_factory=lambda: [0, 0, 0, 0])


@dataclass(frozen=True, slots=True)
class IterationResult:
    iteration: int
    start_cycle: int
    end_cycle: int
    states: tuple[int, ...]

    @property
    def cycles(self) -> int:
        return self.end_cycle - self.start_cycle


@dataclass(frozen=True, slots=True)
class SimulationResult:
    initialization_cycles: int
    total_cycles: int
    iterations: tuple[IterationResult, ...]
    counters: PerformanceCounters


class DirectPerformanceModel:
    """Run the same edge sequence as ``USE_RAMULATOR=0`` in the RTL TB.

    This path includes initialization, all hierarchy-node arithmetic, state and
    partial packets, router backpressure, completion packets, and commit.  It
    intentionally preserves the testbench's serial direct-DMA task ordering.
    """

    def __init__(self, geometry: Geometry, dataset: IsingDataset,
                 config: PerformanceConfig | None = None):
        self.geometry = geometry
        self.dataset = dataset
        self.config = config or PerformanceConfig()
        if self.config.execution_mode != "cir":
            raise NotImplementedError(
                "cores-only is currently implemented by the calibrated event "
                "model; direct RTL-structured execution requires dedicated "
                "destination-core memory frontends"
            )
        if dataset.spin_count != geometry.spin_count:
            raise ValueError(
                f"dataset has {dataset.spin_count} spins, geometry has "
                f"{geometry.spin_count}"
            )
        self.system = IsingMeshSystem(
            geometry,
            h0_mvm_count=self.config.h0_mvm_count,
            h1_mvm_count=self.config.h1_mvm_count,
            cross_mvm_count=self.config.cross_mvm_count,
            fifo_depth=self.config.fifo_depth,
            timing_only=self.config.timing_only,
            interconnect=self.config.interconnect,
        )
        self.counters = PerformanceCounters()
        self.last_outputs: SystemCycleOutputs | None = None

    def _constant_h1_inputs(self) -> list[dict[str, Any]]:
        values = []
        for node in range(self.geometry.node_count):
            init_states = []
            noise_seeds = []
            for h0 in range(self.geometry.h0_per_h1):
                state_row, seed_row = [], []
                for core in range(self.geometry.cores_per_h0):
                    block = (node * self.geometry.blocks_per_h1 +
                             h0 * self.geometry.cores_per_h0 + core)
                    state_row.append(initial_state_for_block(block))
                    seed_row.append(noise_seed_for_block(block))
                init_states.append(state_row)
                noise_seeds.append(seed_row)
            values.append({
                "init_states": init_states,
                "noise_seeds": noise_seeds,
                "coeff_a": self.config.coeff_a,
                "coeff_b": self.config.coeff_b,
                "coeff_c": self.config.coeff_c,
                "noise_amplitude": self.config.noise_amplitude,
                "noise_decay": self.config.noise_decay,
            })
        return values

    def _router_activity(self) -> bool:
        return self.system.mesh.has_link_data or any(
            flit is not None
            for router in self.system.mesh.routers
            for flit in router.outputs().output_flits
        )

    def _record_noc(self, h1_inputs: list[dict[str, Any]],
                    cross_inputs: list[dict[str, Any]],
                    state_publications: list[Flit | None],
                    done_destinations: list[tuple[int, int] | None]) -> None:
        # Monitor the same pre-edge ready/valid transfers used by the RTL
        # counters.  Internal links are counted at their source router output.
        comb = [router.outputs() for router in self.system.mesh.routers]
        epochs = [cross_inputs[node].get("epoch", 0)
                  for node in range(self.geometry.node_count)]
        for node, output in enumerate(comb):
            rx = output.output_flits[LOCAL]
            if rx is not None:
                provisional = RoutedPartial(
                    valid=(rx.packet_type == NOC_PARTIAL and
                           rx.epoch == epochs[node]),
                    data=rx.data, block_id=rx.block_id,
                )
                tile = self.system.h1_tiles[node].outputs(provisional)
                adapter = self.system.noc_adapters[node].outputs(
                    current_epoch=epochs[node], tx_ready=False,
                    state_publish=state_publications[node],
                    done_destination=done_destinations[node], rx=rx,
                    parent_ready=tile.parent_partial_ready,
                )
                ready = (
                    self.system.cross_nodes[node].outputs(
                        epochs[node]
                    ).state_ready
                    if rx.packet_type == NOC_STATE else adapter.rx_ready
                )
                if ready:
                    self.counters.ejected_flits += 1
                else:
                    self.counters.ejection_stalls += 1

            router = self.system.mesh.routers[node]
            for port, nx, ny, neighbor_input in (
                (NORTH, router.x, router.y - 1, SOUTH),
                (SOUTH, router.x, router.y + 1, NORTH),
                (EAST, router.x + 1, router.y, WEST),
                (WEST, router.x - 1, router.y, EAST),
            ):
                flit = output.output_flits[port]
                if flit is None:
                    continue
                ready = self.system.mesh.link_ready(node, port, comb)
                if ready:
                    self.counters.physical_link_flits += 1
                else:
                    self.counters.link_stalls += 1

        # Derive injection offers through the same arbitration used by system.
        # Accepted packets can be counted exactly from the local FIFO length
        # change only when ready is high; producer selection is reconstructed.
        for node in range(self.geometry.node_count):
            cross_tx = self.system.cross_nodes[node].outputs(epochs[node]).tx
            adapter_tx = self.system.noc_adapters[node].outputs(
                current_epoch=epochs[node], tx_ready=False,
                state_publish=state_publications[node],
                done_destination=done_destinations[node], rx=None,
                parent_ready=False,
            ).tx
            ready = comb[node].input_ready[LOCAL]
            selected = self.system.injection_arbiters[node].outputs(
                cross_tx, adapter_tx, ready
            ).selected
            if selected is not None:
                if ready:
                    self.counters.injected_flits += 1
                    packet_type = selected.packet_type
                    self.counters.type_flits[
                        packet_type if 0 <= packet_type < 3 else 3
                    ] += 1
                else:
                    self.counters.injection_stalls += 1

    def _tick(self, *, rst: bool = False,
              h1_changes: list[dict[str, Any]] | None = None,
              cross_changes: list[dict[str, Any]] | None = None,
              state_publications: list[Flit | None] | None = None,
              done_destinations: list[tuple[int, int] | None] | None = None,
              epochs: list[int] | None = None) -> SystemCycleOutputs:
        h1_inputs = self._constant_h1_inputs()
        cross_inputs = [{} for _ in range(self.geometry.node_count)]
        if h1_changes:
            for node, changes in enumerate(h1_changes):
                h1_inputs[node].update(changes)
        if cross_changes:
            for node, changes in enumerate(cross_changes):
                cross_inputs[node].update(changes)
        epochs = epochs or [0] * self.geometry.node_count
        for node in range(self.geometry.node_count):
            cross_inputs[node].setdefault("epoch", epochs[node])
        state_publications = state_publications or [None] * self.geometry.node_count
        done_destinations = done_destinations or [None] * self.geometry.node_count
        if not rst:
            self._record_noc(h1_inputs, cross_inputs, state_publications,
                             done_destinations)
        result = self.system.tick(
            rst=rst, epochs=epochs, h1_inputs=h1_inputs,
            cross_inputs=cross_inputs,
            state_publications=state_publications,
            done_destinations=done_destinations,
        )
        if not rst:
            self.counters.cycles += 1
            if self.counters.cycles > self.config.max_cycles:
                raise TimeoutError(f"simulation exceeded {self.config.max_cycles} cycles")
        self.last_outputs = result
        return result

    def initialize(self) -> int:
        for _ in range(5):
            self._tick(rst=True)
        self._tick()  # reset deasserted for one full edge before init_start
        starts = [{"init_start": True} for _ in range(self.geometry.node_count)]
        self._tick(h1_changes=starts)
        self._tick()  # task's negedge between init_start and first weight

        for block in range(self.geometry.total_blocks):
            node = block // self.geometry.blocks_per_h1
            local = block % self.geometry.blocks_per_h1
            h0 = local // self.geometry.cores_per_h0
            core = local % self.geometry.cores_per_h0
            for beat in range(32):
                while not self.system.h1_tiles[node].h0_tiles[h0].cores[
                        core].outputs().weight_init_ready:
                    self._tick()
                valid = [
                    [[False] * self.geometry.cores_per_h0
                     for _ in range(self.geometry.h0_per_h1)]
                    for _ in range(self.geometry.node_count)
                ]
                data = [
                    [[0] * self.geometry.cores_per_h0
                     for _ in range(self.geometry.h0_per_h1)]
                    for _ in range(self.geometry.node_count)
                ]
                valid[node][h0][core] = True
                data[node][h0][core] = self.dataset.weight_row(block, block, beat)
                changes = [
                    {"core_weight_valid": valid[n], "core_weight_data": data[n]}
                    for n in range(self.geometry.node_count)
                ]
                self._tick(h1_changes=changes)
            self._tick()  # deassert valid; adjacent load tasks add a negedge

        while not all(tile.outputs().init_done for tile in self.system.h1_tiles):
            self._tick()
        return self.counters.cycles

    def _ready_maps(self, outputs: SystemCycleOutputs, epoch: int) -> tuple[
            dict[DispatchPort, bool], dict[DispatchPort, bool]]:
        command_ready: dict[DispatchPort, bool] = {}
        weight_ready: dict[DispatchPort, bool] = {}
        for node, h1_output in enumerate(outputs.h1):
            for h0 in range(self.geometry.h0_per_h1):
                target = WorkTarget(H0, node, h0)
                for engine, ready in enumerate(h1_output.h0_command_ready[h0]):
                    port = DispatchPort(target, engine)
                    command_ready[port] = ready
                    weight_ready[port] = h1_output.h0_weight_ready[h0][engine]
            target = WorkTarget(H1, node, 0)
            for engine, ready in enumerate(h1_output.h1_command_ready):
                port = DispatchPort(target, engine)
                command_ready[port] = ready
                weight_ready[port] = h1_output.h1_weight_ready[engine]
            target = WorkTarget(CROSS, node, 0)
            cross = self.system.cross_nodes[node].outputs(epoch)
            for engine, ready in enumerate(cross.command_ready):
                port = DispatchPort(target, engine)
                command_ready[port] = ready
                weight_ready[port] = cross.weight_ready[engine]
        return command_ready, weight_ready

    def _dispatcher_inputs(self, commands, weight_beats) -> tuple[list[dict], list[dict]]:
        h1_changes: list[dict[str, Any]] = []
        cross_changes: list[dict[str, Any]] = []
        for node in range(self.geometry.node_count):
            h0_commands = [[None] * self.config.h0_mvm_count
                           for _ in range(self.geometry.h0_per_h1)]
            h0_valid = [[False] * self.config.h0_mvm_count
                        for _ in range(self.geometry.h0_per_h1)]
            h0_data = [[0] * self.config.h0_mvm_count
                       for _ in range(self.geometry.h0_per_h1)]
            h1_commands = [None] * self.config.h1_mvm_count
            h1_valid = [False] * self.config.h1_mvm_count
            h1_data = [0] * self.config.h1_mvm_count
            cross_commands = [None] * self.config.cross_mvm_count
            cross_valid = [False] * self.config.cross_mvm_count
            cross_data = [0] * self.config.cross_mvm_count
            for port, command in commands.items():
                if port.target.node != node:
                    continue
                if port.target.level == H0:
                    h0_commands[port.target.index][port.engine] = command
                elif port.target.level == H1:
                    h1_commands[port.engine] = command
                else:
                    cross_commands[port.engine] = command
            for port, beat in weight_beats.items():
                if port.target.node != node:
                    continue
                # Metadata is stable in DirectDispatcher.active while weights
                # are sent, matching the source arrays in the SV tasks.
                active_command = self._direct_command
                if active_command is None:
                    raise RuntimeError("weight valid without active direct command")
                value = self.dataset.weight_row(
                    active_command.block_a, active_command.block_b, beat
                )
                if port.target.level == H0:
                    h0_valid[port.target.index][port.engine] = True
                    h0_data[port.target.index][port.engine] = value
                elif port.target.level == H1:
                    h1_valid[port.engine] = True
                    h1_data[port.engine] = value
                else:
                    cross_valid[port.engine] = True
                    cross_data[port.engine] = value
            h1_changes.append({
                "h0_commands": h0_commands,
                "h0_weight_valid": h0_valid,
                "h0_weight_data": h0_data,
                "h1_commands": h1_commands,
                "h1_weight_valid": h1_valid,
                "h1_weight_data": h1_data,
            })
            cross_changes.append({
                "commands": cross_commands,
                "weight_valid": cross_valid,
                "weight_data": cross_data,
            })
        return h1_changes, cross_changes

    def _states(self) -> tuple[int, ...]:
        states = []
        for tile in self.system.h1_tiles:
            for h0_states in tile.outputs().state_next:
                states.extend(h0_states)
        return tuple(states)

    def _dispatch_schedule(self, schedule, epochs: list[int], epoch: int) -> None:
        dispatcher = DirectDispatcher(
            schedule,
            h0_mvm_count=self.config.h0_mvm_count,
            h1_mvm_count=self.config.h1_mvm_count,
            cross_mvm_count=self.config.cross_mvm_count,
        )
        while not dispatcher.done:
            current = SystemCycleOutputs(
                cycle=self.system.cycle,
                h1=tuple(tile.outputs() for tile in self.system.h1_tiles),
                cross_done=tuple(
                    cross.outputs(epoch).iter_done for cross in self.system.cross_nodes
                ),
                injection_ready=tuple(
                    router.outputs().input_ready[LOCAL]
                    for router in self.system.mesh.routers
                ),
                ejections=tuple(
                    router.outputs().output_flits[LOCAL]
                    for router in self.system.mesh.routers
                ),
            )
            command_ready, weight_ready = self._ready_maps(current, epoch)
            commands, weights = dispatcher.drive(command_ready, weight_ready)
            self._direct_command = dispatcher.last_driven_command
            h1_changes, cross_changes = self._dispatcher_inputs(commands, weights)
            self._tick(
                h1_changes=h1_changes, cross_changes=cross_changes, epochs=epochs
            )

    def _wait_streamers(self, epochs: list[int]) -> None:
        del epochs

    def run_iteration(self, iteration: int,
                      schedule_records: list[ScheduledBlock], *,
                      final: bool = True) -> IterationResult:
        epoch = iteration
        epochs = [epoch] * self.geometry.node_count
        start_cycle = self.counters.cycles
        schedule = compile_schedule(self.geometry, schedule_records)

        self._tick(
            h1_changes=[{"iter_start": True}
                        for _ in range(self.geometry.node_count)],
            epochs=epochs,
        )
        self._tick(epochs=epochs)

        # Publish only cross-owner state, one packet at a time, preserving the
        # numeric associative-array order used by SystemVerilog foreach.
        for block, owner in schedule.publications():
            source = block // self.geometry.blocks_per_h1
            while True:
                local = block % self.geometry.blocks_per_h1
                h0 = local // self.geometry.cores_per_h0
                core = local % self.geometry.cores_per_h0
                state = self.system.h1_tiles[source].h0_tiles[h0].cores[
                    core].outputs().state_current
                packet = Flit(
                    data=state, packet_type=0,
                    dest_x=owner % self.geometry.mesh_x,
                    dest_y=owner // self.geometry.mesh_x,
                    source_id=source, epoch=epoch, block_id=block, last=True,
                )
                publications = [None] * self.geometry.node_count
                publications[source] = packet
                ready = self.system.mesh.routers[source].outputs().input_ready[LOCAL]
                self._tick(state_publications=publications, epochs=epochs)
                if ready:
                    break
            self._tick(epochs=epochs)

        quiet = 0
        while quiet < 4:
            active = self._router_activity()
            self._tick(epochs=epochs)
            quiet = 0 if active else quiet + 1

        self._tick(
            cross_changes=[{"iter_start": True}
                           for _ in range(self.geometry.node_count)],
            epochs=epochs,
        )
        self._tick(epochs=epochs)

        self._dispatch_schedule(schedule, epochs, epoch)
        self._tick(epochs=epochs)
        self._wait_streamers(epochs)

        done_h1 = [{"h0_schedule_done": [True] * self.geometry.h0_per_h1,
                    "h1_schedule_done": True}
                   for _ in range(self.geometry.node_count)]
        done_cross = [{"schedule_done": True}
                      for _ in range(self.geometry.node_count)]
        self._tick(h1_changes=done_h1, cross_changes=done_cross, epochs=epochs)

        while not all(self.system.cross_nodes[node].outputs(epoch).iter_done
                      for node in range(self.geometry.node_count)):
            self._tick(epochs=epochs)
        quiet = 0
        while quiet < 4:
            active = self._router_activity()
            self._tick(epochs=epochs)
            quiet = 0 if active else quiet + 1

        pending = set(range(self.geometry.node_count))
        while pending:
            destinations = [None] * self.geometry.node_count
            accepted = []
            for node in pending:
                destinations[node] = (
                    node % self.geometry.mesh_x, node // self.geometry.mesh_x
                )
                if self.system.mesh.routers[node].outputs().input_ready[LOCAL]:
                    accepted.append(node)
            self._tick(done_destinations=destinations, epochs=epochs)
            pending.difference_update(accepted)

        quiet = 0
        while quiet < 4:
            active = self._router_activity()
            self._tick(epochs=epochs)
            quiet = 0 if active else quiet + 1
        while not all(tile.outputs().iter_done for tile in self.system.h1_tiles):
            self._tick(epochs=epochs)

        states = self._states()
        self._tick(
            h1_changes=[{"commit": True, "done": final}
                        for _ in range(self.geometry.node_count)],
            epochs=epochs,
        )
        self._tick(epochs=epochs)
        return IterationResult(iteration, start_cycle, self.counters.cycles, states)

    def run(self, *, iterations: int = 1,
            schedule: list[ScheduledBlock] | None = None,
            sparse: bool = True) -> SimulationResult:
        initialization_cycles = self.initialize()
        if schedule is None:
            pairs = sorted(self.dataset.active_block_pairs()) if sparse else [
                (a, b) for a in range(self.geometry.total_blocks)
                for b in range(a + 1, self.geometry.total_blocks)
            ]
            schedule = [ScheduledBlock(a, b) for a, b in pairs]
        results = []
        for iteration in range(iterations):
            results.append(self.run_iteration(
                iteration, schedule, final=iteration == iterations - 1
            ))
        return SimulationResult(
            initialization_cycles, self.counters.cycles,
            tuple(results), self.counters,
        )


@dataclass(slots=True)
class _RamulatorFrontend:
    system_id: int
    streamer: DramWeightStreamer
    request_ready: list[bool]
    responses: list[MemoryResponse | None]


class RamulatorPerformanceModel(DirectPerformanceModel):
    """Concurrent projected-GDDR workflow using the RTL's Ramulator2 C++.

    Streamers, hierarchy nodes, and the accelerator all sample the same
    pre-edge values. The C++ backend is the implementation linked by the RTL,
    avoiding a second DRAM timing interpretation in Python.
    """

    def __init__(self, geometry: Geometry, dataset: IsingDataset, *,
                 ramulator_library: str, ramulator_config: str,
                 dataset_path: str, mem_lanes: int = 16,
                 ticks_per_cycle: int = 40,
                 config: PerformanceConfig | None = None):
        super().__init__(geometry, dataset, config)
        self.mem_lanes = mem_lanes
        self.ticks_per_cycle = ticks_per_cycle
        total_h0 = geometry.node_count * geometry.h0_per_h1
        system_count = total_h0 + 2 * geometry.node_count
        self.backend = RamulatorBackend(
            ramulator_library, ramulator_config, dataset_path,
            system_count, geometry.total_blocks,
        )
        self.frontends: dict[WorkTarget, _RamulatorFrontend] = {}
        for global_h0 in range(total_h0):
            target = WorkTarget(
                H0, global_h0 // geometry.h0_per_h1,
                global_h0 % geometry.h0_per_h1,
            )
            self.frontends[target] = self._new_frontend(
                global_h0, self.config.h0_mvm_count
            )
        for node in range(geometry.node_count):
            self.frontends[WorkTarget(H1, node, 0)] = self._new_frontend(
                total_h0 + node, self.config.h1_mvm_count
            )
            self.frontends[WorkTarget(CROSS, node, 0)] = self._new_frontend(
                total_h0 + geometry.node_count + node,
                self.config.cross_mvm_count,
            )
        self._scheduler_commands: dict[DispatchPort, Any] = {}
        self._ramulator_clock_started = False

    def _new_frontend(self, system_id: int, engines: int) -> _RamulatorFrontend:
        return _RamulatorFrontend(
            system_id,
            DramWeightStreamer(
                mvm_count=engines,
                total_block_count=self.geometry.total_blocks,
                request_lanes=self.mem_lanes,
                response_lanes=self.mem_lanes,
            ),
            [False] * self.mem_lanes,
            [None] * self.mem_lanes,
        )

    def close(self) -> None:
        self.backend.finalize()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def _tick(self, *, rst: bool = False,
              h1_changes: list[dict[str, Any]] | None = None,
              cross_changes: list[dict[str, Any]] | None = None,
              state_publications: list[Flit | None] | None = None,
              done_destinations: list[tuple[int, int] | None] | None = None,
              epochs: list[int] | None = None) -> SystemCycleOutputs:
        count = self.geometry.node_count
        h1_changes = [dict(item) for item in (h1_changes or [{} for _ in range(count)])]
        cross_changes = [dict(item) for item in
                         (cross_changes or [{} for _ in range(count)])]

        if rst:
            self._ramulator_clock_started = False
            for frontend in self.frontends.values():
                frontend.streamer.reset()
                frontend.request_ready = [False] * self.mem_lanes
                frontend.responses = [None] * self.mem_lanes
            self._scheduler_commands.clear()
            return super()._tick(
                rst=True, h1_changes=h1_changes, cross_changes=cross_changes,
                state_publications=state_publications,
                done_destinations=done_destinations, epochs=epochs,
            )

        # Falling-edge DRAM bridge: advance DRAM time, offer current requests,
        # and refill response lanes before the next accelerator rising edge.
        # Reset is released after the final reset falling edge. The first
        # nonreset rising edge (accelerator cycle zero) consequently has no
        # preceding active DRAM falling edge; subsequent cycles have one.
        if self._ramulator_clock_started:
            self.backend.tick(self.ticks_per_cycle)
        self._ramulator_clock_started = True
        for frontend in self.frontends.values():
            stream = frontend.streamer.outputs()
            frontend.request_ready = [
                request is not None and self.backend.send(
                    frontend.system_id, request.address, request.tag
                )
                for request in stream.memory_requests
            ]
            frontend.responses = [
                self.backend.pop(frontend.system_id)
                for _ in range(self.mem_lanes)
            ]

        # Snapshot node-side readiness and present each streamer's old outputs.
        node_ready: dict[WorkTarget, tuple[tuple[bool, ...], tuple[bool, ...]]] = {}
        for node in range(count):
            tile_output = self.system.h1_tiles[node].outputs()
            h0_commands, h0_valid, h0_data = [], [], []
            for h0 in range(self.geometry.h0_per_h1):
                target = WorkTarget(H0, node, h0)
                stream = self.frontends[target].streamer.outputs()
                h0_commands.append([
                    out.command if out.command_valid else None for out in stream.node
                ])
                h0_valid.append([out.weight_valid for out in stream.node])
                h0_data.append([out.weight_data for out in stream.node])
                node_ready[target] = (
                    tile_output.h0_command_ready[h0], tile_output.h0_weight_ready[h0]
                )
            h1_target = WorkTarget(H1, node, 0)
            h1_stream = self.frontends[h1_target].streamer.outputs()
            h1_changes[node].update(
                h0_commands=h0_commands,
                h0_weight_valid=h0_valid,
                h0_weight_data=h0_data,
                h1_commands=[out.command if out.command_valid else None
                             for out in h1_stream.node],
                h1_weight_valid=[out.weight_valid for out in h1_stream.node],
                h1_weight_data=[out.weight_data for out in h1_stream.node],
            )
            node_ready[h1_target] = (
                tile_output.h1_command_ready, tile_output.h1_weight_ready
            )
            cross_target = WorkTarget(CROSS, node, 0)
            cross_output = self.system.cross_nodes[node].outputs(
                0 if epochs is None else epochs[node]
            )
            cross_stream = self.frontends[cross_target].streamer.outputs()
            cross_changes[node].update(
                commands=[out.command if out.command_valid else None
                          for out in cross_stream.node],
                weight_valid=[out.weight_valid for out in cross_stream.node],
                weight_data=[out.weight_data for out in cross_stream.node],
            )
            node_ready[cross_target] = (
                cross_output.command_ready, cross_output.weight_ready
            )

        result = super()._tick(
            h1_changes=h1_changes, cross_changes=cross_changes,
            state_publications=state_publications,
            done_destinations=done_destinations, epochs=epochs,
        )

        for target, frontend in self.frontends.items():
            command_ready, weight_ready = node_ready[target]
            scheduler_commands = [None] * len(frontend.streamer.slots)
            for engine in range(len(scheduler_commands)):
                port = DispatchPort(target, engine)
                scheduler_commands[engine] = self._scheduler_commands.get(port)
            frontend.streamer.tick(
                scheduler_commands=scheduler_commands,
                node_command_ready=command_ready,
                node_weight_ready=weight_ready,
                memory_request_ready=frontend.request_ready,
                memory_responses=frontend.responses,
            )
        self._scheduler_commands.clear()
        return result

    def _dispatch_schedule(self, schedule, epochs: list[int], epoch: int) -> None:
        dispatcher = ConcurrentDispatcher(
            schedule,
            h0_mvm_count=self.config.h0_mvm_count,
            h1_mvm_count=self.config.h1_mvm_count,
            cross_mvm_count=self.config.cross_mvm_count,
        )
        while not dispatcher.done:
            ready = {
                DispatchPort(target, engine): is_ready
                for target, frontend in self.frontends.items()
                for engine, is_ready in enumerate(
                    frontend.streamer.outputs().scheduler_ready
                )
            }
            self._scheduler_commands = dispatcher.drive(ready)
            self._tick(epochs=epochs)

    def _wait_streamers(self, epochs: list[int]) -> None:
        while not all(frontend.streamer.outputs().idle
                      for frontend in self.frontends.values()):
            self._tick(epochs=epochs)
