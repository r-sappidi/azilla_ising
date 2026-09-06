"""Architectural parameters shared by all cycle-model components."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchitectureConfig:
    spin_count: int = 32
    weight_width: int = 8
    data_width: int = 256
    accumulator_width: int = 32
    coefficient_width: int = 16
    router_fifo_depth: int = 4
    state_bank_count: int = 8

    def __post_init__(self) -> None:
        if self.spin_count <= 0:
            raise ValueError("spin_count must be positive")
        if self.weight_width <= 0 or self.data_width <= 0:
            raise ValueError("data widths must be positive")
        if self.data_width % self.accumulator_width:
            raise ValueError("data_width must contain whole accumulator lanes")
        if self.spin_count * self.weight_width != self.data_width:
            raise ValueError(
                "current RTL requires one complete J row per data beat"
            )
        if self.router_fifo_depth < 2:
            raise ValueError("FlooNoC optimal FIFO requires depth >= 2")
        if (self.state_bank_count <= 0 or
                self.state_bank_count & (self.state_bank_count - 1)):
            raise ValueError("state_bank_count must be a positive power of two")

    @property
    def partial_lanes(self) -> int:
        return self.data_width // self.accumulator_width

    @property
    def partial_beats(self) -> int:
        return self.spin_count // self.partial_lanes

    @property
    def weight_beats(self) -> int:
        return self.spin_count
