#!/usr/bin/env python3
"""Fail-closed CIR exact-event differential against one fresh VCS trace.

This compares phase timing, every accepted NoC transfer, and aggregate stalls.
It is evidence for the supplied geometry, not a broad validation certificate.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry
from check_exact_event_model import rtl_transfers
from check_16k_cycle_model import rtl_noc_totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--rtl-log", required=True, type=Path)
    parser.add_argument("--rtl-events", required=True, type=Path)
    parser.add_argument("--rtl-noc", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mesh-x", type=int, default=4)
    parser.add_argument("--mesh-y", type=int, default=4)
    parser.add_argument("--h0-per-h1", type=int, default=2)
    parser.add_argument("--cores-per-h0", type=int, default=16)
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--library", type=Path,
                        default=Path("build/cycle_model_ramulator/libazilla_ramulator.so"))
    parser.add_argument("--config", type=Path, default=Path("tb/ramulator_128x32.yaml"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite existing evidence")
    source_paths = sorted((ROOT / "model/azilla_cycle_model").glob("*.py"))
    source_paths += [ROOT / "tb/ising_mesh_tb.sv", ROOT / "tb/ramulator_node_frontend.sv",
                     ROOT / "tb/ramulator_dpi_bridge.sv", ROOT / "tb/ramulator_dpi.cpp"]
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in source_paths}
    log = args.rtl_log.read_text()
    init = re.search(r"initialization complete at cycle (\d+)", log)
    total = re.search(r"total_cycles=(\d+)", log)
    if not init or not total or "VCS" not in log:
        parser.error("missing VCS phase timing in log")
    expected_timing = (int(init[1]), int(total[1]) - int(init[1]), int(total[1]))
    geometry = Geometry(args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0)
    model = RamulatorEventPerformanceModel(
        geometry, BlockOccupancyDataset.load(args.dataset),
        dataset_path=str(args.dataset), ramulator_library=str(args.library),
        ramulator_config=str(args.config),
        config=PerformanceConfig(h0_mvm_count=args.h0_mvms,
                                 h1_mvm_count=args.h1_mvms,
                                 cross_mvm_count=args.cross_mvms, timing_only=True))
    try:
        result = model.run()
    finally:
        model.close()
    observed = sorted(model.transfers)
    expected = rtl_transfers(args.rtl_events)
    timing = (result.initialization_cycles, result.iteration_cycles, result.total_cycles)
    c = result.counters
    noc = (c.injected_flits, c.ejected_flits, c.physical_link_flits,
           c.injection_stalls, c.ejection_stalls, c.link_stalls)
    expected_noc = rtl_noc_totals(args.rtl_noc)
    checks = dict(timing=timing == expected_timing,
                  transfers=observed == expected, noc=noc == expected_noc,
                  sources_unchanged=all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
                                        for p, h in source_hashes.items()))
    evidence = dict(status="pass" if all(checks.values()) else "fail", checks=checks,
                    simulator="vcs", execution_mode="cir", source_sha256=source_hashes,
                    geometry=dict(mesh_x=args.mesh_x, mesh_y=args.mesh_y,
                                  h0_per_h1=args.h0_per_h1, cores_per_h0=args.cores_per_h0),
                    mvms=dict(h0=args.h0_mvms, h1=args.h1_mvms, cross=args.cross_mvms),
                    scope="CIR timing-only; supplied geometry and dataset only",
                    expected_timing=expected_timing, observed_timing=timing,
                    expected_noc=expected_noc, observed_noc=noc,
                    expected_transfers=len(expected), observed_transfers=len(observed),
                    command=sys.argv, sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (args.dataset, args.rtl_log, args.rtl_events, args.rtl_noc,
                              args.library, args.config)})
    if not checks["transfers"]:
        i = next((i for i, (a, b) in enumerate(zip(expected, observed)) if a != b),
                 min(len(expected), len(observed)))
        evidence["first_difference"] = dict(index=i, rtl=expected[i:i+1], model=observed[i:i+1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2), flush=True)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
