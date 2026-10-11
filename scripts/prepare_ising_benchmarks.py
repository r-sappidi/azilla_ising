#!/usr/bin/env python3
"""Fetch published Gset instances and generate reproducible sparse sweeps.

Outputs use Azilla's symmetric, one-indexed text format::

    <padded_spin_count> <known_cut_or_zero>
    <row> <column> <signed_int8_weight>

The generated graph occupies the first ``problem_vertices`` spins; remaining
capacity spins are isolated.  A manifest records provenance, padding, seeds,
hashes, edge counts, and architecture geometry for every dataset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


GSET_BASE = "https://web.stanford.edu/~yyye/yyye/Gset"
GSET_IDS = (14, 19, 21, 22, 31, 32, 35, 37, 47, 70, 81)
LITERATURE_SOURCES = (
    {
        "title": "Stanford Gset Max-Cut collection",
        "url": f"{GSET_BASE}/",
        "benchmarks": "G1-G81, 800-20,000+ vertices, mixed graph structures",
        "campaign_use": "selected exact public instances",
    },
    {
        "title": "Precision meets speed through an FPGA-based natively sparse Ising machine",
        "url": "https://doi.org/10.1038/s41467-026-75119-0",
        "benchmarks": "Gset plus A1/A2/A3 at 10K/16K/20K vertices",
        "campaign_use": "Gset selection and clearly labeled A1-A3 scale proxies",
    },
    {
        "title": "Oscillator-based Ising machine applied for Max-Cut in massively large and sparse graphs",
        "url": "https://doi.org/10.1038/s41598-026-66268-9",
        "benchmarks": "Gset plus regular degree-25 graphs through 20 million vertices",
        "campaign_use": "average-degree-25 random scaling family (not regular)",
    },
    {
        "title": "100,000-spin coherent Ising machine",
        "url": "https://doi.org/10.1126/sciadv.abe7953",
        "benchmarks": "dense Rudy K1000/K10000/K50000/K100000",
        "campaign_use": "documented dense stress family; excluded from sparse scaling",
    },
    {
        "title": "Efficient and Scalable Architecture for Multiple-chip Implementation of Simulated Bifurcation Machines",
        "url": "https://arxiv.org/abs/2311.17370",
        "benchmarks": "full-connectivity 32,768-spin hardware and 100,000-spin projection",
        "campaign_use": "context only; no reusable sparse graph family",
    },
    {
        "title": "Massively parallel probabilistic computing with sparse Ising machines",
        "url": "https://doi.org/10.1038/s41928-022-00774-2",
        "benchmarks": "integer factoring and 3SAT",
        "campaign_use": "future functional workload requiring an auditable frontend",
    },
    {
        "title": "A scalable universal Ising machine based on interaction-centric storage and compute-in-memory",
        "url": "https://doi.org/10.1038/s41928-024-01228-7",
        "benchmarks": "Max-Cut, graph coloring, and EDA layout decomposition",
        "campaign_use": "future application suite",
    },
)


@dataclass(frozen=True)
class Geometry:
    mesh_x: int
    mesh_y: int
    h0_per_h1: int
    cores_per_h0: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def power_of_two_capacity(vertices: int) -> int:
    if vertices <= 0:
        raise ValueError("vertex count must be positive")
    return max(1024, 1 << (vertices - 1).bit_length())


def geometry_for_capacity(spins: int) -> Geometry:
    if spins % (16 * 32):
        raise ValueError("capacity must divide evenly across a 4x4 mesh")
    blocks_per_node = spins // (16 * 32)
    if spins <= 8192:
        return Geometry(4, 4, 1, blocks_per_node)
    if spins == 16384:
        return Geometry(4, 4, 2, 16)
    h0_per_h1 = min(8, max(2, blocks_per_node // 32))
    while blocks_per_node % h0_per_h1:
        h0_per_h1 //= 2
    return Geometry(4, 4, h0_per_h1, blocks_per_node // h0_per_h1)


def download(url: str, path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Azilla-benchmark-preparer/1.0"}
    )
    temporary = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(request, timeout=60) as response:
        temporary.write_bytes(response.read())
    temporary.replace(path)


def write_symmetric_dataset(
    path: Path,
    capacity: int,
    edges: list[tuple[int, int, int]] | np.ndarray,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as output:
        output.write(f"{capacity} 0\n")
        for record in edges:
            u, v, weight = map(int, record)
            if u == v or weight == 0:
                continue
            output.write(f"{u + 1} {v + 1} {weight}\n")
            output.write(f"{v + 1} {u + 1} {weight}\n")
            count += 1
    return count


def convert_gset(raw: Path, output: Path) -> tuple[int, int, int]:
    with raw.open() as source:
        header = source.readline().split()
        if len(header) != 2:
            raise ValueError(f"invalid Gset header in {raw}")
        vertices, declared_edges = map(int, header)
        capacity = power_of_two_capacity(vertices)
        edges: list[tuple[int, int, int]] = []
        for line_number, line in enumerate(source, 2):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 3:
                raise ValueError(f"invalid Gset record {raw}:{line_number}")
            u, v, weight = map(int, fields)
            if not (1 <= u <= vertices and 1 <= v <= vertices):
                raise ValueError(f"Gset endpoint out of range {raw}:{line_number}")
            edges.append((u - 1, v - 1, weight))
    if len(edges) != declared_edges:
        raise ValueError(
            f"Gset edge mismatch for {raw}: {len(edges)} != {declared_edges}"
        )
    written = write_symmetric_dataset(output, capacity, edges)
    return vertices, capacity, written


def random_unique_edges(vertices: int, edge_count: int, seed: int) -> np.ndarray:
    """Return uniformly sampled unique undirected non-loop edges."""
    maximum = vertices * (vertices - 1) // 2
    if not 0 <= edge_count <= maximum:
        raise ValueError("requested random edge count is impossible")
    rng = np.random.default_rng(seed)
    keys = np.empty(0, dtype=np.uint64)
    while keys.size < edge_count:
        need = edge_count - keys.size
        sample_count = max(4096, math.ceil(need * 1.12))
        u = rng.integers(0, vertices, sample_count, dtype=np.uint64)
        v = rng.integers(0, vertices, sample_count, dtype=np.uint64)
        keep = u != v
        lo = np.minimum(u[keep], v[keep])
        hi = np.maximum(u[keep], v[keep])
        sampled = lo * np.uint64(vertices) + hi
        keys = np.unique(np.concatenate((keys, sampled)))
        if keys.size > edge_count:
            keys = rng.choice(keys, size=edge_count, replace=False)
            keys.sort()
    edges = np.empty((edge_count, 3), dtype=np.int64)
    edges[:, 0] = keys // np.uint64(vertices)
    edges[:, 1] = keys % np.uint64(vertices)
    edges[:, 2] = 1
    return edges


def toroidal_edges(vertices: int) -> np.ndarray:
    rows = 1 << ((vertices.bit_length() - 1) // 2)
    while vertices % rows:
        rows //= 2
    columns = vertices // rows
    pairs = []
    for row in range(rows):
        for column in range(columns):
            vertex = row * columns + column
            right = row * columns + (column + 1) % columns
            down = ((row + 1) % rows) * columns + column
            pairs.append((min(vertex, right), max(vertex, right), 1))
            pairs.append((min(vertex, down), max(vertex, down), 1))
    return np.unique(np.asarray(pairs, dtype=np.int64), axis=0)


def manifest_row(
    *,
    name: str,
    family: str,
    source: str,
    path: Path,
    problem_vertices: int,
    undirected_edges: int,
    seed: int | None,
    notes: str,
) -> dict[str, object]:
    with path.open() as dataset:
        capacity = int(dataset.readline().split()[0])
    geometry = geometry_for_capacity(capacity)
    return {
        "name": name,
        "family": family,
        "source": source,
        "dataset": str(path),
        "sha256": sha256(path),
        "problem_vertices": problem_vertices,
        "capacity_spins": capacity,
        "isolated_padding_spins": capacity - problem_vertices,
        "undirected_edges": undirected_edges,
        "directed_records": 2 * undirected_edges,
        "average_degree": 2 * undirected_edges / problem_vertices,
        "density": (
            2 * undirected_edges / (problem_vertices * (problem_vertices - 1))
            if problem_vertices > 1 else 0.0
        ),
        "seed": seed,
        "geometry": asdict(geometry),
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/paper_benchmarks/datasets"),
    )
    parser.add_argument(
        "--include-million", action="store_true",
        help="also generate the 1,048,576-spin toroidal dataset",
    )
    parser.add_argument(
        "--only-million", action="store_true",
        help="append/regenerate only the million-spin toroidal point",
    )
    parser.add_argument(
        "--manifest-only", action="store_true",
        help="refresh manifest metadata without touching prepared datasets",
    )
    args = parser.parse_args()
    root = args.output_dir
    raw = root / "raw" / "gset"
    prepared = root / "prepared"
    rows: list[dict[str, object]] = []
    if args.only_million or args.manifest_only:
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            parser.error("manifest refresh requires an existing manifest")
        rows = json.loads(manifest_path.read_text())["datasets"]
        if args.only_million:
            rows = [
                row for row in rows
                if row["name"] != "toroidal_d4_n1048576"
            ]

    skip_standard = args.only_million or args.manifest_only
    for identifier in (() if skip_standard else GSET_IDS):
        name = f"G{identifier}"
        raw_path = raw / name
        output = prepared / f"gset_{name.lower()}.txt"
        download(f"{GSET_BASE}/{name}", raw_path)
        vertices, _, edges = convert_gset(raw_path, output)
        rows.append(manifest_row(
            name=f"gset_{name.lower()}",
            family="published-gset",
            source=f"{GSET_BASE}/{name}",
            path=output,
            problem_vertices=vertices,
            undirected_edges=edges,
            seed=None,
            notes="Exact Gset graph; padded with isolated spins; symmetric records.",
        ))
        print(f"prepared {name}: vertices={vertices} edges={edges}", flush=True)

    qsb_specs = (
        ("qsb_a1_proxy", 10_000, 524_000 // 2, 2601),
        ("qsb_a2_proxy", 16_000, 786_000 // 2, 2602),
        ("qsb_a3_proxy", 20_000, 1_048_000 // 2, 2603),
    )
    for name, vertices, edges, seed in (() if skip_standard else qsb_specs):
        output = prepared / f"{name}.txt"
        records = random_unique_edges(vertices, edges, seed)
        written = write_symmetric_dataset(
            output, power_of_two_capacity(vertices), records
        )
        rows.append(manifest_row(
            name=name,
            family="published-scale-matched-random-proxy",
            source="https://www.nature.com/articles/s41467-026-75119-0",
            path=output,
            problem_vertices=vertices,
            undirected_edges=written,
            seed=seed,
            notes=(
                "Generated proxy matching published A1/A2/A3 size and symmetric "
                "nonzero count; not the authors' unreleased graph instance."
            ),
        ))
        print(f"prepared {name}: vertices={vertices} edges={written}", flush=True)

    scalable_sizes = (
        [] if skip_standard else [16_384, 32_768, 65_536, 131_072]
    )
    if (args.include_million or args.only_million) and not args.manifest_only:
        scalable_sizes.append(1_048_576)
    for vertices in scalable_sizes:
        output = prepared / f"toroidal_d4_n{vertices}.txt"
        records = toroidal_edges(vertices)
        written = write_symmetric_dataset(output, vertices, records)
        rows.append(manifest_row(
            name=f"toroidal_d4_n{vertices}",
            family="generated-structured-toroidal",
            source="deterministic generator in scripts/prepare_ising_benchmarks.py",
            path=output,
            problem_vertices=vertices,
            undirected_edges=written,
            seed=None,
            notes="Degree-4 2D torus, analogous to the toroidal Gset family.",
        ))
        print(f"prepared toroidal {vertices}: edges={written}", flush=True)

        if vertices <= 131_072:
            edge_count = round(vertices * 25 / 2)
            seed = 2700 + vertices.bit_length()
            output = prepared / f"random_d25_n{vertices}.txt"
            records = random_unique_edges(vertices, edge_count, seed)
            written = write_symmetric_dataset(output, vertices, records)
            rows.append(manifest_row(
                name=f"random_d25_n{vertices}",
                family="generated-random-average-degree-25",
                source="deterministic generator in scripts/prepare_ising_benchmarks.py",
                path=output,
                problem_vertices=vertices,
                undirected_edges=written,
                seed=seed,
                notes=(
                    "Uniform fixed-edge random graph with average degree 25; "
                    "not a random-regular graph."
                ),
            ))
            print(f"prepared random d25 {vertices}: edges={written}", flush=True)

    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "azilla-paper-benchmark-manifest-v1",
        "gset_base": GSET_BASE,
        "format": "symmetric one-indexed signed-int8 edge records",
        "literature_sources": LITERATURE_SOURCES,
        "datasets": rows,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    with (root / "manifest.csv").open("w", newline="") as output:
        flat_rows = []
        for row in rows:
            flat = dict(row)
            geometry = flat.pop("geometry")
            flat.update(geometry)
            flat_rows.append(flat)
        writer = csv.DictWriter(output, fieldnames=list(flat_rows[0]))
        writer.writeheader()
        writer.writerows(flat_rows)
    print(f"manifest={root / 'manifest.json'} datasets={len(rows)}")


if __name__ == "__main__":
    main()
