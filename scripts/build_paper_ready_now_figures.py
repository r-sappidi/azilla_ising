#!/usr/bin/env python3
"""Build conservative paper figures from completed, post-banking results."""
from __future__ import annotations

import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "plots/paper_ready_now_20260905"
DATA = OUT / "source_data"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError(f"no rows for {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def save(fig, stem: str) -> None:
    fig.tight_layout()
    for extension in ("pdf", "png"):
        fig.savefig(OUT / f"{stem}.{extension}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def quality_figure() -> None:
    source = ROOT / "results/cir_evaluation/solution_quality/schedule_sweep_20260904/held_out_scaling/summary.csv"
    rows = list(csv.DictReader(source.open()))
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        grouped[int(row["spins"])].append(100 * (1 - float(row["gap_fraction"])))
    summary = []
    for spins, values in sorted(grouped.items()):
        summary.append({
            "spins": spins, "seeds": len(values),
            "mean_percent_of_known_cut": statistics.mean(values),
            "minimum_percent_of_known_cut": min(values),
            "maximum_percent_of_known_cut": max(values),
            "iterations": 1000,
            "accuracy_scope": "algorithmic-solution-quality",
        })
    write_csv(DATA / "solution_quality_scaling.csv", summary)
    x = [row["spins"] for row in summary]
    y = [row["mean_percent_of_known_cut"] for row in summary]
    low = [a-b for a, b in zip(y, [row["minimum_percent_of_known_cut"] for row in summary])]
    high = [b-a for a, b in zip(y, [row["maximum_percent_of_known_cut"] for row in summary])]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.errorbar(x, y, yerr=[low, high], marker="o", capsize=3, color="#2878B5")
    ax.set_xscale("log", base=2); ax.set_ylim(98.7, 99.4)
    ax.set_xlabel("Problem size (spins)"); ax.set_ylabel("Known cut achieved (%)")
    ax.grid(True, alpha=.25); ax.set_title("Solution quality remains stable through 1M spins")
    save(fig, "fig1_solution_quality_scaling")


def validation_table() -> None:
    source = ROOT / "results/simulator_validation/status.tsv"
    rows = list(csv.DictReader(source.open(), delimiter="\t"))
    keep = {
        "python_units", "component_rtl", "full_256", "cir_16k_timing",
        "cir_16k_arithmetic", "cir_exact_event", "cores_only_256_rtl",
    }
    labels = {
        "python_units": "Python unit suite",
        "component_rtl": "Component RTL/Python differential",
        "full_256": "256-spin full-system differential",
        "cir_16k_timing": "16K CIR timing/traffic audit",
        "cir_16k_arithmetic": "16K RTL/Python/Octave arithmetic",
        "cir_exact_event": "Exact-event reference checks",
        "cores_only_256_rtl": "256-spin cores-only engine",
    }
    selected = [{
        "validation": labels[row["check"]], "status": row["status"],
        "elapsed_seconds": row["elapsed_seconds"], "evidence": row["log"],
    } for row in rows if row["check"] in keep]
    selected.append({
        "validation": "Integrated cores-only full-system differential",
        "status": "IN PROGRESS", "elapsed_seconds": "", 
        "evidence": "Not used for current headline speedup claims",
    })
    write_csv(DATA / "validation_coverage.csv", selected)
    fig, ax = plt.subplots(figsize=(8.4, 3.2)); ax.axis("off")
    table = ax.table(
        cellText=[[r["validation"], r["status"]] for r in selected],
        colLabels=["Validation scope", "Status"], colWidths=[.78, .22],
        cellLoc="left", loc="center",
    )
    table.auto_set_font_size(False); table.set_fontsize(9); table.scale(1, 1.35)
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#D9EAF7"); cell.set_text_props(weight="bold")
        elif col == 1:
            status = selected[row-1]["status"]
            cell.set_facecolor("#DDEEDB" if status == "PASS" else "#FFF0C2")
    ax.set_title("RTL and model validation coverage", pad=12)
    save(fig, "fig2_validation_coverage")


def dense_rtl_figure() -> None:
    root = ROOT / "results/regeneration_20260904_v2/dense_vcs_fixed_h1"
    runs = list(csv.DictReader((root / "summary.csv").open()))
    rows = []
    for run in runs:
        if int(run["simulation_exit"]) != 0:
            continue
        spins = int(run["spins"])
        label = f'{spins}_{run["config"]}'
        log = (root / f"sim_{label}.log").read_text()
        match = re.search(r"PASS: 1 iteration\(s\).*total_cycles=(\d+)", log)
        if not match:
            raise RuntimeError(f"missing completed RTL timing in {label}")
        noc_rows = list(csv.DictReader(Path(run["noc_stats"]).open()))
        injected = sum(int(r["accepted_flits"]) for r in noc_rows if r["scope"] == "inject")
        links = sum(int(r["accepted_flits"]) for r in noc_rows if r["scope"] == "link")
        stalls = sum(int(r["stall_cycles"]) for r in noc_rows if r["scope"] == "inject")
        rows.append({
            "spins": spins, "total_rtl_cycles": int(match.group(1)),
            "vcs_wall_seconds": float(run["simulation_wall_seconds"]),
            "injected_flits": injected, "physical_link_flits": links,
            "injection_stalls": stalls, "accuracy_scope": "executed-vcs-rtl-timing-only",
        })
    rows.sort(key=lambda row: row["spins"])
    write_csv(DATA / "dense_vcs_scaling.csv", rows)
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.5))
    x = [r["spins"] for r in rows]
    axes[0].plot(x, [r["total_rtl_cycles"]/1e6 for r in rows], "o-", color="#2878B5")
    axes[0].set_ylabel("RTL cycles (millions)"); axes[0].set_xlabel("Spins")
    axes[0].set_title("Dense RTL execution time")
    axes[1].plot(x, [r["injected_flits"]/1e6 for r in rows], "o-", label="Injected")
    axes[1].plot(x, [r["physical_link_flits"]/1e6 for r in rows], "s--", label="Physical-link")
    axes[1].set_ylabel("Flits (millions)"); axes[1].set_xlabel("Spins")
    axes[1].set_title("Top-level traffic"); axes[1].legend(frameon=False)
    for ax in axes: ax.grid(True, alpha=.25); ax.ticklabel_format(axis="x", style="sci", scilimits=(0,0))
    fig.suptitle("Completed post-banking VCS RTL points (256K pending)", fontsize=11)
    save(fig, "fig3_dense_rtl_scaling_and_traffic")


