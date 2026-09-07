"""Explicit single-MVM endpoint iteration controller (validation in progress).

This is not a second parallel off-diagonal engine. Slot zero retains diagonal
weights, slot one receives a directed canonical block, and both use one MVM.
Operand reads require an external ready/response handshake.
"""
from dataclasses import dataclass
from .compute import MVM, SpinCore, SynchronousBlockSram
from .fixed import add_signed, lfsr_advance_32, signed, slice_signed, unsigned


class DirectedMVM(MVM):
    transpose_mode = False

    def reset(self):
        super().reset()
        self.transpose_mode = False

    def tick(self, *, start=False, state=0, weight_data=0, transpose=False, rst=False):
        row = self.process_row
        process = self.active and self.read_valid and not rst
        mode = self.transpose_mode
        old_result = self.result.copy() if process and mode else None
        if start and not self.active and not rst:
            self.transpose_mode = transpose
        super().tick(start=start, state=state, weight_data=weight_data, rst=rst)
        if process and mode:
            if self.timing_only:
                self.result = [0] * self.config.spin_count
            else:
                spin = 1 if (state >> row) & 1 else -1
                self.result = [add_signed(old_result[c], spin * slice_signed(
                    weight_data, c*self.config.weight_width, self.config.weight_width),
                    self.config.accumulator_width) for c in range(self.config.spin_count)]


@dataclass(frozen=True)
class CoreIterationOutputs:
    init_done: bool
    weight_init_ready: bool
    iter_done: bool
    state_next: int
    state_current: int
    job_ready: bool
    state_req_valid: bool
    state_req_block_id: int
    weight_ready: bool
    job_done: bool


