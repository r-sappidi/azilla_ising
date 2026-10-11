#!/usr/bin/env python3
"""Measure static-hybrid regret against fixed and searched placement oracles.

The fixed-policy oracle is exact over the two complete policies (CIR and
cores-only).  Per-block placement is exhaustive only when the number of
interaction blocks is small enough.  Paper-scale schedules use deterministic
block-coordinate search and are explicitly labeled ``searched-placement``;
that result is a best-seen bound, not a proof of global optimality.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.mapping_adapter import MappingArtifact
from azilla_cycle_model.scheduler import (
    HYBRID, compile_static_hybrid_schedule,
)


def pair_of(record):
    return record.block_a, record.block_b


def pair_level(geometry, pair):
    a, b = pair
    h0_a, h0_b = (x // geometry.cores_per_h0 for x in pair)
    if h0_a == h0_b:
        return "h0"
    h1_a, h1_b = (x // geometry.blocks_per_h1 for x in pair)
    return "h1" if h1_a == h1_b else "cross"


def bucket_of(pair, buckets):
    digest = hashlib.blake2b(
        f"{pair[0]}:{pair[1]}".encode(), digest_size=8,
    ).digest()
    return int.from_bytes(digest, "little") % buckets


def result_row(label, result, core_count, elapsed):
    return {
        "candidate": label,
        "iteration_cycles": result.iteration_cycles,
        "core_pairs": core_count,
        "cir_pairs": result.unordered_interaction_blocks - core_count,
        "weight_block_reads": result.weight_block_reads,
        "remote_state_packets": result.remote_state_packets,
        "injected_flits": result.injected_flits,
        "physical_link_flits": result.physical_link_flits,
        "injection_stalls": result.injection_stalls,
        "link_stalls": result.link_stalls,
        "wall_seconds": elapsed,
        "accuracy": result.accuracy,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path("results/paper_benchmarks/mapping_comparison"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/hybrid_oracle"),
    )
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--search-buckets", type=int, default=8)
    parser.add_argument("--search-passes", type=int, default=2)
    parser.add_argument("--exhaustive-max-blocks", type=int, default=16)
    parser.add_argument("--datasets", help="comma-separated dataset names")
    parser.add_argument("--mappings", help="comma-separated mapping names")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.search_buckets <= 0 or args.search_passes <= 0:
        parser.error("search buckets and passes must be positive")

    manifests = sorted(args.artifact_root.glob("*/*/manifest.json"))
    datasets = set(args.datasets.split(",")) if args.datasets else None
    mappings = set(args.mappings.split(",")) if args.mappings else None
    manifests = [manifest for manifest in manifests if
                 (datasets is None or manifest.parent.parent.name in datasets) and
                 (mappings is None or manifest.parent.name in mappings)]
    if not manifests:
        parser.error(f"no mapping artifacts below {args.artifact_root}")
    summaries = []
    for manifest in manifests:
        artifact = MappingArtifact.load(manifest.parent)
        dataset = artifact.occupancy_dataset()
        schedule = artifact.schedule()
        pairs = tuple(pair_of(record) for record in schedule)
        pair_set = frozenset(pairs)
        dataset_name = manifest.parent.parent.name
        mapping_name = manifest.parent.name
        run_dir = args.output_dir / dataset_name / mapping_name
        output = run_dir / "summary.json"
        if output.exists() and not args.force:
            summaries.append(json.loads(output.read_text()))
            print("SKIP", dataset_name, mapping_name, flush=True)
            continue

        config_values = dict(
            h0_mvm_count=args.h0_mvms,
            h1_mvm_count=args.h1_mvms,
            cross_mvm_count=args.cross_mvms,
        )
        cache = {}
        candidates = []

        def evaluate(label, core_pairs):
            key = frozenset(core_pairs)
            if key in cache:
                return cache[key]
            started = time.perf_counter()
            result = EventCompressedPerformanceModel(
                artifact.geometry, dataset,
                EventPerformanceConfig(
                    **config_values, execution_mode=HYBRID,
                ),
            ).run(schedule=schedule, hybrid_core_pairs=key)
            row = result_row(label, result, len(key), time.perf_counter()-started)
            row["core_pair_digest"] = hashlib.sha256(
                "\n".join(f"{a},{b}" for a, b in sorted(key)).encode()
            ).hexdigest()
            cache[key] = (row, key)
            candidates.append(row)
            print(dataset_name, mapping_name, label,
                  row["iteration_cycles"], len(key), flush=True)
            return row, key

        fixed = {}
        for mode in ("cir", "cores-only", "hybrid"):
            started = time.perf_counter()
            result = EventCompressedPerformanceModel(
                artifact.geometry, dataset,
                EventPerformanceConfig(
                    **config_values, execution_mode=mode,
                ),
            ).run(schedule=schedule)
            fixed[mode] = result_row(
                mode, result,
                result.directed_core_jobs // 2,
                time.perf_counter()-started,
            )
            print(dataset_name, mapping_name, mode,
                  result.iteration_cycles, flush=True)

        static = compile_static_hybrid_schedule(
            artifact.geometry, schedule, **config_values,
        )
        seed_sets = [frozenset(), pair_set, frozenset(static.core_pairs)]
        levels = {level: frozenset(
            pair for pair in pairs if pair_level(artifact.geometry, pair) == level
        ) for level in ("h0", "h1", "cross")}
        for mask in range(8):
            seed_sets.append(frozenset().union(*(
                levels[level] for bit, level in enumerate(("h0", "h1", "cross"))
                if mask & (1 << bit)
            )))

        if len(pairs) <= args.exhaustive_max_blocks:
            search_kind = "exhaustive-placement-oracle"
            seed_sets = [frozenset(
                pair for bit, pair in enumerate(pairs) if mask & (1 << bit)
            ) for mask in range(1 << len(pairs))]
            for index, selected in enumerate(seed_sets):
                evaluate(f"exhaustive-{index}", selected)
        else:
            search_kind = "searched-placement"
            unique_seeds = []
            observed = set()
            for selected in seed_sets:
                if selected not in observed:
                    observed.add(selected); unique_seeds.append(selected)
            evaluated = [evaluate(f"seed-{i}", selected)
                         for i, selected in enumerate(unique_seeds)]
            _, current = min(
                evaluated, key=lambda item: (
                    item[0]["iteration_cycles"], item[0]["physical_link_flits"],
                    item[0]["weight_block_reads"], item[0]["core_pair_digest"],
                )
            )
            buckets = [frozenset(
                pair for pair in pairs if bucket_of(pair, args.search_buckets) == b
            ) for b in range(args.search_buckets)]
            for pass_index in range(args.search_passes):
                changed = False
                for bucket_index, bucket in enumerate(buckets):
                    candidate = current ^ bucket
                    row, selected = evaluate(
                        f"coordinate-p{pass_index}-b{bucket_index}", candidate,
                    )
                    incumbent = cache[current][0]
                    if (
                        row["iteration_cycles"], row["physical_link_flits"],
                        row["weight_block_reads"], row["core_pair_digest"],
                    ) < (
                        incumbent["iteration_cycles"],
                        incumbent["physical_link_flits"],
                        incumbent["weight_block_reads"],
                        incumbent["core_pair_digest"],
                    ):
                        current = selected; changed = True
                if not changed:
                    break

        best_row, best_pairs = min(cache.values(), key=lambda item: (
            item[0]["iteration_cycles"], item[0]["physical_link_flits"],
            item[0]["weight_block_reads"], item[0]["core_pair_digest"],
        ))
        fixed_oracle = min(fixed["cir"]["iteration_cycles"],
                           fixed["cores-only"]["iteration_cycles"])
        current = fixed["hybrid"]["iteration_cycles"]
        summary = {
            "dataset": dataset_name,
            "mapping": mapping_name,
            "geometry": asdict(artifact.geometry),
            "interaction_blocks": len(pairs),
            "search_kind": search_kind,
            "evaluated_partitions": len(cache),
            "fixed_modes": fixed,
            "fixed_policy_oracle_cycles": fixed_oracle,
            "current_hybrid_cycles": current,
            "current_hybrid_speedup_over_fixed_oracle": fixed_oracle/current,
            "best_searched": best_row,
            "best_searched_speedup_over_fixed_oracle": (
                fixed_oracle / best_row["iteration_cycles"]
            ),
            "current_hybrid_regret_to_best_searched": (
                current / best_row["iteration_cycles"] - 1
            ),
            "configuration": config_values,
            "search": {
                "buckets": args.search_buckets,
                "passes": args.search_passes,
                "exhaustive_max_blocks": args.exhaustive_max_blocks,
            },
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "candidates.json").write_text(
            json.dumps(candidates, indent=2) + "\n"
        )
        (run_dir / "best_core_pairs.csv").write_text(
            "block_a,block_b\n" + "".join(
                f"{a},{b}\n" for a, b in sorted(best_pairs)
            )
        )
        output.write_text(json.dumps(summary, indent=2) + "\n")
        summaries.append(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in summaries:
        rows.append({
            "dataset": item["dataset"], "mapping": item["mapping"],
            "interaction_blocks": item["interaction_blocks"],
            "search_kind": item["search_kind"],
            "evaluated_partitions": item["evaluated_partitions"],
            "cir_cycles": item["fixed_modes"]["cir"]["iteration_cycles"],
            "cores_only_cycles": item["fixed_modes"]["cores-only"]["iteration_cycles"],
            "hybrid_cycles": item["current_hybrid_cycles"],
            "fixed_policy_oracle_cycles": item["fixed_policy_oracle_cycles"],
            "best_searched_cycles": item["best_searched"]["iteration_cycles"],
            "hybrid_speedup_over_fixed_oracle": item["current_hybrid_speedup_over_fixed_oracle"],
            "searched_speedup_over_fixed_oracle": item["best_searched_speedup_over_fixed_oracle"],
            "hybrid_regret_to_best_searched": item["current_hybrid_regret_to_best_searched"],
            "hybrid_core_pairs": item["fixed_modes"]["hybrid"]["core_pairs"],
            "searched_core_pairs": item["best_searched"]["core_pairs"],
            "hybrid_physical_link_flits": item["fixed_modes"]["hybrid"]["physical_link_flits"],
            "searched_physical_link_flits": item["best_searched"]["physical_link_flits"],
        })
    with (args.output_dir / "summary.csv").open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
