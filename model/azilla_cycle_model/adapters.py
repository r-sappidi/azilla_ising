"""Cycle models for the hierarchy and NoC protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config import ArchitectureConfig
from .hierarchy import PartialOutput
from .noc import Flit


NOC_STATE = 0
NOC_PARTIAL = 1
NOC_EPOCH_DONE = 2


@dataclass(frozen=True, slots=True)
class RoutedPartial:
    valid: bool = False
    data: int = 0
    block_id: int = 0


@dataclass(frozen=True, slots=True)
class H0AdapterOutputs:
    engine_ready: tuple[bool, ...]
    core_partials: tuple[RoutedPartial, ...]


class H0Adapter:
    """Fixed-priority, packet-locking mirror of ``rtl/h0_adapter.sv``."""

    def __init__(self, core_count: int, mvm_count: int, base_block_id: int = 0):
        self.core_count = core_count
        self.mvm_count = mvm_count
        self.base_block_id = base_block_id
        self.locked: list[int | None] = [None] * core_count

    def reset(self) -> None:
        self.locked = [None] * self.core_count

    def outputs(
        self, partials: Sequence[PartialOutput], core_ready: Sequence[bool]
    ) -> H0AdapterOutputs:
        engine_ready = [False] * self.mvm_count
        outputs = [RoutedPartial() for _ in range(self.core_count)]
        for core in range(self.core_count):
            selected = self.locked[core]
            if selected is None:
                target = self.base_block_id + core
                selected = next((index for index, partial in enumerate(partials)
                                 if partial.valid and partial.block_id == target), None)
            if selected is not None:
                partial = partials[selected]
                outputs[core] = RoutedPartial(partial.valid, partial.data, partial.block_id)
                engine_ready[selected] = core_ready[core]
        return H0AdapterOutputs(tuple(engine_ready), tuple(outputs))

    def tick(self, partials: Sequence[PartialOutput], core_ready: Sequence[bool],
             *, rst: bool = False) -> H0AdapterOutputs:
        before = self.outputs(partials, core_ready)
        if rst:
            self.reset()
            return before
        for core, output in enumerate(before.core_partials):
            if not (output.valid and core_ready[core]):
                continue
            selected = next((index for index, ready in enumerate(before.engine_ready)
                             if ready and partials[index].block_id == output.block_id), None)
            if self.locked[core] is None:
                if selected is not None and not partials[selected].last:
                    self.locked[core] = selected
            elif partials[self.locked[core]].last:
                self.locked[core] = None
        return before


@dataclass(frozen=True, slots=True)
class H1ChildOutputs:
    node_ready: tuple[bool, ...]
    parent_ready: bool
    children: tuple[RoutedPartial, ...]


class H1ChildAdapter:
    """Mirror of ``rtl/h1_child_adapter.sv``."""

    def __init__(self, child_count: int, blocks_per_child: int, mvm_count: int,
                 base_block_id: int = 0, config: ArchitectureConfig | None = None):
        self.child_count = child_count
        self.blocks_per_child = blocks_per_child
        self.mvm_count = mvm_count
        self.base_block_id = base_block_id
        self.config = config or ArchitectureConfig()
        self.locked_source: list[int | None] = [None] * child_count
        self.parent_beat = 0

    def reset(self) -> None:
        self.locked_source = [None] * self.child_count
        self.parent_beat = 0

    def belongs(self, child: int, block: int) -> bool:
        first = self.base_block_id + child * self.blocks_per_child
        return first <= block < first + self.blocks_per_child

    def _select(self, child: int, partials: Sequence[PartialOutput],
                parent: RoutedPartial) -> int | None:
        if self.locked_source[child] is not None:
            return self.locked_source[child]
        for engine, partial in enumerate(partials):
            if partial.valid and self.belongs(child, partial.block_id):
                return engine
        if parent.valid and self.belongs(child, parent.block_id):
            return self.mvm_count
        return None

    def outputs(self, partials: Sequence[PartialOutput], parent: RoutedPartial,
                child_ready: Sequence[bool]) -> H1ChildOutputs:
        node_ready = [False] * self.mvm_count
        parent_ready = False
        children = [RoutedPartial() for _ in range(self.child_count)]
        for child in range(self.child_count):
            source = self._select(child, partials, parent)
            if source is None:
                continue
            if source == self.mvm_count:
                children[child] = parent
                parent_ready = child_ready[child]
            else:
                partial = partials[source]
                children[child] = RoutedPartial(
                    partial.valid, partial.data, partial.block_id
                )
                node_ready[source] = child_ready[child]
        return H1ChildOutputs(tuple(node_ready), parent_ready, tuple(children))

    def tick(self, partials: Sequence[PartialOutput], parent: RoutedPartial,
             child_ready: Sequence[bool], *, rst: bool = False) -> H1ChildOutputs:
        before = self.outputs(partials, parent, child_ready)
        if rst:
            self.reset()
            return before
        old_parent_beat = self.parent_beat
        if parent.valid and before.parent_ready:
            self.parent_beat = (self.parent_beat + 1) % self.config.partial_beats
        for child, output in enumerate(before.children):
            if not (output.valid and child_ready[child]):
                continue
            source = self._select(child, partials, parent)
            if source is None:
                continue
            last = (old_parent_beat == self.config.partial_beats - 1
                    if source == self.mvm_count else partials[source].last)
            if self.locked_source[child] is None and not last:
                self.locked_source[child] = source
            elif self.locked_source[child] is not None and last:
                self.locked_source[child] = None
        return before


@dataclass(frozen=True, slots=True)
class H1NocOutputs:
    state_publish_ready: bool
    done_publish_ready: bool
    tx: Flit | None
    rx_ready: bool
    parent_partial: RoutedPartial
    parent_partials_done: bool


class H1NocAdapter:
    def __init__(self, source_id: int = 0):
        self.source_id = source_id
        self.parent_done = False

    def outputs(
        self, *, current_epoch: int, tx_ready: bool,
        state_publish: Flit | None, done_destination: tuple[int, int] | None,
        rx: Flit | None, parent_ready: bool,
    ) -> H1NocOutputs:
        select_done = done_destination is not None
        tx = None
        if select_done:
            x, y = done_destination
            tx = Flit(packet_type=NOC_EPOCH_DONE, dest_x=x, dest_y=y,
                      source_id=self.source_id, epoch=current_epoch, last=True)
        elif state_publish is not None:
            tx = Flit(
                data=state_publish.data,
                packet_type=NOC_STATE,
                dest_x=state_publish.dest_x,
                dest_y=state_publish.dest_y,
                source_id=self.source_id,
                epoch=current_epoch,
                block_id=state_publish.block_id,
                last=True,
            )
        current = rx is not None and rx.epoch == current_epoch
        receive_partial = current and rx.packet_type == NOC_PARTIAL if rx else False
        parent = RoutedPartial(
            valid=bool(receive_partial),
            data=0 if rx is None else rx.data,
            block_id=0 if rx is None else rx.block_id,
        )
        return H1NocOutputs(
            state_publish_ready=not select_done and tx_ready,
            done_publish_ready=select_done and tx_ready,
            tx=tx,
            rx_ready=parent_ready if receive_partial else True,
            parent_partial=parent,
            parent_partials_done=self.parent_done,
        )

    def tick(self, *, current_epoch: int, tx_ready: bool,
             state_publish: Flit | None = None,
             done_destination: tuple[int, int] | None = None,
             rx: Flit | None = None, parent_ready: bool = False,
             rst: bool = False) -> H1NocOutputs:
        before = self.outputs(
            current_epoch=current_epoch, tx_ready=tx_ready,
            state_publish=state_publish, done_destination=done_destination,
            rx=rx, parent_ready=parent_ready,
        )
        if rst:
            self.parent_done = False
            return before
        self.parent_done = bool(
            rx is not None and before.rx_ready and
            rx.packet_type == NOC_EPOCH_DONE and rx.epoch == current_epoch and rx.last
        )
        return before