def locality_figure() -> None:
    source = ROOT / "results/regeneration_20260904_v2/canonical_sparse/results.csv"
    rows = list(csv.DictReader(source.open()))
    wanted = {
        "canonical-toroidal-d4": "Toroidal, degree 4",
        "canonical-community-d16": "Community, degree 16",
        "canonical-uniform-d16": "Uniform, degree 16",
    }
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        if row["family"] in wanted:
            grouped[(row["family"], int(row["problem_vertices"]))].append(row)
    summary = []
    for (family, spins), values in sorted(grouped.items()):
        summary.append({
            "family": wanted[family], "spins": spins, "seeds": len(values),
            "mean_cross_job_fraction": statistics.mean(float(v["cross_job_fraction"]) for v in values),
            "mean_physical_link_flits": statistics.mean(float(v["physical_link_flits"]) for v in values),
            "mean_active_block_pairs": statistics.mean(float(v["active_block_pairs"]) for v in values),
            "accuracy_scope": "deterministic-work-and-traffic-accounting",
        })
    write_csv(DATA / "sparse_locality_traffic.csv", summary)
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
    for family in wanted.values():
        values = [r for r in summary if r["family"] == family]
        axes[0].plot([r["spins"] for r in values], [100*r["mean_cross_job_fraction"] for r in values], "o-", label=family)
        axes[1].plot([r["spins"] for r in values], [r["mean_physical_link_flits"] for r in values], "o-", label=family)
    axes[0].set_ylabel("Cross-H1 interaction blocks (%)"); axes[0].set_ylim(-2, 102)
    axes[1].set_ylabel("Physical-link flits"); axes[1].set_yscale("symlog", linthresh=1)
    for ax in axes:
        ax.set_xscale("log", base=2); ax.set_xlabel("Problem size (spins)"); ax.grid(True, alpha=.25)
    axes[0].set_title("Work placement"); axes[1].set_title("Communication volume")
    axes[1].legend(frameon=False, fontsize=8)
    save(fig, "fig4_sparse_locality_and_traffic")


