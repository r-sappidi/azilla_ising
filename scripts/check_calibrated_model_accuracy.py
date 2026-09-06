#!/usr/bin/env python3
"""Compare queued calibrated timing with completed exact-event finalists."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


TRAINING = {
    ("uniform_d16_n65536_s1", "cores-only"),
    ("toroidal_d4_n131072", "cores-only"),
    ("toroidal_d4_n65536", "cir"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrated", type=Path, action="append", required=True)
    parser.add_argument("--exact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--heldout-mean-limit", type=float, default=10.0)
    parser.add_argument("--heldout-max-limit", type=float, default=20.0)
    args = parser.parse_args()

    calibrated = {}
    for summary in args.calibrated:
        for row in csv.DictReader(summary.open()):
            key = (row["dataset"], row["execution_mode"])
            calibrated[key] = int(row["iteration_cycles"])

    rows = []
    for path in args.exact_root.glob("*/*/metrics_summary.json"):
        data = json.loads(path.read_text())
        key = (path.parents[1].name, path.parent.name)
        if key not in calibrated:
            continue
        exact = int(data["timing_cycles"]["iteration"])
        estimate = calibrated[key]
        signed = 100.0 * (estimate - exact) / exact
        rows.append({
            "dataset": key[0], "mode": key[1],
            "split": "training" if key in TRAINING else "held-out",
            "calibrated_cycles": estimate, "exact_cycles": exact,
            "signed_error_percent": signed,
            "absolute_error_percent": abs(signed),
        })
    if not rows:
        raise SystemExit("no matched calibrated/exact points")

    heldout = [row["absolute_error_percent"] for row in rows
               if row["split"] == "held-out"]
    if not heldout:
        raise SystemExit("no held-out points")
    summary = {
        "schema": "azilla-queued-calibration-check-v1",
        "profile": "rtl-vcs-ramulator-128x32-queued-v2",
        "matched_points": len(rows),
        "training_points": sum(row["split"] == "training" for row in rows),
        "heldout_points": len(heldout),
        "all_mean_absolute_error_percent": statistics.mean(
            row["absolute_error_percent"] for row in rows
        ),
        "all_median_absolute_error_percent": statistics.median(
            row["absolute_error_percent"] for row in rows
        ),
        "all_max_absolute_error_percent": max(
            row["absolute_error_percent"] for row in rows
        ),
        "heldout_mean_absolute_error_percent": statistics.mean(heldout),
        "heldout_median_absolute_error_percent": statistics.median(heldout),
        "heldout_max_absolute_error_percent": max(heldout),
        "acceptance_limits_percent": {
            "heldout_mean": args.heldout_mean_limit,
            "heldout_max": args.heldout_max_limit,
        },
    }
    summary["pass"] = (
        summary["heldout_mean_absolute_error_percent"]
        <= args.heldout_mean_limit
        and summary["heldout_max_absolute_error_percent"]
        <= args.heldout_max_limit
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "points": rows},
                                      indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not summary["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
