#!/usr/bin/env python3
"""Create temporal and spatial plots from Azilla NoC trace CSV files."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    from matplotlib import colormaps, colors
    from matplotlib.patches import FancyArrowPatch
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required; install it with: python3 -m pip install matplotlib"
    ) from exc


DIRECTION_DELTA = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
}


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def save_figure(fig, output_dir, name, formats, dpi):
    fig.tight_layout()
    for suffix in formats:
        fig.savefig(output_dir / f"{name}.{suffix}", dpi=dpi,
                    bbox_inches="tight")
    plt.close(fig)


def link_label(row):
    return f"n{row['node']}({row['x']},{row['y']}) {row['direction']}"


def plot_link_timeseries(rows, output_dir, formats, dpi, top_links):
    link_rows = [row for row in rows if row["scope"] == "link"]
    grouped = defaultdict(list)
    totals = defaultdict(int)
    for row in link_rows:
        key = (row["node"], row["x"], row["y"], row["direction"])
        grouped[key].append(row)
        totals[key] += int(row["accepted"])
    selected = sorted(grouped, key=totals.get, reverse=True)[:top_links]

    fig, (util_ax, stall_ax) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for key in selected:
        samples = sorted(grouped[key], key=lambda row: int(row["interval_start"]))
        x = [(int(row["interval_start"]) + int(row["interval_end"])) / 2
             for row in samples]
        label = link_label(samples[0])
        util_ax.step(x, [100 * float(row["utilization"]) for row in samples],
                     where="mid", label=label)
        stall_ax.step(x, [100 * float(row["backpressure"]) for row in samples],
                      where="mid", label=label)
    util_ax.set_ylabel("Link utilization (%)")
    stall_ax.set_ylabel("Backpressure (%)")
    stall_ax.set_xlabel("Accelerator cycle")
    util_ax.set_title(f"Top {len(selected)} directed links over time")
    for axis in (util_ax, stall_ax):
        axis.grid(alpha=0.25)
    if selected:
        util_ax.legend(fontsize=8, ncol=2, loc="upper right")
    save_figure(fig, output_dir, "link_timeseries", formats, dpi)


def plot_mesh_hotspots(rows, output_dir, formats, dpi):
    link_rows = [row for row in rows if row["scope"] == "link"]
    totals = defaultdict(lambda: {"accepted": 0, "stalled": 0})
    max_x = max((int(row["x"]) for row in rows), default=0)
    max_y = max((int(row["y"]) for row in rows), default=0)
    for row in link_rows:
        key = (int(row["x"]), int(row["y"]), row["direction"])
        totals[key]["accepted"] += int(row["accepted"])
        totals[key]["stalled"] += int(row["stall_cycles"])

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for axis, metric, title in zip(
            axes, ("accepted", "stalled"),
            ("Accepted flits", "Stalled cycles")):
        values = [entry[metric] for entry in totals.values()]
        vmax = max(values, default=1) or 1
        norm = colors.Normalize(vmin=0, vmax=vmax)
        cmap = colormaps["viridis" if metric == "accepted" else "magma"]
        for y in range(max_y + 1):
            for x in range(max_x + 1):
                axis.scatter(x, y, s=170, c="white", edgecolors="black", zorder=3)
                axis.text(x, y, f"({x},{y})", ha="center", va="center",
                          fontsize=8, zorder=4)
        for (x, y, direction), entry in totals.items():
            dx, dy = DIRECTION_DELTA[direction]
            value = entry[metric]
            arrow = FancyArrowPatch(
                (x + 0.12 * dx, y + 0.12 * dy),
                (x + 0.78 * dx, y + 0.78 * dy),
                arrowstyle="-|>", mutation_scale=12,
                linewidth=1.0 + 5.0 * value / vmax,
                color=cmap(norm(value)), zorder=2,
            )
            axis.add_patch(arrow)
        scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        fig.colorbar(scalar, ax=axis, shrink=0.75, label=title)
        axis.set_title(title)
        axis.set_aspect("equal")
        axis.set_xlim(-0.8, max_x + 0.8)
        axis.set_ylim(-0.8, max_y + 0.8)
        axis.set_xlabel("Mesh X")
        axis.set_ylabel("Mesh Y")
        axis.grid(alpha=0.15)
    save_figure(fig, output_dir, "mesh_hotspots", formats, dpi)


def plot_inflight(rows, output_dir, formats, dpi):
    by_interval = {}
    for row in rows:
        by_interval[(int(row["interval_start"]), int(row["interval_end"]))] = (
            int(row["inflight_end"]), int(row["peak_inflight"])
        )
    samples = sorted(by_interval.items())
    x = [end for (_, end), _ in samples]
    inflight = [values[0] for _, values in samples]
    peak = [values[1] for _, values in samples]
    fig, axis = plt.subplots(figsize=(10, 4))
    axis.step(x, inflight, where="post", label="In flight at interval end")
    axis.plot(x, peak, linestyle="--", label="Peak to date")
    axis.set(title="NoC in-flight traffic", xlabel="Accelerator cycle",
             ylabel="Flits")
    axis.grid(alpha=0.25)
    axis.legend()
    save_figure(fig, output_dir, "inflight_flits", formats, dpi)


def plot_latency(events, output_dir, formats, dpi):
    injections = defaultdict(list)
    latencies = []
    key_fields = ("packet_type", "source_id", "epoch", "block_id",
                  "dest_x", "dest_y", "last")
    for row in events:
        if row["event"].replace(" ", "") != "accept":
            continue
        key = tuple(row[field] for field in key_fields)
        if row["scope"] == "inject":
            injections[key].append(int(row["cycle"]))
        elif row["scope"] == "eject" and injections[key]:
            latencies.append(int(row["cycle"]) - injections[key].pop(0))
    fig, axis = plt.subplots(figsize=(8, 4.5))
    if latencies:
        bins = range(min(latencies), max(latencies) + 2)
        axis.hist(latencies, bins=bins, align="left", edgecolor="black")
        mean = sum(latencies) / len(latencies)
        axis.axvline(mean, color="tab:red", linestyle="--",
                    label=f"mean = {mean:.2f} cycles")
        axis.legend()
    else:
        axis.text(0.5, 0.5, "No matched injection/ejection events",
                  transform=axis.transAxes, ha="center")
    axis.set(title="Injection-to-ejection flit latency",
             xlabel="Latency (cycles)", ylabel="Flits")
    axis.grid(axis="y", alpha=0.25)
    save_figure(fig, output_dir, "flit_latency_histogram", formats, dpi)


def plot_stalls(events, output_dir, formats, dpi):
    active = {}
    episodes = []
    for row in events:
        event = row["event"].replace(" ", "")
        key = (row["scope"], row["node"], row["direction"])
        if event == "stall_begin":
            active[key] = int(row["cycle"])
        elif event == "stall_end" and key in active:
            start = active.pop(key)
            episodes.append((key, start, int(row["cycle"]) - start))
    fig, axis = plt.subplots(figsize=(11, max(3.5, 0.35 * len(episodes))))
    if episodes:
        labels = []
        for index, (key, start, duration) in enumerate(episodes):
            axis.broken_barh([(start, duration)], (index - 0.35, 0.7))
            labels.append(f"{key[0]} n{key[1]} {key[2]}")
        axis.set_yticks(range(len(labels)), labels, fontsize=8)
    else:
        axis.text(0.5, 0.5, "No completed stall episodes",
                  transform=axis.transAxes, ha="center")
    axis.set(title="NoC backpressure episodes", xlabel="Accelerator cycle",
             ylabel="Resource")
    axis.grid(axis="x", alpha=0.25)
    save_figure(fig, output_dir, "stall_timeline", formats, dpi)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("noc_plots"))
    parser.add_argument("--format", choices=("png", "pdf", "both"),
                        default="both")
    parser.add_argument("--top-links", type=int, default=12)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    if args.top_links <= 0:
        parser.error("--top-links must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    formats = ("png", "pdf") if args.format == "both" else (args.format,)
    timeline = read_csv(args.timeline)
    plot_link_timeseries(timeline, args.output_dir, formats, args.dpi,
                         args.top_links)
    plot_mesh_hotspots(timeline, args.output_dir, formats, args.dpi)
    plot_inflight(timeline, args.output_dir, formats, args.dpi)
    if args.events:
        events = read_csv(args.events)
        plot_latency(events, args.output_dir, formats, args.dpi)
        plot_stalls(events, args.output_dir, formats, args.dpi)
    print(f"Wrote NoC plots to {args.output_dir}")


if __name__ == "__main__":
    main()
