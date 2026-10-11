#!/usr/bin/env python3
"""Closed-form dense-workload invariants and comparison with VCS RTL.

The formulas do not attempt to replace cycle simulation of Ramulator, router
arbitration, or backpressure.  They predict the deterministic work placement,
traffic volume, hop volume, and contention-free service lower bound for the
fixed 8-H0/256-core H1 tile.  Completed VCS runs are checked where available.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.scheduler import allocate_h1_pairs, mesh_distance
from azilla_cycle_model.workload import Geometry


RTL_ROOT = ROOT / "results/dense_config_search/rtl_vcs_fixed_h1_to_1m"
SCHEDULE_RE = re.compile(
    r"compiled concurrent schedule h0=(\d+) h1=(\d+) cross=(\d+) total=(\d+)"
)
TOTAL_RE = re.compile(r"total_cycles=(\d+)")


def choose2(value: int) -> int:
    return value * (value - 1) // 2


def mesh_shape(nodes: int) -> tuple[int, int]:
    y = math.isqrt(nodes)
    while nodes % y:
        y -= 1
    return nodes // y, y


def dense_point(spins: int) -> dict[str, int | str | float]:
    if spins <= 0 or spins % 32:
        raise ValueError("spin count must be a positive multiple of 32")
    active_blocks = spins // 32
    blocks_per_h1 = 8 * 256
    nodes = math.ceil(active_blocks / blocks_per_h1)
    mesh_x, mesh_y = mesh_shape(nodes)
    geometry = Geometry(mesh_x, mesh_y, 8, 256)

    node_blocks = [
        max(0, min(blocks_per_h1, active_blocks - node * blocks_per_h1))
        for node in range(nodes)
    ]
    h0_blocks: list[list[int]] = []
    for count in node_blocks:
        h0_blocks.append([
            max(0, min(256, count - h0 * 256)) for h0 in range(8)
        ])

    h0_per_resource = [choose2(count) for row in h0_blocks for count in row]
    h0_jobs = sum(h0_per_resource)
    h1_per_resource = [
        choose2(node_blocks[node]) - sum(choose2(v) for v in h0_blocks[node])
        for node in range(nodes)
    ]
    h1_jobs = sum(h1_per_resource)
    total_jobs = choose2(active_blocks)
    cross_jobs = total_jobs - h0_jobs - h1_jobs

    owners = allocate_h1_pairs(geometry)
    cross_per_owner = [0] * nodes
    publications: set[tuple[int, int]] = set()
    physical_link_flits = 0
    for a in range(nodes):
        for b in range(a + 1, nodes):
            pair_jobs = node_blocks[a] * node_blocks[b]
            if not pair_jobs:
                continue
            owner = owners[a, b]
            cross_per_owner[owner] += pair_jobs
            for source_node in (a, b):
                base = source_node * blocks_per_h1
                for block in range(base, base + node_blocks[source_node]):
                    publications.add((block, owner))
                    physical_link_flits += mesh_distance(
                        geometry, source_node, owner
                    )
            physical_link_flits += 4 * pair_jobs * (
                mesh_distance(geometry, owner, a)
                + mesh_distance(geometry, owner, b)
            )

    state_flits = len(publications)
    cross_partial_flits = 8 * cross_jobs
    epoch_flits = nodes
    injected_flits = state_flits + cross_partial_flits + epoch_flits
    # An isolated block retires in 67 cycles, but the hierarchy engine and
    # two-buffer streamer overlap adjacent blocks.  Dense steady-state
    # throughput is therefore limited by the 32 row-serial MVM cycles, with
    # the remaining 35 cycles paid once to drain the critical engine.
    initiation_interval = 32
    pipeline_drain = 35
    def service_bound(jobs: int, engines: int) -> int:
        return (
            math.ceil(jobs / engines) * initiation_interval + pipeline_drain
            if jobs else 0
        )
    h0_lower = max((service_bound(v, 4) for v in h0_per_resource), default=0)
    h1_lower = max((service_bound(v, 2) for v in h1_per_resource), default=0)
    cross_lower = max((service_bound(v, 4) for v in cross_per_owner), default=0)

    return {
        "spins": spins, "active_blocks": active_blocks,
        "mesh_x": mesh_x, "mesh_y": mesh_y, "h1_nodes": nodes,
        "h0_per_h1": 8, "cores_per_h0": 256,
        "unordered_blocks": total_jobs,
        "h0_jobs": h0_jobs, "h1_jobs": h1_jobs, "cross_jobs": cross_jobs,
        "weight_reads": total_jobs * 32,
        "weight_bytes": total_jobs * 1024,
        "internal_partial_flits": total_jobs * 8,
        "state_publication_flits": state_flits,
        "cross_partial_flits": cross_partial_flits,
        "epoch_done_flits": epoch_flits,
        "noc_injected_flits": injected_flits,
        "noc_physical_link_flits_no_stalls": physical_link_flits,
        "h0_service_lower_bound": h0_lower,
        "h1_service_lower_bound": h1_lower,
        "cross_service_lower_bound": cross_lower,
        "compute_service_lower_bound": max(h0_lower, h1_lower, cross_lower),
        "isolated_block_latency_cycles": 67,
        "steady_state_initiation_interval_cycles": initiation_interval,
        "timing_scope": "contention-free lower bound; DRAM/NoC cycles require replay",
    }


def rtl_comparison(point: dict[str, object]) -> dict[str, object]:
    spins = int(point["spins"])
    label = (
        f"{spins}_{point['mesh_x']}x{point['mesh_y']}_h08_c256_"
        "e4-2-4_f4_s0_r1_i1_t1"
    )
    log = RTL_ROOT / f"sim_{label}.log"
    if not log.is_file():
        return {"rtl_status": "not-run", "rtl_log": str(log)}
    text = log.read_text(errors="replace")
    schedule = SCHEDULE_RE.search(text)
    total = TOTAL_RE.search(text)
    if schedule is None or total is None:
        return {"rtl_status": "incomplete", "rtl_log": str(log)}
    observed = tuple(map(int, schedule.groups()))
    expected = (
        int(point["h0_jobs"]), int(point["h1_jobs"]),
        int(point["cross_jobs"]), int(point["unordered_blocks"]),
    )
    return {
        "rtl_status": "pass" if observed == expected else "fail",
        "rtl_log": str(log), "rtl_schedule_counts": list(observed),
        "theory_schedule_counts": list(expected),
        "rtl_total_cycles": int(total.group(1)),
        "lower_bound_ratio": int(total.group(1)) /
            max(1, int(point["compute_service_lower_bound"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", default="16384,32768,65536,131072,262144",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/dense_analytical_validation"),
    )
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",")]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    failures = 0
    for spins in sizes:
        point = dense_point(spins)
        point.update(rtl_comparison(point))
        rows.append(point)
        if point["rtl_status"] == "fail":
            failures += 1
        print(
            f"{str(point['rtl_status']).upper()} spins={spins} "
            f"jobs={point['unordered_blocks']} "
            f"theory_lower_bound={point['compute_service_lower_bound']}",
            flush=True,
        )
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with (args.output_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )
    if failures:
        raise SystemExit(f"{failures} RTL schedule-count comparisons failed")


if __name__ == "__main__":
    main()
