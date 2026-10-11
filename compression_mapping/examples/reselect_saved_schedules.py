#!/usr/bin/env python3
"""Apply no-regression schedule selection to saved mapping measurements."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from run_mapping_stage_experiments import plot_results


TEXT_FIELDS = {"stage", "owner_policy", "estimated_accuracy"}
IDENTITY_FIELDS = {
    "vertices", "sparsity", "seed", "edges", "stage",
    "partition_seconds", "permutation_seconds", "scheduling_seconds",
    "stage_increment_seconds", "lossy_seconds", "cumulative_seconds",
    "dropped_weight_fraction", "dropped_blocks",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = list(csv.DictReader(args.input.open()))
    grouped = {}
    for row in raw:
        key = (row["vertices"], row["sparsity"], row["seed"])
        grouped.setdefault(key, {})[row["stage"]] = row

    rows = []
    for stages in grouped.values():
        lossy = stages["lossy"]
        balanced = stages["scheduled"]
        endpoint_cycles = float(lossy["estimated_iteration_cycles"])
        saved_balanced = balanced.get("balanced_candidate_cycles", "")
        balanced_cycles = float(
            saved_balanced if saved_balanced != ""
            else balanced["estimated_iteration_cycles"]
        )
        policy = "balanced" if balanced_cycles < endpoint_cycles else "endpoint-local"
        selected = balanced if policy == "balanced" else lossy
        scheduled = dict(balanced)
        for field, value in selected.items():
            if field not in IDENTITY_FIELDS:
                scheduled[field] = value
        scheduled["stage"] = "scheduled"
        scheduled["owner_policy"] = policy
        scheduled["endpoint_candidate_cycles"] = endpoint_cycles
        scheduled["balanced_candidate_cycles"] = balanced_cycles
        for name, row in stages.items():
            if name != "scheduled":
                row["owner_policy"] = "endpoint-local"
                row["endpoint_candidate_cycles"] = row["estimated_iteration_cycles"]
                row["balanced_candidate_cycles"] = ""
                rows.append(row)
        rows.append(scheduled)

    rows.sort(key=lambda row: (
        int(row["vertices"]), float(row["sparsity"]), int(row["seed"]),
        ("identity", "partition", "permutation", "lossy", "scheduled").index(row["stage"]),
    ))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "mapping_stage_results.csv"
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    numeric_rows = []
    for row in rows:
        numeric_rows.append({
            key: value if key in TEXT_FIELDS or value == "" else float(value)
            for key, value in row.items()
        })
    plot_results(numeric_rows, args.output_dir)
    print(f"wrote {output} and corrected plots")


if __name__ == "__main__":
    main()
