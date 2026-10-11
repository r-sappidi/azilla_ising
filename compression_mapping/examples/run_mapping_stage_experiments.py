#!/usr/bin/env python3
"""Benchmark cumulative Azilla mapping stages over size and sparsity.

The synthetic input is a planted-community sparse graph.  This is preferable
to an Erdos-Renyi graph for this experiment because a mapper cannot recover
locality that does not exist.  Results include both stage runtime and mapping
quality, and every row records the random seed and graph parameters.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path

import numpy as np
import torch

from azilla_cycle_model.mapping_adapter import (
    assign_endpoint_owners, select_cross_owners,
)
from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.scheduler import mesh_distance
from azilla_cycle_model.workload import (
    BlockOccupancyDataset, Geometry, ScheduledBlock,
)
from graph_mapping.graph_compression.tile_compressor import (
    extract_block_coords_from_edges,
    partition_vertices,
    permutation_from_partitions,
    prune_low_weight_blocks,
)


def parse_csv(text: str, convert):
    return [convert(item) for item in text.split(",") if item]


def planted_edges(vertices: int, sparsity: float, communities: int,
                  seed: int, local_probability: float = 0.9):
    """Generate unique undirected edges without constructing a dense matrix."""
    possible = vertices * (vertices - 1) // 2
    target = max(0, round((1.0 - sparsity) * possible))
    rng = np.random.default_rng(seed)
    community_size = vertices // communities
    shuffled = rng.permutation(vertices)
    members = shuffled.reshape(communities, community_size)
    community_of = np.empty(vertices, dtype=np.int64)
    community_of[shuffled] = np.repeat(np.arange(communities), community_size)
    if target > possible // 4:
        # Rejection sampling approaches the coupon-collector worst case for
        # dense graphs. Enumerate the upper triangle and use weighted sampling
        # without replacement instead. The affinity weighting retains planted
        # community structure wherever the requested density permits it.
        rows, columns = np.triu_indices(vertices, k=1)
        if target < possible:
            local = community_of[rows] == community_of[columns]
            natural_local = (community_size - 1) / (vertices - 1)
            affinity = (
                local_probability / max(1.0 - local_probability, 1e-12) *
                (1.0 - natural_local) / max(natural_local, 1e-12)
            )
            sampling_weight = np.where(local, affinity, 1.0)
            keys = -np.log(rng.random(possible)) / sampling_weight
            chosen = np.argpartition(keys, target - 1)[:target]
            rows, columns = rows[chosen], columns[chosen]
        weights = rng.integers(1, 9, size=target).astype(np.float32)
        return rows.astype(np.int64), columns.astype(np.int64), weights
    edges: set[tuple[int, int]] = set()
    while len(edges) < target:
        u = int(rng.integers(vertices))
        if rng.random() < local_probability:
            group = community_of[u]
            v = int(members[group, rng.integers(community_size)])
        else:
            v = int(rng.integers(vertices))
        if u != v:
            edges.add((min(u, v), max(u, v)))
    if not edges:
        empty_i = np.empty(0, dtype=np.int64)
        return empty_i, empty_i.copy(), np.empty(0, dtype=np.float32)
    pairs = np.asarray(sorted(edges), dtype=np.int64)
    weights = rng.integers(1, 9, size=len(pairs)).astype(np.float32)
    return pairs[:, 0], pairs[:, 1], weights


def synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def timed(device: torch.device, operation):
    synchronize(device)
    start = time.perf_counter()
    value = operation()
    synchronize(device)
    return value, time.perf_counter() - start


def trivial_owners(geometry: Geometry, pairs: np.ndarray) -> np.ndarray:
    """Legal baseline: cross work runs at its first endpoint H1."""
    return assign_endpoint_owners(geometry, pairs)


def quality(src, dst, inverse, coords, geometry, owners):
    mapped_src, mapped_dst = inverse[src], inverse[dst]
    spins_per_h1 = geometry.blocks_per_h1 * 32
    edge_a = torch.div(torch.minimum(mapped_src, mapped_dst), 32,
                       rounding_mode="floor")
    edge_b = torch.div(torch.maximum(mapped_src, mapped_dst), 32,
                       rounding_mode="floor")
    active_ids = coords[:, 0] * geometry.total_blocks + coords[:, 1]
    edge_ids = edge_a * geometry.total_blocks + edge_b
    retained = torch.isin(edge_ids, active_ids)
    cross_edges = int(torch.count_nonzero(
        retained & (
            torch.div(mapped_src, spins_per_h1, rounding_mode="floor") !=
            torch.div(mapped_dst, spins_per_h1, rounding_mode="floor")
        )
    ).item())
    pairs = coords.detach().cpu().numpy()
    pairs = pairs[pairs[:, 0] != pairs[:, 1]] if len(pairs) else pairs
    cross_blocks = 0
    owner_distance = 0
    owner_loads = np.zeros(geometry.node_count, dtype=np.int64)
    for (a, b), owner in zip(pairs, owners):
        h1_a = int(a) // geometry.blocks_per_h1
        h1_b = int(b) // geometry.blocks_per_h1
        if h1_a != h1_b:
            cross_blocks += 1
            owner_loads[int(owner)] += 1
            owner_distance += mesh_distance(geometry, int(owner), h1_a)
            owner_distance += mesh_distance(geometry, int(owner), h1_b)
    total_upper = geometry.total_blocks * (geometry.total_blocks - 1) // 2
    return {
        "cross_edges": cross_edges,
        "occupied_blocks": int(len(pairs)),
        "zero_block_fraction": 1.0 - len(pairs) / max(1, total_upper),
        "cross_blocks": cross_blocks,
        "owner_distance": owner_distance,
        "max_owner_load": int(owner_loads.max(initial=0)),
        "owner_load_stddev": float(owner_loads.std()),
    }


def estimate_accelerator(geometry: Geometry, pairs: np.ndarray,
                         owners: np.ndarray, args):
    """Estimate one Azilla iteration for an already compiled mapping."""
    active = {tuple(map(int, pair)) for pair in pairs.tolist()}
    dataset = BlockOccupancyDataset(geometry.spin_count, 0, active)
    schedule = [
        ScheduledBlock(int(a), int(b), int(owner))
        for (a, b), owner in zip(pairs, owners)
    ]
    result = EventCompressedPerformanceModel(
        geometry,
        dataset,
        EventPerformanceConfig(
            h0_mvm_count=args.h0_mvms,
            h1_mvm_count=args.h1_mvms,
            cross_mvm_count=args.cross_mvms,
            fifo_depth=args.fifo_depth,
        ),
    ).run(schedule=schedule, sparse=True, iterations=1)
    return {
        "estimated_initialization_cycles": result.initialization_cycles,
        "estimated_iteration_cycles": result.iteration_cycles,
        "estimated_total_cycles": result.total_cycles,
        "estimated_physical_link_flits": result.physical_link_flits,
        "estimated_accuracy": result.accuracy,
    }


def run_case(vertices, sparsity, seed, device, args):
    geometry = Geometry(4, 4, 1, vertices // (16 * 32))
    src_np, dst_np, weight_np = planted_edges(
        vertices, sparsity, 16, seed, args.local_probability
    )
    src = torch.from_numpy(src_np).to(device)
    dst = torch.from_numpy(dst_np).to(device)
    weight = torch.from_numpy(weight_np).to(device)
    identity = torch.arange(vertices, device=device)

    stages = []
    (identity_coords, identity_inverse), identity_s = timed(
        device, lambda: extract_block_coords_from_edges(
            src, dst, identity, 32, vertices
        )
    )
    stages.append(("identity", identity, identity_inverse, identity_coords,
                   0.0, identity_s, 0.0, 0.0, 0))

    macro, partition_s = timed(device, lambda: partition_vertices(
        src, dst, weight, vertices, 16,
        iterations=args.macro_iterations,
        candidate_swaps=args.candidate_swaps,
        seed=seed,
    ))
    partition_perm, arrange_s = timed(device, lambda: permutation_from_partitions(
        src, dst, weight, macro, vertices, 16, 32,
        refine_tiles=False, seed=seed,
    ))
    partition_coords, partition_inverse = extract_block_coords_from_edges(
        src, dst, partition_perm, 32, vertices
    )
    stages.append(("partition", partition_perm, partition_inverse,
                   partition_coords, partition_s, arrange_s, 0.0, 0.0, 0))

    refined_perm, refine_s = timed(device, lambda: permutation_from_partitions(
        src, dst, weight, macro, vertices, 16, 32,
        refine_tiles=True,
        micro_iterations=args.micro_iterations,
        candidate_swaps=args.candidate_swaps,
        seed=seed,
    ))
    refined_coords, refined_inverse = extract_block_coords_from_edges(
        src, dst, refined_perm, 32, vertices
    )
    stages.append(("permutation", refined_perm, refined_inverse,
                   refined_coords, partition_s, refine_s, 0.0, 0.0, 0))

    (lossy_coords, dropped_weight_fraction, dropped_blocks), lossy_s = timed(
        device, lambda: prune_low_weight_blocks(
            src, dst, weight, refined_inverse, refined_coords,
            32, vertices, args.lossy_weight_budget,
        )
    )
    stages.append(("lossy", refined_perm, refined_inverse, lossy_coords,
                   partition_s, refine_s, lossy_s,
                   dropped_weight_fraction, dropped_blocks))

    rows = []
    for (
        name, perm, inverse, coords, part_s, perm_s, lossy_s,
        dropped_weight_fraction, dropped_blocks,
    ) in stages:
        pairs = coords.detach().cpu().numpy()
        pairs = pairs[pairs[:, 0] != pairs[:, 1]] if len(pairs) else pairs
        owners = trivial_owners(geometry, pairs)
        metrics = quality(src, dst, inverse, coords, geometry, owners)
        accelerator = estimate_accelerator(geometry, pairs, owners, args)
        rows.append({
            "vertices": vertices, "sparsity": sparsity, "seed": seed,
            "edges": len(src_np), "stage": name,
            "partition_seconds": part_s, "permutation_seconds": perm_s,
            "scheduling_seconds": 0.0,
            "stage_increment_seconds": (
                perm_s if name == "identity" else
                part_s if name == "partition" else perm_s
                if name == "permutation" else lossy_s
            ),
            "lossy_seconds": lossy_s,
            "cumulative_seconds": part_s + perm_s + lossy_s,
            "dropped_weight_fraction": dropped_weight_fraction,
            "dropped_blocks": dropped_blocks,
            "owner_policy": "endpoint-local",
            "endpoint_candidate_cycles": accelerator["estimated_iteration_cycles"],
            "balanced_candidate_cycles": "",
            **metrics,
            **accelerator,
        })

    pairs = lossy_coords.detach().cpu().numpy()
    pairs = pairs[pairs[:, 0] != pairs[:, 1]] if len(pairs) else pairs
    endpoint = trivial_owners(geometry, pairs)
    endpoint_accelerator = {
        key: rows[-1][key] for key in (
            "estimated_initialization_cycles", "estimated_iteration_cycles",
            "estimated_total_cycles", "estimated_physical_link_flits",
            "estimated_accuracy",
        )
    }
    accelerator_cache = {endpoint.tobytes(): endpoint_accelerator}

    def candidate_cycles(candidate):
        key = candidate.tobytes()
        if key not in accelerator_cache:
            accelerator_cache[key] = estimate_accelerator(
                geometry, pairs, candidate, args
            )
        return accelerator_cache[key]["estimated_iteration_cycles"]

    selection, schedule_s = timed(
        device,
        lambda: select_cross_owners(
            geometry, pairs, candidate_cycles,
        ),
    )
    owners, owner_policy, candidate_scores = selection
    metrics = quality(src, dst, refined_inverse, lossy_coords, geometry, owners)
    accelerator = accelerator_cache[owners.tobytes()]
    rows.append({
        "vertices": vertices, "sparsity": sparsity, "seed": seed,
        "edges": len(src_np), "stage": "scheduled",
        "partition_seconds": partition_s, "permutation_seconds": refine_s,
        "scheduling_seconds": schedule_s,
        "stage_increment_seconds": schedule_s,
        "lossy_seconds": lossy_s,
        "cumulative_seconds": partition_s + refine_s + lossy_s + schedule_s,
        "dropped_weight_fraction": dropped_weight_fraction,
        "dropped_blocks": dropped_blocks,
        "owner_policy": owner_policy,
        "endpoint_candidate_cycles": candidate_scores["endpoint-local"],
        "balanced_candidate_cycles": candidate_scores["balanced"],
        **metrics,
        **accelerator,
    })
    return rows


def plot_results(rows, output_dir: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(output_dir / ".mpl-cache"))
    import matplotlib.pyplot as plt

    stages = ["partition", "permutation", "lossy", "scheduled"]
    colors = dict(zip(stages, ["#377eb8", "#4daf4a", "#984ea3", "#e41a1c"]))
    for metric, ylabel, filename in [
        ("stage_increment_seconds", "Individual stage runtime (s)", "stage_runtime_vs_sparsity.png"),
        ("cumulative_seconds", "Cumulative compiler time (s)", "compiler_runtime_vs_sparsity.png"),
        ("estimated_iteration_cycles", "Ising Iteration Cycles", "runtime_vs_sparsity.png"),
        ("occupied_blocks", "Occupied off-diagonal 32x32 blocks", "blocks_vs_sparsity.png"),
        ("cross_edges", "Edges crossing 4x4 chip partitions", "cross_edges_vs_sparsity.png"),
        ("owner_distance", "Cross-work owner Manhattan distance", "owner_distance_vs_sparsity.png"),
    ]:
        fig, axes = plt.subplots(1, len(sorted({r['vertices'] for r in rows})),
                                 figsize=(5 * len(sorted({r['vertices'] for r in rows})), 4),
                                 squeeze=False)
        for axis, vertices in zip(axes[0], sorted({r["vertices"] for r in rows})):
            for stage in stages:
                selected = [r for r in rows if r["vertices"] == vertices and r["stage"] == stage]
                xs = sorted({r["sparsity"] for r in selected})
                ys = [np.mean([r[metric] for r in selected if r["sparsity"] == x]) for x in xs]
                axis.plot([100*x for x in xs], ys, marker="o", label=stage, color=colors[stage])
            if metric == "estimated_iteration_cycles":
                identity_rows = [
                    r for r in rows
                    if r["vertices"] == vertices and r["stage"] == "identity"
                ]
                baseline_sparsity = min(r["sparsity"] for r in identity_rows)
                baseline = np.mean([
                    r[metric] for r in identity_rows
                    if r["sparsity"] == baseline_sparsity
                ])
                axis.axhline(
                    baseline, color="#555555", linestyle="--", linewidth=1.5,
                    label=f"identity baseline ({baseline:.0f})",
                )
            axis.set_title(f"{vertices:,} spins")
            axis.set_xlabel("Graph sparsity (%)")
            if metric in {"stage_increment_seconds", "cumulative_seconds"}:
                axis.set_yscale("log")
            axis.grid(alpha=0.25)
        axes[0][0].set_ylabel(ylabel)
        axes[0][-1].legend()
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=180)
        plt.close(fig)

    # Resolve the high-sparsity transition by plotting logarithmic density.
    # Density halves at each sample, so equal x distances represent equal
    # multiplicative reductions in the number of possible edges.
    fig, axes = plt.subplots(
        1, len(sorted({r["vertices"] for r in rows})),
        figsize=(5 * len(sorted({r["vertices"] for r in rows})), 4),
        squeeze=False,
    )
    for axis, vertices in zip(axes[0], sorted({r["vertices"] for r in rows})):
        available = sorted({
            r["sparsity"] for r in rows if r["vertices"] == vertices
        })
        positive_density = [1.0 - value for value in available if value < 1.0]
        floor = min(positive_density) / 2 if positive_density else 1e-6
        x_by_sparsity = {
            value: -np.log10(max(1.0 - value, floor)) for value in available
        }
        for stage in stages:
            selected = [
                r for r in rows
                if r["vertices"] == vertices and r["stage"] == stage
            ]
            sparsities = sorted({r["sparsity"] for r in selected})
            samples = [
                [
                    r["estimated_iteration_cycles"] for r in selected
                    if r["sparsity"] == sparsity
                ]
                for sparsity in sparsities
            ]
            axis.errorbar(
                [x_by_sparsity[sparsity] for sparsity in sparsities],
                [np.mean(values) for values in samples],
                yerr=[np.std(values) for values in samples],
                marker="o", capsize=3, label=stage, color=colors[stage],
            )
        identity_rows = [
            r for r in rows
            if r["vertices"] == vertices and r["stage"] == "identity"
        ]
        baseline_sparsity = min(r["sparsity"] for r in identity_rows)
        baseline = np.mean([
            r["estimated_iteration_cycles"] for r in identity_rows
            if r["sparsity"] == baseline_sparsity
        ])
        axis.axhline(
            baseline, color="#555555", linestyle="--", linewidth=1.5,
            label=f"identity baseline ({baseline:.0f})",
        )
        tick_values = available
        axis.set_xticks([x_by_sparsity[value] for value in tick_values])
        axis.set_xticklabels(
            [f"{100 * value:.4g}" for value in tick_values], rotation=45,
            ha="right",
        )
        axis.set_title(f"{vertices:,} spins")
        axis.set_xlabel("Graph sparsity (%) — logarithmic density spacing")
        axis.grid(alpha=0.25)
    axes[0][0].set_ylabel("Ising Iteration Cycles")
    axes[0][-1].legend()
    fig.tight_layout()
    fig.savefig(output_dir / "runtime_vs_log_sparsity.png", dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sizes", default="512,4096,16384")
    parser.add_argument(
        "--sparsities",
        default="0.9921875,0.99609375,0.998046875,0.9990234375,"
                "0.99951171875,0.999755859375,1.0",
    )
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--local-probability", type=float, default=0.9)
    parser.add_argument("--macro-iterations", type=int, default=4)
    parser.add_argument("--micro-iterations", type=int, default=3)
    parser.add_argument("--candidate-swaps", type=int, default=32)
    parser.add_argument("--lossy-weight-budget", type=float, default=0.01)
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=4)
    parser.add_argument("--cross-mvms", type=int, default=16)
    parser.add_argument("--fifo-depth", type=int, default=4)
    args = parser.parse_args()
    sizes = parse_csv(args.sizes, int)
    sparsities = parse_csv(args.sparsities, float)
    seeds = parse_csv(args.seeds, int)
    for vertices in sizes:
        if vertices % 512:
            parser.error("every size must be divisible by 512 for a 4x4 NoC")
    selected = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if selected == "auto":
        selected = "cpu"
    device = torch.device(selected)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for vertices in sizes:
        for sparsity in sparsities:
            for seed in seeds:
                case_rows = run_case(vertices, sparsity, seed, device, args)
                rows.extend(case_rows)
                print(f"done vertices={vertices} sparsity={sparsity} seed={seed}", flush=True)
    csv_path = args.output_dir / "mapping_stage_results.csv"
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    plot_results(rows, args.output_dir)
    print(f"wrote {csv_path} and plots to {args.output_dir}")


if __name__ == "__main__":
    main()
