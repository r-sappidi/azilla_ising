#!/usr/bin/env python3
"""Differentially check the 16K Ramulator configuration against RTL.

The check compares functional/timing summaries and every accepted NoC
transfer (injection, physical hop, and ejection), including its cycle and
packet metadata.  It deliberately ignores the RTL trace's stall-transition
records; total stall-cycle counters are compared separately.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.adapters import NOC_PARTIAL, NOC_STATE, RoutedPartial
from azilla_cycle_model.noc import EAST, LOCAL, NORTH, SOUTH, WEST, Flit
from azilla_cycle_model.performance import PerformanceConfig, RamulatorPerformanceModel
from azilla_cycle_model.scheduler import compile_schedule
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock


Transfer = tuple[int, str, int, str, int, int, int, int, int, int, int]
PORT_NAMES = {NORTH: "north", SOUTH: "south", EAST: "east", WEST: "west"}


class TracedRamulatorModel(RamulatorPerformanceModel):
    """Ramulator model with a passive pre-edge NoC transfer observer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transfers: list[Transfer] = []

    @staticmethod
    def _transfer(cycle: int, scope: str, node: int, direction: str,
                  flit: Flit) -> Transfer:
        return (
            cycle, scope, node, direction, flit.packet_type, flit.source_id,
            flit.epoch, flit.block_id, flit.dest_x, flit.dest_y, int(flit.last),
        )

    def _record_noc(self, h1_inputs, cross_inputs, state_publications,
                    done_destinations) -> None:
        cycle = self.counters.cycles
        comb = [router.outputs() for router in self.system.mesh.routers]
        epochs = [cross_inputs[node].get("epoch", 0)
                  for node in range(self.geometry.node_count)]

        for node, output in enumerate(comb):
            rx = output.output_flits[LOCAL]
            if rx is not None:
                provisional = RoutedPartial(
                    valid=(rx.packet_type == NOC_PARTIAL and
                           rx.epoch == epochs[node]),
                    data=rx.data, block_id=rx.block_id,
                )
                tile = self.system.h1_tiles[node].outputs(provisional)
                adapter = self.system.noc_adapters[node].outputs(
                    current_epoch=epochs[node], tx_ready=False,
                    state_publish=state_publications[node],
                    done_destination=done_destinations[node], rx=rx,
                    parent_ready=tile.parent_partial_ready,
                )
                ready = (
                    self.system.cross_nodes[node].outputs(
                        epochs[node]
                    ).state_ready
                    if rx.packet_type == NOC_STATE else adapter.rx_ready
                )
                if ready:
                    self.transfers.append(
                        self._transfer(cycle, "eject", node, "local", rx)
                    )

            router = self.system.mesh.routers[node]
            for port, nx, ny, neighbor_input in (
                (NORTH, router.x, router.y - 1, SOUTH),
                (SOUTH, router.x, router.y + 1, NORTH),
                (EAST, router.x + 1, router.y, WEST),
                (WEST, router.x - 1, router.y, EAST),
            ):
                flit = output.output_flits[port]
                if flit is None:
                    continue
                ready = False
                if 0 <= nx < self.geometry.mesh_x and 0 <= ny < self.geometry.mesh_y:
                    neighbor = ny * self.geometry.mesh_x + nx
                    ready = comb[neighbor].input_ready[neighbor_input]
                if ready:
                    self.transfers.append(
                        self._transfer(cycle, "link", node, PORT_NAMES[port], flit)
                    )

        for node in range(self.geometry.node_count):
            cross_tx = self.system.cross_nodes[node].outputs(epochs[node]).tx
            adapter_tx = self.system.noc_adapters[node].outputs(
                current_epoch=epochs[node], tx_ready=False,
                state_publish=state_publications[node],
                done_destination=done_destinations[node], rx=None,
                parent_ready=False,
            ).tx
            ready = comb[node].input_ready[LOCAL]
            selected = self.system.injection_arbiters[node].outputs(
                cross_tx, adapter_tx, ready
            ).selected
            if selected is not None and ready:
                self.transfers.append(
                    self._transfer(cycle, "inject", node, "local", selected)
                )

        super()._record_noc(
            h1_inputs, cross_inputs, state_publications, done_destinations
        )


def rtl_transfers(path: Path) -> list[Transfer]:
    result: list[Transfer] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["event"] != "accept":
                continue
            result.append((
                int(row["cycle"]), row["scope"], int(row["node"]),
                row["direction"], int(row["packet_type"]),
                int(row["source_id"]), int(row["epoch"]),
                int(row["block_id"]), int(row["dest_x"]),
                int(row["dest_y"]), int(row["last"]),
            ))
    return result


def rtl_noc_totals(path: Path) -> tuple[int, int, int, int, int, int]:
    totals = {
        "inject": [0, 0],
        "eject": [0, 0],
        "link": [0, 0],
    }
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            scope = row["scope"]
            totals[scope][0] += int(row["accepted_flits"])
            totals[scope][1] += int(row["stall_cycles"])
    return (
        totals["inject"][0], totals["eject"][0], totals["link"][0],
        totals["inject"][1], totals["eject"][1], totals["link"][1],
    )


def canonical(events: list[Transfer]) -> list[Transfer]:
    # RTL monitors nodes and scopes in a different loop order than the passive
    # Python observer. Transfers on the same edge are simultaneous, so order
    # those lexicographically while retaining strict cycle equality.
    return sorted(events)


