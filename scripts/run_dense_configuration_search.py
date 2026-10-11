#!/usr/bin/env python3
"""Search dense Azilla geometries and MVM counts under resource constraints."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import time
from pathlib import Path

from azilla_cycle_model.events import EventCompressedPerformanceModel, EventPerformanceConfig
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


def integer_list(text: str) -> list[int]:
    return sorted({int(value) for value in text.split(",")})


def choose2(value: int) -> int:
    return value * (value - 1) // 2


def mesh_shape(nodes: int) -> tuple[int, int]:
    y = math.isqrt(nodes)
    while nodes % y:
        y -= 1
    return nodes // y, y


def load_rates(path: Path) -> dict[tuple[int, int], float]:
    with path.open(newline="") as source:
        return {
            (int(row["mvm_count"]), int(row["output_lanes"])):
                float(row["blocks_per_cycle"])
            for row in csv.DictReader(source)
        }


def physical_lanes(level: str, mvms: int, geometry: Geometry, rates: dict) -> int:
    if level == "cross":
        desired = 1
    elif level == "h1":
        desired = min(mvms, geometry.h0_per_h1)
    else:
        desired = min(mvms, geometry.cores_per_h0)
    available = sorted(lanes for count, lanes in rates if count == mvms and lanes <= desired)
    if not available:
        raise ValueError(f"no measured output-lane rate for MVM count {mvms}")
    return available[-1]


def enumerate_points(spins: int, args: argparse.Namespace, rates: dict) -> list[dict]:
    blocks = spins // 32
    points = []
    for nodes in args.h1_nodes:
        if blocks % nodes:
            continue
        mesh_x, mesh_y = mesh_shape(nodes)
        for h0s in args.h0_counts:
            if blocks % (nodes * h0s):
                continue
            cores = blocks // (nodes * h0s)
            if cores > args.max_cores_per_h0 or cores & (cores - 1):
                continue
            geometry = Geometry(mesh_x, mesh_y, h0s, cores)
            interfaces = nodes * (h0s + 2)
            if interfaces > args.max_memory_interfaces:
                continue
            h0_work = choose2(cores)
            h1_work = choose2(h0s * cores) - h0s * h0_work
            cross_total = choose2(blocks) - nodes * choose2(h0s * cores)
            cross_work = math.ceil(cross_total / nodes)
            for h0_mvm, h1_mvm, cross_mvm in itertools.product(args.mvm_counts, repeat=3):
                total_mvms = nodes * (h0s * h0_mvm + h1_mvm + cross_mvm)
                if total_mvms > args.max_total_mvms:
                    continue
                h0_lanes = physical_lanes("h0", h0_mvm, geometry, rates)
                h1_lanes = physical_lanes("h1", h1_mvm, geometry, rates)
                cross_lanes = physical_lanes("cross", cross_mvm, geometry, rates)
                total_lanes = nodes * (h0s * h0_lanes + h1_lanes + cross_lanes)
                if total_lanes > args.max_total_output_lanes:
                    continue
                level_cycles = {
                    "h0": math.ceil(h0_work / rates[h0_mvm, h0_lanes]) if h0_work else 0,
                    "h1": math.ceil(h1_work / rates[h1_mvm, h1_lanes]) if h1_work else 0,
                    "cross": math.ceil(cross_work / rates[cross_mvm, cross_lanes]) if cross_work else 0,
                }
                points.append({
                    "spins": spins, "mesh_x": mesh_x, "mesh_y": mesh_y,
                    "h1_nodes": nodes, "h0_per_h1": h0s, "cores_per_h0": cores,
                    "h0_mvms": h0_mvm, "h1_mvms": h1_mvm, "cross_mvms": cross_mvm,
                    "h0_output_lanes": h0_lanes, "h1_output_lanes": h1_lanes,
                    "cross_output_lanes": cross_lanes,
                    "memory_interfaces": interfaces, "total_mvms": total_mvms,
                    "total_output_lanes": total_lanes,
                    "projected_cycles": max(32, *level_cycles.values()),
                    "projected_bottleneck": max(level_cycles, key=level_cycles.get),
                    **{f"projected_{key}_cycles": value for key, value in level_cycles.items()},
                })
    points.sort(key=lambda row: (row["projected_cycles"], row["total_mvms"], row["memory_interfaces"]))
    return points


def select_finalists(points: list[dict], top_count: int, geometry_group: str) -> list[dict]:
    selected = points[:top_count]
    seen_geometry: set[tuple[int, ...]] = set()
    for point in points:
        if geometry_group == "nodes":
            geometry = (point["h1_nodes"],)
        else:
            geometry = (point["h1_nodes"], point["h0_per_h1"], point["cores_per_h0"])
        if geometry not in seen_geometry:
            selected.append(point)
            seen_geometry.add(geometry)
    unique = {}
    for point in selected:
        key = (
            point["h1_nodes"], point["h0_per_h1"], point["cores_per_h0"],
            point["h0_mvms"], point["h1_mvms"], point["cross_mvms"],
        )
        unique[key] = point
    return list(unique.values())


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("results/dense_config_search/full_system"))
    parser.add_argument("--sizes", type=integer_list, default=integer_list("1024,4096,8192,16384,32768"))
    parser.add_argument("--h1-nodes", type=integer_list, default=integer_list("1,2,4,8,16"))
    parser.add_argument("--h0-counts", type=integer_list, default=integer_list("1,2,4,8,16"))
    parser.add_argument("--mvm-counts", type=integer_list, default=integer_list("1,2,4,8,16"))
    parser.add_argument("--max-cores-per-h0", type=int, default=256)
    parser.add_argument("--max-memory-interfaces", type=int, default=96)
    parser.add_argument("--max-total-mvms", type=int, default=384)
    parser.add_argument("--max-total-output-lanes", type=int, default=384)
    parser.add_argument("--top-finalists", type=int, default=24)
    parser.add_argument("--geometry-group", choices=("all", "nodes"), default="all",
                        help="retain the best point per full geometry or per H1-node count")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rates = load_rates(args.rates)
    all_results = []
    winners = []
    for spins in args.sizes:
        screened = enumerate_points(spins, args, rates)
        if not screened:
            raise RuntimeError(f"no feasible configurations for {spins} spins")
        size_dir = args.output_dir / str(spins)
        size_dir.mkdir(parents=True, exist_ok=True)
        write_csv(size_dir / "screened.csv", screened)
        finalists = select_finalists(screened, args.top_finalists, args.geometry_group)
        print(f"SIZE spins={spins} screened={len(screened)} finalists={len(finalists)}", flush=True)

        blocks = spins // 32
        dataset = BlockOccupancyDataset(
            spins, 0, {(a, b) for a in range(blocks) for b in range(a + 1, blocks)}
        )
        size_results = []
        for index, point in enumerate(finalists, 1):
            geometry = Geometry(
                point["mesh_x"], point["mesh_y"],
                point["h0_per_h1"], point["cores_per_h0"],
            )
            config = EventPerformanceConfig(
                h0_mvm_count=point["h0_mvms"],
                h1_mvm_count=point["h1_mvms"],
                cross_mvm_count=point["cross_mvms"],
            )
            started = time.perf_counter()
            result = EventCompressedPerformanceModel(geometry, dataset, config).run(
                sparse=True, iterations=1
            )
            row = dict(point)
            row.update({
                "accuracy": result.accuracy,
                "initialization_cycles": result.initialization_cycles,
                "iteration_cycles": result.iteration_cycles,
                "total_cycles": result.total_cycles,
                "scheduled_h0": result.scheduled_h0,
                "scheduled_h1": result.scheduled_h1,
                "scheduled_cross": result.scheduled_cross,
                "injected_flits": result.injected_flits,
                "physical_link_flits": result.physical_link_flits,
                "simulation_wall_seconds": time.perf_counter() - started,
            })
            size_results.append(row)
            print(
                f"POINT spins={spins} {index}/{len(finalists)} "
                f"mesh={geometry.mesh_x}x{geometry.mesh_y} h0={geometry.h0_per_h1} "
                f"cores={geometry.cores_per_h0} mvms={config.h0_mvm_count}/"
                f"{config.h1_mvm_count}/{config.cross_mvm_count} cycles={result.total_cycles}",
                flush=True,
            )
        size_results.sort(key=lambda row: (
            row["total_cycles"], row["total_mvms"], row["memory_interfaces"]
        ))
        write_csv(size_dir / "simulated.csv", size_results)
        winner = size_results[0]
        winners.append(winner)
        all_results.extend(size_results)

        winner_geometry = Geometry(
            winner["mesh_x"], winner["mesh_y"],
            winner["h0_per_h1"], winner["cores_per_h0"],
        )
        winner_config = EventPerformanceConfig(
            h0_mvm_count=winner["h0_mvms"], h1_mvm_count=winner["h1_mvms"],
            cross_mvm_count=winner["cross_mvms"],
        )
        winner_result = EventCompressedPerformanceModel(
            winner_geometry, dataset, winner_config
        ).run(sparse=True, iterations=1)
        write_event_performance_metrics(
            size_dir / "winner_metrics", winner_result, winner_geometry, winner_config
        )
        (size_dir / "winner.json").write_text(json.dumps(winner, indent=2, sort_keys=True) + "\n")
        print(f"WINNER spins={spins} cycles={winner['total_cycles']}", flush=True)

    write_csv(args.output_dir / "all_simulated.csv", all_results)
    write_csv(args.output_dir / "winners.csv", winners)
    print(f"COMPLETE winners={args.output_dir / 'winners.csv'}", flush=True)


if __name__ == "__main__":
    main()
