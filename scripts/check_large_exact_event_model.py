#!/usr/bin/env python3
"""Compare large exact-event runs with completed VCS timing-only RTL runs.

This is a resumable aggregate differential.  The historical large VCS runs
saved their logs and aggregate NoC CSVs, but not NOC_EVENT_FILE traces.  The
check therefore compares phase/total timing and aggregate accepted/stalled
flits by scope; it deliberately does not confer the ``rtl-differential``
accuracy label, which requires the strict per-transfer checks used at 16K.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


RTL_ROOT = ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m"
DATASET_ROOT = ROOT / "results/dense_config_search/rtl_datasets"

CASES = {
    65536: (Geometry(1, 1, 8, 256), "65536_1x1_h08_c256_e4-2-4_f4_s0_r1_i1_t1"),
    131072: (Geometry(2, 1, 8, 256), "131072_2x1_h08_c256_e4-2-4_f4_s0_r1_i1_t1"),
    262144: (Geometry(2, 2, 8, 256), "262144_2x2_h08_c256_e4-2-4_f4_s0_r1_i1_t1"),
}


def parse_rtl_log(path: Path) -> dict[str, int]:
    text = path.read_text()
    patterns = {
        "initialization_cycles": r"initialization complete at cycle (\d+)",
        "publication_drained_cycle": r"state publication drained at cycle (\d+)",
        "schedule_issued_cycle": r"schedule issued at cycle (\d+)",
        "compute_done_cycle": r"cross compute done at cycle (\d+)",
        "total_cycles": r"PASS: 1 iteration\(s\).*total_cycles=(\d+)",
    }
    result = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise RuntimeError(f"missing {name} in {path}")
        result[name] = int(match.group(1))
    result["iteration_cycles"] = (
        result["total_cycles"] - result["initialization_cycles"]
    )
    return result


def parse_rtl_noc(path: Path) -> dict[str, int]:
    totals = {
        "injected_flits": 0, "ejected_flits": 0,
        "physical_link_flits": 0, "injection_stalls": 0,
        "ejection_stalls": 0, "link_stalls": 0,
    }
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            scope = row["scope"]
            if scope == "inject":
                totals["injected_flits"] += int(row["accepted_flits"])
                totals["injection_stalls"] += int(row["stall_cycles"])
            elif scope == "eject":
                totals["ejected_flits"] += int(row["accepted_flits"])
                totals["ejection_stalls"] += int(row["stall_cycles"])
            elif scope == "link":
                totals["physical_link_flits"] += int(row["accepted_flits"])
                totals["link_stalls"] += int(row["stall_cycles"])
    return totals


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=list(CASES))
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "results/large_exact_event_differential",
    )
    parser.add_argument(
        "--ramulator-library", type=Path,
        default=ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so",
    )
    parser.add_argument(
        "--ramulator-config", type=Path,
        default=ROOT / "tb/ramulator_128x32.yaml",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for spins in args.sizes:
        if spins not in CASES:
            raise SystemExit(f"no completed RTL reference registered for {spins}")
        output = args.output_dir / str(spins) / "comparison.json"
        if output.exists() and not args.force:
            prior = json.loads(output.read_text())
            if prior.get("status") == "pass":
                print(f"SKIP {spins}: completed aggregate differential")
                continue

        geometry, label = CASES[spins]
        rtl_log = RTL_ROOT / f"sim_{label}.log"
        rtl_noc_path = RTL_ROOT / f"noc_stats_{label}.csv"
        dataset_path = DATASET_ROOT / f"dense_{spins}_header.txt"
        for required in (rtl_log, rtl_noc_path, dataset_path,
                         args.ramulator_library, args.ramulator_config):
            if not required.is_file():
                raise FileNotFoundError(required)

        rtl_timing = parse_rtl_log(rtl_log)
        rtl_noc = parse_rtl_noc(rtl_noc_path)
        dataset = BlockOccupancyDataset.load(dataset_path)
        started = time.monotonic()
        model = RamulatorEventPerformanceModel(
            geometry, dataset, dataset_path=str(dataset_path),
            ramulator_library=str(args.ramulator_library),
            ramulator_config=str(args.ramulator_config),
            mem_lanes=16, ticks_per_cycle=40, idle_tick_modulus=7600,
            config=PerformanceConfig(
                h0_mvm_count=4, h1_mvm_count=2, cross_mvm_count=4,
                fifo_depth=4, timing_only=True, max_cycles=1_000_000_000,
            ),
        )
        try:
            # The RTL references were compiled with SKIP_ZERO_BLOCKS=0.
            result = model.run(sparse=False)
        finally:
            model.close()

        counters = result.counters
        observed_timing = {
            "initialization_cycles": result.initialization_cycles,
            "iteration_cycles": result.iteration_cycles,
            "total_cycles": result.total_cycles,
        }
        observed_noc = {
            "injected_flits": counters.injected_flits,
            "ejected_flits": counters.ejected_flits,
            "physical_link_flits": counters.physical_link_flits,
            "injection_stalls": counters.injection_stalls,
            "ejection_stalls": counters.ejection_stalls,
            "link_stalls": counters.link_stalls,
        }
        expected_timing = {key: rtl_timing[key] for key in observed_timing}
        timing_match = expected_timing == observed_timing
        noc_match = rtl_noc == observed_noc
        record = {
            "status": "pass" if timing_match and noc_match else "fail",
            "scope": "aggregate-large-geometry-differential",
            "accuracy_label_retained": "cycle-structured-unverified",
            "limitation": (
                "Historical VCS run has aggregate NoC statistics but no "
                "per-transfer event trace; this is not a strict trace differential."
            ),
            "spins": spins,
            "geometry": {
                "mesh_x": geometry.mesh_x, "mesh_y": geometry.mesh_y,
                "h0_per_h1": geometry.h0_per_h1,
                "cores_per_h0": geometry.cores_per_h0,
            },
            "dense_off_diagonal": True,
            "rtl": {"timing": rtl_timing, "noc": rtl_noc,
                    "log": str(rtl_log), "noc_csv": str(rtl_noc_path)},
            "exact_event": {"timing": observed_timing, "noc": observed_noc,
                            "reported_accuracy": result.accuracy},
            "timing_match": timing_match, "noc_match": noc_match,
            "wall_seconds": time.monotonic() - started,
        }
        write_json(output, record)
        print(f"{record['status'].upper()} {spins} timing_match={timing_match} "
              f"noc_match={noc_match} output={output}", flush=True)
        if record["status"] != "pass":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
