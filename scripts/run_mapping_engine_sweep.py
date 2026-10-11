#!/usr/bin/env python3
"""Sweep H0, H1, and cross-H1 MVM counts for one mapping artifact."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel,
    EventPerformanceConfig,
)
from azilla_cycle_model.mapping_adapter import MappingArtifact


def integer_list(text: str) -> list[int]:
    values = sorted({int(item) for item in text.split(",")})
    if not values or any(value <= 0 or value & (value - 1) for value in values):
        raise argparse.ArgumentTypeError(
            "expected comma-separated positive powers of two"
        )
    return values


def run_point(task: tuple[str, int, int, int]) -> dict[str, int | str]:
    artifact_path, h0_mvms, h1_mvms, cross_mvms = task
    artifact = MappingArtifact.load(artifact_path)
    result = EventCompressedPerformanceModel(
        artifact.geometry,
        artifact.occupancy_dataset(),
        EventPerformanceConfig(
            h0_mvm_count=h0_mvms,
            h1_mvm_count=h1_mvms,
            cross_mvm_count=cross_mvms,
        ),
    ).run(schedule=artifact.schedule(), sparse=True, iterations=1)
    geometry = artifact.geometry
    total_mvms = geometry.node_count * (
        geometry.h0_per_h1 * h0_mvms + h1_mvms + cross_mvms
    )
    return {
        "h0_mvms": h0_mvms,
        "h1_mvms": h1_mvms,
        "cross_mvms": cross_mvms,
        "total_mvms": total_mvms,
        "initialization_cycles": result.initialization_cycles,
        "iteration_cycles": result.iteration_cycles,
        "total_cycles": result.total_cycles,
        "injected_flits": result.injected_flits,
        "physical_link_flits": result.physical_link_flits,
        "network_active_cycles": result.network_active_cycles,
        "skipped_cycles": result.skipped_cycles,
        "accuracy": result.accuracy,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--h0-mvms", type=integer_list, default=integer_list("1,2,4,8"))
    parser.add_argument("--h1-mvms", type=integer_list, default=integer_list("1,2,4,8"))
    parser.add_argument("--cross-mvms", type=integer_list, default=integer_list("4,8,16"))
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--mvm-budget", type=int, default=384)
    parser.add_argument("--knee-percent", type=float, default=5.0)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    if args.workers <= 0 or args.mvm_budget <= 0 or args.knee_percent < 0:
        parser.error("workers/budget must be positive and knee-percent nonnegative")

    artifact = MappingArtifact.load(args.artifact)
    tasks = [
        (args.artifact, h0, h1, cross)
        for h0, h1, cross in itertools.product(
            args.h0_mvms, args.h1_mvms, args.cross_mvms
        )
    ]
    print(
        f"sweep points={len(tasks)} workers={args.workers} "
        f"spins={artifact.geometry.spin_count}",
        flush=True,
    )
    rows: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(run_point, task): task for task in tasks}
        for index, future in enumerate(as_completed(pending), 1):
            row = future.result()
            rows.append(row)
            print(
                f"[{index}/{len(tasks)}] "
                f"mvm={row['h0_mvms']}/{row['h1_mvms']}/{row['cross_mvms']} "
                f"total_mvms={row['total_mvms']} "
                f"iteration_cycles={row['iteration_cycles']}",
                flush=True,
            )

    rows.sort(key=lambda row: (
        int(row["iteration_cycles"]), int(row["total_mvms"]),
        int(row["h0_mvms"]), int(row["h1_mvms"]), int(row["cross_mvms"]),
    ))
    fastest = rows[0]
    knee_limit = int(
        int(fastest["iteration_cycles"]) * (1.0 + args.knee_percent / 100.0)
    )
    knee = min(
        (row for row in rows if int(row["iteration_cycles"]) <= knee_limit),
        key=lambda row: (int(row["total_mvms"]), int(row["iteration_cycles"])),
    )
    within_budget = [
        row for row in rows if int(row["total_mvms"]) <= args.mvm_budget
    ]
    budget_best = within_budget[0] if within_budget else None

    csv_path = args.output_prefix.with_suffix(".csv")
    summary_path = args.output_prefix.with_name(
        args.output_prefix.name + "_summary.json"
    )
    write_csv(csv_path, rows)
    summary = {
        "artifact": str(Path(args.artifact)),
        "geometry": {
            "mesh_x": artifact.geometry.mesh_x,
            "mesh_y": artifact.geometry.mesh_y,
            "h0_per_h1": artifact.geometry.h0_per_h1,
            "cores_per_h0": artifact.geometry.cores_per_h0,
            "spins": artifact.geometry.spin_count,
        },
        "points": len(rows),
        "workers": args.workers,
        "mvm_budget": args.mvm_budget,
        "knee_percent": args.knee_percent,
        "fastest": fastest,
        "minimum_mvms_within_knee": knee,
        "fastest_within_budget": budget_best,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"fastest={fastest}")
    print(f"minimum_mvms_within_{args.knee_percent:g}_percent={knee}")
    print(f"fastest_within_{args.mvm_budget}_mvm_budget={budget_best}")
    print(f"csv={csv_path}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