class CoreIteration(SpinCore):
    OFF_IDLE = "IDLE"
    STATE_REQ = "STATE_REQ"
    STATE_WAIT = "STATE_WAIT"
    LOAD = "LOAD"
    RUN = "RUN"
    RETIRE = "RETIRE"

    def __init__(self, config=None, *, timing_only=False):
        super().__init__(config, timing_only=timing_only)
        self.local_mvm = DirectedMVM(self.config, timing_only=timing_only)
        self.sram = SynchronousBlockSram(self.config.spin_count, 2, self.config.data_width)

    def reset(self):
        super().reset()
        self.off_state = self.OFF_IDLE
        self.diagonal_captured = False
        self.diagonal_result = [0] * self.config.spin_count
        self.directed_result = [0] * self.config.spin_count
        self.off_beat = 0
        self.source = 0
        self.source_state = 0
        self.transpose = False
        self.job_done = False

    def outputs(self):
        base = super().outputs()
        return CoreIterationOutputs(
            base.init_done, base.weight_init_ready, base.iter_done,
            base.state_next, base.state_current,
            self.core_state == self.ACCUMULATE and self.diagonal_captured
            and self.off_state == self.OFF_IDLE and not self.partials_done_pending,
            self.off_state == self.STATE_REQ, self.source,
            self.off_state == self.LOAD, self.off_state == self.RETIRE)

    def tick(self, *, rst=False, init_start=False, weight_init_valid=False,
             weight_init_data=0, init_state=0, noise_seed=0, coeff_a=0,
             coeff_b=0, coeff_c=0, noise_amplitude=0, noise_decay=0,
             iter_start=False, partials_done=False, commit=False, done=False,
             job_valid=False, source=0, transpose=False,
             state_req_ready=False, state_rsp_valid=False, state_rsp_data=0,
             weight_valid=False, weight_data=0):
        del coeff_c, noise_decay
        before = self.outputs()
        if rst:
            self.reset()
            return before
        old, off = self.core_state, self.off_state
        old_done, old_result = self.local_mvm.done, self.local_mvm.result.copy()
        old_row, old_data = self.local_mvm.weight_row, self.sram.read_data
        diagonal_start = old == self.IDLE and iter_start
        off_start = off == self.LOAD and weight_valid and self.off_beat == 31
        next_state = old
        if old == self.RESET and init_start:
            next_state = self.INIT
        elif old == self.INIT and not before.weight_init_ready:
            next_state = self.IDLE
        elif diagonal_start:
            next_state = self.ACCUMULATE
        elif (old == self.ACCUMULATE and self.diagonal_captured
              and off == self.OFF_IDLE and self.partials_done_pending and not job_valid):
            next_state = self.FINALIZE
        elif old == self.FINALIZE:
            next_state = self.WAIT_COMMIT
        elif old == self.WAIT_COMMIT and commit:
            next_state = self.COMMIT
        elif old == self.COMMIT:
            next_state = self.DONE if self.done_latched or done else self.IDLE

        offdiagonal = off in (self.LOAD, self.RUN, self.RETIRE)
        self.local_mvm.tick(start=diagonal_start or off_start,
                            state=self.source_state if offdiagonal else self.state_current,
                            weight_data=old_data,
                            transpose=self.transpose if offdiagonal else False)
        writing_diagonal = weight_init_valid and before.weight_init_ready
        writing_off = weight_valid and before.weight_ready
        self.sram.tick(read_slot=int(offdiagonal), read_row=old_row,
                       write_enable=writing_diagonal or writing_off,
                       write_slot=int(writing_off),
                       write_row=self.off_beat if writing_off else self.weight_beat_count % 32,
                       write_data=weight_data if writing_off else weight_init_data)
        self.job_done = False
        self.done_latched |= done
        if init_start:
            self.state_current = unsigned(init_state, 32)
            self.lfsr_state = unsigned(noise_seed, 32)
            self.coeff_a_reg = signed(coeff_a, self.config.coefficient_width)
            self.coeff_b_reg = signed(coeff_b, self.config.coefficient_width)
        if iter_start:
            self.lfsr_state = lfsr_advance_32(self.lfsr_state)
            self.partials_done_pending = False
        elif partials_done:
            self.partials_done_pending = True
        if writing_diagonal:
            self.weight_beat_count += 1
        if next_state == self.IDLE:
            self.init_done = True
        if diagonal_start:
            self.coeff_a_reg = signed(coeff_a, self.config.coefficient_width)
            self.coeff_b_reg = signed(coeff_b, self.config.coefficient_width)
            self.diagonal_captured = False
            self.diagonal_result = [0] * 32
            self.directed_result = [0] * 32
            self.off_state = self.OFF_IDLE
        elif old == self.ACCUMULATE:
            if not self.diagonal_captured and old_done:
                self.diagonal_result = old_result
                self.diagonal_captured = True
            if off == self.OFF_IDLE and job_valid and before.job_ready:
                self.source, self.transpose = source, transpose
                self.off_beat = 0
                self.off_state = self.STATE_REQ
            elif off == self.STATE_REQ and state_req_ready:
                self.off_state = self.STATE_WAIT
            elif off == self.STATE_WAIT and state_rsp_valid:
                self.source_state = unsigned(state_rsp_data, 32)
                self.off_state = self.LOAD
            elif off == self.LOAD and writing_off:
                if self.off_beat == 31:
                    self.off_beat = 0
                    self.off_state = self.RUN
                else:
                    self.off_beat += 1
            elif off == self.RUN and old_done:
                self.off_state = self.RETIRE
            elif off == self.RETIRE:
                self.directed_result = [add_signed(a,b,self.config.accumulator_width)
                                        for a,b in zip(self.directed_result, old_result)]
                self.accumulator_h0 = self.directed_result.copy()
                self.off_state = self.OFF_IDLE
        if old == self.FINALIZE:
            width = self.config.accumulator_width
            cw = self.config.coefficient_width
            self.accumulator_total = [add_signed(add_signed(
                signed(self.coeff_a_reg if self.state_current >> s & 1 else -self.coeff_a_reg, cw),
                signed(self.coeff_b_reg * add_signed(self.diagonal_result[s], self.directed_result[s], width), width), width),
                signed(noise_amplitude if self.lfsr_state >> s & 1 else -noise_amplitude, cw), width)
                for s in range(32)]
        if old == self.COMMIT:
            self.state_current = before.state_next
        self.core_state = next_state
        return before
