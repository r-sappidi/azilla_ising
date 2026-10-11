#!/usr/bin/env python3
"""Create paired, paper-oriented CIR/core-only characterization figures."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


LABELS = {
    "canonical-toroidal-d4": "Toroidal, degree 4",
    "canonical-uniform-d16": "Uniform, degree 16",
    "canonical-community-d16": "Community, degree 16",
}
COLORS = {"cir": "#2878B5", "cores-only": "#D95319"}


def save(fig, out: Path, stem: str) -> None:
    fig.tight_layout()
    for suffix in ("pdf", "png"):
        fig.savefig(out / f"{stem}.{suffix}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--summary", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    rows = list(csv.DictReader(args.summary.open(newline="")))
    pairs: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        pairs[row["dataset"]][row["execution_mode"]] = row
    complete = [v for v in pairs.values() if set(v) == {"cir", "cores-only"}]
    if len(complete) * 2 != len(rows):
        raise RuntimeError("execution-mode matrix is not completely paired")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Homogeneous scaling uses every seed as a faint point and the seed mean
    # as the line, preserving variability instead of selecting one dataset.
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.7))
    for family, label in LABELS.items():
        selected = [p for p in complete if p["cir"]["family"] == family]
        grouped: dict[int, list[dict[str, dict[str, str]]]] = defaultdict(list)
        for pair in selected:
            grouped[int(pair["cir"]["problem_vertices"])].append(pair)
        sizes = sorted(grouped)
        speedup = [sum(float(x["cores-only"]["iteration_cycles"]) /
                       float(x["cir"]["iteration_cycles"]) for x in grouped[n]) /
                   len(grouped[n]) for n in sizes]
        axes[0].plot(sizes, speedup, "o-", label=label)
        traffic = [sum(float(x["cores-only"]["physical_link_flits"]) /
                       max(1.0, float(x["cir"]["physical_link_flits"]))
                       for x in grouped[n]) / len(grouped[n]) for n in sizes]
        axes[1].plot(sizes, traffic, "o-", label=label)
    for ax in axes:
        ax.set_xscale("log", base=2); ax.axhline(1, color="black", lw=.8)
        ax.grid(alpha=.25); ax.set_xlabel("Problem size (spins)")
    axes[0].set_ylabel("CIR speedup (core cycles / CIR cycles)")
    axes[1].set_ylabel("Core/CIR physical-link flit ratio")
    axes[0].legend(frameon=False, fontsize=8)
    save(fig, args.output_dir, "execution_mode_scaling")

    # Per-dataset result preserves individual wins/losses hidden by means.
    ordered = sorted(complete, key=lambda x: (
        int(x["cir"]["problem_vertices"]), x["cir"]["family"],
        x["cir"]["dataset"]))
    values = [float(x["cores-only"]["iteration_cycles"]) /
              float(x["cir"]["iteration_cycles"]) for x in ordered]
    fig, ax = plt.subplots(figsize=(10.2, 4.0))
    ax.bar(range(len(values)), values,
           color=["#2878B5" if v >= 1 else "#D95319" for v in values])
    ax.axhline(1, color="black", lw=1); ax.grid(axis="y", alpha=.25)
    ax.set_ylabel("CIR speedup over cores-only")
    ax.set_xlabel("Canonical workload instances, ordered by size")
    save(fig, args.output_dir, "execution_mode_per_instance")

    source = args.output_dir / "execution_mode_pairs.csv"
    with source.open("w", newline="") as stream:
        fields = ["dataset", "family", "spins", "cir_cycles", "core_cycles",
                  "cir_speedup", "cir_link_flits", "core_link_flits",
                  "accuracy", "calibration"]
        writer = csv.DictWriter(stream, fields); writer.writeheader()
        for pair, speedup in zip(ordered, values):
            cir, core = pair["cir"], pair["cores-only"]
            writer.writerow({
                "dataset": cir["dataset"], "family": cir["family"],
                "spins": cir["problem_vertices"],
                "cir_cycles": cir["iteration_cycles"],
                "core_cycles": core["iteration_cycles"],
                "cir_speedup": speedup,
                "cir_link_flits": cir["physical_link_flits"],
                "core_link_flits": core["physical_link_flits"],
                "accuracy": cir["accuracy"], "calibration": cir["calibration"],
            })
    print(f"PASS paired_points={len(complete)} output={args.output_dir}")


if __name__ == "__main__":
    main()
