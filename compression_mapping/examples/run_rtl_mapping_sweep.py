#!/usr/bin/env python3
"""Bounded real-router RTL sweep for 4K staged mapping traffic."""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

from azilla_cycle_model.mapping_adapter import select_cross_owners
from azilla_cycle_model.scheduler import mesh_distance
from azilla_cycle_model.workload import Geometry
from graph_mapping.graph_compression.tile_compressor import (
    extract_block_coords_from_edges,
    partition_vertices,
    permutation_from_partitions,
    prune_low_weight_blocks,
)
from run_mapping_stage_experiments import planted_edges, trivial_owners


STAGES = ("partition", "permutation", "lossy", "scheduled")


def traffic_objective(geometry: Geometry, pairs: np.ndarray,
                      owners: np.ndarray) -> int:
    """Static NoC work proxy: endpoint-to-owner Manhattan traversals."""
    cost = 0
    for (a, b), owner in zip(pairs, owners):
        if owner < 0:
            continue
        h1_a = int(a) // geometry.blocks_per_h1
        h1_b = int(b) // geometry.blocks_per_h1
        cost += mesh_distance(geometry, int(owner), h1_a)
        cost += mesh_distance(geometry, int(owner), h1_b)
    return cost


def selected_owners(geometry: Geometry, pairs: np.ndarray):
    return select_cross_owners(
        geometry, pairs,
        lambda owners: traffic_objective(geometry, pairs, owners),
    )


def write_schedule(path: Path, pairs: np.ndarray, owners: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as output:
        output.write("# block_a block_b owner\n")
        for (a, b), owner in zip(pairs, owners):
            output.write(f"{int(a)} {int(b)} {int(owner)}\n")


def staged_schedules(sparsity: float, seed: int, geometry: Geometry):
    if sparsity <= 0.9:
        pairs = np.asarray([
            (a, b) for a in range(geometry.total_blocks)
            for b in range(a + 1, geometry.total_blocks)
        ], dtype=np.int64)
        trivial = trivial_owners(geometry, pairs)
        scheduled, policy, _ = selected_owners(geometry, pairs)
        return {
            "partition": (pairs, trivial),
            "permutation": (pairs, trivial),
            "lossy": (pairs, trivial),
            "scheduled": (pairs, scheduled),
        }, f"measured-rtl-full-block-schedule:{policy}"

    src_np, dst_np, weight_np = planted_edges(
        geometry.spin_count, sparsity, geometry.node_count, seed
    )
    src, dst, weight = map(torch.from_numpy, (src_np, dst_np, weight_np))
    macro = partition_vertices(
        src, dst, weight, geometry.spin_count, geometry.node_count,
        iterations=4, candidate_swaps=32, seed=seed,
    )
    partition_perm = permutation_from_partitions(
        src, dst, weight, macro, geometry.spin_count, geometry.node_count, 32,
        refine_tiles=False, seed=seed,
    )
    partition_coords, _ = extract_block_coords_from_edges(
        src, dst, partition_perm, 32, geometry.spin_count
    )
    refined_perm = permutation_from_partitions(
        src, dst, weight, macro, geometry.spin_count, geometry.node_count, 32,
        refine_tiles=True, micro_iterations=3, candidate_swaps=32, seed=seed,
    )
    refined_coords, refined_inverse = extract_block_coords_from_edges(
        src, dst, refined_perm, 32, geometry.spin_count
    )
    lossy_coords, _, _ = prune_low_weight_blocks(
        src, dst, weight, refined_inverse, refined_coords,
        32, geometry.spin_count, 0.01,
    )
    result = {}
    for stage, coords in (
        ("partition", partition_coords),
        ("permutation", refined_coords),
        ("lossy", lossy_coords),
    ):
        pairs = coords.numpy()
        pairs = pairs[pairs[:, 0] != pairs[:, 1]] if len(pairs) else pairs
        result[stage] = (pairs, trivial_owners(geometry, pairs))
    pairs = result["lossy"][0]
    scheduled, policy, _ = selected_owners(geometry, pairs)
    result["scheduled"] = (pairs, scheduled)
    return result, f"measured-rtl-generated-mapping:{policy}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=540.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--sparsities",
        default="0,0.5,0.75,0.9,0.93,0.96,0.98,0.9921875,"
                "0.99609375,0.998046875,0.9990234375,0.99951171875,"
                "0.999755859375,1",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    executable = root / "build/hybrid_noc_4x4_f4/Vhybrid_noc_perf_tb"
    if not executable.is_file():
        parser.error("build the router first with: make -C tb hybrid-noc-build")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset = args.output_dir / "g4096_header.txt"
    dataset.write_text("4096 0\n")
    geometry = Geometry(2, 2, 1, 32)
    started = time.monotonic()
    manifest = []
    for sparsity in [float(value) for value in args.sparsities.split(",")]:
        schedules, source = staged_schedules(sparsity, args.seed, geometry)
        for stage in STAGES:
            elapsed = time.monotonic() - started
            remaining = args.max_seconds - elapsed
            if remaining <= 5:
                print("TIME_LIMIT reached; stopping before next RTL replay")
                break
            label = f"s{100*sparsity:.6f}_{stage}".replace(".", "p")
            schedule = args.output_dir / "schedules" / f"{label}.txt"
            traffic = args.output_dir / "traffic" / f"{label}.traffic"
            prefix = args.output_dir / "traces" / label
            pairs, owners = schedules[stage]
            write_schedule(schedule, pairs, owners)
            subprocess.run([
                "python3", str(root / "scripts/generate_hybrid_noc_workload.py"),
                "--dataset", str(dataset), "--schedule", str(schedule),
                "--output", str(traffic), "--mesh-x", "2", "--mesh-y", "2",
                "--h0-per-h1", "1", "--cross-engines", "16",
                "--block-service-cycles", "64",
            ], check=True, timeout=max(1, remaining))
            prefix.parent.mkdir(parents=True, exist_ok=True)
            run_started = time.monotonic()
            completed = subprocess.run([
                str(executable), f"+TRAFFIC={traffic}",
                f"+NOC_STATS_FILE={prefix}.csv",
                f"+NOC_TIMELINE_FILE={prefix}_timeline.csv",
                f"+NOC_EVENT_FILE={prefix}_events.csv",
                "+NOC_STATS_INTERVAL=100",
            ], check=True, timeout=max(1, args.max_seconds - (time.monotonic() - started)),
                capture_output=True, text=True)
            run_seconds = time.monotonic() - run_started
            manifest.append({
                "sparsity": sparsity, "stage": stage, "blocks": len(pairs),
                "source": source, "rtl_wall_seconds": run_seconds,
                "stats_prefix": str(prefix),
            })
            print(f"RTL done sparsity={sparsity} stage={stage} "
                  f"blocks={len(pairs)} wall={run_seconds:.3f}s", flush=True)
        else:
            continue
        break
    manifest_path = args.output_dir / "rtl_sweep_manifest.csv"
    with manifest_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    print(f"completed {len(manifest)} RTL replays in "
          f"{time.monotonic()-started:.1f}s; manifest={manifest_path}")


if __name__ == "__main__":
    main()
