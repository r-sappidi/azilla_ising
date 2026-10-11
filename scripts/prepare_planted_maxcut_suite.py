#!/usr/bin/env python3
"""Generate sparse planted bipartite MaxCut instances with a certified cut.

The left/right partition cuts every positive edge, so the declared optimum is
the number of undirected edges.  Edges form deterministic degree-``d`` cyclic
matchings.  Output uses Azilla's symmetric one-indexed dataset format.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def comma_ints(text: str) -> list[int]:
    return sorted({int(value) for value in text.split(",") if value.strip()})


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_instance(path: Path, spins: int, degree: int) -> int:
    if spins % 2:
        raise ValueError("spin count must be even")
    half = spins // 2
    if not 0 < degree <= half:
        raise ValueError("degree must be between 1 and spins/2")
    edges = half * degree
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w") as output:
        output.write(f"{spins} {edges}\n")
        for left in range(half):
            for offset in range(degree):
                right = half + (left + offset) % half
                # The implemented update aligns with positive J and anti-aligns
                # with negative J.  Negative couplings therefore encode MaxCut.
                output.write(f"{left + 1} {right + 1} -1\n")
                output.write(f"{right + 1} {left + 1} -1\n")
    temporary.replace(path)
    return edges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=comma_ints,
        default=comma_ints("1024,4096,16384,65536,131072,262144,1048576"),
    )
    parser.add_argument("--degrees", type=comma_ints, default=comma_ints("4,16"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/cir_evaluation/solution_quality/datasets"),
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="write datasets; without this flag only emit a size/space plan",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for spins in args.sizes:
        for degree in args.degrees:
            edges = spins // 2 * degree
            path = args.output_dir / f"planted_bipartite_n{spins}_d{degree}.txt"
            if args.generate and not path.is_file():
                write_instance(path, spins, degree)
            entries.append({
                "name": path.stem,
                "family": "planted-bipartite-known-optimum",
                "spins": spins,
                "degree": degree,
                "undirected_edges": edges,
                "known_maxcut": edges,
                "cut_weight_mode": "absolute",
                "dataset": str(path),
                "generated": path.is_file(),
                "bytes": path.stat().st_size if path.is_file() else None,
                "sha256": digest(path) if path.is_file() else None,
                "planted_partition": "spins [0,n/2) versus [n/2,n)",
            })
    manifest = {
        "format": "symmetric one-indexed Azilla dataset",
        "purpose": (
            "solution-quality scaling with known optimum; not RTL timing and "
            "not representative of every hard MaxCut distribution"
        ),
        "recommended_seeds": 30,
        "report": [
            "best and median cut", "optimality gap", "success probability",
            "iterations to best", "time to target", "time to solution",
        ],
        "datasets": entries,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        f"points={len(entries)} generated={sum(x['generated'] for x in entries)} "
        f"manifest={manifest_path}"
    )


if __name__ == "__main__":
    main()
