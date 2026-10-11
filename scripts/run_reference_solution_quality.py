#!/usr/bin/env python3
"""Run an independent sparse-matrix Ising reference and record cut trajectories.

This is an algorithm/solution-quality experiment, not a timing model.  Runs are
resumable per dataset and seed.  The update equation matches the independent
Octave reference: x' = (a*s + b*J*s + noise) >= 0.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np


MASK32 = np.uint64(0xFFFFFFFF)


def comma_ints(text: str) -> list[int]:
    return sorted({int(value) for value in text.split(",") if value.strip()})


def xorshift32(values: np.ndarray) -> np.ndarray:
    values ^= (values << np.uint64(13)) & MASK32
    values ^= values >> np.uint64(17)
    values ^= (values << np.uint64(5)) & MASK32
    return values & MASK32


def initial_state(spins: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    blocks = spins // 32
    ids = np.arange(blocks, dtype=np.uint64)
    words = xorshift32(
        (np.uint64(2654435769) ^ (ids * np.uint64(2246822507)) ^ seed) & MASK32
    )
    bits = np.arange(32, dtype=np.uint64)
    state = ((words[:, None] >> bits) & 1).astype(np.bool_).reshape(-1)
    lfsr = (
        np.uint64(3518319157) ^ (ids * np.uint64(668265261)) ^
        np.uint64(seed)
    ) & MASK32
    lfsr[lfsr == 0] = 1
    return state, lfsr


def lfsr_step(values: np.ndarray) -> np.ndarray:
    feedback = (
        ((values >> 31) ^ (values >> 21) ^ (values >> 1) ^ values) & 1
    )
    return ((values << 1) | feedback) & MASK32


def load_dataset(path: Path):
    with path.open() as source:
        spins, known_cut = map(int, source.readline().split())
        records = np.loadtxt(source, dtype=np.int64)
    if records.size == 0:
        records = np.empty((0, 3), dtype=np.int64)
    records = records.reshape((-1, 3))
    rows = records[:, 0] - 1
    columns = records[:, 1] - 1
    weights = records[:, 2]
    return spins, known_cut, rows, columns, weights


def cut_value(state, rows, columns, objective_weights) -> int:
    directed = objective_weights[
        state[rows] != state[columns]
    ].sum(dtype=np.int64)
    return int(directed // 2)


def load_schedule(path: Path | None, args) -> list[tuple[int, int, int]]:
    if path is None:
        return [
            (args.coeff_a, args.coeff_b, args.noise_amplitude)
            for _ in range(args.iterations)
        ]
    with path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("coefficient schedule is empty")
    schedule = []
    for expected, row in enumerate(rows):
        if int(row["iteration"]) != expected:
            raise ValueError("schedule iterations must be contiguous from zero")
        schedule.append(tuple(
            int(row[key]) for key in ("coeff_a", "coeff_b", "noise_amplitude")
        ))
    return schedule


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/cir_evaluation/solution_quality/reference"),
    )
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seeds", type=comma_ints, default=list(range(30)))
    parser.add_argument("--coeff-a", type=int, default=0)
    parser.add_argument("--coeff-b", type=int, default=1)
    parser.add_argument("--noise-amplitude", type=int, default=0)
    parser.add_argument(
        "--schedule", type=Path,
        help="CSV columns: iteration,coeff_a,coeff_b,noise_amplitude",
    )
    parser.add_argument("--names", default="")
    parser.add_argument(
        "--include-published", action="store_true",
        help="also run manifest entries such as published Gset instances",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    names = {value for value in args.names.split(",") if value}
    manifest = json.loads(args.manifest.read_text())
    schedule = load_schedule(args.schedule, args)
    entries = [
        entry for entry in manifest["datasets"]
        if (entry.get("generated") or args.include_published)
        and (not names or entry["name"] in names)
    ]
    if not entries:
        parser.error("no generated datasets selected")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for entry in entries:
        path = Path(entry["dataset"])
        spins, known_cut, rows, columns, weights = load_dataset(path)
        objective_weights = (
            np.abs(weights) if entry.get("cut_weight_mode") == "absolute"
            else -weights if entry.get("cut_weight_mode") == "negated"
            else weights
        )
        for seed in args.seeds:
            run_dir = args.output_dir / entry["name"] / f"seed_{seed}"
            result_path = run_dir / "result.json"
            if result_path.is_file() and not args.force:
                summary.append(json.loads(result_path.read_text()))
                continue
            run_dir.mkdir(parents=True, exist_ok=True)
            state, lfsr = initial_state(spins, seed)
            trajectory = []
            started = time.perf_counter()
            best_cut = cut_value(state, rows, columns, objective_weights)
            best_iteration = 0
            trajectory.append((0, best_cut))
            for iteration, (coeff_a, coeff_b, noise_amplitude) in enumerate(
                schedule, 1
            ):
                signs = state.astype(np.int64) * 2 - 1
                coupling = np.bincount(
                    rows, weights=weights * signs[columns], minlength=spins
                ).astype(np.int64, copy=False)
                field = coeff_a * signs + coeff_b * coupling
                lfsr = lfsr_step(lfsr)
                if noise_amplitude:
                    bits = np.arange(32, dtype=np.uint64)
                    noise_sign = (
                        ((lfsr[:, None] >> bits) & 1).reshape(-1).astype(np.int64)
                        * 2 - 1
                    )
                    field += noise_amplitude * noise_sign
                state = field >= 0
                value = cut_value(state, rows, columns, objective_weights)
                trajectory.append((iteration, value))
                if value > best_cut:
                    best_cut, best_iteration = value, iteration
            wall = time.perf_counter() - started
            with (run_dir / "cut_trajectory.csv").open("w", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(("iteration", "cut")); writer.writerows(trajectory)
            result = {
                "dataset": entry["name"], "spins": spins, "seed": seed,
                "algorithm": "quantized-noisy-feedback-ising",
                "noise_distribution": "rademacher",
                "iterations": len(schedule), "coeff_a": args.coeff_a,
                "coeff_b": args.coeff_b,
                "noise_amplitude": args.noise_amplitude,
                "schedule": str(args.schedule) if args.schedule else "constant",
                "schedule_first": "/".join(map(str, schedule[0])),
                "schedule_last": "/".join(map(str, schedule[-1])),
                "known_cut": known_cut, "best_cut": best_cut,
                "best_iteration": best_iteration,
                "gap_fraction": ((known_cut - best_cut) / known_cut if known_cut else None),
                "success": bool(known_cut and best_cut >= known_cut),
                "wall_seconds": wall,
            }
            result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            summary.append(result)
            print(
                f"DONE dataset={entry['name']} seed={seed} best={best_cut}/"
                f"{known_cut} wall={wall:.2f}s", flush=True,
            )
        with (args.output_dir / "summary.csv").open("w", newline="") as output:
            fields = list(summary[0])
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader(); writer.writerows(summary)


if __name__ == "__main__":
    main()
