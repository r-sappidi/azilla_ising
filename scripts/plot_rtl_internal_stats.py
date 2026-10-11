#!/usr/bin/env python3
"""Aggregate and plot measured RTL H0/H1/cross hierarchy statistics."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


TRANSFER_ORDER = ("command", "weight", "state_load", "partial")
CLASS_ORDER = ("h0", "h1", "cross")
COLORS = {
    "command": "#4C78A8",
    "weight": "#F58518",
    "state_load": "#54A24B",
    "partial": "#E45756",
}


def read_run(path: Path, label: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "run": label,
                    "source": str(path),
                    "node": int(raw["node"]),
                    "x": int(raw["x"]),
                    "y": int(raw["y"]),
                    "endpoint_class": raw["endpoint_class"],
                    "endpoint_id": int(raw["endpoint_id"]),
                    "transfer": raw["transfer"],
                    "offered": int(raw["offered_lane_cycles"]),
                    "accepted": int(raw["accepted_transfers"]),
                    "stalled": int(raw["stalled_lane_cycles"]),
                    "first": int(raw["first_accept_cycle"]),
                    "last": int(raw["last_accept_cycle"]),
                    "utilization": float(raw["capacity_utilization"]),
                    "backpressure": float(raw["backpressure_fraction"]),
                }
            )
    return rows


def save(fig: plt.Figure, output: Path, stem: str, dpi: int) -> None:
    fig.tight_layout()
    fig.savefig(output / f"{stem}.png", dpi=dpi, bbox_inches="tight")
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_volume(rows: list[dict[str, object]], output: Path, dpi: int) -> None:
    runs = list(dict.fromkeys(str(row["run"]) for row in rows))
    fig, axes = plt.subplots(1, 2, figsize=(max(9, 2.6 * len(runs)), 4.4))
    for axis, group_key, title in (
        (axes[0], "endpoint_class", "Traffic by hierarchy level"),
        (axes[1], "transfer", "Traffic composition"),
    ):
        groups = CLASS_ORDER if group_key == "endpoint_class" else TRANSFER_ORDER
        bottom = np.zeros(len(runs))
        for group in groups:
            values = np.array(
                [
                    sum(
                        int(row["accepted"])
                        for row in rows
                        if row["run"] == run and row[group_key] == group
                    )
                    for run in runs
                ],
                dtype=float,
            )
            color = COLORS.get(group)
            axis.bar(runs, values, bottom=bottom, label=group, color=color)
            bottom += values
        axis.set_title(title)
        axis.set_ylabel("Accepted lane-transfers")
        axis.tick_params(axis="x", rotation=20)
        axis.legend(frameon=False)
    save(fig, output, "internal_traffic_volume", dpi)


def plot_pressure(rows: list[dict[str, object]], output: Path, dpi: int) -> None:
    active = [row for row in rows if int(row["offered"]) > 0]
    labels = [
        f"{row['run']} n{row['node']} {row['endpoint_class']}{row['endpoint_id']} "
        f"{row['transfer']}"
        for row in active
    ]
    order = sorted(
        range(len(active)),
        key=lambda i: max(float(active[i]["utilization"]), float(active[i]["backpressure"])),
        reverse=True,
    )[: min(30, len(active))]
    selected = [active[i] for i in reversed(order)]
    selected_labels = [labels[i] for i in reversed(order)]
    y = np.arange(len(selected))
    fig, axes = plt.subplots(1, 2, figsize=(12, max(4, 0.3 * len(selected))))
    axes[0].barh(y, [100 * float(row["utilization"]) for row in selected],
                 color="#4C78A8")
    axes[1].barh(y, [100 * float(row["backpressure"]) for row in selected],
                 color="#E45756")
    for axis, title in zip(axes, ("Capacity utilization", "Backpressure")):
        axis.set_yticks(y, selected_labels)
        axis.set_xlabel("Percent")
        axis.set_title(title)
        axis.grid(axis="x", alpha=0.25)
    axes[1].tick_params(labelleft=False)
    save(fig, output, "internal_utilization_backpressure", dpi)


def plot_spans(rows: list[dict[str, object]], output: Path, dpi: int) -> None:
    active = [row for row in rows if int(row["accepted"]) > 0]
    active.sort(key=lambda row: (str(row["run"]), int(row["first"])))
    labels = [
        f"{row['run']} n{row['node']} {row['endpoint_class']}{row['endpoint_id']} "
        f"{row['transfer']}"
        for row in active
    ]
    y = np.arange(len(active))
    starts = np.array([int(row["first"]) for row in active])
    widths = np.array([int(row["last"]) - int(row["first"]) + 1 for row in active])
    fig, axis = plt.subplots(figsize=(11, max(4, 0.27 * len(active))))
    axis.barh(y, widths, left=starts,
              color=[COLORS[str(row["transfer"])] for row in active])
    axis.set_yticks(y, labels)
    axis.invert_yaxis()
    axis.set_xlabel("RTL cycle")
    axis.set_title("First-to-last accepted-transfer activity spans")
    axis.grid(axis="x", alpha=0.25)
    save(fig, output, "internal_activity_spans", dpi)


def plot_heatmaps(rows: list[dict[str, object]], output: Path, dpi: int) -> None:
    runs = list(dict.fromkeys(str(row["run"]) for row in rows))
    for run in runs:
        run_rows = [row for row in rows if row["run"] == run]
        endpoints = sorted(
            {(int(row["node"]), str(row["endpoint_class"]), int(row["endpoint_id"]))
             for row in run_rows}
        )
        matrix = np.zeros((len(endpoints), len(TRANSFER_ORDER)))
        for ei, endpoint in enumerate(endpoints):
            for ti, transfer in enumerate(TRANSFER_ORDER):
                matrix[ei, ti] = sum(
                    int(row["accepted"])
                    for row in run_rows
                    if (int(row["node"]), row["endpoint_class"],
                        int(row["endpoint_id"])) == endpoint
                    and row["transfer"] == transfer
                )
        display = np.log10(matrix + 1.0)
        fig, axis = plt.subplots(figsize=(7.5, max(3.5, 0.42 * len(endpoints))))
        image = axis.imshow(display, aspect="auto", cmap="magma")
        axis.set_xticks(range(len(TRANSFER_ORDER)), TRANSFER_ORDER, rotation=25,
                        ha="right")
        axis.set_yticks(
            range(len(endpoints)),
            [f"node {node}: {cls}{eid}" for node, cls, eid in endpoints],
        )
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                axis.text(j, i, f"{int(matrix[i, j]):,}", ha="center", va="center",
                          color="white" if display[i, j] < display.max() * 0.65 else "black",
                          fontsize=8)
        axis.set_title(f"Measured internal endpoint traffic — {run}")
        fig.colorbar(image, ax=axis, label="log10(accepted transfers + 1)")
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run)
        save(fig, output, f"internal_endpoint_heatmap_{safe}", dpi)


def write_aggregates(rows: list[dict[str, object]], output: Path) -> None:
    fields = [
        "run", "source", "node", "x", "y", "endpoint_class", "endpoint_id",
        "transfer", "offered", "accepted", "stalled", "first", "last",
        "activity_span", "utilization", "backpressure",
    ]
    with (output / "internal_metrics_long.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "activity_span":
                int(row["last"]) - int(row["first"]) + 1 if int(row["accepted"]) else 0})

    ranked = sorted(
        (row for row in rows if int(row["offered"]) > 0),
        key=lambda row: (
            float(row["backpressure"]), float(row["utilization"]),
            int(row["stalled"]), int(row["accepted"]),
        ),
        reverse=True,
    )
    with (output / "critical_endpoints.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in ranked:
            writer.writerow({**row, "activity_span":
                int(row["last"]) - int(row["first"]) + 1 if int(row["accepted"]) else 0})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stats", nargs="+", type=Path)
    parser.add_argument("--labels", nargs="*")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    if args.labels and len(args.labels) != len(args.stats):
        parser.error("--labels must contain exactly one label per stats CSV")
    labels = args.labels or [path.parent.name or path.stem for path in args.stats]
    rows = [row for path, label in zip(args.stats, labels) for row in read_run(path, label)]
    if not rows:
        raise SystemExit("no metric rows found")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregates(rows, args.output_dir)
    plot_volume(rows, args.output_dir, args.dpi)
    plot_pressure(rows, args.output_dir, args.dpi)
    plot_spans(rows, args.output_dir, args.dpi)
    plot_heatmaps(rows, args.output_dir, args.dpi)
    print(f"wrote internal RTL metric tables and plots to {args.output_dir}")


if __name__ == "__main__":
    main()
