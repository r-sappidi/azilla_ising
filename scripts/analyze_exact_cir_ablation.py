#!/usr/bin/env python3
"""Collect and plot paired exact-event CIR ablation results.

The collector is intentionally restart-safe: directories without a complete
summary, DRAM table, NoC table, and successful ``/usr/bin/time -v`` record are
reported as incomplete and are not included in paired speedup figures.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


MODES = ("cir", "cores_only")
COLORS = {"cir": "#2878B5", "cores_only": "#D95319"}
LABELS = {"cir": "CIR", "cores_only": "Core-centric"}


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir", type=Path,
                   default=Path("results/cir_evaluation/exact_sparse"))
    p.add_argument("--output-dir", type=Path,
                   default=Path("plots/cir_evaluation/exact_sparse"))
    return p.parse_args()


def elapsed_seconds(text: str) -> float | None:
    match = re.search(r"Elapsed \(wall clock\) time [^\n]*\):\s*([^\s]+)", text)
    if not match:
        return None
    fields = match.group(1).split(":")
    try:
        if len(fields) == 3:
            return float(fields[0]) * 3600 + float(fields[1]) * 60 + float(fields[2])
        if len(fields) == 2:
            return float(fields[0]) * 60 + float(fields[1])
        return float(fields[0])
    except ValueError:
        return None


def csv_sum(path: Path, field: str) -> float:
    with path.open(newline="") as stream:
        return sum(float(row.get(field, 0) or 0) for row in csv.DictReader(stream))


def csv_weighted_average(path: Path, value: str, weight: str) -> float:
    numerator = denominator = 0.0
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            w = float(row.get(weight, 0) or 0)
            numerator += float(row.get(value, 0) or 0) * w
            denominator += w
    return numerator / denominator if denominator else 0.0


def read_run(workload: str, mode: str, directory: Path) -> dict:
    summary_path = directory / "metrics_summary.json"
    noc_path = directory / "metrics_noc.csv"
    dram_path = directory / "metrics_dram.csv"
    wall_path = directory / "wall.time"
    row = {"workload": workload, "mode": mode, "complete": False}
    required = (summary_path, noc_path, dram_path, wall_path)
    if not all(path.is_file() for path in required):
        row["status"] = "missing:" + ",".join(p.name for p in required if not p.is_file())
        return row
    wall_text = wall_path.read_text(errors="replace")
    wall = elapsed_seconds(wall_text)
    exit_match = re.search(r"Exit status:\s*(\d+)", wall_text)
    if wall is None or not exit_match or int(exit_match.group(1)) != 0:
        row["status"] = "unfinished_or_failed"
        return row
    try:
        summary = json.loads(summary_path.read_text())
        geometry = summary["geometry"]
        timing = summary["timing_cycles"]
        jobs = summary["scheduled_jobs"]
        noc = summary["noc"]
        ablation = summary.get("cores_only_ablation", {})
        scheduled = int(jobs["total"])
        reported_reads = int(ablation.get("weight_block_reads", 0) or 0)
        # Legacy CIR summaries left ablation-only storage counters at zero.
        # A CIR job evaluates one stored unordered block exactly once.
        corrected = mode == "cir" and scheduled > 0 and reported_reads == 0
        weight_reads = scheduled if corrected else reported_reads
        logical_blocks = int(ablation.get("logical_weight_blocks_stored", 0) or 0)
        if mode == "cir" and logical_blocks == 0 and scheduled > 0:
            logical_blocks = scheduled
        unordered_blocks = int(ablation.get("unordered_interaction_blocks", 0) or 0)
        if mode == "cir" and unordered_blocks == 0 and scheduled > 0:
            unordered_blocks = scheduled
        accepted = csv_sum(dram_path, "accepted_requests")
        rejected = csv_sum(dram_path, "rejected_requests")
        row.update({
            "complete": True,
            "status": "complete",
            "spins": int(geometry["spin_count"]),
            "mesh_x": int(geometry["mesh_x"]),
            "mesh_y": int(geometry["mesh_y"]),
            "h0_per_h1": int(geometry["h0_per_h1"]),
            "cores_per_h0": int(geometry["cores_per_h0"]),
            "accuracy": summary.get("accuracy", "unknown"),
            "execution_mode": summary.get("execution_mode", mode),
            "initialization_cycles": int(timing["initialization"]),
            "iteration_cycles": int(timing["iteration"]),
            "total_cycles": int(timing["total"]),
            "wall_seconds": wall,
            "h0_jobs": int(jobs["h0"]),
            "h1_jobs": int(jobs["h1"]),
            "cross_jobs": int(jobs["cross"]),
            "scheduled_jobs": scheduled,
            "logical_weight_blocks": logical_blocks,
            "unordered_interaction_blocks": unordered_blocks,
            "weight_block_reads_reported": reported_reads,
            "weight_block_reads": weight_reads,
            "weight_reads_corrected_from_schedule": corrected,
            "weight_read_bytes": weight_reads * 1024,
            "dram_accepted_requests": int(accepted),
            "dram_rejected_requests": int(rejected),
            "dram_rejection_fraction": rejected / (accepted + rejected) if accepted + rejected else 0,
            "dram_average_latency_cycles": csv_weighted_average(
                dram_path, "average_latency_cycles", "completed_requests"),
            "dram_maximum_latency_cycles": csv_sum(dram_path, "maximum_latency_cycles") * 0,
            "state_flits": int(noc.get("state_flits", 0)),
            "partial_flits": int(noc.get("partial_flits", 0)),
            "injected_flits": int(noc.get("injected_flits", 0)),
            "physical_link_flits": int(noc.get("physical_link_flits", 0)),
            "average_hops": float(noc.get("average_hops", 0)),
            "injection_stalls": int(noc.get("injection_stalls", 0)),
            "link_stalls": int(noc.get("link_stalls", 0)),
            "remote_state_packets": int(ablation.get("remote_state_packets", 0) or 0),
            "top_level_partial_packets": int(ablation.get("top_level_partial_packets", 0) or 0),
        })
        with dram_path.open(newline="") as stream:
            row["dram_maximum_latency_cycles"] = max(
                (float(r.get("maximum_latency_cycles", 0) or 0) for r in csv.DictReader(stream)),
                default=0.0)
        return row
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        row["status"] = f"invalid:{type(error).__name__}"
        return row


def save_figure(fig: plt.Figure, output: Path) -> None:
    fig.savefig(output.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def grouped(ax, pairs: list[dict], key: str, ylabel: str, scale: float = 1.0,
            log: bool = False) -> None:
    x = np.arange(len(pairs)); width = 0.36
    for offset, mode in ((-width / 2, "cir"), (width / 2, "cores_only")):
        vals = [p[mode][key] / scale for p in pairs]
        ax.bar(x + offset, vals, width, color=COLORS[mode], label=LABELS[mode])
    ax.set_xticks(x, [f"{p['spins']//1024}K" for p in pairs])
    ax.set_ylabel(ylabel); ax.grid(axis="y", alpha=.25)
    if log: ax.set_yscale("log")


def main() -> None:
    args = arguments()
    hold = Path(__file__).resolve().parents[1] / (
        "results/paper_validation_20260906/performance_validation_hold.json"
    )
    if hold.is_file() and json.loads(hold.read_text()).get("blocked_modes"):
        raise SystemExit(
            "BLOCKED: integrated execution-mode validation is pending; "
            "see " + str(hold)
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for workload_dir in sorted(p for p in args.input_dir.iterdir() if p.is_dir()):
        for mode in MODES:
            directory = workload_dir / mode
            if not directory.is_dir() and mode == "cores_only":
                directory = workload_dir / "cores-only"
            rows.append(read_run(workload_dir.name, mode, directory))
    fields = sorted({key for row in rows for key in row})
    with (args.output_dir / "exact_ablation_runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields); writer.writeheader(); writer.writerows(rows)
    by_workload = {}
    for row in rows:
        if row["complete"]: by_workload.setdefault(row["workload"], {})[row["mode"]] = row
    pairs = []
    for workload, modes in by_workload.items():
        if all(mode in modes for mode in MODES):
            item = {**modes, "workload": workload, "spins": modes["cir"]["spins"]}
            item["iteration_speedup_cir_over_cores"] = (
                modes["cores_only"]["iteration_cycles"] / modes["cir"]["iteration_cycles"])
            pairs.append(item)
    pairs.sort(key=lambda p: (p["spins"], p["workload"]))
    paired_metrics = (
        "iteration_cycles", "total_cycles", "wall_seconds", "weight_block_reads",
        "dram_accepted_requests", "dram_rejected_requests", "dram_rejection_fraction",
        "dram_average_latency_cycles", "dram_maximum_latency_cycles", "state_flits",
        "partial_flits", "physical_link_flits", "injection_stalls", "link_stalls",
    )
    pair_fields = ["workload", "spins", "iteration_speedup_cir_over_cores"]
    pair_fields += [f"{mode}_{metric}" for mode in MODES for metric in paired_metrics]
    with (args.output_dir / "exact_ablation_pairs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, pair_fields); writer.writeheader()
        for p in pairs:
            flat = {key: p[key] for key in pair_fields[:3]}
            for mode in MODES:
                flat.update({f"{mode}_{metric}": p[mode][metric] for metric in paired_metrics})
            writer.writerow(flat)
    if not pairs:
        print(f"wrote status CSV; no complete pairs in {args.input_dir}")
        return

    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6), layout="constrained")
    grouped(axes[0], pairs, "iteration_cycles", "Iteration cycles")
    speedups = [p["iteration_speedup_cir_over_cores"] for p in pairs]
    axes[1].bar(np.arange(len(pairs)), speedups, color="#6A5ACD")
    axes[1].axhline(1, color="black", lw=1); axes[1].set_ylabel("CIR speedup (core cycles / CIR cycles)")
    axes[1].set_xticks(np.arange(len(pairs)), [f"{p['spins']//1024}K" for p in pairs]); axes[1].grid(axis="y", alpha=.25)
    axes[0].legend(frameon=False); fig.suptitle("Exact-event CIR ablation: latency")
    save_figure(fig, args.output_dir / "01_cycles_and_speedup")

    fig, axes = plt.subplots(1, 4, figsize=(18.0, 3.8), layout="constrained")
    grouped(axes[0], pairs, "weight_block_reads", "Weight-block reads", log=True)
    grouped(axes[1], pairs, "dram_accepted_requests", "Accepted DRAM requests", log=True)
    grouped(axes[2], pairs, "dram_rejected_requests", "Rejected DRAM requests")
    grouped(axes[3], pairs, "dram_average_latency_cycles", "Mean DRAM latency (cycles)")
    axes[0].legend(frameon=False); fig.suptitle("Weight-stream and DRAM behavior")
    save_figure(fig, args.output_dir / "02_weights_and_dram")

    fig, axes = plt.subplots(1, 3, figsize=(13.0, 3.6), layout="constrained")
    grouped(axes[0], pairs, "state_flits", "State flits")
    grouped(axes[1], pairs, "partial_flits", "Partial-result flits")
    grouped(axes[2], pairs, "physical_link_flits", "Physical-link flits")
    axes[0].legend(frameon=False); fig.suptitle("Top-level NoC traffic by execution mode")
    save_figure(fig, args.output_dir / "03_noc_traffic")

    fig, ax = plt.subplots(figsize=(9.2, max(2.4, .42 * len(pairs) + 1.5)),
                           layout="constrained")
    ax.axis("off")
    table_rows = []
    for p in pairs:
        table_rows.append([p["workload"], f"{p['spins']:,}", p["cir"]["accuracy"],
                           p["cores_only"]["accuracy"], "Ramulator exact-event (timing-only)"])
    table = ax.table(cellText=table_rows,
                     colLabels=["Workload", "Spins", "CIR label", "Core label", "Simulation tier"],
                     cellLoc="center", loc="center")
    table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1, 1.45)
    for (r, _), cell in table.get_celld().items():
        if r == 0: cell.set_facecolor("#D9EAF7"); cell.set_text_props(weight="bold")
    ax.set_title("Result fidelity and scope", pad=12)
    save_figure(fig, args.output_dir / "04_fidelity_labels")
    print(f"wrote {len(rows)} run records and {len(pairs)} complete pairs to {args.output_dir}")


if __name__ == "__main__":
    main()
