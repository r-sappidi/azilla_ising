#!/usr/bin/env python3
"""Tune RTL-compatible schedules at 16K, then freeze and scale the winner."""

from __future__ import annotations

import csv
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "results/cir_evaluation/solution_quality/d4_to_1m/manifest.json"


def schedule(kind: str, iterations: int, a: int, b: int, noise: int):
    for step in range(iterations):
        if kind == "linear":
            amplitude = noise * (iterations - 1 - step) // (iterations - 1)
        else:
            half = iterations // 2
            amplitude = noise if step < half else noise * (
                iterations - 1 - step
            ) // max(1, iterations - 1 - half)
        yield step, a, b, amplitude


def write_schedule(path: Path, values) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("iteration", "coeff_a", "coeff_b", "noise_amplitude"))
        writer.writerows(values)


def run_reference(output: Path, schedule_path: Path, seeds: str, names: str = ""):
    command = [
        sys.executable, str(ROOT / "scripts/run_reference_solution_quality.py"),
        "--manifest", str(MANIFEST), "--output-dir", str(output),
        "--schedule", str(schedule_path), "--seeds", seeds,
    ]
    if names:
        command += ["--names", names]
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    root = ROOT / "results/cir_evaluation/solution_quality/schedule_sweep_20260904"
    training = root / "training_16k"
    rows = []
    for kind, a, b, noise in itertools.product(
        ("linear", "hold-half"), (0, 1, 2), (1, 2), (2, 4, 8, 16)
    ):
        name = f"{kind}_a{a}_b{b}_n{noise}_i1000"
        schedule_path = root / "schedules" / f"{name}.csv"
        write_schedule(schedule_path, schedule(kind, 1000, a, b, noise))
        output = training / name
        run_reference(output, schedule_path, "0,1,2,3,4",
                      "planted_bipartite_n16384_d4")
        results = list(csv.DictReader((output / "summary.csv").open()))
        fractions = [int(row["best_cut"]) / int(row["known_cut"]) for row in results]
        rows.append({
            "name": name, "kind": kind, "a": a, "b": b, "noise": noise,
            "iterations": 1000, "seeds": len(fractions),
            "mean_fraction": sum(fractions) / len(fractions),
            "minimum_fraction": min(fractions), "maximum_fraction": max(fractions),
            "success_99": sum(value >= .99 for value in fractions),
            "schedule": str(schedule_path.relative_to(ROOT)),
        })
        with (root / "training_summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    # Select by mean quality, then worst-seed quality, without consulting any
    # larger graph.  This freezes the schedule before held-out scaling.
    winner = max(rows, key=lambda row: (
        row["mean_fraction"], row["minimum_fraction"], -row["noise"]
    ))
    (root / "winner.json").write_text(json.dumps(winner, indent=2) + "\n")
    final_output = root / "held_out_scaling"
    run_reference(
        final_output, ROOT / str(winner["schedule"]),
        ",".join(map(str, range(30))),
    )
    print(f"COMPLETE winner={winner['name']} results={final_output}")


if __name__ == "__main__":
    main()
