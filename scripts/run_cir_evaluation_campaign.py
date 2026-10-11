#!/usr/bin/env python3
"""Run the calibrated dense rows of a CIR campaign, resumably and in parallel."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def comma_set(text: str) -> set[str]:
    return {value.strip() for value in text.split(",") if value.strip()}


def comma_ints(text: str) -> set[int]:
    return {int(value) for value in text.split(",") if value.strip()}


def label(row: dict[str, str]) -> str:
    identity = "_".join(
        row[key] for key in (
            "phase", "spins", "mesh_x", "mesh_y", "h0_per_h1",
            "cores_per_h0", "h0_mvms", "h1_mvms", "cross_mvms",
            "execution_mode", "replicate",
        )
    )
    safe = identity.replace("cores-only", "cores_only")
    return safe + "_" + hashlib.sha256(identity.encode()).hexdigest()[:8]


def run_point(
    row: dict[str, str], output_dir: str, force: bool,
) -> dict[str, object]:
    run_dir = Path(output_dir) / label(row)
    point_path = run_dir / "point.json"
    if point_path.is_file() and not force:
        return json.loads(point_path.read_text())
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset = run_dir / "dense_header.txt"
    dataset.write_text(f"{row['spins']} 0\n")
    prefix = run_dir / "metrics"
    command = [
        sys.executable, "-m", "azilla_cycle_model.cli", "simulate-events",
        "--dataset", str(dataset.resolve()), "--dense",
        "--mesh-x", row["mesh_x"], "--mesh-y", row["mesh_y"],
        "--h0-per-h1", row["h0_per_h1"],
        "--cores-per-h0", row["cores_per_h0"],
        "--h0-mvms", row["h0_mvms"], "--h1-mvms", row["h1_mvms"],
        "--cross-mvms", row["cross_mvms"],
        "--iterations", row["iterations"],
        "--execution-mode", row["execution_mode"],
        "--metrics-prefix", str(prefix.resolve()),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "model")
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    wall = time.perf_counter() - started
    (run_dir / "run.log").write_text(completed.stdout)
    (run_dir / "command.json").write_text(json.dumps({
        "argv": command, "cwd": str(ROOT), "wall_seconds": wall,
        "exit_code": completed.returncode,
    }, indent=2) + "\n")
    summary_path = prefix.with_name(prefix.name + "_event_summary.json")
    point: dict[str, object] = dict(row)
    point.update({
        "run_id": label(row), "wall_seconds": wall,
        "exit_code": completed.returncode, "metrics_summary": str(summary_path),
    })
    if completed.returncode == 0 and summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        point["accuracy"] = summary.get("accuracy", "")
        for output_key, section, source_key in (
            ("initialization_cycles", "timing_cycles", "initialization"),
            ("iteration_cycles", "timing_cycles", "iteration"),
            ("total_cycles", "timing_cycles", "total"),
            ("scheduled_h0", "scheduled_jobs", "h0"),
            ("scheduled_h1", "scheduled_jobs", "h1"),
            ("scheduled_cross", "scheduled_jobs", "cross"),
            ("scheduled_total", "scheduled_jobs", "total"),
            ("injected_flits", "noc", "injected_flits"),
            ("physical_link_flits", "noc", "physical_link_flits"),
            ("injection_stalls", "noc", "injection_stalls"),
            ("link_stalls", "noc", "link_stalls"),
            ("average_hops", "noc", "average_hops"),
        ):
            point[output_key] = summary.get(section, {}).get(source_key, 0)
        for key, value in summary.get("cores_only_ablation", {}).items():
            point[f"ablation_{key}"] = value
        point["estimated_dram_requests"] = summary.get(
            "estimated_dram_requests", 0
        )
    temporary = point_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(point, indent=2, sort_keys=True) + "\n")
    temporary.replace(point_path)
    return point


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix", type=Path,
        default=Path("results/cir_evaluation/campaign/experiment_matrix.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/cir_evaluation/calibrated_dense"),
    )
    parser.add_argument(
        "--phases", type=comma_set,
        default=comma_set("dense-dse-screen,mesh-shape-screen,cir-ablation-screen"),
    )
    parser.add_argument(
        "--sizes", type=comma_ints,
        help="comma-separated spin sizes; stage large O(blocks^2) dense runs",
    )
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("workers must be positive")

    with args.matrix.open(newline="") as source:
        rows = [
            row for row in csv.DictReader(source)
            if row["phase"] in args.phases
            and row["fidelity"] == "calibrated-event"
            and row["workload"] == "dense-block-stress"
            and (not args.sizes or int(row["spins"]) in args.sizes)
        ]
    # The generator can intentionally produce duplicates (e.g., a reference
    # point appearing in both DSE and mesh phases); phase remains part of the
    # identity because each answers a different plotted comparison.
    if args.dry_run:
        for row in rows:
            print(label(row))
        print(f"points={len(rows)}")
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    failures = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_point, row, str(args.output_dir), args.force): row
            for row in rows
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            failures += int(result["exit_code"] != 0)
            print(
                f"DONE run={result['run_id']} exit={result['exit_code']} "
                f"wall={float(result['wall_seconds']):.2f}s",
                flush=True,
            )
    # Consolidate every completed point in the output directory. A staged
    # 64K/128K/256K invocation must not replace the earlier sizes in the
    # campaign-level summary.
    completed_points = {}
    for point_path in args.output_dir.glob("*/point.json"):
        point = json.loads(point_path.read_text())
        completed_points[str(point["run_id"])] = point
    completed_points.update({str(result["run_id"]): result for result in results})
    results = sorted(completed_points.values(), key=lambda item: str(item["run_id"]))
    if results:
        fields = sorted({key for result in results for key in result})
        with (args.output_dir / "summary.csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader(); writer.writerows(results)
    print(f"complete={len(results) - failures} failed={failures}")
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
