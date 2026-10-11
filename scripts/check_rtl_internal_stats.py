#!/usr/bin/env python3
"""Conservation checks for ising_mesh_tb internal hierarchy counters."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


WEIGHT_BEATS_PER_JOB = 32
PARTIAL_BEATS_PER_JOB = 8


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stats", type=Path)
    args = parser.parse_args()

    rows: dict[tuple[int, str, int, str], dict[str, str]] = {}
    with args.stats.open(newline="") as handle:
        for row in csv.DictReader(handle):
            key = (
                int(row["node"]),
                row["endpoint_class"],
                int(row["endpoint_id"]),
                row["transfer"],
            )
            rows[key] = row

    errors: list[str] = []
    endpoints = sorted({key[:3] for key in rows})
    total_jobs = 0
    for endpoint in endpoints:
        command = int(rows[(*endpoint, "command")]["accepted_transfers"])
        weight = int(rows[(*endpoint, "weight")]["accepted_transfers"])
        partial = int(rows[(*endpoint, "partial")]["accepted_transfers"])
        total_jobs += command
        if weight != command * WEIGHT_BEATS_PER_JOB:
            errors.append(
                f"{endpoint}: {weight} weight beats != {command} jobs * "
                f"{WEIGHT_BEATS_PER_JOB}"
            )
        if partial != command * PARTIAL_BEATS_PER_JOB:
            errors.append(
                f"{endpoint}: {partial} partial beats != {command} jobs * "
                f"{PARTIAL_BEATS_PER_JOB}"
            )
        for transfer in ("command", "weight", "state_load", "partial"):
            row = rows[(*endpoint, transfer)]
            offered = int(row["offered_lane_cycles"])
            accepted = int(row["accepted_transfers"])
            stalled = int(row["stalled_lane_cycles"])
            if offered != accepted + stalled:
                errors.append(
                    f"{endpoint}/{transfer}: offered {offered} != accepted "
                    f"{accepted} + stalled {stalled}"
                )
            first = int(row["first_accept_cycle"])
            last = int(row["last_accept_cycle"])
            if accepted and (first == 0 or last < first):
                errors.append(
                    f"{endpoint}/{transfer}: invalid acceptance interval {first}..{last}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        f"PASS: {len(endpoints)} endpoints, {total_jobs} jobs; command/weight/"
        "partial volume and ready/valid conservation checks passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
