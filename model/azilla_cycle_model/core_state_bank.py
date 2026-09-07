"""Shared synchronous operand SRAM for endpoint-only computation.

Mirrors banked_state_sram: fixed lane priority within each interleaved bank,
one read per bank per edge, one-cycle tagged responses, read-before-write.
The caller must explicitly populate the frozen operand table before use.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class StateBankResponse:
    lane: int
    index: int
    data: int


class CoreStateBank:
    def __init__(self, entries, *, banks=8, lanes=256, timing_only=False):
        if entries <= 0 or lanes <= 0 or banks <= 0 or banks & (banks - 1):
            raise ValueError("positive entries/lanes and power-of-two banks required")
        self.entries, self.banks, self.lanes = entries, banks, lanes
        self.timing_only = timing_only
        self.memory = {}
        self.responses = {}
        self.accepted_reads = 0
        self.stalled_reads = 0
        self.writes = 0

    def ready(self, requests):
        selected = {}
        used = set()
        for lane, index in sorted(requests.items()):
            if not 0 <= lane < self.lanes or not 0 <= index < self.entries:
                raise ValueError("operand SRAM lane/index out of range")
            bank = index % self.banks
            selected[lane] = bank not in used
            used.add(bank)
        return selected

    def tick(self, requests, *, write=None, rst=False):
        """Return pre-edge responses, then register this edge's accepted reads."""
        old = self.responses
        self.responses = {}
        if rst:
            return old
        ready = self.ready(requests)
        for lane, index in sorted(requests.items()):
            if not ready[lane]:
                self.stalled_reads += 1
                continue
            if index not in self.memory:
                raise RuntimeError(f"operand {index} read before frozen-state publication")
            self.responses[lane] = StateBankResponse(lane, index, self.memory[index])
            self.accepted_reads += 1
        if write is not None:
            index, data = write
            if not 0 <= index < self.entries:
                raise ValueError("operand SRAM write index out of range")
            self.memory[index] = 0 if self.timing_only else data & 0xffffffff
            self.writes += 1
        return old
