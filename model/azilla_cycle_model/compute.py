"""Cycle-accurate arithmetic primitives from ``rtl/mvm.sv`` and friends."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ArchitectureConfig
from .fixed import (
    add_signed,
    dot_spin_row,
    lfsr_advance_32,
    signed,
    slice_signed,
    unpack_signed_lanes,
    unsigned,
)


@dataclass(slots=True)
class SynchronousBlockSram:
    """Behavioral mirror of ``j_block_sram``.

    ``read_data`` is the registered output visible before the next rising
    edge.  A same-edge read/write collision returns the old memory value, as
    the RTL nonblocking assignments do in simulation.
    """

    row_count: int = 32
    slot_count: int = 2
    row_width: int = 256
    rows: list[list[int]] = field(init=False)
    read_data: int = 0

    def __post_init__(self) -> None:
        self.rows = [[0 for _ in range(self.row_count)]
                     for _ in range(self.slot_count)]

    def reset(self) -> None:
        # j_block_sram has no reset; only its registered read output starts as
        # zero in the Python model to match the testbench's time-zero value.
        self.read_data = 0

    def tick(
        self,
        *,
        read_slot: int,
        read_row: int,
        write_enable: bool = False,
        write_slot: int = 0,
        write_row: int = 0,
        write_data: int = 0,
    ) -> None:
        next_read = self.rows[read_slot][read_row]
        if write_enable:
            self.rows[write_slot][write_row] = unsigned(write_data, self.row_width)
        self.read_data = next_read


@dataclass(slots=True)
class MVM:
    """Row-serial mirror of ``rtl/mvm.sv``."""

    config: ArchitectureConfig = field(default_factory=ArchitectureConfig)
    timing_only: bool = False
    active: bool = False
    process_row: int = 0
    request_row: int = 0
    read_valid: bool = False
    result: list[int] = field(init=False)
    done: bool = False

    def __post_init__(self) -> None:
        self.result = [0] * self.config.spin_count

    @property
    def weight_row(self) -> int:
        return self.request_row

    def reset(self) -> None:
        self.active = False
        self.process_row = 0
        self.request_row = 0
        self.read_valid = False
        self.result = [0] * self.config.spin_count
        self.done = False

    def tick(self, *, start: bool, state: int, weight_data: int, rst: bool = False) -> None:
        if rst:
            self.reset()
            return

        if start and not self.active:
            self.active = True
            self.done = False
            self.result = [0] * self.config.spin_count
            self.process_row = 0
            self.request_row = 1 % self.config.spin_count
            self.read_valid = True
            return

        if self.active and self.read_valid:
            row_dot = 0 if self.timing_only else dot_spin_row(
                weight_data,
                state,
                spin_count=self.config.spin_count,
                weight_width=self.config.weight_width,
                accumulator_width=self.config.accumulator_width,
            )
            self.result[self.process_row] = row_dot
            if self.process_row == self.config.spin_count - 1:
                self.process_row = 0
                self.request_row = 0
                self.read_valid = False
                self.active = False
                self.done = True
            else:
                self.process_row += 1
                if self.request_row != self.config.spin_count - 1:
                    self.request_row += 1


@dataclass(slots=True)
class SymmetricMVM:
    """Fused bidirectional mirror of ``rtl/symmetric_mvm.sv``."""

    config: ArchitectureConfig = field(default_factory=ArchitectureConfig)
    timing_only: bool = False
    active: bool = False
    process_row: int = 0
    request_row: int = 0
    read_valid: bool = False
    result_a: list[int] = field(init=False)
    result_b: list[int] = field(init=False)
    done: bool = False

    def __post_init__(self) -> None:
        self.result_a = [0] * self.config.spin_count
        self.result_b = [0] * self.config.spin_count

    @property
    def weight_row(self) -> int:
        return self.request_row

    def reset(self) -> None:
        self.active = False
        self.process_row = 0
        self.request_row = 0
        self.read_valid = False
        self.result_a = [0] * self.config.spin_count
        self.result_b = [0] * self.config.spin_count
        self.done = False

    def tick(
        self,
        *,
        start: bool,
        state_a: int,
        state_b: int,
        weight_data: int,
        rst: bool = False,
    ) -> None:
        if rst:
            self.reset()
            return

        # Unlike rtl/mvm.sv, symmetric_mvm pulses done for one cycle.
        self.done = False

        if start and not self.active:
            self.active = True
            self.process_row = 0
            self.request_row = 1 % self.config.spin_count
            self.read_valid = True
            self.result_a = [0] * self.config.spin_count
            self.result_b = [0] * self.config.spin_count
            return

        if not (self.active and self.read_valid):
            return

        row = self.process_row
        if not self.timing_only:
            self.result_a[row] = dot_spin_row(
                weight_data,
                state_b,
                spin_count=self.config.spin_count,
                weight_width=self.config.weight_width,
                accumulator_width=self.config.accumulator_width,
            )
            state_a_positive = bool((state_a >> row) & 1)
            for column in range(self.config.spin_count):
                weight = slice_signed(
                    weight_data,
                    column * self.config.weight_width,
                    self.config.weight_width,
                )
                term = weight if state_a_positive else -weight
                self.result_b[column] = add_signed(
                    self.result_b[column], term, self.config.accumulator_width
                )

        if row == self.config.spin_count - 1:
            self.active = False
            self.process_row = 0
            self.request_row = 0
            self.read_valid = False
            self.done = True
        else:
            self.process_row += 1
            if self.request_row != self.config.spin_count - 1:
                self.request_row += 1


def run_symmetric_block(
    rows: list[int], state_a: int, state_b: int,
    config: ArchitectureConfig | None = None,
) -> tuple[list[int], list[int], int]:
    """Convenience runner retaining the exact start/read/complete latency."""

    cfg = config or ArchitectureConfig()
    if len(rows) != cfg.spin_count:
        raise ValueError("one packed weight row is required per spin")
    sram = SynchronousBlockSram(cfg.spin_count, 2, cfg.data_width)
    sram.rows[0] = [unsigned(row, cfg.data_width) for row in rows]
    mvm = SymmetricMVM(cfg)
    cycle = 0
    start = True
    while True:
        # Both sequential blocks sample pre-edge values.
        old_data = sram.read_data
        old_address = mvm.weight_row
        mvm.tick(start=start, state_a=state_a, state_b=state_b,
                 weight_data=old_data)
        sram.tick(read_slot=0, read_row=old_address)
        cycle += 1
        start = False
        if mvm.done:
            return list(mvm.result_a), list(mvm.result_b), cycle


@dataclass(frozen=True, slots=True)
class SpinCoreOutputs:
    init_done: bool
    weight_init_ready: bool
    h0_partial_ready: bool
    ext_partial_ready: bool
    iter_done: bool
    state_next: int
    state_current: int


class SpinCore:
    """Cycle mirror of ``rtl/core.sv`` including its resident MVM."""

    RESET = "RESET"
    INIT = "INIT"
    IDLE = "IDLE"
    ACCUMULATE = "ACCUMULATE"
    FINALIZE = "FINALIZE"
    WAIT_COMMIT = "WAIT_COMMIT"
    COMMIT = "COMMIT"
    DONE = "DONE"

    def __init__(self, config: ArchitectureConfig | None = None, *, timing_only: bool = False):
        self.config = config or ArchitectureConfig()
        self.timing_only = timing_only
        self.sram = SynchronousBlockSram(
            self.config.spin_count, 1, self.config.data_width
        )
        self.local_mvm = MVM(self.config, timing_only=timing_only)
        self.reset()

    def reset(self) -> None:
        count = self.config.spin_count
        self.core_state = self.RESET
        self.state_current = 0
        self.coeff_a_reg = 0
        self.coeff_b_reg = 0
        self.weight_beat_count = 0
        self.h0_partial_beat_count = 0
        self.ext_partial_beat_count = 0
        self.partials_done_pending = False
        self.init_done = False
        self.accumulator_h0 = [0] * count
        self.accumulator_ext = [0] * count
        self.accumulator_total = [0] * count
        self.done_latched = False
        self.lfsr_state = 0
        self.local_mvm.reset()
        self.sram.reset()

    def outputs(self) -> SpinCoreOutputs:
        state_next = 0
        for spin, value in enumerate(self.accumulator_total):
            if signed(value, self.config.accumulator_width) >= 0:
                state_next |= 1 << spin
        return SpinCoreOutputs(
            init_done=self.init_done,
            weight_init_ready=(self.core_state == self.INIT and
                               self.weight_beat_count < self.config.weight_beats),
            h0_partial_ready=self.core_state == self.ACCUMULATE,
            ext_partial_ready=self.core_state == self.ACCUMULATE,
            iter_done=self.core_state == self.WAIT_COMMIT,
            state_next=state_next,
            state_current=self.state_current,
        )

    def _next_state(self, *, init_start: bool, iter_start: bool, commit: bool,
                    done: bool) -> str:
        state = self.core_state
        if state == self.RESET:
            return self.INIT if init_start else state
        if state == self.INIT:
            return self.IDLE if not self.outputs().weight_init_ready else state
        if state == self.IDLE:
            return self.ACCUMULATE if iter_start else state
        if state == self.ACCUMULATE:
            if (self.local_mvm.done and self.partials_done_pending and
                    self.h0_partial_beat_count == 0 and
                    self.ext_partial_beat_count == 0):
                return self.FINALIZE
            return state
        if state == self.FINALIZE:
            return self.WAIT_COMMIT
        if state == self.WAIT_COMMIT:
            return self.COMMIT if commit else state
        if state == self.COMMIT:
            return self.DONE if (self.done_latched or done) else self.IDLE
        return self.DONE

    def tick(
        self,
        *,
        rst: bool = False,
        init_start: bool = False,
        weight_init_valid: bool = False,
        weight_init_data: int = 0,
        noise_seed: int = 0,
        coeff_a: int = 0,
        coeff_b: int = 0,
        coeff_c: int = 0,
        noise_amplitude: int = 0,
        init_state: int = 0,
        iter_start: bool = False,
        partials_done: bool = False,
        commit: bool = False,
        noise_decay: int = 0,
        done: bool = False,
        h0_partial_valid: bool = False,
        h0_partial_data: int = 0,
        ext_partial_valid: bool = False,
        ext_partial_data: int = 0,
    ) -> SpinCoreOutputs:
        del coeff_c, noise_decay  # Present but intentionally unused in RTL.
        before = self.outputs()
        old_sram_data = self.sram.read_data
        old_sram_address = self.local_mvm.weight_row
        local_start = iter_start and self.core_state == self.IDLE

        if rst:
            self.reset()
            return before

        next_state = self._next_state(
            init_start=init_start, iter_start=iter_start, commit=commit, done=done
        )
        old_state = self.core_state
        old_state_next = before.state_next

        if self.core_state == self.IDLE and iter_start:
            self.coeff_a_reg = signed(coeff_a, self.config.coefficient_width)
            self.coeff_b_reg = signed(coeff_b, self.config.coefficient_width)

        # The MVM and SRAM sample the same pre-edge signals as core.sv.
        self.local_mvm.tick(
            start=local_start,
            state=self.state_current,
            weight_data=old_sram_data,
        )
        self.sram.tick(
            read_slot=0,
            read_row=old_sram_address,
            write_enable=weight_init_valid and before.weight_init_ready,
            write_slot=0,
            write_row=self.weight_beat_count % self.config.weight_beats,
            write_data=weight_init_data,
        )

        self.done_latched = self.done_latched or done
        self.core_state = next_state

        if iter_start:
            self.lfsr_state = lfsr_advance_32(self.lfsr_state)
            self.partials_done_pending = False
        elif partials_done:
            self.partials_done_pending = True

        if init_start:
            self.state_current = unsigned(init_state, self.config.spin_count)
            self.coeff_a_reg = signed(coeff_a, self.config.coefficient_width)
            self.coeff_b_reg = signed(coeff_b, self.config.coefficient_width)
            self.lfsr_state = unsigned(noise_seed, 32)

        if old_state == self.INIT and weight_init_valid and before.weight_init_ready:
            self.weight_beat_count += 1

        if next_state == self.IDLE:
            self.init_done = True

        if old_state == self.IDLE and iter_start:
            self.accumulator_h0 = [0] * self.config.spin_count
            self.accumulator_ext = [0] * self.config.spin_count
            self.h0_partial_beat_count = 0
            self.ext_partial_beat_count = 0
            self.partials_done_pending = False

        if old_state == self.ACCUMULATE:
            lanes = self.config.partial_lanes
            if h0_partial_valid and before.h0_partial_ready:
                values = unpack_signed_lanes(
                    h0_partial_data, self.config.accumulator_width, lanes
                )
                base = self.h0_partial_beat_count * lanes
                for lane, value in enumerate(values):
                    self.accumulator_h0[base + lane] = add_signed(
                        self.accumulator_h0[base + lane], value,
                        self.config.accumulator_width,
                    )
                self.h0_partial_beat_count = (
                    self.h0_partial_beat_count + 1
                ) % self.config.partial_beats

            if ext_partial_valid and before.ext_partial_ready:
                values = unpack_signed_lanes(
                    ext_partial_data, self.config.accumulator_width, lanes
                )
                base = self.ext_partial_beat_count * lanes
                for lane, value in enumerate(values):
                    self.accumulator_ext[base + lane] = add_signed(
                        self.accumulator_ext[base + lane], value,
                        self.config.accumulator_width,
                    )
                self.ext_partial_beat_count = (
                    self.ext_partial_beat_count + 1
                ) % self.config.partial_beats

        if old_state == self.FINALIZE:
            acc_width = self.config.accumulator_width
            coeff_width = self.config.coefficient_width
            next_total = [0] * self.config.spin_count
            for spin in range(self.config.spin_count):
                a_term = self.coeff_a_reg if (self.state_current >> spin) & 1 \
                    else -self.coeff_a_reg
                a_term = signed(a_term, coeff_width)
                noise = noise_amplitude if (self.lfsr_state >> spin) & 1 \
                    else -noise_amplitude
                noise = signed(noise, coeff_width)
                interaction = add_signed(
                    self.local_mvm.result[spin], self.accumulator_h0[spin], acc_width
                )
                interaction = add_signed(
                    interaction, self.accumulator_ext[spin], acc_width
                )
                product = signed(self.coeff_b_reg * interaction, acc_width)
                total = add_signed(a_term, product, acc_width)
                next_total[spin] = add_signed(total, noise, acc_width)
            self.accumulator_total = next_total

        if old_state == self.COMMIT:
            self.state_current = old_state_next

        return before
