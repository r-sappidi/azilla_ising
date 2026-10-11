#!/usr/bin/env python3
"""Plot only exact-event cores-only points accepted by the paper audit."""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/paper_final_20260906/audit/exact_cores_only_accepted.csv"
QUALITY = ROOT / "results/paper_final_20260906/audit/solution_quality_accepted.csv"
OUT = ROOT / "plots/paper_final_20260906/audited_cores_only"
LABEL = {
    "toroidal_d4": "Toroidal, degree 4",
    "uniform_d16": "Uniform random, degree 16",
    "community_d16": "Community, degree 16",
}
COLOR = {"toroidal_d4": "#2878B5", "uniform_d16": "#D95319",
         "community_d16": "#3A923A"}


def family(dataset: str) -> str:
    return dataset.split("_n", 1)[0].rsplit("_s", 1)[0]


def save(fig, name: str) -> None:
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix in ("pdf", "png"):
        fig.savefig(OUT / f"{name}.{suffix}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    rows = list(csv.DictReader(SOURCE.open(newline="")))
    if not rows or any(row["accuracy"] != "cycle-structured-unverified"
                       for row in rows):
        raise RuntimeError("audit source is empty or contains an invalid label")
    for row in rows:
        row["family"] = family(row["dataset"])
        for key in ("spins", "iteration_cycles", "physical_link_flits",
                    "dram_accepted", "dram_rejected"):
            row[key] = int(row[key])
        row["average_hops"] = float(row["average_hops"])
        row["dram_rejection_fraction"] = float(row["dram_rejection_fraction"])

    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
    for name, label in LABEL.items():
        values = sorted((r for r in rows if r["family"] == name),
                        key=lambda r: r["spins"])
        axes[0].plot([r["spins"] for r in values],
                     [r["iteration_cycles"] for r in values], "o-",
                     color=COLOR[name], label=label)
        axes[1].plot([r["spins"] for r in values],
                     [r["iteration_cycles"] / r["spins"] for r in values],
                     "o-", color=COLOR[name], label=label)
    for ax in axes:
        ax.set_xscale("log", base=2); ax.set_yscale("log", base=2)
        ax.grid(alpha=.25); ax.set_xlabel("Problem size (spins)")
    axes[0].set_ylabel("Iteration latency (cycles)")
    axes[1].set_ylabel("Iteration cycles per spin")
    axes[0].legend(frameon=False, fontsize=8)
    save(fig, "cores_only_latency_scaling")

    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
    for name, label in LABEL.items():
        values = sorted((r for r in rows if r["family"] == name),
                        key=lambda r: r["spins"])
        axes[0].plot([r["spins"] for r in values],
                     [r["physical_link_flits"] for r in values], "o-",
                     color=COLOR[name], label=label)
        axes[1].plot([r["spins"] for r in values],
                     [100 * r["dram_rejection_fraction"] for r in values],
                     "o-", color=COLOR[name], label=label)
    axes[0].set_yscale("symlog", linthresh=1)
    axes[0].set_ylabel("Physical-link flits per iteration")
    axes[1].set_ylabel("DRAM request rejection (%)")
    for ax in axes:
        ax.set_xscale("log", base=2); ax.grid(alpha=.25)
        ax.set_xlabel("Problem size (spins)")
    axes[0].legend(frameon=False, fontsize=8)
    save(fig, "cores_only_communication_memory_scaling")

    quality = list(csv.DictReader(QUALITY.open(newline="")))
    sizes = sorted({int(row["spins"]) for row in quality})
    distributions = [
        [100 * float(row["fraction_of_known_cut"]) for row in quality
         if int(row["spins"]) == size] for size in sizes
    ]
    convergence = [
        [int(row["best_iteration"]) for row in quality
         if int(row["spins"]) == size] for size in sizes
    ]
    labels = [f"{size // 1024}K" if size < 1048576 else "1M"
              for size in sizes]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
    axes[0].boxplot(distributions, tick_labels=labels, showfliers=False)
    axes[1].boxplot(convergence, tick_labels=labels, showfliers=False)
    axes[0].set_ylabel("Known cut achieved (%)")
    axes[1].set_ylabel("Iteration producing best solution")
    for ax in axes:
        ax.set_xlabel("Problem size"); ax.grid(axis="y", alpha=.25)
    save(fig, "solution_quality_scaling")
    print(f"PASS points={len(rows)} output={OUT}")


if __name__ == "__main__":
    main()
