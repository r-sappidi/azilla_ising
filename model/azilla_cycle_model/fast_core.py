"""Timing-only endpoint optimization; the original lifecycle remains the oracle.

This opt-in implementation does not alter CoreIteration or production selection.
It retains SRAM payload/read behavior and coefficient/noise/commit arithmetic.
Only timing-only MVM zero-result work and repeated output sign scans are removed.
"""
from .core_iteration import CoreIteration, CoreIterationOutputs
from .fixed import signed


class _CounterDirectedMVM:
    """One completion countdown; row signals are derived, not stepped arrays."""
    def __init__(self, config):
        self.config = config
        self.timing_only = True
        self.reset()

    def reset(self):
        self.remaining = 0
        self.done = False
        self.transpose_mode = False
        self.result = [0] * self.config.spin_count

    @property
    def active(self):
        return self.remaining != 0

    @property
    def read_valid(self):
        return self.active

    @property
    def process_row(self):
        return self.config.spin_count-self.remaining if self.active else 0

    @property
    def request_row(self):
        return min(self.process_row+1,self.config.spin_count-1) if self.active else 0

    @property
    def weight_row(self):
        return self.request_row

    def tick(self, *, start=False, state=0, weight_data=0, transpose=False, rst=False):
        if rst:
            self.reset()
        elif start and not self.active:
            self.remaining = self.config.spin_count
            self.done = False
            self.transpose_mode = transpose
        elif self.active:
            self.remaining -= 1
            if not self.remaining:
                self.done = True


class FastCoreIteration(CoreIteration):
    """Drop-in timing-only core. Arithmetic-enabled use is deliberately rejected.

    All lifecycle transitions use the unchanged CoreIteration.tick. Outputs
    cache the sign vector until FINALIZE/reset replaces accumulator_total.
    Direct external mutation of accumulator_total is not a controller input;
    callers doing that must call invalidate_outputs() afterward.
    """
    def __init__(self, config=None, *, timing_only=True):
        if not timing_only:
            raise ValueError("FastCoreIteration supports timing_only=True only")
        super().__init__(config,timing_only=True)
        self.local_mvm = _CounterDirectedMVM(self.config)

    def invalidate_outputs(self):
        self._total_identity = None

    def reset(self):
        super().reset()
        self.invalidate_outputs()

    def outputs(self):
        if self._total_identity is not self.accumulator_total:
            self._state_next = sum((1 << i) for i,value in enumerate(self.accumulator_total)
                if signed(value,self.config.accumulator_width) >= 0)
            self._total_identity = self.accumulator_total
        return CoreIterationOutputs(
            self.init_done,
            self.core_state == self.INIT and self.weight_beat_count < self.config.weight_beats,
            self.core_state == self.WAIT_COMMIT,self._state_next,self.state_current,
            self.core_state == self.ACCUMULATE and self.diagonal_captured
                and self.off_state == self.OFF_IDLE and not self.partials_done_pending,
            self.off_state == self.STATE_REQ,self.source,
            self.off_state == self.LOAD,self.off_state == self.RETIRE)
