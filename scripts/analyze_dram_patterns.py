#!/usr/bin/env python3
"""Summarize per-tile Ramulator demand and latency for exact confirmations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import fmean, pstdev


def coefficient_of_variation(values: list[float]) -> float:
    mean = fmean(values) if values else 0.0
    return pstdev(values) / mean if mean else 0.0


def gini(values: list[float]) -> float:
    ordered = sorted(value for value in values if value >= 0)
    total = sum(ordered)
    count = len(ordered)
    if not count or not total:
        return 0.0
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return (2 * weighted) / (count * total) - (count + 1) / count


def summarize(dataset: str, level: str, systems: list[dict]) -> dict:
    accepted = [float(row["accepted_requests"]) for row in systems]
    accepted_total = sum(accepted)
    rejected_total = sum(int(row["rejected_requests"]) for row in systems)
    completed_total = sum(int(row["completed_requests"]) for row in systems)
    weighted_latency = sum(
        float(row["average_latency_cycles"])
        * int(row["accepted_requests"])
        for row in systems
    )
    return {
        "dataset": dataset,
        "level": level,
        "memory_systems": len(systems),
        "scheduled_jobs": sum(int(row["scheduled_jobs"]) for row in systems),
        "accepted_requests": int(accepted_total),
        "completed_requests": completed_total,
        "rejected_requests": rejected_total,
        "request_rejection_fraction": (
            rejected_total / (accepted_total + rejected_total)
            if accepted_total + rejected_total else 0.0
        ),
        "request_weighted_average_latency_cycles": (
            weighted_latency / accepted_total if accepted_total else 0.0
        ),
        "maximum_latency_cycles": max(
            (float(row["maximum_latency_cycles"]) for row in systems),
            default=0.0,
        ),
        "mean_requests_per_system": fmean(accepted) if accepted else 0.0,
        "minimum_requests_per_system": min(accepted, default=0.0),
        "maximum_requests_per_system": max(accepted, default=0.0),
        "request_load_cv": coefficient_of_variation(accepted),
        "request_load_gini": gini(accepted),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path,
        default=Path("results/paper_benchmarks/exact_confirmation"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("results/paper_benchmarks/dram_patterns.csv"),
    )
    args = parser.parse_args()
    rows = []
    for path in sorted(args.root.glob("*/*_summary.json")):
        dataset = path.parent.name
        summary = json.loads(path.read_text())
        systems = summary.get("dram_systems", [])
        if not systems:
            continue
        rows.append(summarize(dataset, "all", systems))
        for level in sorted({str(row["level"]) for row in systems}):
            rows.append(summarize(
                dataset, level,
                [row for row in systems if row["level"] == level],
            ))
    if not rows:
        raise SystemExit(f"no exact DRAM summaries below {args.root}")
    write_csv(args.output, rows)
    args.output.with_suffix(".json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"results={args.output} datasets="
        f"{len({row['dataset'] for row in rows})} rows={len(rows)}"
    )


if __name__ == "__main__":
    main()
