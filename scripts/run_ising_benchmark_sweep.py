#!/usr/bin/env python3
"""Run a resumable calibrated-event sweep with paper-oriented diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.noc import InterconnectConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


def comma_list(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def coefficient_of_variation(values: list[float]) -> float:
    mean = fmean(values) if values else 0.0
    return pstdev(values) / mean if mean else 0.0


def gini(values: list[float]) -> float:
    values = sorted(value for value in values if value >= 0)
    total = sum(values)
    count = len(values)
    if not count or not total:
        return 0.0
    weighted = sum((index + 1) * value for index, value in enumerate(values))
    return (2 * weighted) / (count * total) - (count + 1) / count


def percentile_from_histogram(
    histogram: tuple[tuple[int, int], ...], percentile: float
) -> int:
    total = sum(count for _, count in histogram)
    if not total:
        return 0
    threshold = math.ceil(percentile * total)
    cumulative = 0
    for value, count in histogram:
        cumulative += count
        if cumulative >= threshold:
            return value
    return histogram[-1][0]


def task_row(
    entry: dict[str, Any], output_dir: str, configuration: dict[str, Any],
    force: bool,
) -> dict[str, Any]:
    name = entry["name"]
    run_dir = Path(output_dir) / name / "identity"
    prefix = run_dir / "metrics"
    summary_path = prefix.with_name(prefix.name + "_event_summary.json")
    point_path = run_dir / "point.json"
    if point_path.is_file() and summary_path.is_file() and not force:
        return json.loads(point_path.read_text())

    geometry = Geometry(**entry["geometry"])
    dataset = BlockOccupancyDataset.load(entry["dataset"])
    active_pairs = len(dataset.active_block_pairs())
    config = EventPerformanceConfig(
        h0_mvm_count=configuration["h0_mvms"],
        h1_mvm_count=configuration["h1_mvms"],
        cross_mvm_count=configuration["cross_mvms"],
        fifo_depth=configuration["fifo_depth"],
        chiplet_link_latency_cycles=configuration["chiplet_link_latency_cycles"],
        interconnect=InterconnectConfig(
            forward_latency_cycles=configuration["link_latency_cycles"],
            flit_interval_cycles=configuration["link_flit_interval_cycles"],
            max_inflight_flits=configuration["link_max_inflight_flits"],
            credit_return_latency_cycles=configuration[
                "link_credit_return_cycles"
            ],
        ),
    )
    start = time.perf_counter()
    result = EventCompressedPerformanceModel(
        geometry, dataset, config
    ).run(sparse=True, iterations=1)
    wall_seconds = time.perf_counter() - start
    paths = write_event_performance_metrics(prefix, result, geometry, config)

    links = [row for row in result.noc_resources if row.scope == "link"]
    link_flits = [float(row.accepted_flits) for row in links]
    nonzero_link_flits = [value for value in link_flits if value]
    node_jobs = [float(row.total_jobs) for row in result.nodes]
    cross_jobs = [float(row.cross_jobs) for row in result.nodes]
    busiest = max(links, key=lambda row: row.accepted_flits, default=None)
    most_stalled = max(links, key=lambda row: row.stall_cycles, default=None)
    potential_pairs = geometry.total_blocks * (geometry.total_blocks - 1) // 2
    total_jobs = result.scheduled_h0 + result.scheduled_h1 + result.scheduled_cross
    point = {
        "dataset": name,
        "family": entry["family"],
        "mapping": "identity",
        "owner_policy": "rtl-h1-pair-balanced-distance",
        "problem_vertices": entry["problem_vertices"],
        "capacity_spins": entry["capacity_spins"],
        "padding_spins": entry["isolated_padding_spins"],
        "undirected_edges": entry["undirected_edges"],
        "average_degree": entry["average_degree"],
        "graph_density": entry["density"],
        "active_block_pairs": active_pairs,
        "block_pair_occupancy_fraction": (
            active_pairs / potential_pairs if potential_pairs else 0.0
        ),
        "edges_per_active_block_pair": (
            entry["undirected_edges"] / active_pairs if active_pairs else 0.0
        ),
        "mesh_x": geometry.mesh_x,
        "mesh_y": geometry.mesh_y,
        "h0_per_h1": geometry.h0_per_h1,
        "cores_per_h0": geometry.cores_per_h0,
        "h0_mvms": config.h0_mvm_count,
        "h1_mvms": config.h1_mvm_count,
        "cross_mvms": config.cross_mvm_count,
        "accuracy": result.accuracy,
        "calibration": result.calibration,
        "initialization_cycles": result.initialization_cycles,
        "iteration_cycles": result.iteration_cycles,
        "total_cycles": result.total_cycles,
        "simulation_wall_seconds": wall_seconds,
        "scheduled_h0": result.scheduled_h0,
        "scheduled_h1": result.scheduled_h1,
        "scheduled_cross": result.scheduled_cross,
        "cross_job_fraction": (
            result.scheduled_cross / total_jobs if total_jobs else 0.0
        ),
        "estimated_dram_requests": sum(
            row.estimated_dram_requests for row in result.work_resources
        ),
        "injected_flits": result.injected_flits,
        "physical_link_flits": result.physical_link_flits,
        "noc_bytes_on_physical_links": result.physical_link_flits * 32,
        "injection_stalls": result.injection_stalls,
        "link_stalls": result.link_stalls,
        "average_hops": result.average_hops,
        "median_hops": percentile_from_histogram(result.hop_histogram, 0.5),
        "p95_hops": percentile_from_histogram(result.hop_histogram, 0.95),
        "network_active_cycles": result.network_active_cycles,
        "network_skipped_cycles": result.skipped_cycles,
        "maximum_link_flits": max(link_flits, default=0.0),
        "mean_link_flits": fmean(link_flits) if link_flits else 0.0,
        "mean_nonzero_link_flits": (
            fmean(nonzero_link_flits) if nonzero_link_flits else 0.0
        ),
        "link_flit_cv": coefficient_of_variation(link_flits),
        "link_flit_gini": gini(link_flits),
        "maximum_link_window_utilization": max(
            (row.accepted_flits / result.iteration_cycles for row in links),
            default=0.0,
        ),
        "maximum_link_backpressure": max(
            (row.backpressure for row in links), default=0.0
        ),
        "node_job_cv": coefficient_of_variation(node_jobs),
        "cross_job_cv": coefficient_of_variation(cross_jobs),
        "maximum_node_jobs": max(node_jobs, default=0.0),
        "minimum_node_jobs": min(node_jobs, default=0.0),
        "busiest_link": (
            f"n{busiest.node}:{busiest.direction}" if busiest else ""
        ),
        "most_stalled_link": (
            f"n{most_stalled.node}:{most_stalled.direction}"
            if most_stalled else ""
        ),
        "dataset_sha256": entry["sha256"],
        "metrics_summary": str(paths["summary"]),
        "metrics_noc": str(paths["noc"]),
        "metrics_nodes": str(paths["nodes"]),
        "metrics_work": str(paths["work"]),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    point_path.write_text(json.dumps(point, indent=2, sort_keys=True) + "\n")
    return point


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def git_context() -> dict[str, Any]:
    def command(*args: str) -> str:
        return subprocess.run(
            (
                "git", "-c", f"safe.directory={Path.cwd().resolve()}",
                *args,
            ),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False,
        ).stdout.strip()
    return {
        "commit": command("rev-parse", "HEAD"),
        "branch": command("branch", "--show-current"),
        "dirty": bool(command("status", "--porcelain")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("results/paper_benchmarks/datasets/manifest.json"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/event_sweep"),
    )
    parser.add_argument("--names", type=comma_list)
    parser.add_argument("--families", type=comma_list)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--h0-mvms", type=int, default=1)
    parser.add_argument("--h1-mvms", type=int, default=1)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--fifo-depth", type=int, default=4)
    parser.add_argument("--chiplet-link-latency-cycles", type=int, default=0)
    parser.add_argument("--link-latency-cycles", type=int, default=0)
    parser.add_argument("--link-flit-interval-cycles", type=int, default=1)
    parser.add_argument("--link-max-inflight-flits", type=int, default=4)
    parser.add_argument("--link-credit-return-cycles", type=int, default=0)
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("workers must be positive")
    manifest = json.loads(args.manifest.read_text())
    entries = manifest["datasets"]
    if args.names:
        entries = [entry for entry in entries if entry["name"] in args.names]
    if args.families:
        entries = [entry for entry in entries if entry["family"] in args.families]
    if not entries:
        parser.error("dataset filters selected no entries")

    configuration = {
        "h0_mvms": args.h0_mvms,
        "h1_mvms": args.h1_mvms,
        "cross_mvms": args.cross_mvms,
        "fifo_depth": args.fifo_depth,
        "chiplet_link_latency_cycles": args.chiplet_link_latency_cycles,
        "link_latency_cycles": args.link_latency_cycles,
        "link_flit_interval_cycles": args.link_flit_interval_cycles,
        "link_max_inflight_flits": args.link_max_inflight_flits,
        "link_credit_return_cycles": args.link_credit_return_cycles,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    context = {
        "schema": "azilla-paper-event-sweep-v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "workers": args.workers,
        "manifest": str(args.manifest),
        "configuration": configuration,
        "git": git_context(),
    }
    (args.output_dir / "experiment_context.json").write_text(
        json.dumps(context, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"sweep datasets={len(entries)} workers={args.workers} "
        f"output={args.output_dir}", flush=True,
    )
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                task_row, entry, str(args.output_dir), configuration, args.force
            ): entry["name"]
            for entry in entries
        }
        for index, future in enumerate(as_completed(futures), 1):
            name = futures[future]
            row = future.result()
            rows.append(row)
            print(
                f"[{index}/{len(entries)}] {name} "
                f"cycles={row['iteration_cycles']} "
                f"blocks={row['active_block_pairs']} "
                f"wall={row['simulation_wall_seconds']:.3f}s",
                flush=True,
            )
    # Always consolidate every completed point already present in this output
    # directory, so a filtered/resumed invocation cannot erase earlier rows.
    completed_points = {}
    for path in args.output_dir.glob("*/identity/point.json"):
        point = json.loads(path.read_text())
        completed_points[point["dataset"]] = point
    completed_points.update({row["dataset"]: row for row in rows})
    rows = sorted(
        completed_points.values(),
        key=lambda row: (
            row["family"], row["capacity_spins"], row["dataset"]
        ),
    )
    write_csv(args.output_dir / "results.csv", rows)
    completed = dict(context)
    completed["completed_utc"] = datetime.now(timezone.utc).isoformat()
    completed["points"] = rows
    (args.output_dir / "results.json").write_text(
        json.dumps(completed, indent=2, sort_keys=True) + "\n"
    )
    print(f"results={args.output_dir / 'results.csv'}")


if __name__ == "__main__":
    main()
