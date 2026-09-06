#!/usr/bin/env python3
"""Fail-closed manifest for the VCS-only representative cores-only envelope."""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def metric(path: Path) -> tuple[str, str]:
    text = path.read_text()
    dram = re.search(r"DRAM\[0\] accepted=(\d+) rejected=(\d+) completed=(\d+) "
                     r"avg_latency_ticks=([0-9.]+) max_latency_ticks=(\d+)", text)
    perf = re.search(r"PERF_CSV,.*work_blocks=(\d+),cycles=(\d+),.*output_flits=(\d+),"
                     r"output_stall_cycles=(\d+)", text)
    if dram is None or perf is None:
        raise AssertionError(f"missing VCS Ramulator metrics: {path}")
    if tuple(map(int, dram.groups()[:3])) != (2048, 0, 2048):
        raise AssertionError(f"unexpected Ramulator request totals: {dram.groups()}")
    if tuple(map(int, perf.groups())) != (64, 885, 512, 402):
        raise AssertionError(f"unexpected endpoint timing: {perf.groups()}")
    return dram.group(0), perf.group(0)


def displayed_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path,
                        default=Path("results/cores_only_representative_validation"))
    parser.add_argument("--capacity-root", type=Path,
                        default=Path("results/core_only_rtl_scale_check"))
    args = parser.parse_args()
    base = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    capacity_root = (args.capacity_root if args.capacity_root.is_absolute()
                     else ROOT / args.capacity_root)
    if (base / "prechecks_vcs.status").read_text().strip() != "EXIT_CODE=0":
        raise AssertionError("VCS prechecks did not pass")
    router_csv = base / "vcs_router_core/results.csv"
    if len(router_csv.read_text().splitlines()) != 10:
        raise AssertionError("representative router/core matrix is incomplete")
    first = metric(base / "vcs_ramulator/contention.log")
    second = metric(base / "vcs_ramulator/contention_repeat.log")
    if first != second:
        raise AssertionError("live Ramulator VCS replay is nondeterministic")
    capacities = [16384, 65536, 131072, 262144]
    for spins in capacities:
        if not (capacity_root / str(spins) / "PASS").is_file():
            raise AssertionError(f"missing VCS RTL capacity check: {spins}")
    vcs = shutil.which("vcs")
    if vcs is None and os.environ.get("VCS_HOME"):
        candidate = Path(os.environ["VCS_HOME"]) / "bin/vcs"
        vcs = str(candidate) if candidate.is_file() else None
    vcs_version = [] if vcs is None else subprocess.run(
        [vcs, "-ID"], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    ).stdout.strip().splitlines()
    manifest = {
        "schema": "azilla-cores-only-representative-vcs-validation-v1",
        "simulator": vcs_version[0] if vcs_version else "Synopsys VCS",
        "accuracy_label": "rtl-differential-representative-envelope",
        "scope": {
            "integrated_router_core": "2 top-level router endpoints, 64 spins, 3 seeds, 3 backpressure levels",
            "directed_compute_latency_cycles": 67,
            "live_ramulator_endpoint": "4 streams, 64 blocks, 2048 accepted/completed requests",
            "capacity_elaboration_spins": capacities,
        },
        "evidence": {
            "router_core_matrix": displayed_path(router_csv),
            "ramulator_runs": [
                displayed_path(base / "vcs_ramulator/contention.log"),
                displayed_path(base / "vcs_ramulator/contention_repeat.log"),
            ],
        },
        "limitations": [
            "Ramulator endpoint and integrated router/core datapath are composed from separate VCS subchecks.",
            "Configurations above the representative envelope are simulator projections, not direct RTL differentials.",
            "Capacity checks do not establish large-geometry cycle accuracy.",
        ],
    }
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (base / "PASS").touch()
    print(f"PASS cores-only representative VCS validation: {base/'manifest.json'}")


if __name__ == "__main__":
    main()
