#!/usr/bin/env python3
"""Classify and summarize per-dataset NoC traffic patterns."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import fmean


def log_slope(rows: list[dict[str, object]], metric: str) -> float | None:
    points = [
        (math.log(float(row["problem_vertices"])), math.log(float(row[metric])))
        for row in rows
        if float(row["problem_vertices"]) > 0 and float(row[metric]) > 0
    ]
    if len(points) < 2:
        return None
    x_mean = fmean(x for x, _ in points)
    y_mean = fmean(y for _, y in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return sum(
        (x - x_mean) * (y - y_mean) for x, y in points
    ) / denominator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("results/paper_benchmarks")
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("results/paper_benchmarks/traffic_patterns.csv"),
    )
    parser.add_argument(
        "--family-output", type=Path,
        default=Path("results/paper_benchmarks/traffic_family_summary.csv"),
    )
    args = parser.parse_args()
    rows = []
    for point_path in sorted(args.root.glob("event_sweep*/*/identity/point.json")):
        point = json.loads(point_path.read_text())
        summary_path = Path(point["metrics_summary"])
        summary = json.loads(summary_path.read_text())
        noc_path = Path(point["metrics_noc"])
        with noc_path.open(newline="") as source:
            resources = list(csv.DictReader(source))
        links = [row for row in resources if row["scope"] == "link"]
        direction_flits = Counter()
        direction_stalls = Counter()
        for link in links:
            direction_flits[link["direction"]] += int(link["accepted_flits"])
            direction_stalls[link["direction"]] += int(link["stall_cycles"])
        noc = summary["noc"]
        injected = noc["injected_flits"]
        cross_fraction = point["cross_job_fraction"]
        maximum_utilization = point["maximum_link_window_utilization"]
        if cross_fraction < 0.1:
            traffic_class = "local-dominated"
        elif cross_fraction > 0.8:
            traffic_class = "cross-dominated"
        else:
            traffic_class = "mixed"
        if maximum_utilization >= 0.3:
            pressure_class = "high-utilization"
        elif maximum_utilization >= 0.1:
            pressure_class = "moderate-utilization"
        else:
            pressure_class = "low-utilization"
        rows.append({
            "dataset": point["dataset"],
            "family": point["family"],
            "problem_vertices": point["problem_vertices"],
            "capacity_spins": point["capacity_spins"],
            "active_block_pairs": point["active_block_pairs"],
            "cross_job_fraction": cross_fraction,
            "traffic_class": traffic_class,
            "pressure_class": pressure_class,
            "state_flits": noc["state_flits"],
            "partial_flits": noc["partial_flits"],
            "epoch_done_flits": noc["epoch_done_flits"],
            "state_flit_fraction": noc["state_flits"] / injected if injected else 0.0,
            "partial_flit_fraction": noc["partial_flits"] / injected if injected else 0.0,
            "injected_flits": injected,
            "physical_link_flits": noc["physical_link_flits"],
            "iteration_cycles": point["iteration_cycles"],
            "average_hops": noc["average_hops"],
            "north_flits": direction_flits["north"],
            "south_flits": direction_flits["south"],
            "east_flits": direction_flits["east"],
            "west_flits": direction_flits["west"],
            "horizontal_flit_fraction": (
                (direction_flits["east"] + direction_flits["west"])
                / noc["physical_link_flits"]
                if noc["physical_link_flits"] else 0.0
            ),
            "vertical_flit_fraction": (
                (direction_flits["north"] + direction_flits["south"])
                / noc["physical_link_flits"]
                if noc["physical_link_flits"] else 0.0
            ),
            "injection_stalls": noc["injection_stalls"],
            "link_stalls": noc["link_stalls"],
            "north_stalls": direction_stalls["north"],
            "south_stalls": direction_stalls["south"],
            "east_stalls": direction_stalls["east"],
            "west_stalls": direction_stalls["west"],
            "maximum_link_utilization": maximum_utilization,
            "maximum_link_backpressure": point["maximum_link_backpressure"],
            "link_flit_cv": point["link_flit_cv"],
            "link_flit_gini": point["link_flit_gini"],
            "node_job_cv": point["node_job_cv"],
            "busiest_link": point["busiest_link"],
            "most_stalled_link": point["most_stalled_link"],
        })
    if not rows:
        raise SystemExit(f"no event points below {args.root}")
    # De-duplicate points if multiple sweep directories contain the same name.
    rows = list({row["dataset"]: row for row in rows}.values())
    rows.sort(key=lambda row: (row["family"], row["problem_vertices"], row["dataset"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.output.with_suffix(".json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    families = []
    for family in sorted({str(row["family"]) for row in rows}):
        members = [row for row in rows if row["family"] == family]
        homogeneous_scaling = family in {
            "generated-random-average-degree-25",
            "generated-structured-toroidal",
        }
        families.append({
            "family": family,
            "points": len(members),
            "homogeneous_scaling_family": homogeneous_scaling,
            "minimum_vertices": min(int(row["problem_vertices"]) for row in members),
            "maximum_vertices": max(int(row["problem_vertices"]) for row in members),
            "mean_cross_job_fraction": fmean(
                float(row["cross_job_fraction"]) for row in members
            ),
            "minimum_cross_job_fraction": min(
                float(row["cross_job_fraction"]) for row in members
            ),
            "maximum_cross_job_fraction": max(
                float(row["cross_job_fraction"]) for row in members
            ),
            "mean_partial_flit_fraction": fmean(
                float(row["partial_flit_fraction"]) for row in members
            ),
            "mean_maximum_link_utilization": fmean(
                float(row["maximum_link_utilization"]) for row in members
            ),
            "maximum_link_utilization": max(
                float(row["maximum_link_utilization"]) for row in members
            ),
            "mean_link_flit_gini": fmean(
                float(row["link_flit_gini"]) for row in members
            ),
            "mean_node_job_cv": fmean(
                float(row["node_job_cv"]) for row in members
            ),
            "cycle_scaling_exponent": (
                log_slope(members, "iteration_cycles")
                if homogeneous_scaling else None
            ),
            "link_flit_scaling_exponent": (
                log_slope(members, "physical_link_flits")
                if homogeneous_scaling else None
            ),
        })
    args.family_output.parent.mkdir(parents=True, exist_ok=True)
    with args.family_output.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(families[0]))
        writer.writeheader()
        writer.writerows(families)
    args.family_output.with_suffix(".json").write_text(
        json.dumps(families, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"results={args.output} datasets={len(rows)} "
        f"families={args.family_output}"
    )


if __name__ == "__main__":
    main()
