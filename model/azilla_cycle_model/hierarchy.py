"""Cycle model of ``rtl/hierarchy_node.sv``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .compute import SymmetricMVM, SynchronousBlockSram
from .config import ArchitectureConfig
from .fixed import pack_lanes, unsigned


@dataclass(frozen=True, slots=True)
class DmaCommand:
    state_a_index: int
    state_b_index: int
    block_a: int
    block_b: int


@dataclass(frozen=True, slots=True)
class PartialOutput:
    valid: bool = False
    data: int = 0
    block_id: int = 0
    last: bool = False


@dataclass(frozen=True, slots=True)
class HierarchyOutputs:
    iter_done: bool
    state_ready: bool
    command_ready: tuple[bool, ...]
    weight_ready: tuple[bool, ...]
    partials: tuple[PartialOutput, ...]


class _Engine:
    IDLE = "IDLE"
    FETCH = "FETCH"
    START = "START"
    COMPUTE = "COMPUTE"
    OUTPUT_IDLE = "OUTPUT_IDLE"
    SEND_A = "SEND_A"
    SEND_B = "SEND_B"

    def __init__(self, config: ArchitectureConfig, timing_only: bool):
        self.config = config
        self.timing_only = timing_only
        self.mvm = SymmetricMVM(config, timing_only=timing_only)
        self.sram = SynchronousBlockSram(config.spin_count, 2, config.data_width)
        self.reset()

    def reset(self) -> None:
        count = self.config.spin_count
        self.state = self.IDLE
        self.output_state = self.OUTPUT_IDLE
        self.slot_valid = [False, False]
        self.load_active = False
        self.load_slot = 0
        self.weight_beat = 0
        self.active_slot = 0
        self.slot_commands: list[DmaCommand | None] = [None, None]
        self.result_valid = [False, False]
        self.result_reserved = [False, False]
        self.result_write_slot = 0
        self.result_read_slot = 0
        self.result_a = [[[0] * count for _ in range(2)]][0]
        self.result_b = [[[0] * count for _ in range(2)]][0]
        self.result_blocks: list[tuple[int, int]] = [(0, 0), (0, 0)]
        self.partial_beat = 0
        self.state_a = 0
        self.state_b = 0
        self.state_a_valid = False
        self.state_b_valid = False
        self.state_a_pending = False
        self.state_b_pending = False
        self.mvm.reset()
        self.sram.reset()

    def work_done(self) -> bool:
        return (
            self.state == self.IDLE
            and not any(self.slot_valid)
            and not self.load_active
            and self.output_state == self.OUTPUT_IDLE
            and not any(self.result_valid)
            and not any(self.result_reserved)
        )

    def partial(self) -> PartialOutput:
        if self.output_state not in (self.SEND_A, self.SEND_B):
            return PartialOutput()
        slot = self.result_read_slot
        values = self.result_a[slot] if self.output_state == self.SEND_A \
            else self.result_b[slot]
        which = 0 if self.output_state == self.SEND_A else 1
        block = self.result_blocks[slot][which]
        lanes = self.config.partial_lanes
        base = self.partial_beat * lanes
        data = 0 if self.timing_only else pack_lanes(
            values[base:base + lanes], self.config.accumulator_width
        )
        return PartialOutput(
            valid=True,
            data=data,
            block_id=block,
            last=self.partial_beat == self.config.partial_beats - 1,
        )


class HierarchyNode:
    RESET = "RESET"
    IDLE = "IDLE"
    RUN = "RUN"
    DONE = "DONE"

    def __init__(
        self,
        state_entry_count: int = 32,
        mvm_count: int = 1,
        *,
        config: ArchitectureConfig | None = None,
        timing_only: bool = False,
        state_bank_count: int | None = None,
    ):
        if state_entry_count <= 0 or mvm_count <= 0:
            raise ValueError("state and engine counts must be positive")
        resolved_bank_count = (config.state_bank_count if config is not None
                               else 8) if state_bank_count is None else state_bank_count
        if (resolved_bank_count <= 0 or
                resolved_bank_count & (resolved_bank_count - 1)):
            raise ValueError("state_bank_count must be a positive power of two")
        self.config = config or ArchitectureConfig()
        self.timing_only = timing_only
        self.state_mem = [0] * state_entry_count
        self.state_bank_count = resolved_bank_count
        self._state_responses: list[tuple[int, int]] = []
        self.engines = [_Engine(self.config, timing_only) for _ in range(mvm_count)]
        self.reset()

    def reset(self) -> None:
        self.node_state = self.RESET
        self.schedule_done_pending = False
        self._state_responses = []
        for engine in self.engines:
            engine.reset()

    def outputs(self) -> HierarchyOutputs:
        return HierarchyOutputs(
            iter_done=self.node_state == self.DONE,
            state_ready=self.node_state != self.RUN,
            command_ready=tuple(
                self.node_state == self.RUN
                and not engine.load_active
                and not all(engine.slot_valid)
                for engine in self.engines
            ),
            weight_ready=tuple(
                self.node_state == self.RUN and engine.load_active
                for engine in self.engines
            ),
            partials=tuple(engine.partial() for engine in self.engines),
        )

    def _node_next(self, iter_start: bool) -> str:
        if self.node_state == self.RESET:
            return self.IDLE
        if self.node_state == self.IDLE:
            return self.RUN if iter_start else self.IDLE
        if self.node_state == self.RUN:
            work_done = self.schedule_done_pending and all(
                engine.work_done() for engine in self.engines
            )
            return self.DONE if work_done else self.RUN
        return self.RUN if iter_start else self.DONE

    @staticmethod
    def _engine_next(engine: _Engine) -> str:
        if engine.state == engine.IDLE:
            result_busy = [
                engine.result_valid[index] or engine.result_reserved[index]
                for index in range(2)
            ]
            if any(engine.slot_valid) and not all(result_busy):
                return engine.FETCH
        elif engine.state == engine.FETCH:
            if engine.state_a_valid and engine.state_b_valid:
                return engine.START
        elif engine.state == engine.START:
            return engine.COMPUTE
        elif engine.state == engine.COMPUTE and engine.mvm.done:
            return engine.IDLE
        return engine.state

    @staticmethod
    def _output_next(engine: _Engine, accepted: bool) -> str:
        if engine.output_state == engine.OUTPUT_IDLE:
            return engine.SEND_A if any(engine.result_valid) else engine.OUTPUT_IDLE
        if (engine.output_state == engine.SEND_A and accepted and
                engine.partial_beat == engine.config.partial_beats - 1):
            return engine.SEND_B
        if (engine.output_state == engine.SEND_B and accepted and
                engine.partial_beat == engine.config.partial_beats - 1):
            return engine.OUTPUT_IDLE
        return engine.output_state

    def tick(
        self,
        *,
        rst: bool = False,
        iter_start: bool = False,
        state_valid: bool = False,
        state_index: int = 0,
        state_data: int = 0,
        schedule_done: bool = False,
        commands: Sequence[DmaCommand | None] | None = None,
        weight_valid: Sequence[bool] | None = None,
        weight_data: Sequence[int] | None = None,
        partial_ready: Sequence[bool] | None = None,
    ) -> HierarchyOutputs:
        count = len(self.engines)
        commands = tuple(commands or [None] * count)
        weight_valid = tuple(weight_valid or [False] * count)
        weight_data = tuple(weight_data or [0] * count)
        partial_ready = tuple(partial_ready or [False] * count)
        if not all(len(values) == count for values in
                   (commands, weight_valid, weight_data, partial_ready)):
            raise ValueError("one DMA/partial value is required per engine")

        before = self.outputs()
        if rst:
            self.reset()
            return before

        old_node_state = self.node_state
        node_next = self._node_next(iter_start)
        engine_next = [self._engine_next(engine) for engine in self.engines]
        accepted = [
            before.partials[index].valid and partial_ready[index]
            for index in range(count)
        ]
        output_next = [
            self._output_next(engine, accepted[index])
            for index, engine in enumerate(self.engines)
        ]

        # The RTL state banks return accepted reads after one cycle. Responses
        # are latched at this edge, so FETCH can advance on the following edge.
        old_state_responses = self._state_responses
        next_state_responses: list[tuple[int, int]] = []
        used_banks: set[int] = set()
        for lane in range(2 * count):
            engine = self.engines[lane // 2]
            operand_b = bool(lane & 1)
            valid = engine.state == engine.FETCH and (
                not engine.state_b_valid and not engine.state_b_pending
                if operand_b else
                not engine.state_a_valid and not engine.state_a_pending
            )
            if not valid:
                continue
            command = engine.slot_commands[engine.active_slot]
            if command is None:
                raise RuntimeError("state fetch has no command metadata")
            state_index = command.state_b_index if operand_b else command.state_a_index
            bank = state_index % self.state_bank_count
            if bank in used_banks:
                continue
            used_banks.add(bank)
            next_state_responses.append((lane, self.state_mem[state_index]))
            if operand_b:
                engine.state_b_pending = True
            else:
                engine.state_a_pending = True

        self.node_state = node_next
        if state_valid and before.state_ready:
            self.state_mem[state_index] = unsigned(state_data, self.config.spin_count)
        if iter_start:
            self.schedule_done_pending = False
        elif schedule_done:
            self.schedule_done_pending = True

        for index, engine in enumerate(self.engines):
            old_state = engine.state
            old_output_state = engine.output_state
            old_done = engine.mvm.done
            old_results_a = None if self.timing_only else list(engine.mvm.result_a)
            old_results_b = None if self.timing_only else list(engine.mvm.result_b)
            old_read_data = 0 if self.timing_only else engine.sram.read_data
            old_read_row = engine.mvm.weight_row
            old_active_slot = engine.active_slot
            old_load_slot = engine.load_slot
            old_weight_beat = engine.weight_beat
            old_partial_beat = engine.partial_beat
            old_result_read_slot = engine.result_read_slot
            old_result_write_slot = engine.result_write_slot
            # RTL nonblocking assignments select from pre-edge validity even
            # when loading or computing completes on this same edge.
            old_slot_valid = tuple(engine.slot_valid)
            old_result_valid = tuple(engine.result_valid)
            old_result_reserved = tuple(engine.result_reserved)

            command_accepted = commands[index] is not None and before.command_ready[index]
            weight_accepted = weight_valid[index] and before.weight_ready[index]

            engine.mvm.tick(
                start=old_state == engine.START,
                state_a=engine.state_a,
                state_b=engine.state_b,
                weight_data=old_read_data,
            )
            if not self.timing_only:
                engine.sram.tick(
                    read_slot=old_active_slot,
                    read_row=old_read_row,
                    write_enable=weight_accepted,
                    write_slot=old_load_slot,
                    write_row=old_weight_beat,
                    write_data=weight_data[index],
                )

            engine.state = engine_next[index]
            engine.output_state = output_next[index]

            if command_accepted:
                slot = 0 if not engine.slot_valid[0] else 1
                engine.load_slot = slot
                engine.slot_commands[slot] = commands[index]
                engine.load_active = True
                engine.weight_beat = 0

            if weight_accepted:
                if old_weight_beat == self.config.weight_beats - 1:
                    engine.slot_valid[old_load_slot] = True
                    engine.load_active = False
                    engine.weight_beat = 0
                else:
                    engine.weight_beat = old_weight_beat + 1

            if old_state == engine.IDLE and engine_next[index] == engine.FETCH:
                selected_j = 0 if old_slot_valid[0] else 1
                selected_result = int(
                    old_result_valid[0] or old_result_reserved[0]
                )
                engine.active_slot = selected_j
                engine.result_write_slot = selected_result
                engine.result_reserved[selected_result] = True
                command = engine.slot_commands[selected_j]
                if command is None:
                    raise RuntimeError("valid J slot has no command metadata")
                engine.result_blocks[selected_result] = (
                    command.block_a, command.block_b
                )
                engine.state_a_valid = False
                engine.state_b_valid = False
                engine.state_a_pending = False
                engine.state_b_pending = False

            if old_state == engine.COMPUTE and old_done:
                if not self.timing_only:
                    engine.result_a[old_result_write_slot] = old_results_a
                    engine.result_b[old_result_write_slot] = old_results_b
                engine.result_reserved[old_result_write_slot] = False
                engine.result_valid[old_result_write_slot] = True
                engine.slot_valid[old_active_slot] = False

            if (old_output_state == engine.OUTPUT_IDLE and
                    output_next[index] == engine.SEND_A):
                engine.result_read_slot = 0 if old_result_valid[0] else 1

            if old_output_state in (engine.SEND_A, engine.SEND_B):
                if accepted[index]:
                    engine.partial_beat = (
                        old_partial_beat + 1
                    ) % self.config.partial_beats
            else:
                engine.partial_beat = 0

            if (old_output_state == engine.SEND_B and accepted[index] and
                    old_partial_beat == self.config.partial_beats - 1):
                engine.result_valid[old_result_read_slot] = False

        for lane, data in old_state_responses:
            engine = self.engines[lane // 2]
            if lane & 1:
                engine.state_b = data
                engine.state_b_valid = True
                engine.state_b_pending = False
            else:
                engine.state_a = data
                engine.state_a_valid = True
                engine.state_a_pending = False
        self._state_responses = next_state_responses

        return before
