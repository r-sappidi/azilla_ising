#!/usr/bin/env python3
"""Plot directed-link and node-load heatmaps from benchmark metric exports."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    from matplotlib import colormaps, colors
    from matplotlib.patches import FancyArrowPatch, Rectangle
except ImportError as exc:
    raise SystemExit("matplotlib is required; install requirements.txt") from exc


DIRECTION_DELTA = {
    "north": (0, -1),
    "south": (0, 1),
    "east": (1, 0),
    "west": (-1, 0),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as source:
        return list(csv.DictReader(source))


def draw_mesh_nodes(axis, width: int, height: int) -> None:
    for y in range(height):
        for x in range(width):
            axis.add_patch(Rectangle(
                (x - 0.12, y - 0.12), 0.24, 0.24,
                facecolor="white", edgecolor="black", linewidth=0.7,
                zorder=4,
            ))
    axis.set_xlim(-0.55, width - 0.45)
    axis.set_ylim(height - 0.45, -0.55)
    axis.set_aspect("equal")
    axis.set_xticks(range(width))
    axis.set_yticks(range(height))
    axis.set_xlabel("mesh x")
    axis.set_ylabel("mesh y")
    axis.grid(alpha=0.12, linewidth=0.5)


def draw_links(
    axis,
    rows: list[dict[str, str]],
    width: int,
    height: int,
    field: str,
    title: str,
    colorbar_label: str,
    cmap_name: str,
) -> None:
    links = [row for row in rows if row["scope"] == "link"]
    values = [float(row[field]) for row in links]
    maximum = max(values, default=0.0)
    norm = colors.Normalize(vmin=0, vmax=maximum or 1.0)
    cmap = colormaps[cmap_name]
    draw_mesh_nodes(axis, width, height)
    for row, value in zip(links, values):
        x, y = int(row["x"]), int(row["y"])
        dx, dy = DIRECTION_DELTA[row["direction"]]
        # Offset opposite directions onto opposite sides of the physical link.
        perpendicular_x, perpendicular_y = -dy * 0.045, dx * 0.045
        start = (
            x + 0.16 * dx + perpendicular_x,
            y + 0.16 * dy + perpendicular_y,
        )
        end = (
            x + 0.84 * dx + perpendicular_x,
            y + 0.84 * dy + perpendicular_y,
        )
        axis.add_patch(FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=8,
            linewidth=0.5 + 4.0 * (value / maximum if maximum else 0.0),
            color=cmap(norm(value)), zorder=2,
        ))
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    axis.figure.colorbar(scalar, ax=axis, shrink=0.72, label=colorbar_label)
    axis.set_title(title)


def draw_node_load(
    axis, rows: list[dict[str, str]], width: int, height: int
) -> None:
    values = [float(row["total_jobs"]) for row in rows]
    maximum = max(values, default=0.0)
    norm = colors.Normalize(vmin=0, vmax=maximum or 1.0)
    cmap = colormaps["cividis"]
    for row, value in zip(rows, values):
        x, y = int(row["x"]), int(row["y"])
        axis.add_patch(Rectangle(
            (x - 0.42, y - 0.42), 0.84, 0.84,
            facecolor=cmap(norm(value)), edgecolor="black", linewidth=0.7,
        ))
        axis.text(
            x, y, f"{int(value):,}", ha="center", va="center", fontsize=7,
            color="white" if norm(value) > 0.55 else "black",
        )
    axis.set_xlim(-0.55, width - 0.45)
    axis.set_ylim(height - 0.45, -0.55)
    axis.set_aspect("equal")
    axis.set_xticks(range(width))
    axis.set_yticks(range(height))
    axis.set_xlabel("mesh x")
    axis.set_ylabel("mesh y")
    axis.set_title("Scheduled block jobs per H1 package")
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    axis.figure.colorbar(scalar, ax=axis, shrink=0.72, label="jobs")


def plot_one(
    summary_path: Path, sweep_dir: Path, output_dir: Path,
    formats: list[str], dpi: int,
) -> None:
    summary = json.loads(summary_path.read_text())
    if summary_path.name.endswith("_event_summary.json"):
        prefix = summary_path.name.removesuffix("_event_summary.json")
        noc_path = summary_path.with_name(prefix + "_event_noc.csv")
        node_path = summary_path.with_name(prefix + "_event_nodes.csv")
    else:
        prefix = summary_path.name.removesuffix("_summary.json")
        noc_path = summary_path.with_name(prefix + "_noc.csv")
        node_path = summary_path.with_name(prefix + "_nodes.csv")
    noc_rows = read_csv(noc_path)
    node_rows = read_csv(node_path)
    width = int(summary["geometry"]["mesh_x"])
    height = int(summary["geometry"]["mesh_y"])
    relative = summary_path.relative_to(sweep_dir)
    dataset = relative.parts[0]
    mapping = relative.parts[1] if len(relative.parts) >= 3 else prefix

    figure, axes = plt.subplots(2, 2, figsize=(12.5, 10))
    draw_links(
        axes[0, 0], noc_rows, width, height, "accepted_flits",
        "Directed-link traffic volume", "accepted flits", "viridis",
    )
    draw_links(
        axes[0, 1], noc_rows, width, height, "window_utilization",
        "Directed-link utilization over iteration", "flits / iteration cycle",
        "plasma",
    )
    draw_links(
        axes[1, 0], noc_rows, width, height, "stall_cycles",
        "Directed-link backpressure", "stalled cycles", "magma",
    )
    draw_node_load(axes[1, 1], node_rows, width, height)
    figure.suptitle(
        f"{dataset} — {mapping} mapping\n"
        f"{summary['geometry']['spin_count']:,} spin capacity, "
        f"{summary['timing_cycles']['iteration']:,} cycles/iteration, "
        f"accuracy={summary['accuracy']}",
        fontsize=12,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{dataset}_{mapping}_traffic_heatmap"
    for suffix in formats:
        figure.savefig(
            output_dir / f"{stem}.{suffix}", dpi=dpi, bbox_inches="tight"
        )
    plt.close(figure)
    print(f"plotted {dataset}/{mapping}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--formats", default="png,pdf")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    output_dir = args.output_dir or args.sweep_dir / "figures"
    formats = [item.strip() for item in args.formats.split(",") if item.strip()]
    summaries = set(args.sweep_dir.glob("*/*/*_event_summary.json"))
    summaries.update(args.sweep_dir.glob("*/*/*_summary.json"))
    summaries.update(args.sweep_dir.glob("*/*_summary.json"))
    summaries = sorted(summaries)
    if not summaries:
        raise SystemExit(f"no event summaries below {args.sweep_dir}")
    for summary in summaries:
        plot_one(summary, args.sweep_dir, output_dir, formats, args.dpi)
    print(f"figures={output_dir} count={len(summaries)}")


if __name__ == "__main__":
    main()
