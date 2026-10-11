#!/usr/bin/env python3
"""Generate compact Markdown/JSON tables from completed benchmark results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="") as source:
        return list(csv.DictReader(source))


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(str(value) for value in row) + " |" for row in rows
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("results/paper_benchmarks")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.root / "PAPER_TABLES.md"
    manifest = json.loads((args.root / "datasets/manifest.json").read_text())
    by_name = {row["name"]: row for row in manifest["datasets"]}
    calibration = csv_rows(args.root / "exact_calibration.csv")
    exact_mapping = csv_rows(args.root / "exact_mapping_confirmation.csv")
    exact_interconnect = csv_rows(
        args.root / "exact_interconnect_sensitivity.csv"
    )
    mapping = csv_rows(args.root / "mapping_comparison/results.csv")
    owner = csv_rows(args.root / "owner_policy_comparison/results.csv")
    traffic = csv_rows(args.root / "traffic_patterns.csv")
    traffic_families = csv_rows(args.root / "traffic_family_summary.csv")
    dram = csv_rows(args.root / "dram_patterns.csv")

    sections = [
        "# Generated paper tables",
        "",
        "Generated from machine-readable manifests and metric exports. Values "
        "are simulator cycles, not time-to-solution.",
        "",
        "## Exact/Ramulator timing confirmation",
        "",
    ]
    exact_rows = []
    for row in sorted(calibration, key=lambda value: int(value["spins"])):
        dataset = by_name[row["dataset"]]
        exact_rows.append([
            row["dataset"], f"{dataset['problem_vertices']:,}",
            f"{int(row['spins']):,}", f"{dataset['undirected_edges']:,}",
            f"{int(row['exact_cycles']):,}",
            f"{int(row['calibrated_cycles']):,}",
            f"{100 * float(row['absolute_percentage_error']):.1f}%",
            f"{int(row['exact_link_flits']):,}",
            f"{float(row['request_weighted_average_dram_latency_cycles']):.2f}",
        ])
    sections.append(markdown_table(
        [
            "dataset", "vertices", "spin capacity", "edges", "exact cycles",
            "calibrated cycles", "calibration error", "link flits",
            "avg DRAM latency (cycles)",
        ], exact_rows,
    ))
    sections.extend(["", "## Exact/Ramulator DRAM balance", ""])
    dram_all = [row for row in dram if row["level"] == "all"]
    sections.append(markdown_table(
        [
            "dataset", "requests", "avg latency", "max latency",
            "request-load CV", "request-load Gini", "rejections",
        ], [[
            row["dataset"], f"{int(row['accepted_requests']):,}",
            f"{float(row['request_weighted_average_latency_cycles']):.2f}",
            f"{float(row['maximum_latency_cycles']):.2f}",
            f"{float(row['request_load_cv']):.3f}",
            f"{float(row['request_load_gini']):.3f}",
            f"{int(row['rejected_requests']):,}",
        ] for row in dram_all],
    ))
    sections.extend(["", "## Exact G70 mapping confirmation", ""])
    sections.append(markdown_table(
        [
            "mapping", "blocks", "cross blocks", "cycles", "vs random",
            "vs identity", "link flits", "avg hops",
        ], [[
            row["mapping"], f"{int(row['scheduled_total']):,}",
            f"{int(row['scheduled_cross']):,}",
            f"{int(row['iteration_cycles']):,}",
            f"{float(row['speedup_vs_random']):.3f}×",
            f"{float(row['speedup_vs_identity']):.3f}×",
            f"{int(row['physical_link_flits']):,}",
            f"{float(row['average_hops']):.3f}",
        ] for row in exact_mapping],
    ))
    sections.extend(["", "## Exact G70 interconnect sensitivity", ""])
    sections.append(markdown_table(
        [
            "variant", "cycles", "slowdown", "link latency", "flit interval",
            "credits", "credit return", "package latency", "link stalls",
        ], [[
            row["variant"], f"{int(row['iteration_cycles']):,}",
            f"{float(row['slowdown_vs_rtl_direct']):.3f}×",
            row["link_latency_cycles"], row["link_flit_interval_cycles"],
            row["link_max_inflight_flits"], row["link_credit_return_cycles"],
            row["chiplet_link_latency_cycles"],
            f"{int(row['link_stalls']):,}",
        ] for row in exact_interconnect],
    ))
    sections.extend(["", "## Broad mapping comparison", ""])
    provided = [row for row in mapping if row["mapping"] == "provided_mapper"]
    sections.append(markdown_table(
        ["dataset", "blocks", "cycles", "vs random", "vs identity"],
        [[
            row["dataset"], f"{int(row['active_block_pairs']):,}",
            f"{int(row['iteration_cycles']):,}",
            f"{float(row['speedup_vs_random']):.3f}×",
            f"{float(row['speedup_vs_identity']):.3f}×",
        ] for row in provided],
    ))
    sections.extend(["", "## Owner-policy interaction", ""])
    rtl_owner = [row for row in owner if row["owner_policy"] == "rtl_h1_pair"]
    sections.append(markdown_table(
        ["dataset", "mapping", "cycles", "vs optimized per-block owner"],
        [[
            row["dataset"], row["mapping"],
            f"{int(row['iteration_cycles']):,}",
            f"{float(row['speedup_vs_optimized_per_block']):.3f}×",
        ] for row in rtl_owner],
    ))
    sections.extend(["", "## Traffic-family summary", ""])
    sections.append(markdown_table(
        [
            "family", "points", "vertex range", "mean cross jobs",
            "mean max link util", "max link util", "mean link Gini",
            "cycle scaling exponent",
        ], [[
            row["family"], row["points"],
            f"{int(row['minimum_vertices']):,}–{int(row['maximum_vertices']):,}",
            f"{float(row['mean_cross_job_fraction']):.3f}",
            f"{float(row['mean_maximum_link_utilization']):.3f}",
            f"{float(row['maximum_link_utilization']):.3f}",
            f"{float(row['mean_link_flit_gini']):.3f}",
            (
                f"{float(row['cycle_scaling_exponent']):.3f}"
                if row["cycle_scaling_exponent"] else "n/a"
            ),
        ] for row in traffic_families],
    ))
    sections.extend(["", "## Traffic-pattern classification", ""])
    sections.append(markdown_table(
        [
            "dataset", "class", "cross jobs", "partial flits", "max link util",
            "link Gini", "node-load CV",
        ], [[
            row["dataset"], row["traffic_class"],
            f"{float(row['cross_job_fraction']):.3f}",
            f"{float(row['partial_flit_fraction']):.3f}",
            f"{float(row['maximum_link_utilization']):.3f}",
            f"{float(row['link_flit_gini']):.3f}",
            f"{float(row['node_job_cv']):.3f}",
        ] for row in traffic],
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(sections) + "\n")
    machine = {
        "manifest": manifest,
        "exact_calibration": calibration,
        "exact_mapping": exact_mapping,
        "exact_interconnect": exact_interconnect,
        "mapping": mapping,
        "owner_policy": owner,
        "traffic_patterns": traffic,
        "traffic_family_summary": traffic_families,
        "dram_patterns": dram,
    }
    machine_path = output.with_name("campaign_summary.json")
    machine_path.write_text(json.dumps(machine, indent=2, sort_keys=True) + "\n")
    print(f"markdown={output} json={machine_path}")


if __name__ == "__main__":
    main()
