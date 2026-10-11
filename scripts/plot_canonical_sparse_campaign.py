#!/usr/bin/env python3
"""Create consistent paper figures from the canonical fixed-H1 sweep."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import fmean

import matplotlib.pyplot as plt


METRICS = (
    ("iteration_cycles", "Iteration cycles"),
    ("physical_link_flits", "Physical-link flits per spin"),
    ("maximum_link_window_utilization", "Maximum directed-link utilization"),
    ("cross_job_fraction", "Cross-H1 block-job fraction"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path,
        default=Path("results/paper_canonical/calibrated_identity/results.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("plots/paper_canonical")
    )
    args = parser.parse_args()
    with args.results.open(newline="") as source:
        rows = list(csv.DictReader(source))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    labels = {
        "canonical-toroidal-d4": "Toroidal, degree 4",
        "canonical-uniform-d16": "Uniform random, degree 16",
        "canonical-community-d16": "Community-local, degree 16",
    }
    weak = [row for row in rows if row["family"] in labels]
    grouped = defaultdict(list)
    for row in weak:
        grouped[(row["family"], int(row["problem_vertices"]))].append(row)

    fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.2))
    for ax, (metric, ylabel) in zip(axes.flat, METRICS):
        for family, label in labels.items():
            points = []
            for (candidate, spins), samples in grouped.items():
                if candidate != family:
                    continue
                values = [float(row[metric]) for row in samples]
                if metric == "physical_link_flits":
                    values = [value / spins for value in values]
                points.append((spins, fmean(values), min(values), max(values)))
            points.sort()
            if not points:
                continue
            x = [item[0] for item in points]
            y = [item[1] for item in points]
            lo = [item[2] for item in points]
            hi = [item[3] for item in points]
            ax.plot(x, y, marker="o", linewidth=2, label=label)
            if any(a != b for a, b in zip(lo, hi)):
                ax.fill_between(x, lo, hi, alpha=0.15)
        ax.set_xscale("log", base=2)
        if metric in {"iteration_cycles", "physical_link_flits"}:
            ax.set_yscale("log")
        ax.set_xlabel("Spins")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
    axes[0, 0].legend(fontsize=9)
    fig.suptitle("Canonical fixed-H1 sparse weak scaling (4/2/4 MVMs)")
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(args.output_dir / f"canonical_weak_scaling.{suffix}", dpi=220)
    plt.close(fig)

    degree_rows = [
        row for row in rows
        if row["family"].startswith("degree-sensitivity-uniform-d")
        or (
            row["family"] == "canonical-uniform-d16"
            and int(row["problem_vertices"]) == 131_072
        )
    ]
    degree_groups = defaultdict(list)
    for row in degree_rows:
        degree_groups[int(round(float(row["average_degree"])))].append(row)
    if degree_groups:
        degrees = sorted(degree_groups)
        cycles = [fmean(float(r["iteration_cycles"]) for r in degree_groups[d]) for d in degrees]
        flits = [fmean(float(r["physical_link_flits"]) for r in degree_groups[d]) for d in degrees]
        fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
        axes[0].plot(degrees, cycles, "o-", linewidth=2)
        axes[0].set_ylabel("Iteration cycles")
        axes[1].plot(degrees, flits, "o-", linewidth=2)
        axes[1].set_ylabel("Physical-link flits")
        for ax in axes:
            ax.set_xlabel("Average degree")
            ax.set_yscale("log")
            ax.grid(True, alpha=0.25)
        fig.suptitle("128K uniform-random sparsity sensitivity")
        fig.tight_layout()
        for suffix in ("png", "pdf"):
            fig.savefig(args.output_dir / f"degree_sensitivity_128k.{suffix}", dpi=220)
        plt.close(fig)

    print(f"figures={args.output_dir}")


if __name__ == "__main__":
    main()
