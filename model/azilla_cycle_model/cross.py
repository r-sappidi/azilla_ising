"""Cross-H1 compute endpoint and local packet arbitration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .adapters import NOC_PARTIAL, NOC_STATE
from .hierarchy import DmaCommand, HierarchyNode
from .noc import Flit


@dataclass(frozen=True, slots=True)
class CrossOutputs:
    iter_done: bool
    state_ready: bool
    command_ready: tuple[bool, ...]
    weight_ready: tuple[bool, ...]
    tx: Flit | None


class CrossH1Node:
    """Mirror of ``rtl/cross_h1_node.sv`` around :class:`HierarchyNode`."""

    def __init__(self, *, state_entry_count: int, mvm_count: int,
                 blocks_per_h1: int, mesh_x_count: int, node_id: int,
                 timing_only: bool = False):
        self.blocks_per_h1 = blocks_per_h1
        self.mesh_x_count = mesh_x_count
        self.node_id = node_id
        self.node = HierarchyNode(
            state_entry_count, mvm_count, timing_only=timing_only
        )
        self.output_locked = False
        self.locked_engine = 0
        self.round_robin_start = 0

    def reset(self) -> None:
        self.node.reset()
        self.output_locked = False
        self.locked_engine = 0
        self.round_robin_start = 0

    def _selected_engine(self) -> int | None:
        partials = self.node.outputs().partials
        if self.output_locked:
            return self.locked_engine if partials[self.locked_engine].valid else None
        for offset in range(len(partials)):
            candidate = (self.round_robin_start + offset) % len(partials)
            if partials[candidate].valid:
                return candidate
        return None

    def outputs(self, epoch: int) -> CrossOutputs:
        node_outputs = self.node.outputs()
        selected = self._selected_engine()
        tx = None
        if selected is not None:
            partial = node_outputs.partials[selected]
            destination = partial.block_id // self.blocks_per_h1
            tx = Flit(
                data=partial.data,
                packet_type=NOC_PARTIAL,
                dest_x=destination % self.mesh_x_count,
                dest_y=destination // self.mesh_x_count,
                source_id=self.node_id,
                epoch=epoch,
                block_id=partial.block_id,
                last=partial.last,
            )
        return CrossOutputs(
            iter_done=node_outputs.iter_done,
            state_ready=node_outputs.state_ready,
            command_ready=node_outputs.command_ready,
            weight_ready=node_outputs.weight_ready,
            tx=tx,
        )

    def tick(self, *, epoch: int, rst: bool = False, iter_start: bool = False,
             state_packet: Flit | None = None, schedule_done: bool = False,
             commands: Sequence[DmaCommand | None] | None = None,
             weight_valid: Sequence[bool] | None = None,
             weight_data: Sequence[int] | None = None,
             tx_ready: bool = False) -> CrossOutputs:
        before = self.outputs(epoch)
        if rst:
            self.reset()
            return before
        selected = self._selected_engine()
        partial_ready = [False] * len(self.node.engines)
        if selected is not None:
            partial_ready[selected] = tx_ready
        valid_state = bool(
            state_packet is not None and
            state_packet.packet_type == NOC_STATE and
            state_packet.epoch == epoch and
            state_packet.block_id < len(self.node.state_mem)
        )
        self.node.tick(
            iter_start=iter_start,
            state_valid=valid_state,
            state_index=0 if state_packet is None else state_packet.block_id,
            state_data=0 if state_packet is None else state_packet.data,
            schedule_done=schedule_done,
            commands=commands,
            weight_valid=weight_valid,
            weight_data=weight_data,
            partial_ready=partial_ready,
        )
        if before.tx is not None and tx_ready and selected is not None:
            if self.output_locked:
                if before.tx.last:
                    self.output_locked = False
                    self.round_robin_start = (selected + 1) % len(self.node.engines)
            elif not before.tx.last:
                self.output_locked = True
                self.locked_engine = selected
            else:
                self.round_robin_start = (selected + 1) % len(self.node.engines)
        return before


@dataclass(frozen=True, slots=True)
class InjectionOutputs:
    selected: Flit | None
    cross_ready: bool
    h1_ready: bool


class LocalInjectionArbiter:
    """Top-node cross/H1 local injection arbiter."""

    def __init__(self):
        self.locked = False
        self.cross_selected = False

    def reset(self) -> None:
        self.locked = False
        self.cross_selected = False

    def outputs(self, cross: Flit | None, h1: Flit | None,
                router_ready: bool) -> InjectionOutputs:
        select_cross = self.cross_selected if self.locked else cross is not None
        selected = cross if select_cross else h1
        return InjectionOutputs(
            selected=selected,
            cross_ready=select_cross and router_ready,
            h1_ready=(not select_cross) and router_ready,
        )

    def tick(self, cross: Flit | None, h1: Flit | None, router_ready: bool,
             *, rst: bool = False) -> InjectionOutputs:
        before = self.outputs(cross, h1, router_ready)
        if rst:
            self.reset()
            return before
        if before.selected is not None and router_ready:
            select_cross = before.cross_ready
            if not self.locked and not before.selected.last:
                self.locked = True
                self.cross_selected = select_cross
            elif self.locked and before.selected.last:
                self.locked = False
        return before
