#!/usr/bin/env python3
"""Strict RTL differential for the accelerated Ramulator event model."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


def rtl_transfers(path: Path):
    transfers = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["event"] != "accept":
                continue
            transfers.append((
                int(row["cycle"]), row["scope"], int(row["node"]),
                row["direction"], int(row["packet_type"]),
                int(row["source_id"]), int(row["epoch"]),
                int(row["block_id"]), int(row["dest_x"]),
                int(row["dest_y"]), int(row["last"]),
            ))
    return sorted(transfers)


def check_case(
    *,
    name: str,
    geometry: Geometry,
    dataset_path: Path,
    trace_path: Path,
    expected_timing: tuple[int, int, int],
    expected_noc: tuple[int, int, int, int, int, int],
    h0_mvms: int,
    h1_mvms: int,
    cross_mvms: int,
    library: Path,
    ramulator_config: Path,
) -> None:
    dataset = BlockOccupancyDataset.load(dataset_path)
    model = RamulatorEventPerformanceModel(
        geometry,
        dataset,
        dataset_path=str(dataset_path),
        ramulator_library=str(library),
        ramulator_config=str(ramulator_config),
        idle_tick_modulus=7600,
        config=PerformanceConfig(
            h0_mvm_count=h0_mvms,
            h1_mvm_count=h1_mvms,
            cross_mvm_count=cross_mvms,
            timing_only=True,
            max_cycles=25_000,
        ),
    )
    try:
        result = model.run()
    finally:
        model.close()

    timing = (
        result.initialization_cycles,
        result.iteration_cycles,
        result.total_cycles,
    )
    counters = result.counters
    noc = (
        counters.injected_flits,
        counters.ejected_flits,
        counters.physical_link_flits,
        counters.injection_stalls,
        counters.ejection_stalls,
        counters.link_stalls,
    )
    expected_transfers = rtl_transfers(trace_path)
    observed_transfers = sorted(model.transfers)
    if timing != expected_timing:
        raise AssertionError(
            f"{name} timing mismatch expected={expected_timing} observed={timing}"
        )
    if noc != expected_noc:
        raise AssertionError(
            f"{name} NoC mismatch expected={expected_noc} observed={noc}"
        )
    if expected_transfers != observed_transfers:
        mismatch = next(
            (index for index, pair in enumerate(zip(
                expected_transfers, observed_transfers
            )) if pair[0] != pair[1]),
            min(len(expected_transfers), len(observed_transfers)),
        )
        raise AssertionError(
            f"{name} transfer mismatch at {mismatch}: "
            f"RTL={expected_transfers[mismatch:mismatch + 1]} "
            f"model={observed_transfers[mismatch:mismatch + 1]}"
        )
    if result.accuracy != "rtl-differential":
        raise AssertionError(f"{name} has unexpected label {result.accuracy}")
    print(
        f"PASS exact-events {name} timing={timing} noc={noc} "
        f"transfers={len(observed_transfers)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ramulator-library",
        default="build/cycle_model_ramulator/libazilla_ramulator.so",
    )
    parser.add_argument(
        "--ramulator-config", default="tb/ramulator_128x32.yaml"
    )
    args = parser.parse_args()
    library = ROOT / args.ramulator_library
    config = ROOT / args.ramulator_config

    check_case(
        name="g256",
        geometry=Geometry(2, 1, 2, 2),
        dataset_path=ROOT / "tb/datasets/g256_smoke.txt",
        trace_path=ROOT / "build/trace256_rtl_events.csv",
        expected_timing=(267, 179, 446),
        expected_noc=(22, 22, 10, 1, 3, 0),
        h0_mvms=1,
        h1_mvms=1,
        cross_mvms=1,
        library=library,
        ramulator_config=config,
    )
    check_case(
        name="g16384",
        geometry=Geometry(4, 4, 2, 16),
        dataset_path=ROOT / "tb/datasets/g16384_kings.txt",
        trace_path=ROOT / "build/full_cycle_model_16k_rtl_events.csv",
        expected_timing=(16_899, 1_821, 18_720),
        expected_noc=(1_336, 1_336, 2_200, 122, 131, 215),
        h0_mvms=1,
        h1_mvms=1,
        cross_mvms=16,
        library=library,
        ramulator_config=config,
    )


if __name__ == "__main__":
    main()
