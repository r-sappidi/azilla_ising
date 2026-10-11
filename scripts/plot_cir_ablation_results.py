#!/usr/bin/env python3
"""Plot CIR versus cores-only latency, work, memory, and NoC traffic."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as source:
        return [row for row in csv.DictReader(source) if row.get("exit_code") == "0"]


def number(row: dict[str, str], key: str) -> float:
    return float(row.get(key) or 0)


def key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[name] for name in (
        "spins", "mesh_x", "mesh_y", "h0_per_h1", "cores_per_h0",
        "h0_mvms", "h1_mvms", "cross_mvms", "workload",
    ))


def save(fig, output: Path, name: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output / f"{name}.png", dpi=220, bbox_inches="tight")
    fig.savefig(output / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary", type=Path,
        default=Path("results/cir_evaluation/calibrated_dense/summary.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("plots/cir_evaluation")
    )
    args = parser.parse_args()
    rows = read_rows(args.summary)
    paired: dict[tuple[str, ...], dict[str, dict[str, str]]] = {}
    for row in rows:
        if row["phase"] == "cir-ablation-screen":
            paired.setdefault(key(row), {})[row["execution_mode"]] = row
    pairs = [value for value in paired.values() if set(value) >= {"cir", "cores-only"}]
    pairs.sort(key=lambda value: int(value["cir"]["spins"]))
    if not pairs:
        parser.error("summary contains no completed CIR/cores-only pairs")

    labels = [f"{int(pair['cir']['spins']) // 1024}K" for pair in pairs]
    x = np.arange(len(labels))
    width = 0.36
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for offset, mode, title in ((-width/2, "cir", "CIR"), (width/2, "cores-only", "Cores only")):
        ax.bar(x + offset, [number(p[mode], "iteration_cycles") for p in pairs], width, label=title)
    ax.set_yscale("log"); ax.set_ylabel("Iteration cycles")
    ax.set_xticks(x, labels); ax.set_xlabel("Spin capacity"); ax.legend(frameon=False)
    save(fig, args.output_dir, "cir_vs_cores_only_latency")

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.5))
    metrics = (
        ("ablation_weight_block_reads", "Weight-block reads"),
        ("estimated_dram_requests", "Estimated DRAM requests"),
        ("physical_link_flits", "Top-level link flits"),
    )
    for ax, (metric, title) in zip(axes, metrics):
        for offset, mode, legend in ((-width/2, "cir", "CIR"), (width/2, "cores-only", "Cores only")):
            ax.bar(x + offset, [number(p[mode], metric) for p in pairs], width, label=legend)
        ax.set_title(title); ax.set_yscale("symlog", linthresh=1)
        ax.set_xticks(x, labels); ax.set_xlabel("Spins")
    axes[0].legend(frameon=False)
    save(fig, args.output_dir, "cir_vs_cores_only_work_and_traffic")

    comparison = args.output_dir / "cir_ablation_comparison.csv"
    comparison.parent.mkdir(parents=True, exist_ok=True)
    with comparison.open("w", newline="") as output:
        fields = (
            "spins", "cir_iteration_cycles", "cores_only_iteration_cycles",
            "cir_speedup", "cir_weight_reads", "cores_only_weight_reads",
            "cir_dram_requests", "cores_only_dram_requests",
            "cir_link_flits", "cores_only_link_flits",
        )
        writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
        for pair in pairs:
            cir, baseline = pair["cir"], pair["cores-only"]
            writer.writerow({
                "spins": cir["spins"],
                "cir_iteration_cycles": number(cir, "iteration_cycles"),
                "cores_only_iteration_cycles": number(baseline, "iteration_cycles"),
                "cir_speedup": number(baseline, "iteration_cycles") / number(cir, "iteration_cycles"),
                "cir_weight_reads": number(cir, "ablation_weight_block_reads"),
                "cores_only_weight_reads": number(baseline, "ablation_weight_block_reads"),
                "cir_dram_requests": number(cir, "estimated_dram_requests"),
                "cores_only_dram_requests": number(baseline, "estimated_dram_requests"),
                "cir_link_flits": number(cir, "physical_link_flits"),
                "cores_only_link_flits": number(baseline, "physical_link_flits"),
            })
    print(f"pairs={len(pairs)} output={args.output_dir}")


if __name__ == "__main__":
    main()
