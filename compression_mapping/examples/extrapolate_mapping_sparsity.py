#!/usr/bin/env python3
"""Add clearly labeled dense-regime heuristic points to measured mapping data."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from run_mapping_stage_experiments import plot_results


INTEGER_FIELDS = {
    "vertices", "seed", "edges", "dropped_blocks", "cross_edges",
    "occupied_blocks", "cross_blocks", "owner_distance", "max_owner_load",
    "estimated_initialization_cycles", "estimated_iteration_cycles",
    "estimated_total_cycles", "estimated_physical_link_flits",
}
TEXT_FIELDS = {"stage", "estimated_accuracy", "result_source", "owner_policy"}


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open() as source:
        for raw in csv.DictReader(source):
            row = {}
            for key, value in raw.items():
                if value == "":
                    row[key] = ""
                elif key in TEXT_FIELDS:
                    row[key] = value
                elif key in INTEGER_FIELDS:
                    row[key] = int(float(value))
                else:
                    row[key] = float(value)
            row.setdefault("result_source", "measured")
            rows.append(row)
    return rows


def extrapolate(rows: list[dict], sparsities: list[float]) -> list[dict]:
    output = [dict(row, result_source="measured") for row in rows]
    for spins in sorted({row["vertices"] for row in rows}):
        size_rows = [row for row in rows if row["vertices"] == spins]
        anchor_sparsity = min(row["sparsity"] for row in size_rows)
        anchors = [
            row for row in size_rows if row["sparsity"] == anchor_sparsity
        ]
        identity_by_seed = {
            row["seed"]: row for row in anchors if row["stage"] == "identity"
        }
        for sparsity in sparsities:
            for anchor in anchors:
                # In the dense regime essentially every 32x32 block is
                # occupied. Reuse the first measured block-saturated timing
                # distribution rather than pretending to simulate edges that
                # cannot change the scheduled block count.
                row = dict(anchor)
                row["sparsity"] = sparsity
                row["result_source"] = "heuristic-block-saturated"
                identity = identity_by_seed[anchor["seed"]]
                # A deliberately conservative dense-regime heuristic: sparse
                # edges begin reducing arbitration/traffic before many entire
                # 32x32 blocks disappear. Keep the effect modest and converge
                # toward the measured high-sparsity region.
                initialization = identity["estimated_initialization_cycles"]
                progress_points = {0.0: 0.00, 0.5: 0.03, 0.75: 0.07, 0.9: 0.12}
                progress = progress_points.get(sparsity, 0.12 * sparsity / 0.9)
                identity_iteration = identity["estimated_iteration_cycles"]
                measured_iteration = anchor["estimated_iteration_cycles"]
                iteration = round(
                    identity_iteration -
                    progress * max(0, identity_iteration - measured_iteration)
                )
                scale = iteration / max(identity_iteration, 1)
                row["estimated_initialization_cycles"] = initialization
                row["estimated_iteration_cycles"] = iteration
                row["estimated_total_cycles"] = initialization + iteration
                row["estimated_physical_link_flits"] = round(
                    identity["estimated_physical_link_flits"] * scale
                )
                output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sparsities", default="0,0.5,0.75,0.9")
    args = parser.parse_args()
    rows = load_rows(args.input)
    sparsities = [float(value) for value in args.sparsities.split(",")]
    combined = extrapolate(rows, sparsities)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "mapping_stage_results_with_extrapolation.csv"
    fieldnames = list(combined[0])
    with output.open("w", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(combined)
    plot_results(combined, args.output_dir)
    print(f"wrote {output} and extrapolated plots to {args.output_dir}")


if __name__ == "__main__":
    main()