def quality_distribution_figure() -> None:
    source = ROOT / "results/cir_evaluation/solution_quality/schedule_sweep_20260904/held_out_scaling/summary.csv"
    rows = list(csv.DictReader(source.open()))
    sizes = sorted({int(row["spins"]) for row in rows})
    output = [{
        "spins": int(row["spins"]), "seed": int(row["seed"]),
        "percent_of_known_cut": 100 * (1-float(row["gap_fraction"])),
        "best_iteration": int(row["best_iteration"]),
        "iterations_run": int(row["iterations"]),
        "accuracy_scope": "algorithmic-solution-quality",
    } for row in rows]
    write_csv(DATA / "solution_quality_trials.csv", output)
    quality = [[r["percent_of_known_cut"] for r in output if r["spins"] == n] for n in sizes]
    convergence = [[r["best_iteration"] for r in output if r["spins"] == n] for n in sizes]
    labels = [f"{n//1024}K" if n < 1048576 else "1M" for n in sizes]
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.5))
    axes[0].boxplot(quality, tick_labels=labels, showfliers=False)
    axes[0].set_ylabel("Known cut achieved (%)"); axes[0].set_title("Quality across 30 seeds")
    axes[1].boxplot(convergence, tick_labels=labels, showfliers=False)
    axes[1].axhline(1000, color="#777777", linestyle="--", linewidth=1, label="Iteration budget")
    axes[1].set_ylabel("Iteration of best solution"); axes[1].set_title("Convergence within fixed budget")
    axes[1].legend(frameon=False, fontsize=8)
    for ax in axes: ax.set_xlabel("Problem size"); ax.grid(axis="y", alpha=.25)
    save(fig, "fig5_solution_quality_variability_and_convergence")


def block_occupancy_figure() -> None:
    rows = list(csv.DictReader((ROOT / "results/regeneration_20260904_v2/canonical_sparse/results.csv").open()))
    selected = [row for row in rows if row["family"] in {
        "canonical-toroidal-d4", "canonical-community-d16", "canonical-uniform-d16",
    }]
    names = {
        "canonical-toroidal-d4": "Toroidal, degree 4",
        "canonical-community-d16": "Community, degree 16",
        "canonical-uniform-d16": "Uniform, degree 16",
    }
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in selected: grouped[(row["family"], int(row["problem_vertices"]))].append(row)
    summary = []
    for (family, spins), values in sorted(grouped.items()):
        summary.append({
            "family": names[family], "spins": spins, "seeds": len(values),
            "mean_block_pair_occupancy_percent": 100*statistics.mean(float(v["block_pair_occupancy_fraction"]) for v in values),
            "mean_edges_per_active_block": statistics.mean(float(v["edges_per_active_block_pair"]) for v in values),
            "mean_active_blocks": statistics.mean(float(v["active_block_pairs"]) for v in values),
            "accuracy_scope": "deterministic-block-structure",
        })
    write_csv(DATA / "block_occupancy_scaling.csv", summary)
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
    for family in names.values():
        values = [r for r in summary if r["family"] == family]
        x = [r["spins"] for r in values]
        axes[0].plot(x, [r["mean_block_pair_occupancy_percent"] for r in values], "o-", label=family)
        axes[1].plot(x, [r["mean_edges_per_active_block"] for r in values], "o-", label=family)
    axes[0].set_ylabel("Occupied block pairs (%)"); axes[0].set_yscale("log")
    axes[1].set_ylabel("Edges per occupied block pair")
    for ax in axes: ax.set_xscale("log", base=2); ax.set_xlabel("Problem size (spins)"); ax.grid(True, alpha=.25)
    axes[0].set_title("Block-sparse storage opportunity"); axes[1].set_title("Useful work per fetched block")
    axes[1].legend(frameon=False, fontsize=8)
    save(fig, "fig6_block_occupancy_efficiency")


