#!/usr/bin/env python3
"""Plot metrics emitted by completed dense VCS RTL simulations."""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


PATTERNS = {
    "total_cycles": re.compile(r"PASS:.*total_cycles=(\d+)"),
    "noc": re.compile(
        r"NOC summary window_cycles=(\d+) injected_flits=(\d+) "
        r"ejected_flits=(\d+) physical_link_flits=(\d+) average_hops=([\d.]+) "
        r"link_stall_cycles=(\d+)"
    ),
    "types": re.compile(
        r"NOC injected types state=(\d+) partial=(\d+) epoch_done=(\d+) "
        r"other=(\d+) packets=(\d+)"
    ),
    "busiest": re.compile(
        r"NOC busiest link node=(\d+) \((\d+),(\d+)\) dir=(\w+) "
        r"accepted_flits=(\d+) utilization=([\d.]+)%"
    ),
    "backpressured": re.compile(
        r"NOC most backpressured link node=(\d+) \((\d+),(\d+)\) dir=(\w+) "
        r"stall_cycles=(\d+)"
    ),
    "dram": re.compile(
        r"DRAM\[(\d+)\] accepted=(\d+) rejected=(\d+) completed=(\d+) "
        r"avg_latency_ticks=([\d.]+) max_latency_ticks=(\d+)"
    ),
}


def parse_summary(path: Path) -> dict[int, dict]:
    result = {}
    with path.open(newline="") as source:
        for row in csv.DictReader(source):
            if row["simulation_exit"] == "0":
                result[int(row["spins"])] = {
                    "spins": int(row["spins"]),
                    "config": row["config"],
                    "build_wall_seconds": float(row["build_wall_seconds"]),
                    "simulation_wall_seconds": float(row["simulation_wall_seconds"]),
                }
    return result


def parse_log(path: Path, point: dict) -> None:
    text = path.read_text(errors="replace")
    match = PATTERNS["total_cycles"].search(text)
    if not match:
        raise ValueError(f"missing PASS counters: {path}")
    point["total_cycles"] = int(match.group(1))
    match = PATTERNS["noc"].search(text)
    keys = ("window_cycles", "injected_flits", "ejected_flits",
            "physical_link_flits", "average_hops", "link_stall_cycles")
    point.update(dict(zip(keys, (int(match.group(i)) if i != 5 else float(match.group(i))
                                 for i in range(1, 7)))))
    match = PATTERNS["types"].search(text)
    point.update(dict(zip(("state_flits", "partial_flits", "epoch_done_flits",
                           "other_flits", "packets"), map(int, match.groups()))))
    match = PATTERNS["busiest"].search(text)
    point["busiest_link"] = f"n{match.group(1)}:{match.group(4)}"
    point["busiest_link_flits"] = int(match.group(5))
    point["maximum_link_utilization"] = float(match.group(6)) / 100
    match = PATTERNS["backpressured"].search(text)
    point["most_backpressured_link"] = f"n{match.group(1)}:{match.group(4)}"
    point["maximum_link_stalls"] = int(match.group(5))
    dram = []
    for match in PATTERNS["dram"].finditer(text):
        dram.append({
            "system": int(match.group(1)), "accepted": int(match.group(2)),
            "rejected": int(match.group(3)), "completed": int(match.group(4)),
            "average_ticks": float(match.group(5)), "maximum_ticks": int(match.group(6)),
        })
    active = [row for row in dram if row["accepted"]]
    requests = sum(row["accepted"] for row in active)
    point["dram_requests"] = requests
    point["dram_rejections"] = sum(row["rejected"] for row in dram)
    point["dram_weighted_average_ticks"] = (
        sum(row["accepted"] * row["average_ticks"] for row in active) / requests
    )
    point["dram_maximum_ticks"] = max(row["maximum_ticks"] for row in active)
    loads = np.array([row["accepted"] for row in active], dtype=float)
    point["dram_request_cv"] = float(loads.std() / loads.mean())


def collect(roots: list[Path]) -> list[dict]:
    points = {}
    for root in roots:
        points.update(parse_summary(root / "summary.csv"))
    for root in roots:
        for log in root.glob("sim_*.log"):
            match = re.match(r"sim_(\d+)_", log.name)
            if match and int(match.group(1)) in points:
                parse_log(log, points[int(match.group(1))])
    return [points[key] for key in sorted(points) if "total_cycles" in points[key]]


def save(fig, output: Path, stem: str, dpi: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(output / f"{stem}.{suffix}", dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def common_axis(ax, ylabel: str, log_y: bool = True) -> None:
    ax.set_xscale("log", base=2)
    if log_y:
        ax.set_yscale("log")
    ax.set_xlabel("Dense problem size (spins)")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=.25, which="both")


def plot_performance(data: list[dict], output: Path, dpi: int) -> None:
    x = [r["spins"] for r in data]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5))
    axes[0, 0].plot(x, [r["total_cycles"] for r in data], "o-")
    common_axis(axes[0, 0], "RTL cycles per iteration")
    axes[0, 1].plot(x, [r["simulation_wall_seconds"] for r in data], "o-", color="#d55e00")
    common_axis(axes[0, 1], "VCS simulation wall time (s)")
    axes[1, 0].plot(x, [r["total_cycles"] / r["simulation_wall_seconds"] for r in data],
                    "o-", color="#009e73")
    common_axis(axes[1, 0], "Simulated RTL cycles / wall second", False)
    axes[1, 1].plot(x, [r["window_cycles"] / r["total_cycles"] for r in data],
                    "o-", color="#cc79a7")
    common_axis(axes[1, 1], "NoC-active window / total cycles", False)
    fig.suptitle("VCS RTL dense-simulation performance scaling")
    save(fig, output, "vcs_rtl_performance_scaling", dpi)


