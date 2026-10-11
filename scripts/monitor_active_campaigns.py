#!/usr/bin/env python3
"""Persistently audit long Azilla campaigns and record actionable failures."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs/active_campaign_watchdog.json"
EVENTS = ROOT / "logs/active_campaign_watchdog.log"


def session(name):
    return subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def processes(pattern):
    result = subprocess.run(
        ["pgrep", "-af", pattern], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return [line for line in result.stdout.splitlines()
            if "monitor_active_campaigns.py" not in line]


def csv_rows(path):
    try:
        with path.open(newline="") as source:
            return len(list(csv.DictReader(source)))
    except (FileNotFoundError, OSError, csv.Error):
        return 0


def log(message):
    stamp = datetime.now(timezone.utc).isoformat()
    with EVENTS.open("a") as output:
        output.write(f"{stamp} {message}\n")


def main():
    interval = int(os.environ.get("AZILLA_MONITOR_INTERVAL", "60"))
    previous_alerts = set()
    while True:
        subset_rows = csv_rows(
            ROOT / "results/paper_benchmarks/dynamic_hybrid_subset/summary.csv"
        )
        full_rows = csv_rows(
            ROOT / "results/paper_benchmarks/dynamic_hybrid/summary.csv"
        )
        batch_tuning_rows = csv_rows(
            ROOT / "results/paper_benchmarks/batch_hybrid_tuning/summary.csv"
        )
        batch_heldout_rows = csv_rows(
            ROOT / "results/paper_benchmarks/batch_hybrid_heldout/summary.csv"
        )
        sparse_geometry_rows = csv_rows(
            ROOT / "results/paper_benchmarks/sparse_geometry/summary.csv"
        )
        plot_count = len(list(
            (ROOT / "plots/paper/dynamic_hybrid").glob("dynamic_*.pdf")
        ))
        exact_output = (
            ROOT / "results/cir_evaluation/exact_sparse/random_d25_n65536/"
                   "cir/metrics_summary.json"
        ).is_file()
        vcs_time = (
            ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m/"
                   "sim_262144_2x2_h08_c256_e4-2-4_f4_s0_r1_i1_t1.time"
        )
        vcs_complete = vcs_time.is_file() and vcs_time.stat().st_size > 0
        vcs_scale_processes = processes(
            "simv.*dense_(262144|524288|1048576)_header"
        )
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dynamic_subset": {
                "session": session("dynamic_hybrid_subset_20260902"),
                "rows": subset_rows, "expected_rows": 45,
            },
            "dynamic_full": {
                "session": session("dynamic_hybrid_full_20260902"),
                "rows": full_rows, "expected_rows": 120,
            },
            "dynamic_plots": {
                "session": session("dynamic_hybrid_plots_20260902"),
                "pdfs": plot_count, "expected_pdfs": 2,
            },
            "batch_hybrid": {
                "session": session("batch_hybrid_pipeline_20260902"),
                "tuning_rows": batch_tuning_rows,
                "expected_tuning_rows": 108,
                "heldout_rows": batch_heldout_rows,
                "expected_heldout_rows": 18,
                "frozen_config": (
                    ROOT / "results/paper_benchmarks/"
                           "batch_hybrid_frozen_config.json"
                ).is_file(),
            },
            "sparse_geometry": {
                "session": session("sparse_geometry_sweep_20260903"),
                "rows": sparse_geometry_rows,
                "expected_rows": 25,
            },
            "exact_64k": {
                "processes": processes("simulate-exact-events.*random_d25_n65536"),
                "complete": exact_output,
            },
            "vcs_256k": {
                "processes": processes("simv.*dense_262144_header"),
                "complete": vcs_complete,
            },
            "vcs_scale": {
                "processes": vcs_scale_processes,
                "completed_sizes": [size for size in (262144, 524288, 1048576)
                    if (ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m" /
                        f"sim_{size}_{'2x2' if size == 262144 else '4x2' if size == 524288 else '4x4'}_h08_c256_e4-2-4_f4_s0_r1_i1_t1.time").is_file()
                    and (ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m" /
                        f"sim_{size}_{'2x2' if size == 262144 else '4x2' if size == 524288 else '4x4'}_h08_c256_e4-2-4_f4_s0_r1_i1_t1.time").stat().st_size > 0],
            },
        }
        alerts = set()
        subset = state["dynamic_subset"]
        full = state["dynamic_full"]
        plots = state["dynamic_plots"]
        batch = state["batch_hybrid"]
        sparse_geometry = state["sparse_geometry"]
        if not subset["session"] and subset["rows"] != subset["expected_rows"]:
            alerts.add(f"dynamic subset stopped at {subset['rows']}/45 rows")
        if (not full["session"] and subset["rows"] == 45
                and full["rows"] != full["expected_rows"]):
            alerts.add(f"dynamic full sweep stopped at {full['rows']}/120 rows")
        if (not plots["session"] and full["rows"] == 120
                and plots["pdfs"] < plots["expected_pdfs"]):
            alerts.add("dynamic plotting stopped before expected figures")
        if (not batch["session"] and
                (batch["tuning_rows"] != batch["expected_tuning_rows"] or
                 batch["heldout_rows"] != batch["expected_heldout_rows"] or
                 not batch["frozen_config"])):
            alerts.add(
                "batch hybrid pipeline stopped before tuning/freeze/heldout completion"
            )
        if (not sparse_geometry["session"] and
                sparse_geometry["rows"] != sparse_geometry["expected_rows"]):
            alerts.add(
                f"sparse geometry sweep stopped at {sparse_geometry['rows']}/25 rows"
            )
        if not state["exact_64k"]["complete"] and not state["exact_64k"]["processes"]:
            alerts.add("64K exact run exited without metrics summary")
        if not state["vcs_256k"]["complete"] and not state["vcs_256k"]["processes"]:
            alerts.add("256K VCS run exited without time summary")
        state["alerts"] = sorted(alerts)
        temporary = STATUS.with_suffix(".json.part")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(STATUS)
        for alert in sorted(alerts - previous_alerts):
            log("ALERT " + alert)
        for resolved in sorted(previous_alerts - alerts):
            log("RESOLVED " + resolved)
        previous_alerts = alerts
        time.sleep(interval)


if __name__ == "__main__":
    main()
