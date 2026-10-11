#!/usr/bin/env python3
"""Summarize exact/Ramulator interconnect sensitivity runs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def row_from_summary(dataset: str, variant: str, summary: dict) -> dict:
    noc = summary["noc"]
    interconnect = summary["interconnect"]
    return {
        "dataset": dataset,
        "variant": variant,
        "spins": summary["geometry"]["spin_count"],
        "iteration_cycles": summary["timing_cycles"]["iteration"],
        "link_latency_cycles": interconnect["forward_latency_cycles"],
        "link_flit_interval_cycles": interconnect["flit_interval_cycles"],
        "link_max_inflight_flits": interconnect["max_inflight_flits"],
        "link_credit_return_cycles": interconnect[
            "credit_return_latency_cycles"
        ],
        "chiplet_link_latency_cycles": summary["package"][
            "chiplet_link_latency_cycles"
        ],
        "injected_flits": noc["injected_flits"],
        "physical_link_flits": noc["physical_link_flits"],
        "injection_stalls": noc["injection_stalls"],
        "link_stalls": noc["link_stalls"],
        "accuracy": summary["accuracy"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path,
        default=Path("results/paper_benchmarks/exact_interconnect_sensitivity"),
    )
    parser.add_argument(
        "--baseline", type=Path,
        default=Path(
            "results/paper_benchmarks/exact_confirmation/gset_g70/identity_summary.json"
        ),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path(
            "results/paper_benchmarks/exact_interconnect_sensitivity.csv"
        ),
    )
    args = parser.parse_args()
    baseline_summary = json.loads(args.baseline.read_text())
    dataset = args.baseline.parent.name
    rows = [row_from_summary(dataset, "rtl_direct", baseline_summary)]
    for path in sorted(args.root.glob("*/*/*_summary.json")):
        rows.append(row_from_summary(
            path.parents[1].name, path.parent.name, json.loads(path.read_text())
        ))
    baseline_cycles = rows[0]["iteration_cycles"]
    for row in rows:
        row["slowdown_vs_rtl_direct"] = row["iteration_cycles"] / baseline_cycles
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.output.with_suffix(".json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    print(f"results={args.output} points={len(rows)}")


if __name__ == "__main__":
    main()
