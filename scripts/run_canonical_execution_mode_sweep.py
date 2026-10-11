#!/usr/bin/env python3
"""Run a resumable CIR versus cores-only sweep over a dataset manifest."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def comma_set(text: str) -> set[str]:
    return {value.strip() for value in text.split(",") if value.strip()}


def comma_ints(text: str) -> set[int]:
    return {int(value) for value in text.split(",") if value.strip()}


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_point(
    entry: dict[str, Any], mode: str, output_dir: str,
    configuration: dict[str, int], force: bool,
) -> dict[str, Any]:
    run_dir = Path(output_dir) / entry["name"] / mode.replace("-", "_")
    point_path = run_dir / "point.json"
    summary_path = run_dir / "metrics_event_summary.json"
    if point_path.is_file() and summary_path.is_file() and not force:
        return json.loads(point_path.read_text())

    run_dir.mkdir(parents=True, exist_ok=True)
    geometry = entry["geometry"]
    command = [
        sys.executable, "-m", "azilla_cycle_model.cli", "simulate-events",
        "--dataset", str((ROOT / entry["dataset"]).resolve()),
        "--mesh-x", str(geometry["mesh_x"]),
        "--mesh-y", str(geometry["mesh_y"]),
        "--h0-per-h1", str(geometry["h0_per_h1"]),
        "--cores-per-h0", str(geometry["cores_per_h0"]),
        "--h0-mvms", str(configuration["h0_mvms"]),
        "--h1-mvms", str(configuration["h1_mvms"]),
        "--cross-mvms", str(configuration["cross_mvms"]),
        "--fifo-depth", str(configuration["fifo_depth"]),
        "--iterations", "1", "--execution-mode", mode,
        "--metrics-prefix", str((run_dir / "metrics").resolve()),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "model")
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    wall_seconds = time.perf_counter() - started
    (run_dir / "run.log").write_text(completed.stdout)
    atomic_json(run_dir / "command.json", {
        "argv": command, "cwd": str(ROOT), "exit_code": completed.returncode,
        "wall_seconds": wall_seconds,
    })
    point: dict[str, Any] = {
        "dataset": entry["name"], "family": entry["family"],
        "problem_vertices": entry["problem_vertices"],
        "capacity_spins": entry["capacity_spins"],
        "average_degree": entry["average_degree"],
        "undirected_edges": entry["undirected_edges"],
        "dataset_sha256": entry["sha256"],
        "mesh_x": geometry["mesh_x"], "mesh_y": geometry["mesh_y"],
        "h0_per_h1": geometry["h0_per_h1"],
        "cores_per_h0": geometry["cores_per_h0"],
        "h0_mvms": configuration["h0_mvms"],
        "h1_mvms": configuration["h1_mvms"],
        "cross_mvms": configuration["cross_mvms"],
        "fifo_depth": configuration["fifo_depth"],
        "execution_mode": mode, "wall_seconds": wall_seconds,
        "exit_code": completed.returncode,
        "metrics_summary": str(summary_path),
    }
    if completed.returncode == 0 and summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        point.update({
            "accuracy": summary.get("accuracy", ""),
            "calibration": summary.get("calibration", ""),
            "initialization_cycles": summary["timing_cycles"]["initialization"],
            "iteration_cycles": summary["timing_cycles"]["iteration"],
            "total_cycles": summary["timing_cycles"]["total"],
            "scheduled_h0": summary["scheduled_jobs"]["h0"],
            "scheduled_h1": summary["scheduled_jobs"]["h1"],
            "scheduled_cross": summary["scheduled_jobs"]["cross"],
            "scheduled_total": summary["scheduled_jobs"]["total"],
            "estimated_dram_requests": summary.get("estimated_dram_requests", 0),
            "injected_flits": summary["noc"]["injected_flits"],
            "physical_link_flits": summary["noc"]["physical_link_flits"],
            "injection_stalls": summary["noc"]["injection_stalls"],
            "link_stalls": summary["noc"]["link_stalls"],
            "average_hops": summary["noc"]["average_hops"],
        })
        point.update({
            "ablation_" + key: value
            for key, value in summary.get("cores_only_ablation", {}).items()
        })
    atomic_json(point_path, point)
    return point


def write_summary(output_dir: Path) -> int:
    rows = [json.loads(path.read_text()) for path in output_dir.glob("*/*/point.json")]
    rows.sort(key=lambda row: (row["problem_vertices"], row["dataset"], row["execution_mode"]))
    if not rows:
        return 0
    fields = sorted({key for row in rows for key in row})
    temporary = output_dir / "summary.csv.part"
    with temporary.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output_dir / "summary.csv")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sizes", type=comma_ints)
    parser.add_argument("--families", type=comma_set)
    parser.add_argument("--names", type=comma_set)
    parser.add_argument("--modes", type=comma_set, default={"cir", "cores-only"})
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--fifo-depth", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("workers must be positive")
    if not args.modes <= {"cir", "cores-only"}:
        parser.error("modes must contain only cir and/or cores-only")

    entries = json.loads(args.manifest.read_text())["datasets"]
    if args.sizes:
        entries = [entry for entry in entries if entry["problem_vertices"] in args.sizes]
    if args.families:
        entries = [entry for entry in entries if entry["family"] in args.families]
    if args.names:
        entries = [entry for entry in entries if entry["name"] in args.names]
    tasks = [(entry, mode) for entry in entries for mode in sorted(args.modes)]
    if not tasks:
        parser.error("filters selected no experiment points")
    print(f"datasets={len(entries)} points={len(tasks)} workers={args.workers}", flush=True)
    if args.dry_run:
        for entry, mode in tasks:
            print(f"{entry['name']} {mode}")
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    configuration = {
        "h0_mvms": args.h0_mvms, "h1_mvms": args.h1_mvms,
        "cross_mvms": args.cross_mvms, "fifo_depth": args.fifo_depth,
    }
    atomic_json(args.output_dir / "experiment_context.json", {
        "schema": "azilla-canonical-execution-modes-v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "workers": args.workers,
        "manifest": str(args.manifest), "configuration": configuration,
        "modes": sorted(args.modes), "dataset_count": len(entries),
        "point_count": len(tasks),
    })
    failures = 0
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                run_point, entry, mode, str(args.output_dir),
                configuration, args.force,
            ): (entry["name"], mode)
            for entry, mode in tasks
        }
        for index, future in enumerate(as_completed(futures), 1):
            name, mode = futures[future]
            try:
                row = future.result()
                failures += int(row["exit_code"] != 0)
                print(
                    f"[{index}/{len(tasks)}] {name} {mode} "
                    f"exit={row['exit_code']} cycles={row.get('iteration_cycles', '')} "
                    f"wall={row['wall_seconds']:.3f}s", flush=True,
                )
            except Exception as error:
                failures += 1
                print(f"[{index}/{len(tasks)}] {name} {mode} ERROR {error!r}", flush=True)
            write_summary(args.output_dir)
    print(f"recorded={write_summary(args.output_dir)} failures={failures}", flush=True)
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
