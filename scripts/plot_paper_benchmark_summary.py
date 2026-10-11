#!/usr/bin/env python3
"""Create scaling, mapping, and interconnect summary figures for the paper."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib.pyplot as plt
except ImportError as exc:
    raise SystemExit("matplotlib is required; install requirements.txt") from exc


FAMILY_LABELS = {
    "published-gset": "Gset",
    "published-scale-matched-random-proxy": "A1–A3 scale-matched proxies",
    "generated-structured-toroidal": "degree-4 toroidal",
    "generated-random-average-degree-25": "random average degree 25",
}


def save(figure, output_dir: Path, stem: str, formats: list[str], dpi: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    for suffix in formats:
        figure.savefig(
            output_dir / f"{stem}.{suffix}", dpi=dpi, bbox_inches="tight"
        )
    plt.close(figure)


def event_points(root: Path) -> list[dict]:
    points = {}
    for path in root.glob("event_sweep*/*/identity/point.json"):
        point = json.loads(path.read_text())
        points[point["dataset"]] = point
    return list(points.values())


def exact_cycles(root: Path) -> dict[str, int]:
    values = {}
    for path in (root / "exact_confirmation").glob("*/*_summary.json"):
        values[path.parent.name] = json.loads(path.read_text())[
            "timing_cycles"
        ]["iteration"]
    return values


def plot_scaling(root: Path, output: Path, formats: list[str], dpi: int) -> None:
    points = event_points(root)
    exact = exact_cycles(root)
    grouped = defaultdict(list)
    for point in points:
        grouped[point["family"]].append(point)
    figure, axes = plt.subplots(2, 2, figsize=(11.5, 8.5))
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    exact_label_pending = True
    for color, (family, rows) in zip(colors, sorted(grouped.items())):
        rows.sort(key=lambda row: row["problem_vertices"])
        label = FAMILY_LABELS.get(family, family)
        x = [row["problem_vertices"] for row in rows]
        marker = "o" if len(rows) > 1 else "s"
        plot_method = axes[0, 0].scatter if family == "published-gset" else axes[0, 0].plot
        plot_method(
            x, [row["iteration_cycles"] for row in rows], marker=marker,
            color=color, linewidth=1.2, label=label,
        )
        exact_rows = [row for row in rows if row["dataset"] in exact]
        if exact_rows:
            axes[0, 0].scatter(
                [row["problem_vertices"] for row in exact_rows],
                [exact[row["dataset"]] for row in exact_rows],
                marker="*", s=100, color=color, edgecolor="black",
                linewidth=0.5,
                label="Ramulator exact point" if exact_label_pending else None,
                zorder=5,
            )
            exact_label_pending = False
        secondary_method = (
            axes[0, 1].scatter if family == "published-gset"
            else axes[0, 1].plot
        )
        secondary_method(
            x, [row["active_block_pairs"] for row in rows], marker=marker,
            color=color, linewidth=1.2, label=label,
        )
        cross_method = (
            axes[1, 0].scatter if family == "published-gset"
            else axes[1, 0].plot
        )
        cross_method(
            x, [row["cross_job_fraction"] for row in rows], marker=marker,
            color=color, linewidth=1.2, label=label,
        )
        traffic_method = (
            axes[1, 1].scatter if family == "published-gset"
            else axes[1, 1].plot
        )
        traffic_method(
            x, [row["physical_link_flits"] for row in rows], marker=marker,
            color=color, linewidth=1.2, label=label,
        )
    titles = (
        "Cycles per Ising iteration",
        "Occupied off-diagonal 32×32 blocks",
        "Fraction of block jobs crossing H1 packages",
        "Physical directed-link flits per iteration",
    )
    ylabels = ("cycles", "active block pairs", "cross-job fraction", "flits")
    for axis, title, ylabel in zip(axes.flat, titles, ylabels):
        axis.set_title(title)
        axis.set_xlabel("problem vertices")
        axis.set_ylabel(ylabel)
        axis.set_xscale("log", base=2)
        axis.grid(alpha=0.25, which="both")
    for axis in (axes[0, 0], axes[0, 1], axes[1, 1]):
        axis.set_yscale("log")
    axes[0, 0].legend(fontsize=8, ncol=2)
    axes[0, 1].legend(fontsize=8)
    save(figure, output, "scaling_summary", formats, dpi)


def plot_mapping(root: Path, output: Path, formats: list[str], dpi: int) -> None:
    path = root / "mapping_comparison" / "results.json"
    if not path.is_file():
        return
    rows = json.loads(path.read_text())
    provided = [row for row in rows if row["mapping"] == "provided_mapper"]
    if not provided:
        return
    provided.sort(key=lambda row: (row["capacity_spins"], row["dataset"]))
    labels = [row["dataset"].replace("_", "\n") for row in provided]
    x = list(range(len(provided)))
    width = 0.38
    figure, axis = plt.subplots(figsize=(max(8, 1.15 * len(provided)), 4.8))
    axis.bar(
        [value - width / 2 for value in x],
        [row["speedup_vs_random"] for row in provided], width,
        label="versus random placement",
    )
    axis.bar(
        [value + width / 2 for value in x],
        [row["speedup_vs_identity"] for row in provided], width,
        label="versus identity placement",
    )
    axis.axhline(1.0, color="black", linewidth=0.8)
    axis.set_xticks(x, labels, fontsize=8)
    axis.set_ylabel("speedup (higher is better)")
    axis.set_title(
        "Bundled mapper performance with common optimized owner policy "
        "(calibrated sweep)"
    )
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    save(figure, output, "mapping_speedup", formats, dpi)


def plot_interconnect(
    root: Path, output: Path, formats: list[str], dpi: int
) -> None:
    exact_path = root / "exact_interconnect_sensitivity.json"
    path = (
        exact_path if exact_path.is_file()
        else root / "interconnect_sensitivity" / "results.json"
    )
    if not path.is_file():
        return
    rows = json.loads(path.read_text())
    datasets = sorted({row["dataset"] for row in rows})
    variants = sorted(
        {row["variant"] for row in rows},
        key=lambda value: (
            {
                "rtl_direct": 0,
                "link_latency_16": 1,
                "serialization_4": 2,
                "credit_1_return_8": 3,
                "combined_stress": 4,
            }.get(value, 99),
            value,
        ),
    )
    figure, axis = plt.subplots(figsize=(11.5, 5.5))
    group_width = 0.8
    bar_width = group_width / len(datasets)
    for dataset_index, dataset in enumerate(datasets):
        by_variant = {
            row["variant"]: row for row in rows if row["dataset"] == dataset
        }
        positions = [
            index - group_width / 2 + bar_width / 2
            + dataset_index * bar_width
            for index in range(len(variants))
        ]
        axis.bar(
            positions,
            [by_variant[variant]["slowdown_vs_rtl_direct"] for variant in variants],
            width=bar_width, label=dataset,
        )
    axis.axhline(1.0, color="black", linewidth=0.8)
    axis.set_xticks(
        range(len(variants)), [value.replace("_", "\n") for value in variants],
        fontsize=8,
    )
    axis.set_ylabel("iteration slowdown versus direct RTL link")
    axis.set_title(
        "Exact/Ramulator package and inter-package link sensitivity"
        if path == exact_path else
        "Calibrated package and inter-package link sensitivity"
    )
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    save(figure, output, "interconnect_sensitivity", formats, dpi)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("results/paper_benchmarks")
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--formats", default="png,pdf")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    output = args.output_dir or args.root / "figures"
    formats = [value.strip() for value in args.formats.split(",") if value.strip()]
    plot_scaling(args.root, output, formats, args.dpi)
    plot_mapping(args.root, output, formats, args.dpi)
    plot_interconnect(args.root, output, formats, args.dpi)
    print(f"figures={output}")


if __name__ == "__main__":
    main()
