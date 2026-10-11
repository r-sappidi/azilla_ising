#!/usr/bin/env python3
"""Run fully dense block-occupancy points through the calibrated event model."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel,
    EventPerformanceConfig,
)
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


GEOMETRIES = {
    1024: Geometry(mesh_x=1, mesh_y=1, h0_per_h1=8, cores_per_h0=4),
    4096: Geometry(mesh_x=1, mesh_y=1, h0_per_h1=8, cores_per_h0=16),
    8192: Geometry(mesh_x=1, mesh_y=1, h0_per_h1=8, cores_per_h0=32),
    16384: Geometry(mesh_x=1, mesh_y=1, h0_per_h1=8, cores_per_h0=64),
    32768: Geometry(mesh_x=1, mesh_y=1, h0_per_h1=8, cores_per_h0=128),
}


def dense_occupancy(geometry: Geometry) -> BlockOccupancyDataset:
    """Represent a dense Ising matrix without materializing spin-level edges."""
    blocks = geometry.total_blocks
    pairs = {(a, b) for a in range(blocks) for b in range(a + 1, blocks)}
    return BlockOccupancyDataset(geometry.spin_count, 0, pairs)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", default="1024,4096,8192,16384,32768",
        help="comma-separated subset of 1024,4096,8192,16384,32768",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/dense_performance_sweep"),
    )
    parser.add_argument("--h0-mvms", type=int, default=1)
    parser.add_argument("--h1-mvms", type=int, default=4)
    parser.add_argument("--cross-mvms", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    sizes = [int(value) for value in args.sizes.split(",")]
    unsupported = set(sizes) - GEOMETRIES.keys()
    if unsupported:
        parser.error(f"unsupported sizes: {sorted(unsupported)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = EventPerformanceConfig(
        h0_mvm_count=args.h0_mvms,
        h1_mvm_count=args.h1_mvms,
        cross_mvm_count=args.cross_mvms,
    )
    rows: list[dict[str, object]] = []

    for spins in sizes:
        geometry = GEOMETRIES[spins]
        run_dir = args.output_dir / f"dense_{spins}"
        point_path = run_dir / "point.json"
        if point_path.is_file() and not args.force:
            row = json.loads(point_path.read_text())
            rows.append(row)
            print(f"SKIP spins={spins} existing={point_path}", flush=True)
            continue

        run_dir.mkdir(parents=True, exist_ok=True)
        dataset = dense_occupancy(geometry)
        active_pairs = len(dataset.active_block_pairs())
        print(
            f"RUN spins={spins} blocks={geometry.total_blocks} "
            f"dense_pairs={active_pairs}", flush=True,
        )
        started = time.perf_counter()
        result = EventCompressedPerformanceModel(
            geometry, dataset, config
        ).run(sparse=True, iterations=1)
        wall_seconds = time.perf_counter() - started

        prefix = run_dir / "metrics"
        write_event_performance_metrics(prefix, result, geometry, config)
        total_jobs = (
            result.scheduled_h0 + result.scheduled_h1 + result.scheduled_cross
        )
        row: dict[str, object] = {
            "spins": spins,
            "blocks": geometry.total_blocks,
            "dense_block_pairs": active_pairs,
            "mesh_x": geometry.mesh_x,
            "mesh_y": geometry.mesh_y,
            "h0_per_h1": geometry.h0_per_h1,
            "cores_per_h0": geometry.cores_per_h0,
            "h0_mvms": config.h0_mvm_count,
            "h1_mvms": config.h1_mvm_count,
            "cross_mvms": config.cross_mvm_count,
            "accuracy": result.accuracy,
            "initialization_cycles": result.initialization_cycles,
            "iteration_cycles": result.iteration_cycles,
            "total_cycles": result.total_cycles,
            "scheduled_h0": result.scheduled_h0,
            "scheduled_h1": result.scheduled_h1,
            "scheduled_cross": result.scheduled_cross,
            "total_jobs": total_jobs,
            "injected_flits": result.injected_flits,
            "physical_link_flits": result.physical_link_flits,
            "simulation_wall_seconds": wall_seconds,
        }
        point_path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
        rows.append(row)
        write_csv(args.output_dir / "summary.csv", rows)
        print(
            f"PASS spins={spins} cycles={result.total_cycles} "
            f"wall_seconds={wall_seconds:.3f}", flush=True,
        )

    write_csv(args.output_dir / "summary.csv", rows)
    manifest = {
        "workload": "fully-dense off-diagonal 32x32 block occupancy",
        "model": "calibrated event-compressed performance model",
        "iterations": 1,
        "sizes": sizes,
        "h0_mvms": args.h0_mvms,
        "h1_mvms": args.h1_mvms,
        "cross_mvms": args.cross_mvms,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"COMPLETE summary={args.output_dir / 'summary.csv'}", flush=True)


if __name__ == "__main__":
    main()
