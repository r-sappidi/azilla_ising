"""SystemVerilog-compatible fixed-width helpers.

Python integers do not overflow.  Every state update that corresponds to an
RTL packed ``logic`` therefore passes through these helpers.
"""

from __future__ import annotations

from collections.abc import Iterable


def mask(width: int) -> int:
    if width <= 0:
        raise ValueError("width must be positive")
    return (1 << width) - 1


def unsigned(value: int, width: int) -> int:
    return value & mask(width)


def signed(value: int, width: int) -> int:
    value &= mask(width)
    sign = 1 << (width - 1)
    return value - (1 << width) if value & sign else value


def add_signed(lhs: int, rhs: int, width: int) -> int:
    return signed(lhs + rhs, width)


def mul_signed(lhs: int, rhs: int, width: int) -> int:
    return signed(lhs * rhs, width)


def slice_unsigned(word: int, lsb: int, width: int) -> int:
    return (word >> lsb) & mask(width)


def slice_signed(word: int, lsb: int, width: int) -> int:
    return signed(slice_unsigned(word, lsb, width), width)


def unpack_signed_lanes(word: int, lane_width: int, lane_count: int) -> list[int]:
    return [slice_signed(word, lane * lane_width, lane_width)
            for lane in range(lane_count)]


def pack_lanes(values: Iterable[int], lane_width: int) -> int:
    word = 0
    for lane, value in enumerate(values):
        word |= unsigned(value, lane_width) << (lane * lane_width)
    return word


def dot_spin_row(
    weight_word: int,
    state: int,
    *,
    spin_count: int = 32,
    weight_width: int = 8,
    accumulator_width: int = 32,
) -> int:
    """Mirror the RTL add/subtract loop for one row of ``J*x``."""

    total = 0
    for column in range(spin_count):
        weight = slice_signed(weight_word, column * weight_width, weight_width)
        total = add_signed(total, weight if (state >> column) & 1 else -weight,
                           accumulator_width)
    return total


def lfsr_feedback_32(value: int) -> int:
    value = unsigned(value, 32)
    return ((value >> 31) ^ (value >> 21) ^ (value >> 1) ^ value) & 1


def lfsr_advance_32(value: int) -> int:
    return unsigned((unsigned(value, 32) << 1) | lfsr_feedback_32(value), 32)
