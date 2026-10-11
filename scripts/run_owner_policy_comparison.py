#!/usr/bin/env python3
"""Isolate cross-H1 owner scheduling effects for existing mapping artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.mapping_adapter import MappingArtifact
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.workload import ScheduledBlock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path("results/paper_benchmarks/mapping_comparison"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/owner_policy_comparison"),
    )
    args = parser.parse_args()
    config = EventPerformanceConfig(
        h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=4
    )
    rows = []
    for manifest in sorted(args.artifact_root.glob("*/*/manifest.json")):
        dataset = manifest.parent.parent.name
        mapping = manifest.parent.name
        artifact = MappingArtifact.load(manifest.parent)
        schedules = {
            "optimized_per_block": artifact.schedule(),
            "rtl_h1_pair": [
                ScheduledBlock(int(pair[0]), int(pair[1]), -1)
                for pair in artifact.block_pairs
            ],
        }
        for owner_policy, schedule in schedules.items():
            start = time.perf_counter()
            result = EventCompressedPerformanceModel(
                artifact.geometry, artifact.occupancy_dataset(), config
            ).run(schedule=schedule)
            wall = time.perf_counter() - start
            directory = args.output_dir / dataset / f"{mapping}__{owner_policy}"
            paths = write_event_performance_metrics(
                directory / "metrics", result, artifact.geometry, config
            )
            total_jobs = (
                result.scheduled_h0 + result.scheduled_h1
                + result.scheduled_cross
            )
            rows.append({
                "dataset": dataset,
                "mapping": mapping,
                "owner_policy": owner_policy,
                "spins": artifact.geometry.spin_count,
                "active_block_pairs": total_jobs,
                "scheduled_cross": result.scheduled_cross,
                "iteration_cycles": result.iteration_cycles,
                "simulation_wall_seconds": wall,
                "injected_flits": result.injected_flits,
                "physical_link_flits": result.physical_link_flits,
                "injection_stalls": result.injection_stalls,
                "link_stalls": result.link_stalls,
                "average_hops": result.average_hops,
                "metrics_summary": str(paths["summary"]),
            })
            print(
                f"{dataset} {mapping} {owner_policy}: "
                f"cycles={result.iteration_cycles}", flush=True,
            )
    if not rows:
        raise SystemExit(f"no mapping artifacts below {args.artifact_root}")
    optimized = {
        (row["dataset"], row["mapping"]): row["iteration_cycles"]
        for row in rows if row["owner_policy"] == "optimized_per_block"
    }
    for row in rows:
        row["speedup_vs_optimized_per_block"] = (
            optimized[(row["dataset"], row["mapping"])]
            / row["iteration_cycles"]
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "results.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.output_dir / "results.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    print(f"results={args.output_dir / 'results.csv'}")


if __name__ == "__main__":
    main()