def plot_traffic(data: list[dict], output: Path, dpi: int) -> None:
    x = [r["spins"] for r in data]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5))
    axes[0, 0].plot(x, [r["injected_flits"] for r in data], "o-", label="injected")
    axes[0, 0].plot(x, [r["physical_link_flits"] for r in data], "s-", label="link traversals")
    common_axis(axes[0, 0], "Flits per iteration")
    axes[0, 0].legend()
    axes[0, 1].plot(x, [r["average_hops"] for r in data], "o-", color="#009e73")
    common_axis(axes[0, 1], "Average physical hops", False)
    axes[1, 0].plot(x, [r["maximum_link_utilization"] for r in data], "o-", label="utilization")
    axes[1, 0].plot(x, [r["maximum_link_stalls"] / r["window_cycles"] for r in data],
                    "s-", label="most-stalled link / window")
    common_axis(axes[1, 0], "Fraction of NoC window", False)
    axes[1, 0].legend()
    axes[1, 1].plot(x, [r["link_stall_cycles"] for r in data], "o-", color="#d55e00")
    common_axis(axes[1, 1], "Aggregate link-stall cycles")
    fig.suptitle("VCS RTL NoC traffic and congestion scaling")
    save(fig, output, "vcs_rtl_noc_scaling", dpi)


def plot_mix(data: list[dict], output: Path, dpi: int) -> None:
    labels = [f"{r['spins'] // 1024}K" for r in data]
    totals = np.array([r["injected_flits"] for r in data], dtype=float)
    state = np.array([r["state_flits"] for r in data]) / totals
    partial = np.array([r["partial_flits"] for r in data]) / totals
    control = 1 - state - partial
    fig, ax = plt.subplots(figsize=(8.8, 5.1))
    idx = np.arange(len(data))
    ax.bar(idx, state, label="State")
    ax.bar(idx, partial, bottom=state, label="Partial vectors")
    ax.bar(idx, control, bottom=state + partial, label="Epoch/other")
    ax.set_xticks(idx, labels)
    ax.set(xlabel="Dense problem size", ylabel="Fraction of injected flits", ylim=(0, 1),
           title="VCS RTL NoC packet composition")
    ax.grid(axis="y", alpha=.25)
    ax.legend(ncol=3)
    save(fig, output, "vcs_rtl_packet_composition", dpi)


def plot_dram(data: list[dict], output: Path, dpi: int) -> None:
    x = [r["spins"] for r in data]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5))
    axes[0, 0].plot(x, [r["dram_requests"] for r in data], "o-")
    common_axis(axes[0, 0], "Accepted DRAM requests")
    axes[0, 1].plot(x, [r["dram_weighted_average_ticks"] for r in data], "o-", label="weighted average")
    axes[0, 1].plot(x, [r["dram_maximum_ticks"] for r in data], "s-", label="maximum")
    common_axis(axes[0, 1], "Ramulator latency (ticks)", False)
    axes[0, 1].legend()
    axes[1, 0].plot(x, [r["dram_request_cv"] for r in data], "o-", color="#009e73")
    common_axis(axes[1, 0], "Request-count CV across active endpoints", False)
    axes[1, 1].plot(x, [r["dram_rejections"] for r in data], "o-", color="#d55e00")
    common_axis(axes[1, 1], "Rejected DRAM requests", False)
    axes[1, 1].set_ylim(-.05, max(1, max(r["dram_rejections"] for r in data) * 1.1))
    fig.suptitle("VCS RTL Ramulator behavior")
    save(fig, output, "vcs_rtl_dram_scaling", dpi)


def write_csv(data: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(data[0])
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=keys)
        writer.writeheader()
        writer.writerows(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("plots/vcs_rtl_metrics"))
    parser.add_argument(
        "--input-root",
        action="append",
        type=Path,
        dest="input_roots",
        help="VCS result directory containing summary.csv and sim_*.log; repeatable",
    )
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    roots = args.input_roots or [Path("results/dense_config_search/rtl_vcs"),
                                 Path("results/dense_config_search/rtl_vcs_64k_128k")]
    data = collect(roots)
    if not data:
        raise SystemExit("no completed VCS RTL points with parseable logs")
    write_csv(data, args.output_dir / "vcs_rtl_metrics.csv")
    plot_performance(data, args.output_dir, args.dpi)
    plot_traffic(data, args.output_dir, args.dpi)
    plot_mix(data, args.output_dir, args.dpi)
    plot_dram(data, args.output_dir, args.dpi)
    print(f"points={len(data)} figures=4 output={args.output_dir}")


if __name__ == "__main__":
    main()
