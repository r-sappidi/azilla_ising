#!/usr/bin/env python3
"""Compare CIR, cores-only, and static hybrid on block-level workloads."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from random import Random

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


def ring_pairs(blocks: int, degree: int) -> set[tuple[int, int]]:
    return {
        tuple(sorted((a, (a + offset) % blocks)))
        for a in range(blocks) for offset in range(1, degree // 2 + 1)
    }


def random_pairs(blocks: int, degree: int, seed: int) -> set[tuple[int, int]]:
    rng = Random(seed)
    pairs: set[tuple[int, int]] = set()
    # Random cyclic permutations produce approximately regular graphs without
    # the quadratic degree scans used by rejection-based generators.
    for _ in range((degree + 1) // 2):
        permutation = list(range(blocks))
        rng.shuffle(permutation)
        for index, a in enumerate(permutation):
            b = permutation[(index + 1) % blocks]
            if a != b:
                pairs.add(tuple(sorted((a, b))))
    return pairs


def hotspot_pairs(blocks: int, degree: int, seed: int) -> set[tuple[int, int]]:
    pairs = random_pairs(blocks, max(2, degree // 2), seed)
    hubs = max(1, blocks // 64)
    for a in range(blocks):
        for offset in range(max(1, degree // 4)):
            b = (a * 17 + offset * 13) % hubs
            if a != b:
                pairs.add(tuple(sorted((a, b))))
    return pairs


def star_pairs(blocks: int, degree: int, seed: int) -> set[tuple[int, int]]:
    del degree, seed
    return {(0, block) for block in range(1, blocks)}


def geometry_for(spins: int) -> Geometry:
    if spins % 65536:
        raise ValueError("sizes must be multiples of the fixed 65,536-spin H1")
    nodes = spins // 65536
    mesh_x = 1
    while (mesh_x + 1) * (mesh_x + 1) <= nodes:
        mesh_x += 1
    while nodes % mesh_x:
        mesh_x -= 1
    return Geometry(mesh_x, nodes // mesh_x, 8, 256)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="65536,131072")
    parser.add_argument("--output-dir", default="results/cir_evaluation/static_hybrid")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    workloads = (("ring-d4", ring_pairs, 4), ("ring-d16", ring_pairs, 16),
                 ("random-d16", random_pairs, 16),
                 ("hotspot-d16", hotspot_pairs, 16),
                 ("star", star_pairs, 0))
    rows = []
    for spins in map(int, args.sizes.split(",")):
        geometry = geometry_for(spins)
        for name, generator, degree in workloads:
            pairs = (generator(geometry.total_blocks, degree) if generator is ring_pairs
                     else generator(geometry.total_blocks, degree, args.seed))
            dataset = BlockOccupancyDataset(spins, 0, pairs)
            for mode in ("cir", "cores-only", "hybrid"):
                config = EventPerformanceConfig(
                    h0_mvm_count=4, h1_mvm_count=2, cross_mvm_count=4,
                    execution_mode=mode,
                )
                result = EventCompressedPerformanceModel(
                    geometry, dataset, config,
                ).run()
                rows.append({
                    "spins": spins, "workload": name, "blocks": len(pairs),
                    "mode": mode, "iteration_cycles": result.iteration_cycles,
                    "directed_core_jobs": result.directed_core_jobs,
                    "weight_block_reads": result.weight_block_reads,
                    "remote_state_packets": result.remote_state_packets,
                    "injected_flits": result.injected_flits,
                    "physical_link_flits": result.physical_link_flits,
                    "accuracy": result.accuracy,
                })
                print(spins, name, mode, result.iteration_cycles, flush=True)
    with (output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader(); writer.writerows(rows)
    (output / "manifest.json").write_text(json.dumps({
        "model": "calibrated-event-compressed", "seed": args.seed,
        "fixed_h1": {"h0_per_h1": 8, "cores_per_h0": 256,
                     "h0_mvms": 4, "h1_mvms": 2, "cross_mvms": 4},
        "sizes": [int(value) for value in args.sizes.split(",")],
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
