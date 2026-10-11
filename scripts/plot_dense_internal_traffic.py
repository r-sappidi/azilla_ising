#!/usr/bin/env python3
"""Derive and plot internal hierarchy traffic from completed dense RTL runs."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CONFIG = re.compile(
    r"(?P<mx>\d+)x(?P<my>\d+)_h0(?P<h0>\d+)_c(?P<cores>\d+)_e"
)
JOBS = re.compile(
    r"compiled concurrent schedule h0=(\d+) h1=(\d+) cross=(\d+) total=(\d+)"
)
FLIT_BYTES = 32
WEIGHT_FLITS_PER_JOB = 32
PARTIAL_FLITS_PER_JOB = 8


def completed_points(root: Path, series: str) -> list[dict[str, object]]:
    summary = root / "summary.csv"
    if not summary.is_file():
        return []
    points = []
    with summary.open(newline="") as source:
        for row in csv.DictReader(source):
            if row.get("simulation_exit") != "0":
                continue
            spins = int(row["spins"])
            logs = sorted(root.glob(f"sim_{spins}_*.log"))
            if not logs:
                continue
            text = logs[-1].read_text(errors="replace")
            if "PASS: 1 iteration" not in text:
                continue
            jobs = JOBS.search(text)
            config = CONFIG.search(row["config"])
            if not jobs or not config:
                continue
            h0_jobs, h1_jobs, cross_jobs, total_jobs = map(int, jobs.groups())
            mesh_x, mesh_y, h0_per_h1, cores_per_h0 = (
                int(config.group(name)) for name in ("mx", "my", "h0", "cores")
            )
            point: dict[str, object] = {
                "series": series,
                "spins": spins,
                "config": row["config"],
                "mesh_x": mesh_x,
                "mesh_y": mesh_y,
                "h1_nodes": mesh_x * mesh_y,
                "h0_per_h1": h0_per_h1,
                "cores_per_h0": cores_per_h0,
                "h0_jobs": h0_jobs,
                "h1_jobs": h1_jobs,
                "cross_jobs": cross_jobs,
                "total_jobs": total_jobs,
                "state_table_loads_h0": spins // 32,
                "state_table_loads_h1": spins // 32,
            }
            for level, count in (
                ("h0", h0_jobs), ("h1", h1_jobs), ("cross", cross_jobs)
            ):
                point[f"{level}_weight_flits"] = count * WEIGHT_FLITS_PER_JOB
                point[f"{level}_partial_flits"] = count * PARTIAL_FLITS_PER_JOB
                point[f"{level}_weight_bytes"] = (
                    count * WEIGHT_FLITS_PER_JOB * FLIT_BYTES
                )
                point[f"{level}_partial_bytes"] = (
                    count * PARTIAL_FLITS_PER_JOB * FLIT_BYTES
                )
            points.append(point)
    return points


def write_csv(points: list[dict[str, object]], path: Path) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(points[0]))
        writer.writeheader()
        writer.writerows(points)


def save(fig: plt.Figure, output: Path, stem: str) -> None:
    fig.tight_layout()
    fig.savefig(output / f"{stem}.png", dpi=220, bbox_inches="tight")
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def stack_plot(points: list[dict[str, object]], output: Path) -> None:
    legacy = [row for row in points if row["series"] == "4x4_capacity_scaling"]
    x = np.arange(len(legacy))
    labels = [f"{int(row['spins']) // 1024}K" for row in legacy]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    colors = ("#0072B2", "#009E73", "#D55E00")
    for axis, suffix, title in (
        (axes[0], "partial_bytes", "Internal partial-vector traffic"),
        (axes[1], "weight_bytes", "Internal weight-stream traffic"),
    ):
        bottom = np.zeros(len(legacy))
        for level, label, color in zip(
            ("h0", "h1", "cross"),
            ("H0-local", "H1-local", "Cross-H1"),
            colors,
        ):
            values = np.array(
                [float(row[f"{level}_{suffix}"]) / 2**30 for row in legacy]
            )
            axis.bar(x, values, bottom=bottom, label=label, color=color)
            bottom += values
        axis.set_xticks(x, labels)
        axis.set_yscale("log")
        axis.set_xlabel("Dense problem size")
        axis.set_ylabel("GiB per iteration")
        axis.set_title(title)
        axis.grid(axis="y", which="both", alpha=.25)
    axes[0].legend(frameon=False)
    fig.suptitle("Analytically derived dense traffic volume by hierarchy")
    save(fig, output, "07_dense_internal_traffic_by_hierarchy")


def fixed_comparison(points: list[dict[str, object]], output: Path) -> None:
    fixed = [row for row in points if row["series"] == "fixed_h1_scaling"]
    x = np.arange(len(fixed))
    width = .24
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for axis, suffix, title in (
        (axes[0], "partial_bytes", "Partial-vector volume"),
        (axes[1], "weight_bytes", "Weight-stream volume"),
    ):
        for offset, level, label in (
            (-width, "h0", "H0-local"),
            (0, "h1", "H1-local"),
            (width, "cross", "Cross-H1"),
        ):
            values = [float(row[f"{level}_{suffix}"]) / 2**30 for row in fixed]
            axis.bar(x + offset, values, width, label=label)
        axis.set_xticks(x, [f"{int(row['spins']) // 1024}K" for row in fixed])
        axis.set_yscale("log")
        axis.set(xlabel="Dense problem size", ylabel="GiB per iteration", title=title)
        axis.grid(axis="y", which="both", alpha=.25)
    axes[0].legend(frameon=False)
    fig.suptitle("Fixed-H1 dense traffic: 8 H0/H1, 256 cores/H0")
    save(fig, output, "08_fixed_h1_internal_traffic")


def endpoint_heatmap(points: list[dict[str, object]], output: Path) -> None:
    row = next(
        point for point in points
        if point["series"] == "fixed_h1_scaling" and point["spins"] == 131072
    )
    h0_count = int(row["h0_per_h1"])
    h1_nodes = int(row["h1_nodes"])
    # Dense geometry is symmetric across H1s. The two-node allocator owns the
    # sole cross-H1 pair at node 0, matching the RTL allocator's tie break.
    matrix = np.zeros((6, h1_nodes), dtype=float)
    matrix[0, :] = float(row["h0_weight_bytes"]) / (h1_nodes * h0_count)
    matrix[1, :] = float(row["h0_partial_bytes"]) / (h1_nodes * h0_count)
    matrix[2, :] = float(row["h1_weight_bytes"]) / h1_nodes
    matrix[3, :] = float(row["h1_partial_bytes"]) / h1_nodes
    matrix[4, 0] = float(row["cross_weight_bytes"])
    matrix[5, 0] = float(row["cross_partial_bytes"])
    matrix /= 2**30
    display = np.log10(np.maximum(matrix, 1e-6))
    fig, axis = plt.subplots(figsize=(7.8, 5.8))
    image = axis.imshow(display, cmap="magma", aspect="auto")
    axis.set_xticks(range(h1_nodes), [f"H1 {index}" for index in range(h1_nodes)])
    axis.set_yticks(range(6), [
        "avg H0 weight in", "avg H0 partial out",
        "H1-local weight in", "H1-local partial out",
        "cross owner weight in", "cross owner partial out",
    ])
    for y in range(matrix.shape[0]):
        for x in range(matrix.shape[1]):
            label = "0" if matrix[y, x] == 0 else f"{matrix[y, x]:.2f}"
            axis.text(x, y, label, ha="center", va="center", color="white")
    bar = fig.colorbar(image, ax=axis)
    bar.set_label("log10(GiB per iteration); cells show GiB")
    axis.set_title("128K fixed-H1 internal endpoint traffic (derived)")
    save(fig, output, "09_fixed_h1_128k_endpoint_heatmap")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("plots/dense_results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    points = []
    points += completed_points(
        ROOT / "results/dense_config_search/rtl_vcs", "4x4_capacity_scaling"
    )
    points += completed_points(
        ROOT / "results/dense_config_search/rtl_vcs_64k_128k",
        "4x4_capacity_scaling",
    )
    points += completed_points(
        ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m",
        "fixed_h1_scaling",
    )
    points.sort(key=lambda row: (str(row["series"]), int(row["spins"])))
    if not points:
        raise SystemExit("no completed dense RTL points")
    write_csv(points, args.output_dir / "dense_internal_traffic_derived.csv")
    stack_plot(points, args.output_dir)
    fixed_comparison(points, args.output_dir)
    endpoint_heatmap(points, args.output_dir)
    print(f"points={len(points)} output={args.output_dir}")


if __name__ == "__main__":
    main()