def require(pattern: str, text: str, label: str) -> tuple[int, ...]:
    match = re.search(pattern, text)
    if match is None:
        raise RuntimeError(f"could not parse {label}\n{text}")
    return tuple(map(int, match.groups()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rtl",
        default=("build/ising_mesh_floo_4x4_h02_c16_e1-1-16_"
                 "f4_s1_r1_i1_t1/Vising_mesh_tb"),
    )
    parser.add_argument("--dataset", default="tb/datasets/g16384_kings.txt")
    parser.add_argument(
        "--ramulator-library",
        default="build/cycle_model_ramulator/libazilla_ramulator.so",
    )
    parser.add_argument("--ramulator-config", default="tb/ramulator_128x32.yaml")
    parser.add_argument(
        "--rtl-event-trace", default="build/full_cycle_model_16k_rtl_events.csv"
    )
    parser.add_argument(
        "--rtl-stats", default="build/full_cycle_model_16k_rtl.csv"
    )
    parser.add_argument(
        "--python-event-trace",
        default="build/full_cycle_model_16k_python_events.csv",
    )
    parser.add_argument(
        "--reuse-rtl-trace", action="store_true",
        help="use an already generated RTL event trace instead of rerunning RTL",
    )
    args = parser.parse_args()

    geometry = Geometry(4, 4, 2, 16)
    dataset = IsingDataset.load(ROOT / args.dataset)
    pairs = sorted(dataset.active_block_pairs())
    schedule = [ScheduledBlock(a, b) for a, b in pairs]
    counts = compile_schedule(geometry, schedule).counts

    rtl_output = ""
    trace_path = ROOT / args.rtl_event_trace
    stats_path = ROOT / args.rtl_stats
    if not args.reuse_rtl_trace:
        env = dict(os.environ)
        ramulator_dir = str(ROOT / "third_party/ramulator2")
        env["LD_LIBRARY_PATH"] = (
            ramulator_dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        )
        completed = subprocess.run(
            [
                str(ROOT / args.rtl), "+DATASET=g16384_kings.txt",
                f"+NOC_STATS_FILE={stats_path}",
                f"+NOC_EVENT_FILE={trace_path}",
            ],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=True,
        )
        rtl_output = completed.stdout
        if "PASS: 1 iteration(s)" not in rtl_output:
            raise AssertionError("RTL did not report a passing iteration")

    model = TracedRamulatorModel(
        geometry, dataset,
        ramulator_library=str(ROOT / args.ramulator_library),
        ramulator_config=str(ROOT / args.ramulator_config),
        dataset_path=str(ROOT / args.dataset),
        mem_lanes=16, ticks_per_cycle=40,
        config=PerformanceConfig(
            h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=16,
            fifo_depth=4, timing_only=True, max_cycles=25_000,
        ),
    )
    try:
        result = model.run(schedule=schedule, sparse=True)
    finally:
        model.close()

    expected = canonical(rtl_transfers(trace_path))
    observed = canonical(model.transfers)

    python_trace_path = ROOT / args.python_event_trace
    python_trace_path.parent.mkdir(parents=True, exist_ok=True)
    with python_trace_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow((
            "cycle", "scope", "node", "direction", "packet_type",
            "source_id", "epoch", "block_id", "dest_x", "dest_y", "last",
        ))
        writer.writerows(observed)

    rtl_noc = rtl_noc_totals(stats_path)
    python_noc = (
        result.counters.injected_flits, result.counters.ejected_flits,
        result.counters.physical_link_flits, result.counters.injection_stalls,
        result.counters.ejection_stalls, result.counters.link_stalls,
    )
    if rtl_noc != python_noc:
        raise AssertionError(f"NoC totals mismatch RTL={rtl_noc} Python={python_noc}")

    if args.reuse_rtl_trace:
        rtl_timing = (16_899, 18_720)
        if rtl_timing != (result.initialization_cycles, result.total_cycles):
            raise AssertionError(
                f"timing mismatch RTL={rtl_timing} Python="
                f"{(result.initialization_cycles, result.total_cycles)}"
            )

    if expected != observed:
        mismatch = next(
            (index for index, pair in enumerate(zip(expected, observed))
             if pair[0] != pair[1]), min(len(expected), len(observed))
        )
        raise AssertionError(
            f"NoC transfer mismatch at sorted record {mismatch}: "
            f"RTL={expected[mismatch:mismatch + 1]} "
            f"Python={observed[mismatch:mismatch + 1]} "
            f"counts={len(expected)}/{len(observed)}; "
            f"Python trace={python_trace_path}"
        )

    if rtl_output:
        rtl_timing = require(
            r"initialization complete at cycle (\d+)", rtl_output,
            "RTL initialization",
        ) + require(r"total_cycles=(\d+)", rtl_output, "RTL total")
        if rtl_timing != (result.initialization_cycles, result.total_cycles):
            raise AssertionError(
                f"timing mismatch RTL={rtl_timing} Python="
                f"{(result.initialization_cycles, result.total_cycles)}"
            )

    print(
        "PASS 16K Ramulator differential "
        f"initialization_cycles={result.initialization_cycles} "
        f"iteration_cycles={result.iterations[0].cycles} "
        f"total_cycles={result.total_cycles} "
        f"h0_blocks={counts[0]} h1_blocks={counts[1]} "
        f"cross_blocks={counts[2]} accepted_noc_transfers={len(observed)}"
    )
    print(
        "noc "
        f"injected_flits={result.counters.injected_flits} "
        f"ejected_flits={result.counters.ejected_flits} "
        f"physical_link_flits={result.counters.physical_link_flits} "
        f"injection_stalls={result.counters.injection_stalls} "
        f"ejection_stalls={result.counters.ejection_stalls} "
        f"link_stalls={result.counters.link_stalls}"
    )


if __name__ == "__main__":
    main()