def exact_gset_traffic_figure() -> None:
    root = ROOT / "results/regeneration_20260904_v2/exact_execution_modes"
    rows = []
    for path in sorted(root.glob("*/cir/metrics_summary.json")):
        data = json.loads(path.read_text()); noc = data["noc"]; jobs = data["scheduled_jobs"]["total"]
        dram = data["dram_systems"]; accepted = sum(x["accepted_requests"] for x in dram)
        rows.append({
            "dataset": path.parent.parent.name.upper().replace("_", " "),
            "interaction_blocks": jobs,
            "injected_flits_per_block": noc["injected_flits"]/jobs,
            "physical_link_flits_per_block": noc["physical_link_flits"]/jobs,
            "state_flits_per_block": noc["state_flits"]/jobs,
            "partial_flits_per_block": noc["partial_flits"]/jobs,
            "average_hops": noc["average_hops"],
            "dram_rejection_percent": 100*sum(x["rejected_requests"] for x in dram)/(accepted+sum(x["rejected_requests"] for x in dram)),
            "accuracy_scope": data["accuracy"],
        })
    write_csv(DATA / "exact_gset_cir_traffic.csv", rows)
    labels = [r["dataset"].replace("GSET G", "G") for r in rows]; x = range(len(rows)); width=.34
    fig, axes = plt.subplots(1, 2, figsize=(8.8, 3.5))
    axes[0].bar([i-width/2 for i in x], [r["state_flits_per_block"] for r in rows], width, label="State")
    axes[0].bar([i+width/2 for i in x], [r["partial_flits_per_block"] for r in rows], width, label="Partial")
    axes[0].set_ylabel("Injected flits per interaction block"); axes[0].legend(frameon=False)
    axes[1].bar(x, [r["average_hops"] for r in rows], color="#2E8B57")
    axes[1].set_ylabel("Average top-level hops")
    for ax in axes: ax.set_xticks(list(x), labels); ax.grid(axis="y", alpha=.25)
    axes[0].set_title("Traffic composition"); axes[1].set_title("Communication distance")
    fig.suptitle("Ramulator exact-event CIR traffic on published Gset graphs", fontsize=11)
    save(fig, "fig7_exact_gset_cir_traffic")


def rtl_congestion_figure() -> None:
    path = ROOT / "results/regeneration_20260904_v2/dense_vcs_fixed_h1/noc_stats_131072_2x1_h08_c256_e4-2-4_f4_s0_r1_i1_t1.csv"
    raw = list(csv.DictReader(path.open()))
    rows = [{
        "resource": f'{r["scope"]}:n{r["node"]}:{r["direction"]}',
        "scope": r["scope"], "node": int(r["node"]), "direction": r["direction"],
        "accepted_flits": int(r["accepted_flits"]), "stall_cycles": int(r["stall_cycles"]),
        "window_utilization": float(r["window_utilization"]),
        "backpressure_fraction": float(r["backpressure_fraction"]),
        "state_flits": int(r["state_flits"]), "partial_flits": int(r["partial_flits"]),
        "accuracy_scope": "executed-vcs-rtl-timing-only",
    } for r in raw]
    write_csv(DATA / "dense_128k_vcs_noc_resources.csv", rows)
    labels = [r["resource"] for r in rows]; x=range(len(rows))
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.6))
    axes[0].bar(x, [100*r["window_utilization"] for r in rows], color="#2878B5")
    axes[0].set_ylabel("Window utilization (%)")
    axes[1].bar(x, [100*r["backpressure_fraction"] for r in rows], color="#D95319")
    axes[1].set_ylabel("Backpressure (%)")
    for ax in axes:
        ax.set_xticks(list(x), labels, rotation=35, ha="right", fontsize=8); ax.grid(axis="y", alpha=.25)
    axes[0].set_title("Resource utilization"); axes[1].set_title("Offered cycles stalled")
    fig.suptitle("128K dense workload: measured RTL NoC congestion", fontsize=11)
    save(fig, "fig8_dense_128k_vcs_congestion")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True); DATA.mkdir(parents=True, exist_ok=True)
    # Only emit complete performance figures into the paper-ready directory.
    # Validation is better reported as a methodology table, while dense RTL
    # scaling needs at least one more completed point before plotting a trend.
    quality_figure(); locality_figure(); quality_distribution_figure()
    block_occupancy_figure(); exact_gset_traffic_figure(); rtl_congestion_figure()
    manifest = {
        "scope": "paper-ready completed evidence as of 2026-09-05",
        "limitations": [
            "Sparse traffic counts are retained independently of calibrated endpoint latency.",
            "No CIR-versus-cores headline speedup is claimed before integrated cores-only validation.",
        ],
        "figures": sorted(path.name for path in OUT.glob("fig*.pdf")),
        "source_tables": sorted(path.name for path in DATA.glob("*.csv")),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(OUT)


if __name__ == "__main__":
    main()
