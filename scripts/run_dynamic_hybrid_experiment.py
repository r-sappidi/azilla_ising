#!/usr/bin/env python3
"""Evaluate implementable online hybrid policies on frozen mappings."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from azilla_cycle_model.dynamic_scheduler import (
    QUEUE_AWARE, READY_ONLY, TRAFFIC_AWARE,
    select_dynamic_hybrid_core_pairs,
)
from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.mapping_adapter import MappingArtifact


def comma_set(value):
    return {item.strip() for item in value.split(",") if item.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root", type=Path,
        default=Path("results/paper_benchmarks/mapping_comparison"),
    )
    parser.add_argument(
        "--oracle-root", type=Path,
        default=Path("results/paper_benchmarks/hybrid_oracle"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/dynamic_hybrid"),
    )
    parser.add_argument("--datasets", type=comma_set)
    parser.add_argument("--mappings", type=comma_set)
    parser.add_argument("--traffic-weights", default="1,2,4")
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    weights = [int(value) for value in args.traffic_weights.split(",")]
    manifests = sorted(args.artifact_root.glob("*/*/manifest.json"))
    manifests = [path for path in manifests if
                 (not args.datasets or path.parent.parent.name in args.datasets) and
                 (not args.mappings or path.parent.name in args.mappings)]
    if not manifests:
        parser.error("no matching mapping artifacts")
    all_rows = []
    for manifest in manifests:
        artifact = MappingArtifact.load(manifest.parent)
        dataset = artifact.occupancy_dataset()
        schedule = artifact.schedule()
        dataset_name, mapping = manifest.parent.parent.name, manifest.parent.name
        run_dir = args.output_dir / dataset_name / mapping
        output = run_dir / "summary.json"
        if output.exists() and not args.force:
            rows = json.loads(output.read_text())["policies"]
            all_rows.extend(rows); print("SKIP", dataset_name, mapping, flush=True)
            continue
        oracle_path = args.oracle_root / dataset_name / mapping / "summary.json"
        oracle = json.loads(oracle_path.read_text()) if oracle_path.exists() else {}
        policy_specs = [(READY_ONLY, 0), (QUEUE_AWARE, 0)] + [
            (TRAFFIC_AWARE, weight) for weight in weights
        ]
        rows = []
        for policy, weight in policy_specs:
            started = time.perf_counter()
            placement = select_dynamic_hybrid_core_pairs(
                artifact.geometry, schedule, policy=policy,
                h0_mvm_count=args.h0_mvms,
                h1_mvm_count=args.h1_mvms,
                cross_mvm_count=args.cross_mvms,
                traffic_cycle_weight=weight,
            )
            selection_wall = time.perf_counter() - started
            started = time.perf_counter()
            result = EventCompressedPerformanceModel(
                artifact.geometry, dataset,
                EventPerformanceConfig(
                    h0_mvm_count=args.h0_mvms,
                    h1_mvm_count=args.h1_mvms,
                    cross_mvm_count=args.cross_mvms,
                    execution_mode="hybrid",
                ),
            ).run(schedule=schedule, hybrid_core_pairs=placement.core_pairs)
            simulation_wall = time.perf_counter() - started
            fixed = oracle.get("fixed_policy_oracle_cycles", 0)
            searched = oracle.get("best_searched", {}).get("iteration_cycles", 0)
            label = policy if policy != TRAFFIC_AWARE else f"{policy}-w{weight}"
            row = {
                "dataset": dataset_name, "mapping": mapping, "policy": label,
                "iteration_cycles": result.iteration_cycles,
                "core_pairs": placement.core_decisions,
                "cir_pairs": placement.cir_decisions,
                "predicted_completion_cycles": placement.predicted_completion_cycles,
                "estimated_state_flit_hops": placement.estimated_state_flit_hops,
                "estimated_partial_flit_hops": placement.estimated_partial_flit_hops,
                "weight_block_reads": result.weight_block_reads,
                "remote_state_packets": result.remote_state_packets,
                "injected_flits": result.injected_flits,
                "physical_link_flits": result.physical_link_flits,
                "injection_stalls": result.injection_stalls,
                "link_stalls": result.link_stalls,
                "selection_wall_seconds": selection_wall,
                "simulation_wall_seconds": simulation_wall,
                "fixed_policy_oracle_cycles": fixed,
                "searched_placement_cycles": searched,
                "speedup_over_fixed_oracle": fixed / result.iteration_cycles if fixed else 0,
                "regret_to_searched": result.iteration_cycles / searched - 1 if searched else 0,
                "accuracy": result.accuracy,
            }
            rows.append(row); all_rows.append(row)
            print(dataset_name, mapping, label, result.iteration_cycles,
                  f"core={placement.core_decisions}", flush=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "dataset": dataset_name, "mapping": mapping,
            "policies": rows,
        }, indent=2) + "\n")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "summary.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(all_rows[0]))
        writer.writeheader(); writer.writerows(all_rows)


if __name__ == "__main__":
    main()
