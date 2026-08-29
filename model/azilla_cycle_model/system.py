"""Cycle composition of H1 tiles, cross endpoints, adapters, and Floo mesh."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .adapters import H1NocAdapter, NOC_STATE, RoutedPartial
from .cross import CrossH1Node, LocalInjectionArbiter
from .noc import LOCAL, Flit, Mesh
from .tiles import H1TileOutputs, H1Tile
from .workload import Geometry


@dataclass(frozen=True, slots=True)
class SystemCycleOutputs:
    cycle: int
    h1: tuple[H1TileOutputs, ...]
    cross_done: tuple[bool, ...]
    injection_ready: tuple[bool, ...]
    ejections: tuple[Flit | None, ...]


class IsingMeshSystem:
    """Structural mirror of ``ising_mesh``/``mesh_h1_tile`` at clock edges.

    Scheduler and DMA signals remain explicit dictionaries because they are
    testbench/controller inputs in the RTL rather than autonomous hardware.
    """

    def __init__(self, geometry: Geometry, *, h0_mvm_count: int = 1,
                 h1_mvm_count: int = 1, cross_mvm_count: int = 1,
                 fifo_depth: int = 4, timing_only: bool = False):
        self.geometry = geometry
        self.mesh = Mesh(geometry.mesh_x, geometry.mesh_y, fifo_depth)
        self.h1_tiles = [
            H1Tile(
                geometry.h0_per_h1, geometry.cores_per_h0,
                h0_mvm_count, h1_mvm_count,
                node * geometry.blocks_per_h1,
                timing_only=timing_only,
            )
            for node in range(geometry.node_count)
        ]
        self.cross_nodes = [
            CrossH1Node(
                state_entry_count=geometry.total_blocks,
                mvm_count=cross_mvm_count,
                blocks_per_h1=geometry.blocks_per_h1,
                mesh_x_count=geometry.mesh_x,
                node_id=node,
                timing_only=timing_only,
            )
            for node in range(geometry.node_count)
        ]
        self.noc_adapters = [H1NocAdapter(node)
                             for node in range(geometry.node_count)]
        self.injection_arbiters = [LocalInjectionArbiter()
                                   for _ in range(geometry.node_count)]
        self.cycle = 0

    def reset(self) -> None:
        self.mesh.reset()
        for tile in self.h1_tiles:
            tile.reset()
        for cross in self.cross_nodes:
            cross.reset()
        for adapter in self.noc_adapters:
            adapter.parent_done = False
        for arbiter in self.injection_arbiters:
            arbiter.reset()
        self.cycle = 0

    def tick(
        self,
        *,
        rst: bool = False,
        epochs: Sequence[int] | None = None,
        h1_inputs: Sequence[dict[str, Any]] | None = None,
        cross_inputs: Sequence[dict[str, Any]] | None = None,
        state_publications: Sequence[Flit | None] | None = None,
        done_destinations: Sequence[tuple[int, int] | None] | None = None,
    ) -> SystemCycleOutputs:
        count = self.geometry.node_count
        epochs = tuple(epochs or [0] * count)
        h1_inputs = tuple(h1_inputs or [{} for _ in range(count)])
        cross_inputs = tuple(cross_inputs or [{} for _ in range(count)])
        state_publications = tuple(state_publications or [None] * count)
        done_destinations = tuple(done_destinations or [None] * count)
        if not all(len(values) == count for values in
                   (epochs, h1_inputs, cross_inputs,
                    state_publications, done_destinations)):
            raise ValueError("one endpoint input is required per mesh node")

        router_comb = [router.outputs() for router in self.mesh.routers]
        ejections = tuple(output.output_flits[LOCAL] for output in router_comb)
        injection_ready = tuple(output.input_ready[LOCAL] for output in router_comb)

        parent_inputs: list[RoutedPartial] = []
        parent_done: list[bool] = []
        h1_tx: list[Flit | None] = []
        cross_tx: list[Flit | None] = []
        local_ready: dict[int, bool] = {}
        injection_flits: dict[int, Flit] = {}
        injection_results = []
        adapter_before = []

        # First determine the ejection consumers and producer flits.  Ready
        # does not affect route selection in any of these adapters.
        for node in range(count):
            rx = ejections[node]
            provisional_parent = RoutedPartial(
                valid=bool(rx is not None and rx.packet_type == 1 and
                           rx.epoch == epochs[node]),
                data=0 if rx is None else rx.data,
                block_id=0 if rx is None else rx.block_id,
            )
            tile_output = self.h1_tiles[node].outputs(provisional_parent)
            adapter = self.noc_adapters[node].outputs(
                current_epoch=epochs[node], tx_ready=False,
                state_publish=state_publications[node],
                done_destination=done_destinations[node],
                rx=rx, parent_ready=tile_output.parent_partial_ready,
            )
            adapter_before.append(adapter)
            parent_inputs.append(adapter.parent_partial)
            parent_done.append(adapter.parent_partials_done)
            h1_tx.append(adapter.tx)
            cross_output = self.cross_nodes[node].outputs(epochs[node])
            cross_tx.append(cross_output.tx)
            local_ready[node] = (
                cross_output.state_ready if rx is not None and
                rx.packet_type == NOC_STATE else adapter.rx_ready
            )

        for node in range(count):
            arbitration = self.injection_arbiters[node].outputs(
                cross_tx[node], h1_tx[node], injection_ready[node]
            )
            injection_results.append(arbitration)
            if arbitration.selected is not None:
                injection_flits[node] = arbitration.selected

        before_h1 = tuple(
            self.h1_tiles[node].outputs(parent_inputs[node])
            for node in range(count)
        )
        before = SystemCycleOutputs(
            cycle=self.cycle,
            h1=before_h1,
            cross_done=tuple(
                self.cross_nodes[node].outputs(epochs[node]).iter_done
                for node in range(count)
            ),
            injection_ready=injection_ready,
            ejections=ejections,
        )

        if rst:
            self.reset()
            return before

        # All components below sample the pre-edge values constructed above.
        self.mesh.tick(injection_flits, local_ready)
        for node in range(count):
            h1_kwargs = dict(h1_inputs[node])
            h1_kwargs.update(
                parent_partial=parent_inputs[node],
                parent_partials_done=parent_done[node],
            )
            self.h1_tiles[node].tick(**h1_kwargs)

            rx = ejections[node]
            state_packet = rx if rx is not None and rx.packet_type == NOC_STATE else None
            cross_kwargs = dict(cross_inputs[node])
            cross_kwargs.update(
                epoch=epochs[node],
                state_packet=state_packet,
                tx_ready=injection_results[node].cross_ready,
            )
            self.cross_nodes[node].tick(**cross_kwargs)

            self.noc_adapters[node].tick(
                current_epoch=epochs[node],
                tx_ready=injection_results[node].h1_ready,
                state_publish=state_publications[node],
                done_destination=done_destinations[node],
                rx=rx,
                parent_ready=before_h1[node].parent_partial_ready,
            )
            self.injection_arbiters[node].tick(
                cross_tx[node], h1_tx[node], injection_ready[node]
            )
        self.cycle += 1
        return before
