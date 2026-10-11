#!/usr/bin/env python3
"""Generate paper-oriented traffic, congestion, memory, and fidelity figures."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


FAMILY = {
    "generated-random-average-degree-25": "Random, degree 25",
    "generated-structured-toroidal": "Toroidal, degree 4",
    "published-gset": "Gset",
    "published-scale-matched-random-proxy": "A1–A3 proxies",
}
COLORS = {
    "generated-random-average-degree-25": "#d55e00",
    "generated-structured-toroidal": "#009e73",
    "published-gset": "#0072b2",
    "published-scale-matched-random-proxy": "#cc79a7",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as source:
        return list(csv.DictReader(source))


def number(row: dict[str, str], key: str) -> float:
    return float(row[key])


def save(fig, output: Path, stem: str, dpi: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(output / f"{stem}.{suffix}", dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def short_dataset(value: str) -> str:
    return (value.replace("random_d25_n", "random-")
                 .replace("toroidal_d4_n", "torus-")
                 .replace("qsb_", "")
                 .replace("_proxy", "")
                 .replace("gset_", "")
                 .replace("_", "-"))


def plot_traffic_regimes(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 5.7))
    for family in FAMILY:
        group = [r for r in data if r["family"] == family]
        if not group:
            continue
        sizes = 30 + 90 * np.sqrt(np.array([number(r, "problem_vertices") for r in group]) /
                                  max(number(r, "problem_vertices") for r in data))
        ax.scatter([number(r, "cross_job_fraction") for r in group],
                   [number(r, "maximum_link_utilization") for r in group],
                   s=sizes, alpha=.82, color=COLORS[family], edgecolor="white",
                   linewidth=.5, label=FAMILY[family])
    for name in ("gset_g70", "gset_g81", "random_d25_n131072", "toroidal_d4_n1048576"):
        r = next((x for x in data if x["dataset"] == name), None)
        if r:
            ax.annotate(short_dataset(name),
                        (number(r, "cross_job_fraction"), number(r, "maximum_link_utilization")),
                        xytext=(5, 5), textcoords="offset points", fontsize=8)
    ax.set(xlabel="Cross-H1 block-job fraction", ylabel="Peak directed-link utilization",
           title="Workload traffic regimes")
    ax.grid(alpha=.25)
    ax.legend(fontsize=8)
    save(fig, output, "traffic_regimes", dpi)


def plot_homogeneous_scaling(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5))
    metrics = [
        ("iteration_cycles", "Cycles per iteration", True),
        ("physical_link_flits", "Physical link flits", True),
        ("maximum_link_utilization", "Peak link utilization", False),
        ("maximum_link_backpressure", "Peak link backpressure", False),
    ]
    families = ("generated-random-average-degree-25", "generated-structured-toroidal")
    for family in families:
        group = sorted((r for r in data if r["family"] == family),
                       key=lambda r: number(r, "problem_vertices"))
        x = [number(r, "problem_vertices") for r in group]
        for ax, (key, _, _) in zip(axes.flat, metrics):
            ax.plot(x, [number(r, key) for r in group], marker="o",
                    color=COLORS[family], label=FAMILY[family])
    for ax, (_, label, log_y) in zip(axes.flat, metrics):
        ax.set_xscale("log", base=2)
        if log_y:
            ax.set_yscale("log")
        ax.set(xlabel="Problem size (spins)", ylabel=label)
        ax.grid(alpha=.25, which="both")
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("Traffic and congestion scaling across locality regimes")
    save(fig, output, "traffic_scaling", dpi)


def plot_balance(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 5.7))
    for family in FAMILY:
        group = [r for r in data if r["family"] == family]
        if group:
            ax.scatter([number(r, "link_flit_gini") for r in group],
                       [number(r, "node_job_cv") for r in group],
                       c=[number(r, "maximum_link_utilization") for r in group],
                       cmap="viridis", vmin=0, vmax=.4, marker="o", s=55,
                       edgecolor=COLORS[family], linewidth=1.5, label=FAMILY[family])
    scalar = plt.cm.ScalarMappable(cmap="viridis", norm=plt.Normalize(0, .4))
    fig.colorbar(scalar, ax=ax, label="Peak link utilization")
    ax.set(xlabel="Directed-link traffic Gini (spatial imbalance)",
           ylabel="H1 node job-count CV", title="Traffic hotspots versus compute-load imbalance")
    ax.grid(alpha=.25)
    ax.legend(fontsize=8)
    save(fig, output, "traffic_load_balance", dpi)


def plot_packet_mix(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    selected_names = ["gset_g32", "gset_g70", "gset_g81", "qsb_a1_proxy",
                      "random_d25_n16384", "toroidal_d4_n16384", "toroidal_d4_n1048576"]
    selected = [next(r for r in data if r["dataset"] == name) for name in selected_names]
    x = np.arange(len(selected))
    state = np.array([number(r, "state_flit_fraction") for r in selected])
    partial = np.array([number(r, "partial_flit_fraction") for r in selected])
    other = np.maximum(0, 1 - state - partial)
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    ax.bar(x, state, label="State", color="#56b4e9")
    ax.bar(x, partial, bottom=state, label="Partial vectors", color="#e69f00")
    ax.bar(x, other, bottom=state + partial, label="Epoch/other", color="#999999")
    ax.set_xticks(x, [short_dataset(r["dataset"]) for r in selected], rotation=25, ha="right")
    ax.set(ylabel="Fraction of injected flits", ylim=(0, 1),
           title="NoC packet composition for representative workloads")
    ax.grid(axis="y", alpha=.25)
    ax.legend(ncol=3)
    save(fig, output, "packet_composition", dpi)


def plot_calibration(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6))
    exact = np.array([number(r, "exact_cycles") for r in data])
    calibrated = np.array([number(r, "calibrated_cycles") for r in data])
    limit = max(exact.max(), calibrated.max())
    axes[0].scatter(exact, calibrated, color="#0072b2")
    axes[0].plot([1, limit], [1, limit], "--", color="black", linewidth=1, label="ideal")
    axes[0].set(xscale="log", yscale="log", xlabel="Exact/Ramulator cycles",
                ylabel="Calibrated-model cycles", title="Timing calibration")
    axes[0].legend()
    order = np.argsort([number(r, "absolute_percentage_error") for r in data])
    axes[1].barh(np.arange(len(data)),
                 [100 * number(data[i], "absolute_percentage_error") for i in order],
                 color="#d55e00")
    axes[1].set_yticks(np.arange(len(data)), [short_dataset(data[i]["dataset"]) for i in order], fontsize=8)
    axes[1].set(xlabel="Absolute timing error (%)", title="Per-workload calibration error")
    for ax in axes:
        ax.grid(alpha=.25)
    save(fig, output, "exact_vs_calibrated", dpi)


def plot_exact_mapping(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    if not data:
        return
    baseline = next(r for r in data if r["mapping"] == "random")
    order = ["random", "identity_optimized_owner", "provided_mapper"]
    labels = ["Random", "Identity + optimized owner", "Bundled mapper"]
    metrics = [("iteration_cycles", "Iteration cycles"),
               ("physical_link_flits", "Physical link flits"),
               ("injection_stalls", "Injection stalls"),
               ("link_stalls", "Link stalls")]
    fig, ax = plt.subplots(figsize=(9.2, 5.2))
    width = .2
    x = np.arange(len(metrics))
    for i, (mapping, label) in enumerate(zip(order, labels)):
        r = next(row for row in data if row["mapping"] == mapping)
        values = [number(r, key) / number(baseline, key) for key, _ in metrics]
        ax.bar(x + (i - 1) * width, values, width, label=label)
    ax.axhline(1, color="black", linewidth=.8)
    ax.set_xticks(x, [label for _, label in metrics])
    ax.set(ylabel="Normalized to random placement", title="Exact/Ramulator G70 mapping impact")
    ax.grid(axis="y", alpha=.25)
    ax.legend(fontsize=8)
    save(fig, output, "exact_mapping_impact", dpi)


def plot_dram(data: list[dict[str, str]], output: Path, dpi: int) -> None:
    all_rows = [r for r in data if r["level"] == "all"]
    x = np.arange(len(all_rows))
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 7.2), sharex=True)
    axes[0].bar(x, [number(r, "request_weighted_average_latency_cycles") for r in all_rows],
                color="#0072b2", label="weighted average")
    axes[0].scatter(x, [number(r, "maximum_latency_cycles") for r in all_rows],
                    color="#d55e00", marker="D", label="maximum", zorder=3)
    axes[0].set(ylabel="DRAM latency (cycles)", title="Exact/Ramulator memory behavior")
    axes[0].legend()
    axes[1].bar(x, [number(r, "request_load_gini") for r in all_rows], color="#009e73")
    axes[1].set(ylabel="Request-load Gini", ylim=(0, 1))
    axes[1].set_xticks(x, [short_dataset(r["dataset"]) for r in all_rows], rotation=25, ha="right")
    for ax in axes:
        ax.grid(axis="y", alpha=.25)
    save(fig, output, "dram_latency_and_balance", dpi)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("results/paper_benchmarks"))
    parser.add_argument("--output-dir", type=Path, default=Path("plots/paper_metrics"))
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    traffic = rows(args.root / "traffic_patterns.csv")
    plot_traffic_regimes(traffic, args.output_dir, args.dpi)
    plot_homogeneous_scaling(traffic, args.output_dir, args.dpi)
    plot_balance(traffic, args.output_dir, args.dpi)
    plot_packet_mix(traffic, args.output_dir, args.dpi)
    plot_calibration(rows(args.root / "exact_calibration.csv"), args.output_dir, args.dpi)
    plot_exact_mapping(rows(args.root / "exact_mapping_confirmation.csv"), args.output_dir, args.dpi)
    plot_dram(rows(args.root / "dram_patterns.csv"), args.output_dir, args.dpi)
    print(f"figures={args.output_dir} count=7 (PNG and PDF)")


if __name__ == "__main__":
    main()
