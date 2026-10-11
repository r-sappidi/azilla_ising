#!/usr/bin/env python3
"""Replace edge weights with deterministic mixed-sign nonzero int8 values."""

from __future__ import annotations

import argparse
from pathlib import Path


def mixed_weight(source: int, destination: int) -> int:
    """Return the same deterministic weight for both directions of an edge."""
    low, high = sorted((source, destination))
    value = ((low * 1_103_515_245) ^ (high * 12_345)) & 0xFFFFFFFF
    magnitude = 1 + ((low * 131 + high * 17) % 7)
    return magnitude if value & 1 else -magnitude


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    positive = negative = records = 0
    with args.input.open() as source, args.output.open("w") as destination:
        header = source.readline()
        if not header:
            raise ValueError(f"empty dataset: {args.input}")
        destination.write(header)
        for line_number, line in enumerate(source, start=2):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 3:
                raise ValueError(f"{args.input}:{line_number}: expected three fields")
            row, column, _ = map(int, fields)
            weight = mixed_weight(row, column)
            destination.write(f"{row} {column} {weight}\n")
            records += 1
            positive += weight > 0
            negative += weight < 0

    print(
        f"wrote {args.output}: records={records} positive={positive} "
        f"negative={negative} weights=[-7,-1]U[1,7]"
    )


if __name__ == "__main__":
    main()
