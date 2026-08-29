"""Cycle-stepped hierarchy tile composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .adapters import H0Adapter, H1ChildAdapter, RoutedPartial
from .compute import SpinCore
from .config import ArchitectureConfig
from .hierarchy import DmaCommand, HierarchyNode


@dataclass(frozen=True, slots=True)
class H0TileOutputs:
    init_done: bool
    iter_done: bool
    command_ready: tuple[bool, ...]
    weight_ready: tuple[bool, ...]
    external_partial_ready: bool
    state_current: tuple[int, ...]
    state_next: tuple[int, ...]


class H0Tile:
    IDLE = "IDLE"
    LOAD_STATES = "LOAD_STATES"
    START_NODE = "START_NODE"
    RUN = "RUN"
    DONE = "DONE"

    def __init__(self, core_count: int = 32, mvm_count: int = 1,
                 base_block_id: int = 0,
                 config: ArchitectureConfig | None = None,
                 *, timing_only: bool = False):
        self.config = config or ArchitectureConfig()
        self.core_count = core_count
        self.base_block_id = base_block_id
        self.cores = [SpinCore(self.config, timing_only=timing_only)
                      for _ in range(core_count)]
        self.node = HierarchyNode(core_count, mvm_count,
                                  config=self.config, timing_only=timing_only)
        self.adapter = H0Adapter(core_count, mvm_count, base_block_id)
        self.reset_wrapper()

    def reset_wrapper(self) -> None:
        self.tile_state = self.IDLE
        self.state_load_index = 0
        self.external_done_pending = False

    def reset(self) -> None:
        self.reset_wrapper()
        for core in self.cores:
            core.reset()
        self.node.reset()
        self.adapter.reset()

    def outputs(self, external_partial: RoutedPartial | None = None) -> H0TileOutputs:
        core_outputs = [core.outputs() for core in self.cores]
        node_outputs = self.node.outputs()
        external_ready = False
        if external_partial is not None:
            local = external_partial.block_id - self.base_block_id
            if 0 <= local < self.core_count:
                external_ready = core_outputs[local].ext_partial_ready
        return H0TileOutputs(
            init_done=all(output.init_done for output in core_outputs),
            iter_done=all(output.iter_done for output in core_outputs),
            command_ready=node_outputs.command_ready,
            weight_ready=node_outputs.weight_ready,
            external_partial_ready=external_ready,
            state_current=tuple(output.state_current for output in core_outputs),
            state_next=tuple(output.state_next for output in core_outputs),
        )

    def _next_state(self, iter_start: bool, commit: bool, iter_done: bool,
                    node_state_ready: bool) -> str:
        if self.tile_state == self.IDLE:
            return self.LOAD_STATES if iter_start else self.IDLE
        if self.tile_state == self.LOAD_STATES:
            if node_state_ready and self.state_load_index == self.core_count - 1:
                return self.START_NODE
            return self.LOAD_STATES
        if self.tile_state == self.START_NODE:
            return self.RUN
        if self.tile_state == self.RUN:
            if iter_done:
                return self.IDLE if commit else self.DONE
            return self.RUN
        return self.IDLE if commit else self.DONE

    def tick(
        self,
        *,
        rst: bool = False,
        init_start: bool = False,
        core_weight_valid: Sequence[bool] | None = None,
        core_weight_data: Sequence[int] | None = None,
        init_states: Sequence[int] | None = None,
        noise_seeds: Sequence[int] | None = None,
        coeff_a: int = 0,
        coeff_b: int = 0,
        coeff_c: int = 0,
        noise_amplitude: int = 0,
        iter_start: bool = False,
        commit: bool = False,
        done: bool = False,
        noise_decay: int = 0,
        schedule_done: bool = False,
        commands: Sequence[DmaCommand | None] | None = None,
        weight_valid: Sequence[bool] | None = None,
        weight_data: Sequence[int] | None = None,
        external_partial: RoutedPartial | None = None,
        external_partials_done: bool = False,
    ) -> H0TileOutputs:
        core_weight_valid = tuple(core_weight_valid or [False] * self.core_count)
        core_weight_data = tuple(core_weight_data or [0] * self.core_count)
        init_states = tuple(init_states or [0] * self.core_count)
        noise_seeds = tuple(noise_seeds or [0] * self.core_count)
        if not all(len(values) == self.core_count for values in
                   (core_weight_valid, core_weight_data, init_states, noise_seeds)):
            raise ValueError("one initialization value is required per core")

        before = self.outputs(external_partial)
        core_outputs = [core.outputs() for core in self.cores]
        node_outputs = self.node.outputs()
        adapter_outputs = self.adapter.outputs(
            node_outputs.partials,
            [output.h0_partial_ready for output in core_outputs],
        )
        old_tile_state = self.tile_state
        next_tile_state = self._next_state(
            iter_start, commit, before.iter_done, node_outputs.state_ready
        )
        core_iter_start = iter_start and old_tile_state == self.IDLE
        node_iter_start = old_tile_state == self.START_NODE
        partials_done_to_cores = (
            node_outputs.iter_done and self.external_done_pending
        )

        if rst:
            self.reset()
            return before

        for index, core in enumerate(self.cores):
            local_h0 = adapter_outputs.core_partials[index]
            ext = RoutedPartial()
            if (external_partial is not None and external_partial.valid and
                    external_partial.block_id == self.base_block_id + index):
                ext = external_partial
            core.tick(
                init_start=init_start,
                weight_init_valid=core_weight_valid[index],
                weight_init_data=core_weight_data[index],
                noise_seed=noise_seeds[index],
                coeff_a=coeff_a, coeff_b=coeff_b, coeff_c=coeff_c,
                noise_amplitude=noise_amplitude,
                init_state=init_states[index],
                iter_start=core_iter_start,
                partials_done=partials_done_to_cores,
                commit=commit, noise_decay=noise_decay, done=done,
                h0_partial_valid=local_h0.valid,
                h0_partial_data=local_h0.data,
                ext_partial_valid=ext.valid,
                ext_partial_data=ext.data,
            )

        state_valid = old_tile_state == self.LOAD_STATES
        self.node.tick(
            iter_start=node_iter_start,
            state_valid=state_valid,
            state_index=self.state_load_index,
            state_data=core_outputs[self.state_load_index].state_current,
            schedule_done=schedule_done,
            commands=commands,
            weight_valid=weight_valid,
            weight_data=weight_data,
            partial_ready=adapter_outputs.engine_ready,
        )
        self.adapter.tick(
            node_outputs.partials,
            [output.h0_partial_ready for output in core_outputs],
        )

        self.tile_state = next_tile_state
        if iter_start:
            self.state_load_index = 0
            self.external_done_pending = False
        else:
            if (old_tile_state == self.LOAD_STATES and node_outputs.state_ready and
                    self.state_load_index != self.core_count - 1):
                self.state_load_index += 1
            if external_partials_done:
                self.external_done_pending = True
        return before


@dataclass(frozen=True, slots=True)
class H1TileOutputs:
    init_done: bool
    iter_done: bool
    h0_command_ready: tuple[tuple[bool, ...], ...]
    h0_weight_ready: tuple[tuple[bool, ...], ...]
    h1_command_ready: tuple[bool, ...]
    h1_weight_ready: tuple[bool, ...]
    parent_partial_ready: bool
    state_current: tuple[tuple[int, ...], ...]
    state_next: tuple[tuple[int, ...], ...]


class H1Tile:
    IDLE = "IDLE"
    LOAD_STATES = "LOAD_STATES"
    START_NODE = "START_NODE"
    RUN = "RUN"
    DONE = "DONE"

    def __init__(self, h0_count: int = 2, cores_per_h0: int = 32,
                 h0_mvm_count: int = 1, h1_mvm_count: int = 1,
                 base_block_id: int = 0,
                 config: ArchitectureConfig | None = None,
                 *, timing_only: bool = False):
        self.config = config or ArchitectureConfig()
        self.h0_count = h0_count
        self.cores_per_h0 = cores_per_h0
        self.base_block_id = base_block_id
        self.h0_tiles = [
            H0Tile(cores_per_h0, h0_mvm_count,
                   base_block_id + index * cores_per_h0,
                   self.config, timing_only=timing_only)
            for index in range(h0_count)
        ]
        self.node = HierarchyNode(
            h0_count * cores_per_h0, h1_mvm_count,
            config=self.config, timing_only=timing_only,
        )
        self.adapter = H1ChildAdapter(
            h0_count, cores_per_h0, h1_mvm_count,
            base_block_id, self.config,
        )
        self.reset_wrapper()

    def reset_wrapper(self) -> None:
        self.tile_state = self.IDLE
        self.state_load_index = 0
        self.parent_done_pending = False

    def reset(self) -> None:
        self.reset_wrapper()
        for tile in self.h0_tiles:
            tile.reset()
        self.node.reset()
        self.adapter.reset()

    def _selected_children(self, parent_partial: RoutedPartial) -> tuple:
        node_outputs = self.node.outputs()
        provisional = self.adapter.outputs(
            node_outputs.partials, parent_partial, [True] * self.h0_count
        )
        child_ready = [
            self.h0_tiles[index].outputs(provisional.children[index]).external_partial_ready
            for index in range(self.h0_count)
        ]
        return self.adapter.outputs(node_outputs.partials, parent_partial, child_ready), child_ready

    def outputs(self, parent_partial: RoutedPartial | None = None) -> H1TileOutputs:
        parent = parent_partial or RoutedPartial()
        adapter_outputs, _ = self._selected_children(parent)
        h0_outputs = [
            tile.outputs(adapter_outputs.children[index])
            for index, tile in enumerate(self.h0_tiles)
        ]
        node_outputs = self.node.outputs()
        return H1TileOutputs(
            init_done=all(output.init_done for output in h0_outputs),
            iter_done=all(output.iter_done for output in h0_outputs),
            h0_command_ready=tuple(output.command_ready for output in h0_outputs),
            h0_weight_ready=tuple(output.weight_ready for output in h0_outputs),
            h1_command_ready=node_outputs.command_ready,
            h1_weight_ready=node_outputs.weight_ready,
            parent_partial_ready=adapter_outputs.parent_ready,
            state_current=tuple(output.state_current for output in h0_outputs),
            state_next=tuple(output.state_next for output in h0_outputs),
        )

    def _next_state(self, iter_start: bool, commit: bool, iter_done: bool,
                    node_state_ready: bool) -> str:
        final_index = self.h0_count * self.cores_per_h0 - 1
        if self.tile_state == self.IDLE:
            return self.LOAD_STATES if iter_start else self.IDLE
        if self.tile_state == self.LOAD_STATES:
            if node_state_ready and self.state_load_index == final_index:
                return self.START_NODE
            return self.LOAD_STATES
        if self.tile_state == self.START_NODE:
            return self.RUN
        if self.tile_state == self.RUN:
            if iter_done:
                return self.IDLE if commit else self.DONE
            return self.RUN
        return self.IDLE if commit else self.DONE

    def tick(
        self,
        *,
        rst: bool = False,
        init_start: bool = False,
        core_weight_valid: Sequence[Sequence[bool]] | None = None,
        core_weight_data: Sequence[Sequence[int]] | None = None,
        init_states: Sequence[Sequence[int]] | None = None,
        noise_seeds: Sequence[Sequence[int]] | None = None,
        coeff_a: int = 0, coeff_b: int = 0, coeff_c: int = 0,
        noise_amplitude: int = 0,
        iter_start: bool = False, commit: bool = False, done: bool = False,
        noise_decay: int = 0,
        h0_schedule_done: Sequence[bool] | None = None,
        h0_commands: Sequence[Sequence[DmaCommand | None]] | None = None,
        h0_weight_valid: Sequence[Sequence[bool]] | None = None,
        h0_weight_data: Sequence[Sequence[int]] | None = None,
        h1_schedule_done: bool = False,
        h1_commands: Sequence[DmaCommand | None] | None = None,
        h1_weight_valid: Sequence[bool] | None = None,
        h1_weight_data: Sequence[int] | None = None,
        parent_partial: RoutedPartial | None = None,
        parent_partials_done: bool = False,
    ) -> H1TileOutputs:
        parent = parent_partial or RoutedPartial()
        empty_bool = [[False] * len(tile.node.engines) for tile in self.h0_tiles]
        empty_int = [[0] * len(tile.node.engines) for tile in self.h0_tiles]
        empty_cmd = [[None] * len(tile.node.engines) for tile in self.h0_tiles]
        core_weight_valid = core_weight_valid or [
            [False] * self.cores_per_h0 for _ in range(self.h0_count)]
        core_weight_data = core_weight_data or [
            [0] * self.cores_per_h0 for _ in range(self.h0_count)]
        init_states = init_states or [
            [0] * self.cores_per_h0 for _ in range(self.h0_count)]
        noise_seeds = noise_seeds or [
            [0] * self.cores_per_h0 for _ in range(self.h0_count)]
        h0_schedule_done = h0_schedule_done or [False] * self.h0_count
        h0_commands = h0_commands or empty_cmd
        h0_weight_valid = h0_weight_valid or empty_bool
        h0_weight_data = h0_weight_data or empty_int

        before = self.outputs(parent)
        node_outputs = self.node.outputs()
        adapter_outputs, child_ready = self._selected_children(parent)
        old_tile_state = self.tile_state
        next_state = self._next_state(
            iter_start, commit, before.iter_done, node_outputs.state_ready
        )
        h1_partials_done = node_outputs.iter_done and self.parent_done_pending

        if rst:
            self.reset()
            return before

        for index, tile in enumerate(self.h0_tiles):
            tile.tick(
                init_start=init_start,
                core_weight_valid=core_weight_valid[index],
                core_weight_data=core_weight_data[index],
                init_states=init_states[index], noise_seeds=noise_seeds[index],
                coeff_a=coeff_a, coeff_b=coeff_b, coeff_c=coeff_c,
                noise_amplitude=noise_amplitude,
                iter_start=iter_start, commit=commit, done=done,
                noise_decay=noise_decay,
                schedule_done=h0_schedule_done[index],
                commands=h0_commands[index],
                weight_valid=h0_weight_valid[index],
                weight_data=h0_weight_data[index],
                external_partial=adapter_outputs.children[index],
                external_partials_done=h1_partials_done,
            )

        h0_index = self.state_load_index // self.cores_per_h0
        core_index = self.state_load_index % self.cores_per_h0
        self.node.tick(
            iter_start=old_tile_state == self.START_NODE,
            state_valid=old_tile_state == self.LOAD_STATES,
            state_index=self.state_load_index,
            state_data=before.state_current[h0_index][core_index],
            schedule_done=h1_schedule_done,
            commands=h1_commands,
            weight_valid=h1_weight_valid,
            weight_data=h1_weight_data,
            partial_ready=adapter_outputs.node_ready,
        )
        self.adapter.tick(node_outputs.partials, parent, child_ready)

        self.tile_state = next_state
        if iter_start:
            self.state_load_index = 0
            self.parent_done_pending = False
        else:
            final_index = self.h0_count * self.cores_per_h0 - 1
            if (old_tile_state == self.LOAD_STATES and node_outputs.state_ready and
                    self.state_load_index != final_index):
                self.state_load_index += 1
            if parent_partials_done:
                self.parent_done_pending = True
        return before
