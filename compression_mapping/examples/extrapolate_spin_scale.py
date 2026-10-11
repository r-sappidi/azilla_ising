#!/usr/bin/env python3
"""Extrapolate larger spin-count timing from saved multi-size trials."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from math import log10
from pathlib import Path

import numpy as np


STAGES = ("partition", "permutation", "lossy", "scheduled")
COLORS = {
    "partition": "#377eb8", "permutation": "#4daf4a",
    "lossy": "#984ea3", "scheduled": "#e41a1c",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--target-spins", type=int, default=65536)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = list(csv.DictReader(args.input.open()))
    samples: dict[tuple[int, float, str], list[float]] = defaultdict(list)
    for row in rows:
        samples[int(row["vertices"]), float(row["sparsity"]), row["stage"]].append(
            float(row["estimated_iteration_cycles"])
        )
    source_sizes = sorted({key[0] for key in samples})
    if len(source_sizes) < 2:
        raise ValueError("power-law extrapolation requires at least two spin sizes")
    measured_sparsities = sorted({key[1] for key in samples})
    first = min(value for value in measured_sparsities if value < 1.0)

    def predict(sparsity: float, stage: str) -> tuple[float, float, float]:
        sizes, means, cvs = [], [], []
        for size in source_sizes:
            values = np.asarray(samples[size, sparsity, stage])
            if values.size and values.mean() > 0:
                sizes.append(size)
                means.append(float(values.mean()))
                cvs.append(float(values.std() / values.mean()))
        log_sizes, log_means = np.log(sizes), np.log(means)
        exponent, intercept = np.polyfit(log_sizes, log_means, 1)
        prediction = float(np.exp(intercept + exponent * np.log(args.target_spins)))
        residual = float(np.sqrt(np.mean(
            (log_means - (intercept + exponent * log_sizes)) ** 2
        )))
        relative_uncertainty = float(np.sqrt(residual ** 2 + np.mean(cvs) ** 2))
        return prediction, prediction * relative_uncertainty, float(exponent)

    identity, identity_std, identity_exponent = predict(first, "identity")
    points = [0.0, 0.5, 0.75, 0.9, 0.93, 0.96, 0.98] + measured_sparsities
    points = sorted(set(points))
    output_rows = []
    for stage in STAGES:
        anchor, anchor_std, anchor_exponent = predict(first, stage)
        for sparsity in points:
            if sparsity in measured_sparsities:
                mean, std, exponent = predict(sparsity, stage)
                source_kind = "empirical-power-law-from-512-4k-16k"
            elif sparsity <= 0.9:
                progress = {0.0: 0.0, 0.5: 0.03, 0.75: 0.07, 0.9: 0.12}[sparsity]
                mean = identity - progress * max(0.0, identity - anchor)
                std = 0.0
                exponent = anchor_exponent
                source_kind = "dense-regime-heuristic"
            else:
                # Interpolate in logarithmic density between the 90% heuristic
                # and the first scaled measured point at 99.21875% sparsity.
                at_90 = identity - 0.12 * max(0.0, identity - anchor)
                x = -log10(1.0 - sparsity)
                x0, x1 = -log10(0.1), -log10(1.0 - first)
                fraction = (x - x0) / (x1 - x0)
                mean = at_90 + fraction * (anchor - at_90)
                std = 0.0
                exponent = anchor_exponent
                source_kind = "log-density-interpolation"
            output_rows.append({
                "spins": args.target_spins,
                "sparsity": sparsity,
                "stage": stage,
                "iteration_cycles_mean": round(mean, 3),
                "iteration_cycles_stddev": round(std, 3),
                "source": source_kind,
                "fitted_size_exponent": round(exponent, 6),
                "source_spin_counts": ",".join(map(str, source_sizes)),
            })
    # Independent power-law fits can cross even though every measured
    # scheduled candidate is selected against lossy endpoint-local. Preserve
    # that algorithmic no-regression envelope in extrapolated curves.
    lossy_by_sparsity = {
        row["sparsity"]: row for row in output_rows if row["stage"] == "lossy"
    }
    for row in output_rows:
        if row["stage"] != "scheduled":
            continue
        lossy = lossy_by_sparsity[row["sparsity"]]
        if row["iteration_cycles_mean"] > lossy["iteration_cycles_mean"]:
            row["iteration_cycles_mean"] = lossy["iteration_cycles_mean"]
            row["iteration_cycles_stddev"] = lossy["iteration_cycles_stddev"]
            row["source"] += "+no-regression-envelope"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / f"mapping_{args.target_spins}_extrapolated.csv"
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    import matplotlib.pyplot as plt
    fig, axis = plt.subplots(figsize=(10, 5.5))
    positive = [1.0 - value for value in points if value < 1.0]
    floor = min(positive) / 2
    xmap = {value: -log10(max(1.0 - value, floor)) for value in points}
    for stage in STAGES:
        selected = [row for row in output_rows if row["stage"] == stage]
        axis.errorbar(
            [xmap[row["sparsity"]] for row in selected],
            [row["iteration_cycles_mean"] for row in selected],
            yerr=[row["iteration_cycles_stddev"] for row in selected],
            marker="o", capsize=3, label=stage, color=COLORS[stage],
        )
    axis.axhline(identity, color="#555555", linestyle="--", linewidth=1.5,
                 label=f"identity baseline ({identity:.0f})")
    axis.set_xticks([xmap[value] for value in points])
    axis.set_xticklabels(
        [f"{100 * value:.4g}" for value in points], rotation=45, ha="right"
    )
    axis.set_xlabel("Graph sparsity (%) — logarithmic density spacing")
    axis.set_ylabel("Ising Iteration Cycles")
    axis.set_title(f"{args.target_spins:,} spins — heuristic extrapolation")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    plot_path = args.output_dir / f"runtime_{args.target_spins}_vs_log_sparsity.png"
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)
    print(
        f"wrote {csv_path} and {plot_path}; "
        f"source_sizes={source_sizes} identity_exponent={identity_exponent:.3f}"
    )


if __name__ == "__main__":
    main()
