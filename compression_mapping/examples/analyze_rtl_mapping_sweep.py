#!/usr/bin/env python3
"""Aggregate and plot a staged hybrid-RTL NoC sweep."""

from __future__ import annotations

import argparse
import csv
from math import log10
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


STAGES = ("partition", "permutation", "lossy", "scheduled")
COLORS = dict(zip(STAGES, ("#377eb8", "#4daf4a", "#984ea3", "#e41a1c")))


def summarize(prefix: Path) -> dict:
    with prefix.with_suffix(".csv").open() as source:
        aggregate = list(csv.DictReader(source))
    links = [row for row in aggregate if row["scope"] == "link"]
    inject = [row for row in aggregate if row["scope"] == "inject"]
    link_accepted = sum(int(row["accepted_flits"]) for row in links)
    link_offered = sum(int(row["offered_cycles"]) for row in links)
    link_stalls = sum(int(row["stall_cycles"]) for row in links)
    utilizations = [float(row["window_utilization"]) for row in links]
    completion_cycle = peak_inflight = 0
    with prefix.with_name(prefix.name + "_timeline.csv").open() as source:
        for row in csv.DictReader(source):
            completion_cycle = max(completion_cycle, int(row["interval_end"]))
            peak_inflight = max(peak_inflight, int(row["peak_inflight"]))
    return {
        "completion_cycle": completion_cycle,
        "injected_flits": sum(int(row["accepted_flits"]) for row in inject),
        "physical_link_flits": link_accepted,
        "mean_link_utilization": float(np.mean(utilizations)) if utilizations else 0,
        "max_link_utilization": max(utilizations, default=0),
        "weighted_link_backpressure": link_stalls / link_offered if link_offered else 0,
        "link_stall_cycles": link_stalls,
        "peak_inflight_flits": peak_inflight,
        "mean_hops_per_injected_flit": (
            link_accepted / sum(int(row["accepted_flits"]) for row in inject)
            if inject else 0
        ),
    }


def plot(rows: list[dict], output_dir: Path) -> None:
    sparsities = sorted({row["sparsity"] for row in rows})
    positive = [1 - value for value in sparsities if value < 1]
    floor = min(positive) / 2
    xmap = {value: -log10(max(1 - value, floor)) for value in sparsities}
    specifications = [
        ("traffic_patterns.png", [
            ("injected_flits", "Injected flits"),
            ("physical_link_flits", "Physical-link flit traversals"),
            ("mean_hops_per_injected_flit", "Mean link hops / injected flit"),
            ("peak_inflight_flits", "Peak in-flight flits"),
        ]),
        ("utilization_and_stalls.png", [
            ("mean_link_utilization", "Mean directed-link utilization"),
            ("max_link_utilization", "Maximum directed-link utilization"),
            ("weighted_link_backpressure", "Weighted link backpressure"),
            ("link_stall_cycles", "Total link stall cycles"),
        ]),
    ]
    for filename, panels in specifications:
        fig, axes = plt.subplots(2, 2, figsize=(13, 8))
        for axis, (metric, title) in zip(axes.flat, panels):
            for stage in STAGES:
                selected = sorted(
                    (row for row in rows if row["stage"] == stage),
                    key=lambda row: row["sparsity"],
                )
                axis.plot(
                    [xmap[row["sparsity"]] for row in selected],
                    [row[metric] for row in selected], marker="o",
                    label=stage, color=COLORS[stage],
                )
            axis.set_title(title)
            axis.set_xticks([xmap[value] for value in sparsities])
            axis.set_xticklabels(
                [f"{100*value:.4g}" for value in sparsities],
                rotation=45, ha="right",
            )
            axis.grid(alpha=0.25)
        axes[1, 0].set_xlabel("Graph sparsity (%) — logarithmic density spacing")
        axes[1, 1].set_xlabel("Graph sparsity (%) — logarithmic density spacing")
        axes[0, 1].legend()
        fig.suptitle("4,096-spin real-router RTL replay")
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=180)
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with args.manifest.open() as source:
        manifest = list(csv.DictReader(source))
    rows = []
    for entry in manifest:
        row = {
            "sparsity": float(entry["sparsity"]),
            "stage": entry["stage"],
            "blocks": int(entry["blocks"]),
            "schedule_source": entry["source"],
            "rtl_wall_seconds": float(entry["rtl_wall_seconds"]),
        }
        row.update(summarize(Path(entry["stats_prefix"])))
        rows.append(row)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "rtl_noc_summary.csv"
    with output.open("w", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    plot(rows, args.output_dir)
    print(f"wrote {output} and plots to {args.output_dir}")


if __name__ == "__main__":
    main()
