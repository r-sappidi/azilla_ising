"""Opt-in, cycle-preserving timing-only compute optimizations.

No memory, NoC, scheduler, handshake, or arithmetic latency is approximated.
HierarchyNode.tick remains the reference implementation. Its immutable output
record is reused between edges instead of repeatedly allocating identical
records when adapters, streamers and arbitration query the same node.

As in normal simulation, mutate engine state through tick/reset. Diagnostic
code that directly edits an engine field must call invalidate_outputs() after
the edit. Direct node_state edits used by exact-event initialization are
detected automatically. Instances are not shared across simulator threads.
"""

from __future__ import annotations

from typing import Sequence

from .compute import SymmetricMVM
from .config import ArchitectureConfig
from .hierarchy import DmaCommand, HierarchyNode, HierarchyOutputs


class FastSymmetricMVM(SymmetricMVM):
    """Same timing-only row controller with invariant configuration hoisted."""

    __slots__ = ("_last_row", "_start_row")

    def __init__(self, config: ArchitectureConfig | None = None, *, timing_only: bool = True):
        if not timing_only:
            raise ValueError("FastSymmetricMVM supports timing-only execution")
        super().__init__(config=config or ArchitectureConfig(), timing_only=True)
        self._last_row = self.config.spin_count - 1
        self._start_row = 1 % self.config.spin_count

    def tick(self, *, start: bool, state_a: int, state_b: int,
             weight_data: int, rst: bool = False) -> None:
        if rst:
            self.reset()
            return
        self.done = False
        if start and not self.active:
            self.active = True
            self.process_row = 0
            self.request_row = self._start_row
            self.read_valid = True
            self.result_a = [0] * self.config.spin_count
            self.result_b = [0] * self.config.spin_count
            return
        if not (self.active and self.read_valid):
            return
        if self.process_row == self._last_row:
            self.active = False
            self.process_row = 0
            self.request_row = 0
            self.read_valid = False
            self.done = True
        else:
            self.process_row += 1
            if self.request_row != self._last_row:
                self.request_row += 1


class FastHierarchyNode(HierarchyNode):
    """Reference cycle transitions with memoized between-edge outputs."""

    def __init__(self, state_entry_count: int = 32, mvm_count: int = 1, *,
                 config: ArchitectureConfig | None = None,
                 timing_only: bool = True, state_bank_count: int | None = None):
        if not timing_only:
            raise ValueError("FastHierarchyNode supports timing-only execution")
        self._cached_outputs: HierarchyOutputs | None = None
        self._cached_node_state: str | None = None
        super().__init__(state_entry_count, mvm_count, config=config,
                         timing_only=True, state_bank_count=state_bank_count)
        for engine in self.engines:
            engine.mvm = FastSymmetricMVM(self.config)

    def invalidate_outputs(self) -> None:
        self._cached_outputs = None

    def reset(self) -> None:
        self.invalidate_outputs()
        super().reset()

    def outputs(self) -> HierarchyOutputs:
        cached = self._cached_outputs
        if cached is None or self._cached_node_state != self.node_state:
            cached = super().outputs()
            self._cached_outputs = cached
            self._cached_node_state = self.node_state
        return cached

    def tick(self, *, rst: bool = False, iter_start: bool = False,
             state_valid: bool = False, state_index: int = 0, state_data: int = 0,
             schedule_done: bool = False,
             commands: Sequence[DmaCommand | None] | None = None,
             weight_valid: Sequence[bool] | None = None,
             weight_data: Sequence[int] | None = None,
             partial_ready: Sequence[bool] | None = None) -> HierarchyOutputs:
        try:
            return super().tick(
                rst=rst, iter_start=iter_start, state_valid=state_valid,
                state_index=state_index, state_data=state_data,
                schedule_done=schedule_done, commands=commands,
                weight_valid=weight_valid, weight_data=weight_data,
                partial_ready=partial_ready,
            )
        finally:
            self.invalidate_outputs()
