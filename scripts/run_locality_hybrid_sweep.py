#!/usr/bin/env python3
"""Evaluate the fixed same-H0-CIR/otherwise-cores locality policy."""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from azilla_cycle_model.events import EventCompressedPerformanceModel, EventPerformanceConfig
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.metrics import write_event_performance_metrics, write_exact_event_metrics
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.scheduler import compile_locality_hybrid_schedule
from azilla_cycle_model.dynamic_scheduler import select_batch_balanced_core_pairs
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry, ScheduledBlock

ROOT = Path(__file__).resolve().parents[1]


def run_point(entry: dict, output: str, model_kind: str, policy: str,
              cir_cost_scale: float, traffic_weight: int) -> dict:
    geometry = Geometry(**entry["geometry"])
    dataset_path = ROOT / entry["dataset"]
    dataset = BlockOccupancyDataset.load(dataset_path)
    records = [ScheduledBlock(a, b) for a, b in sorted(dataset.active_block_pairs())]
    placement = (
        compile_locality_hybrid_schedule(geometry, records)
        if policy == "locality" else
        select_batch_balanced_core_pairs(
            geometry, records, h0_mvm_count=4, h1_mvm_count=2,
            cross_mvm_count=4, cir_cost_scale=cir_cost_scale,
            traffic_cycle_weight=traffic_weight,
        )
    )
    run_dir = Path(output) / entry["name"]
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    if model_kind == "calibrated":
        config = EventPerformanceConfig(
            h0_mvm_count=4, h1_mvm_count=2, cross_mvm_count=4,
            fifo_depth=4, execution_mode="hybrid",
        )
        result = EventCompressedPerformanceModel(
            geometry, dataset, config,
        ).run(schedule=records, hybrid_core_pairs=placement.core_pairs)
        paths = write_event_performance_metrics(run_dir / "metrics", result, geometry, config)
    else:
        config = PerformanceConfig(
            h0_mvm_count=4, h1_mvm_count=2, cross_mvm_count=4,
            fifo_depth=4, timing_only=True, max_cycles=1_000_000_000,
            execution_mode="hybrid",
        )
        simulator = RamulatorEventPerformanceModel(
            geometry, dataset, dataset_path=str(dataset_path),
            ramulator_library=str(ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so"),
            ramulator_config=str(ROOT / "tb/ramulator_128x32.yaml"),
            mem_lanes=16, ticks_per_cycle=40, idle_tick_modulus=7600,
            config=config,
        )
        try:
            result = simulator.run(
                schedule=records, hybrid_core_pairs=placement.core_pairs,
            )
        finally:
            simulator.close()
        paths = write_exact_event_metrics(run_dir / "metrics", result, geometry)
    row = {
        "dataset": entry["name"], "family": entry["family"],
        "problem_vertices": entry["problem_vertices"], "model": model_kind,
        "policy": ("same-h0-cir-otherwise-cores" if policy == "locality"
                   else "static-overflow"),
        "cir_cost_scale": cir_cost_scale,
        "traffic_weight": traffic_weight,
        "accuracy": result.accuracy, "iteration_cycles": result.iteration_cycles,
        "total_cycles": result.total_cycles, "unordered_pairs": len(records),
        "core_pairs": len(placement.core_pairs), "cir_pairs": len(placement.cir_pairs),
        "directed_core_jobs": result.directed_core_jobs,
        "weight_block_reads": result.weight_block_reads,
        "injected_flits": result.counters.injected_flits if model_kind == "exact" else result.injected_flits,
        "physical_link_flits": result.counters.physical_link_flits if model_kind == "exact" else result.physical_link_flits,
        "wall_seconds": time.perf_counter() - started,
        "metrics_summary": str(paths["summary"]),
    }
    (run_dir / "point.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", choices=("calibrated", "exact"), required=True)
    parser.add_argument("--policy", choices=("locality", "overflow"), default="locality")
    parser.add_argument("--cir-cost-scale", type=float, default=1.0)
    parser.add_argument("--traffic-weight", type=int, default=1)
    parser.add_argument("--names", required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    wanted = set(args.names.split(","))
    entries = [e for e in json.loads(args.manifest.read_text())["datasets"] if e["name"] in wanted]
    missing = wanted - {e["name"] for e in entries}
    if missing:
        raise SystemExit(f"unknown datasets: {sorted(missing)}")
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(
            run_point, e, str(args.output_dir), args.model, args.policy,
            args.cir_cost_scale, args.traffic_weight,
        ) for e in entries]
        for future in as_completed(futures):
            row = future.result(); rows.append(row)
            print(f"PASS {row['dataset']} cycles={row['iteration_cycles']}", flush=True)
    rows.sort(key=lambda r: (r["problem_vertices"], r["dataset"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
