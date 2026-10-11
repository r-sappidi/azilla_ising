#!/usr/bin/env python3
"""Exact-event dense active-subset checks on one fixed 64K-capacity H1."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.metrics import write_exact_event_metrics
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry, ScheduledBlock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="16384,32768")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/dense_analytical_validation/exact_active_subset"),
    )
    args = parser.parse_args()
    geometry = Geometry(1, 1, 8, 256)
    dataset_path = ROOT / "results/dense_config_search/rtl_datasets/dense_65536_header.txt"
    for spins in (int(v) for v in args.sizes.split(",")):
        output = args.output_dir / str(spins)
        summary = output / "metrics_summary.json"
        if summary.is_file():
            print(f"SKIP spins={spins} existing={summary}", flush=True)
            continue
        blocks = spins // 32
        schedule = [
            ScheduledBlock(a, b) for a in range(blocks)
            for b in range(a + 1, blocks)
        ]
        dataset = BlockOccupancyDataset(geometry.spin_count, 0, set())
        output.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        model = RamulatorEventPerformanceModel(
            geometry, dataset, dataset_path=str(dataset_path),
            ramulator_library=str(ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so"),
            ramulator_config=str(ROOT / "tb/ramulator_128x32.yaml"),
            mem_lanes=16, ticks_per_cycle=40, idle_tick_modulus=7600,
            config=PerformanceConfig(
                h0_mvm_count=4, h1_mvm_count=2, cross_mvm_count=4,
                fifo_depth=4, timing_only=True, max_cycles=1_000_000_000,
            ),
        )
        try:
            result = model.run(schedule=schedule)
            write_exact_event_metrics(output / "metrics", result, geometry)
        finally:
            model.close()
        record = {
            "spins": spins, "physical_capacity_spins": geometry.spin_count,
            "active_blocks": blocks, "unordered_blocks": len(schedule),
            "accuracy": result.accuracy,
            "initialization_cycles": result.initialization_cycles,
            "iteration_cycles": result.iteration_cycles,
            "total_cycles": result.total_cycles,
            "wall_seconds": time.monotonic() - started,
            "validation_scope": (
                "fixed-H1 active-subset exact-event projection; no matching "
                "full-geometry RTL trace"
            ),
        }
        (output / "point.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"PASS spins={spins} cycles={result.total_cycles}", flush=True)


if __name__ == "__main__":
    main()
