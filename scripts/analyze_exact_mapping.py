#!/usr/bin/env python3
"""Summarize exact/Ramulator mapping confirmation runs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path,
        default=Path("results/paper_benchmarks/exact_mapping_confirmation"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("results/paper_benchmarks/exact_mapping_confirmation.csv"),
    )
    args = parser.parse_args()
    rows = []
    for path in sorted(args.root.glob("*/*/*_summary.json")):
        summary = json.loads(path.read_text())
        noc = summary["noc"]
        jobs = summary["scheduled_jobs"]
        rows.append({
            "dataset": path.parents[1].name,
            "mapping": path.parent.name,
            "spins": summary["geometry"]["spin_count"],
            "iteration_cycles": summary["timing_cycles"]["iteration"],
            "total_cycles": summary["timing_cycles"]["total"],
            "scheduled_h0": jobs["h0"],
            "scheduled_h1": jobs["h1"],
            "scheduled_cross": jobs["cross"],
            "scheduled_total": jobs["total"],
            "injected_flits": noc["injected_flits"],
            "physical_link_flits": noc["physical_link_flits"],
            "injection_stalls": noc["injection_stalls"],
            "link_stalls": noc["link_stalls"],
            "average_hops": noc["average_hops"],
            "accuracy": summary["accuracy"],
        })
    if not rows:
        raise SystemExit(f"no exact mapping summaries below {args.root}")
    for dataset in {row["dataset"] for row in rows}:
        selected = [row for row in rows if row["dataset"] == dataset]
        random_cycles = next(
            row["iteration_cycles"] for row in selected
            if row["mapping"] == "random"
        )
        identity_cycles = next(
            row["iteration_cycles"] for row in selected
            if row["mapping"] == "identity_optimized_owner"
        )
        for row in selected:
            row["speedup_vs_random"] = random_cycles / row["iteration_cycles"]
            row["speedup_vs_identity"] = identity_cycles / row["iteration_cycles"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.output.with_suffix(".json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    print(f"results={args.output}")


if __name__ == "__main__":
    main()
