#!/usr/bin/env python3
"""Generate the fixed-H1 sparse datasets used by the canonical paper sweep.

The main weak-scaling series holds one H1 tile fixed at 8 H0s, 256 cores/H0,
and 32 spins/core (65,536 spins/H1).  Problem size grows only by adding H1
nodes in the sequence 1x1, 2x1, 2x2, 4x2, and 4x4.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from prepare_ising_benchmarks import (
    Geometry,
    manifest_row,
    random_unique_edges,
    toroidal_edges,
    write_symmetric_dataset,
)


GEOMETRIES = {
    65_536: Geometry(1, 1, 8, 256),
    131_072: Geometry(2, 1, 8, 256),
    262_144: Geometry(2, 2, 8, 256),
    524_288: Geometry(4, 2, 8, 256),
    1_048_576: Geometry(4, 4, 8, 256),
}


def integer_list(text: str) -> list[int]:
    return [int(item) for item in text.split(",") if item.strip()]


def community_edges(
    vertices: int, degree: int, seed: int, local_fraction: float = 0.8,
) -> np.ndarray:
    """Generate a reproducible fixed-edge graph with aligned communities."""
    edge_count = vertices * degree // 2
    local_target = round(edge_count * local_fraction)
    rng = np.random.default_rng(seed)
    communities = max(8, vertices // 65_536)
    community_size = vertices // communities
    keys = np.empty(0, dtype=np.uint64)
    while keys.size < local_target:
        need = local_target - keys.size
        count = max(4096, int(need * 1.15))
        group = rng.integers(0, communities, count, dtype=np.uint64)
        u = group * community_size + rng.integers(
            0, community_size, count, dtype=np.uint64
        )
        v = group * community_size + rng.integers(
            0, community_size, count, dtype=np.uint64
        )
        keep = u != v
        lo, hi = np.minimum(u[keep], v[keep]), np.maximum(u[keep], v[keep])
        keys = np.unique(np.concatenate((keys, lo * np.uint64(vertices) + hi)))
        if keys.size > local_target:
            keys = np.sort(rng.choice(keys, size=local_target, replace=False))
    while keys.size < edge_count:
        need = edge_count - keys.size
        count = max(4096, int(need * 1.15))
        u = rng.integers(0, vertices, count, dtype=np.uint64)
        v = rng.integers(0, vertices, count, dtype=np.uint64)
        keep = u != v
        lo, hi = np.minimum(u[keep], v[keep]), np.maximum(u[keep], v[keep])
        keys = np.unique(np.concatenate((keys, lo * np.uint64(vertices) + hi)))
        if keys.size > edge_count:
            preserve = np.arange(local_target)
            extra = rng.choice(
                np.arange(local_target, keys.size),
                size=edge_count - local_target,
                replace=False,
            )
            keys = np.sort(keys[np.concatenate((preserve, extra))])
    edges = np.empty((edge_count, 3), dtype=np.int64)
    edges[:, 0] = keys // np.uint64(vertices)
    edges[:, 1] = keys % np.uint64(vertices)
    edges[:, 2] = 1
    return edges


def add_row(rows, *, name, family, path, vertices, edges, seed, notes):
    row = manifest_row(
        name=name,
        family=family,
        source="canonical generator in scripts/prepare_canonical_sparse_campaign.py",
        path=path,
        problem_vertices=vertices,
        undirected_edges=edges,
        seed=seed,
        notes=notes,
    )
    row["geometry"] = asdict(GEOMETRIES[vertices])
    row["h1_tile"] = {
        "h0_per_h1": 8,
        "cores_per_h0": 256,
        "spins_per_core": 32,
        "spins_per_h1": 65_536,
    }
    rows.append(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_canonical/datasets"),
    )
    parser.add_argument(
        "--sizes", type=integer_list,
        default=list(GEOMETRIES),
        help="comma-separated subset of 65536,131072,262144,524288,1048576",
    )
    parser.add_argument("--seeds", type=integer_list, default=[1, 2, 3])
    parser.add_argument("--core-degree", type=int, default=16)
    parser.add_argument("--degree-sweep-size", type=int, default=131_072)
    parser.add_argument(
        "--degree-sweep", type=integer_list, default=[4, 8, 16, 32, 64]
    )
    args = parser.parse_args()
    invalid = sorted(set(args.sizes) - set(GEOMETRIES))
    if invalid:
        parser.error(f"unsupported sizes: {invalid}")
    prepared = args.output_dir / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    rows = []
    for vertices in args.sizes:
        path = prepared / f"toroidal_d4_n{vertices}.txt"
        edges = toroidal_edges(vertices)
        written = write_symmetric_dataset(path, vertices, edges)
        add_row(
            rows, name=f"toroidal_d4_n{vertices}",
            family="canonical-toroidal-d4", path=path, vertices=vertices,
            edges=written, seed=None, notes="Deterministic degree-4 2D torus.",
        )
        print(f"prepared {path.name}: edges={written}", flush=True)
        for seed in args.seeds:
            for family in ("uniform", "community"):
                degree = args.core_degree
                name = f"{family}_d{degree}_n{vertices}_s{seed}"
                path = prepared / f"{name}.txt"
                if family == "uniform":
                    edges = random_unique_edges(
                        vertices, vertices * degree // 2,
                        10_000 + seed + vertices.bit_length() * 100,
                    )
                    notes = "Uniform fixed-edge random graph."
                else:
                    edges = community_edges(
                        vertices, degree,
                        20_000 + seed + vertices.bit_length() * 100,
                    )
                    notes = "Fixed-edge graph with 80% community-local sampling."
                written = write_symmetric_dataset(path, vertices, edges)
                add_row(
                    rows, name=name, family=f"canonical-{family}-d{degree}",
                    path=path, vertices=vertices, edges=written, seed=seed,
                    notes=notes,
                )
                print(f"prepared {path.name}: edges={written}", flush=True)

    if args.degree_sweep_size in args.sizes:
        vertices = args.degree_sweep_size
        existing = {row["name"] for row in rows}
        for degree in args.degree_sweep:
            for seed in args.seeds:
                name = f"uniform_d{degree}_n{vertices}_s{seed}"
                if name in existing:
                    continue
                path = prepared / f"{name}.txt"
                edges = random_unique_edges(
                    vertices, vertices * degree // 2,
                    30_000 + degree * 100 + seed,
                )
                written = write_symmetric_dataset(path, vertices, edges)
                add_row(
                    rows, name=name, family=f"degree-sensitivity-uniform-d{degree}",
                    path=path, vertices=vertices, edges=written, seed=seed,
                    notes="Degree-sensitivity fixed-edge random graph.",
                )
                print(f"prepared {path.name}: edges={written}", flush=True)

    manifest = {
        "schema": "azilla-canonical-sparse-v1",
        "architecture": {
            "h0_per_h1": 8, "cores_per_h0": 256,
            "spins_per_core": 32, "h0_mvms": 4,
            "h1_mvms": 2, "cross_mvms": 4, "fifo_depth": 4,
        },
        "scaling_rule": "fixed 65536-spin H1 tile; add H1 mesh nodes",
        "datasets": rows,
    }
    path = args.output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    flat = []
    for row in rows:
        item = dict(row)
        item.update(item.pop("geometry"))
        item.pop("h1_tile")
        flat.append(item)
    with (args.output_dir / "manifest.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    print(f"manifest={path} datasets={len(rows)}")


if __name__ == "__main__":
    main()
