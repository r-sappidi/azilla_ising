#!/usr/bin/env python3
"""Sweep package/link timing parameters on representative sparse workloads."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.noc import InterconnectConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


VARIANTS = {
    "rtl_direct": (0, 1, 4, 0, 0),
    "package_latency_4": (0, 1, 4, 0, 4),
    "package_latency_8": (0, 1, 4, 0, 8),
    "link_latency_4": (4, 1, 4, 0, 0),
    "link_latency_8": (8, 1, 4, 0, 0),
    "link_latency_16": (16, 1, 4, 0, 0),
    "serialization_2": (0, 2, 4, 0, 0),
    "serialization_4": (0, 4, 4, 0, 0),
    "credit_1_return_4": (0, 1, 1, 4, 0),
    "credit_1_return_8": (0, 1, 1, 8, 0),
    "combined_stress": (16, 2, 4, 16, 8),
}


def comma_list(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def run_point(entry: dict, variant: str, output_dir: str,
              h0_mvms: int, h1_mvms: int, cross_mvms: int) -> dict:
    forward, interval, inflight, credit_return, package = VARIANTS[variant]
    geometry = Geometry(**entry["geometry"])
    dataset = BlockOccupancyDataset.load(entry["dataset"])
    config = EventPerformanceConfig(
        h0_mvm_count=h0_mvms,
        h1_mvm_count=h1_mvms,
        cross_mvm_count=cross_mvms,
        chiplet_link_latency_cycles=package,
        interconnect=InterconnectConfig(
            forward_latency_cycles=forward,
            flit_interval_cycles=interval,
            max_inflight_flits=inflight,
            credit_return_latency_cycles=credit_return,
        ),
    )
    start = time.perf_counter()
    result = EventCompressedPerformanceModel(
        geometry, dataset, config
    ).run(sparse=True)
    wall = time.perf_counter() - start
    directory = Path(output_dir) / entry["name"] / variant
    paths = write_event_performance_metrics(
        directory / "metrics", result, geometry, config
    )
    links = [row for row in result.noc_resources if row.scope == "link"]
    return {
        "dataset": entry["name"],
        "family": entry["family"],
        "variant": variant,
        "problem_vertices": entry["problem_vertices"],
        "capacity_spins": entry["capacity_spins"],
        "dataset_sha256": entry["sha256"],
        "h0_per_h1": geometry.h0_per_h1,
        "cores_per_h0": geometry.cores_per_h0,
        "h0_mvms": h0_mvms,
        "h1_mvms": h1_mvms,
        "cross_mvms": cross_mvms,
        "link_latency_cycles": forward,
        "link_flit_interval_cycles": interval,
        "link_max_inflight_flits": inflight,
        "link_credit_return_cycles": credit_return,
        "chiplet_link_latency_cycles": package,
        "iteration_cycles": result.iteration_cycles,
        "total_cycles": result.total_cycles,
        "simulation_wall_seconds": wall,
        "injected_flits": result.injected_flits,
        "physical_link_flits": result.physical_link_flits,
        "injection_stalls": result.injection_stalls,
        "link_stalls": result.link_stalls,
        "maximum_link_utilization": max(
            (row.accepted_flits / result.iteration_cycles for row in links),
            default=0.0,
        ),
        "maximum_link_backpressure": max(
            (row.backpressure for row in links), default=0.0
        ),
        "accuracy": result.accuracy,
        "metrics_summary": str(paths["summary"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("results/paper_benchmarks/datasets/manifest.json"),
    )
    parser.add_argument("--names", type=comma_list, required=True)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/interconnect_sensitivity"),
    )
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    entries = [
        entry for entry in manifest["datasets"] if entry["name"] in args.names
    ]
    missing = args.names - {entry["name"] for entry in entries}
    if missing:
        parser.error(f"unknown datasets: {sorted(missing)}")
    tasks = [(entry, variant) for entry in entries for variant in VARIANTS]
    rows = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        pending = {
            executor.submit(
                run_point, entry, variant, str(args.output_dir),
                args.h0_mvms, args.h1_mvms, args.cross_mvms,
            ):
            (entry["name"], variant)
            for entry, variant in tasks
        }
        for index, future in enumerate(as_completed(pending), 1):
            name, variant = pending[future]
            row = future.result()
            rows.append(row)
            print(
                f"[{index}/{len(tasks)}] {name}/{variant} "
                f"cycles={row['iteration_cycles']}", flush=True,
            )
    baselines = {
        row["dataset"]: row["iteration_cycles"]
        for row in rows if row["variant"] == "rtl_direct"
    }
    for row in rows:
        row["slowdown_vs_rtl_direct"] = (
            row["iteration_cycles"] / baselines[row["dataset"]]
        )
    rows.sort(key=lambda row: (row["dataset"], row["variant"]))
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
