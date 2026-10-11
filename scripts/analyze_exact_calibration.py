#!/usr/bin/env python3
"""Compare calibrated-event results with Ramulator exact-event confirmations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def dataset_from_event(path: Path) -> str:
    return path.parent.parent.name


def dataset_from_exact(path: Path) -> str:
    return path.parent.name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("results/paper_benchmarks")
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("results/paper_benchmarks/exact_calibration.csv"),
    )
    args = parser.parse_args()
    event = {}
    for path in args.root.glob("event_sweep*/**/*_event_summary.json"):
        if path.parent.name != "identity":
            continue
        event[dataset_from_event(path)] = json.loads(path.read_text())
    exact = {
        dataset_from_exact(path): json.loads(path.read_text())
        for path in (args.root / "exact_confirmation").glob("*/*_summary.json")
    }
    rows = []
    for dataset in sorted(exact.keys() & event.keys()):
        calibrated = event[dataset]
        reference = exact[dataset]
        cal_cycles = calibrated["timing_cycles"]["iteration"]
        exact_cycles = reference["timing_cycles"]["iteration"]
        cal_noc = calibrated["noc"]
        exact_noc = reference["noc"]
        dram = reference["dram_systems"]
        accepted = sum(row["accepted_requests"] for row in dram)
        weighted_latency = sum(
            row["average_latency_cycles"] * row["accepted_requests"]
            for row in dram
        )
        rows.append({
            "dataset": dataset,
            "spins": reference["geometry"]["spin_count"],
            "calibrated_cycles": cal_cycles,
            "exact_cycles": exact_cycles,
            "calibrated_over_exact": cal_cycles / exact_cycles,
            "absolute_percentage_error": abs(cal_cycles - exact_cycles) / exact_cycles,
            "calibrated_injected_flits": cal_noc["injected_flits"],
            "exact_injected_flits": exact_noc["injected_flits"],
            "injected_flit_difference": (
                cal_noc["injected_flits"] - exact_noc["injected_flits"]
            ),
            "calibrated_link_flits": cal_noc["physical_link_flits"],
            "exact_link_flits": exact_noc["physical_link_flits"],
            "link_flit_difference": (
                cal_noc["physical_link_flits"]
                - exact_noc["physical_link_flits"]
            ),
            "calibrated_average_hops": cal_noc["average_hops"],
            "exact_average_hops": exact_noc["average_hops"],
            "hop_histogram_equal": (
                cal_noc["hop_histogram"] == exact_noc["hop_histogram"]
            ),
            "calibrated_injection_stalls": cal_noc["injection_stalls"],
            "exact_injection_stalls": exact_noc["injection_stalls"],
            "calibrated_link_stalls": cal_noc["link_stalls"],
            "exact_link_stalls": exact_noc["link_stalls"],
            "dram_requests": accepted,
            "request_weighted_average_dram_latency_cycles": (
                weighted_latency / accepted if accepted else 0.0
            ),
            "maximum_dram_latency_cycles": max(
                (row["maximum_latency_cycles"] for row in dram), default=0.0
            ),
            "exact_accuracy_label": reference["accuracy"],
            "calibrated_accuracy_label": calibrated["accuracy"],
        })
    if not rows:
        raise SystemExit("no matching event and exact summaries found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "points": len(rows),
        "mean_absolute_percentage_error": sum(
            row["absolute_percentage_error"] for row in rows
        ) / len(rows),
        "maximum_absolute_percentage_error": max(
            row["absolute_percentage_error"] for row in rows
        ),
        "traffic_exact_for_all_points": all(
            row["injected_flit_difference"] == 0
            and row["link_flit_difference"] == 0
            and row["hop_histogram_equal"]
            for row in rows
        ),
        "rows": rows,
    }
    summary_path = args.output.with_suffix(".json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"csv={args.output} json={summary_path} points={len(rows)}")


if __name__ == "__main__":
    main()
