#!/usr/bin/env python3
"""Compare identity, random, and bundled graph mappings on common schedules."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.mapping_adapter import (
    MappingArtifact, artifact_from_mapping, load_sparse_dataset_arrays,
    map_sparse_dataset, write_permuted_dataset,
)
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.workload import Geometry


def comma_list(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def occupied_coords(
    src: np.ndarray, dst: np.ndarray, inverse: np.ndarray, total_blocks: int,
) -> np.ndarray:
    mapped_src = inverse[src]
    mapped_dst = inverse[dst]
    block_a = np.minimum(mapped_src, mapped_dst) // 32
    block_b = np.maximum(mapped_src, mapped_dst) // 32
    keep = block_a != block_b
    ids = np.unique(block_a[keep] * total_blocks + block_b[keep])
    return np.column_stack((ids // total_blocks, ids % total_blocks))


def build_artifact(
    policy: str,
    entry: dict,
    geometry: Geometry,
    directory: Path,
    seed: int,
    macro_iterations: int,
    micro_iterations: int,
    candidate_swaps: int,
    force: bool,
) -> tuple[MappingArtifact, float]:
    manifest = directory / "manifest.json"
    timing_path = directory / "mapping_timing.json"
    if manifest.is_file() and not force:
        timing = (
            json.loads(timing_path.read_text()).get("mapping_wall_seconds", 0.0)
            if timing_path.is_file() else 0.0
        )
        return MappingArtifact.load(directory), float(timing)
    start = time.perf_counter()
    if policy == "provided_mapper":
        artifact = map_sparse_dataset(
            entry["dataset"], geometry, device="cpu",
            macro_iterations=macro_iterations,
            micro_iterations=micro_iterations,
            candidate_swaps=candidate_swaps,
            seed=seed,
            require_cuda=False,
        )
    else:
        spin_count, known_cut, src, dst, _ = load_sparse_dataset_arrays(
            entry["dataset"]
        )
        rng = np.random.default_rng(seed)
        permutation = (
            np.arange(spin_count, dtype=np.int64)
            if policy == "identity_optimized_owner"
            else rng.permutation(spin_count).astype(np.int64, copy=False)
        )
        inverse = np.empty_like(permutation)
        inverse[permutation] = np.arange(spin_count)
        coords = occupied_coords(src, dst, inverse, geometry.total_blocks)
        artifact = artifact_from_mapping(
            geometry, permutation, inverse, coords, known_cut=known_cut,
            metadata={
                "source_dataset": entry["dataset"],
                "edge_records": int(src.size),
                "seed": seed,
                "vertex_policy": policy,
                "cross_owner_policy": "strict-balance-block-reuse-distance",
            },
        )
    wall_seconds = time.perf_counter() - start
    artifact.save(directory)
    write_permuted_dataset(
        entry["dataset"], directory / "dataset.txt", artifact
    )
    timing_path.write_text(json.dumps({
        "mapping_wall_seconds": wall_seconds,
        "policy": policy,
        "seed": seed,
    }, indent=2, sort_keys=True) + "\n")
    return artifact, wall_seconds


def simulate(
    artifact: MappingArtifact,
    directory: Path,
    mapping_wall_seconds: float,
    policy: str,
    config: EventPerformanceConfig,
) -> dict:
    start = time.perf_counter()
    result = EventCompressedPerformanceModel(
        artifact.geometry, artifact.occupancy_dataset(), config
    ).run(schedule=artifact.schedule())
    simulation_wall_seconds = time.perf_counter() - start
    paths = write_event_performance_metrics(
        directory / "metrics", result, artifact.geometry, config
    )
    links = [row for row in result.noc_resources if row.scope == "link"]
    total_jobs = result.scheduled_h0 + result.scheduled_h1 + result.scheduled_cross
    return {
        "mapping": policy,
        "mapping_wall_seconds": mapping_wall_seconds,
        "simulation_wall_seconds": simulation_wall_seconds,
        "active_block_pairs": total_jobs,
        "scheduled_h0": result.scheduled_h0,
        "scheduled_h1": result.scheduled_h1,
        "scheduled_cross": result.scheduled_cross,
        "cross_job_fraction": (
            result.scheduled_cross / total_jobs if total_jobs else 0.0
        ),
        "iteration_cycles": result.iteration_cycles,
        "total_cycles": result.total_cycles,
        "injected_flits": result.injected_flits,
        "physical_link_flits": result.physical_link_flits,
        "injection_stalls": result.injection_stalls,
        "link_stalls": result.link_stalls,
        "average_hops": result.average_hops,
        "maximum_link_flits": max(
            (row.accepted_flits for row in links), default=0
        ),
        "maximum_link_utilization": max(
            (row.accepted_flits / result.iteration_cycles for row in links),
            default=0.0,
        ),
        "maximum_link_backpressure": max(
            (row.backpressure for row in links), default=0.0
        ),
        "accuracy": result.accuracy,
        "metrics_summary": str(paths["summary"]),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("results/paper_benchmarks/datasets/manifest.json"),
    )
    parser.add_argument("--names", type=comma_list, required=True)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/mapping_comparison"),
    )
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--macro-iterations", type=int, default=8)
    parser.add_argument("--micro-iterations", type=int, default=6)
    parser.add_argument("--candidate-swaps", type=int, default=64)
    parser.add_argument("--h0-mvms", type=int, default=1)
    parser.add_argument("--h1-mvms", type=int, default=1)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    entries = [
        entry for entry in manifest["datasets"] if entry["name"] in args.names
    ]
    missing = args.names - {entry["name"] for entry in entries}
    if missing:
        parser.error(f"unknown dataset names: {sorted(missing)}")
    config = EventPerformanceConfig(
        h0_mvm_count=args.h0_mvms,
        h1_mvm_count=args.h1_mvms,
        cross_mvm_count=args.cross_mvms,
    )
    all_rows = []
    for entry in entries:
        geometry = Geometry(**entry["geometry"])
        dataset_rows = []
        for policy in (
            "identity_optimized_owner", "random", "provided_mapper",
        ):
            directory = args.output_dir / entry["name"] / policy
            artifact, mapping_wall = build_artifact(
                policy, entry, geometry, directory,
                args.seed + sum(map(ord, entry["name"])),
                args.macro_iterations, args.micro_iterations,
                args.candidate_swaps, args.force,
            )
            row = simulate(artifact, directory, mapping_wall, policy, config)
            row = {
                "dataset": entry["name"],
                "family": entry["family"],
                "problem_vertices": entry["problem_vertices"],
                "capacity_spins": entry["capacity_spins"],
                **row,
            }
            dataset_rows.append(row)
            print(
                f"{entry['name']} {policy}: blocks={row['active_block_pairs']} "
                f"cycles={row['iteration_cycles']} "
                f"map_wall={mapping_wall:.3f}s",
                flush=True,
            )
        random_cycles = next(
            row["iteration_cycles"] for row in dataset_rows
            if row["mapping"] == "random"
        )
        identity_cycles = next(
            row["iteration_cycles"] for row in dataset_rows
            if row["mapping"] == "identity_optimized_owner"
        )
        for row in dataset_rows:
            row["speedup_vs_random"] = random_cycles / row["iteration_cycles"]
            row["speedup_vs_identity"] = identity_cycles / row["iteration_cycles"]
        all_rows.extend(dataset_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    existing_rows = []
    existing_path = args.output_dir / "results.json"
    if existing_path.is_file():
        existing_rows = json.loads(existing_path.read_text())
    consolidated = {
        (row["dataset"], row["mapping"]): row
        for row in existing_rows
    }
    consolidated.update({
        (row["dataset"], row["mapping"]): row for row in all_rows
    })
    all_rows = sorted(
        consolidated.values(),
        key=lambda row: (row["capacity_spins"], row["dataset"], row["mapping"]),
    )
    write_csv(args.output_dir / "results.csv", all_rows)
    (args.output_dir / "results.json").write_text(
        json.dumps(all_rows, indent=2, sort_keys=True) + "\n"
    )
    print(f"results={args.output_dir / 'results.csv'}")


if __name__ == "__main__":
    main()
