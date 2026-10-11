#!/usr/bin/env python3
"""Stream-validate benchmark hashes, geometry, counts, and edge symmetry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("results/paper_benchmarks/datasets/manifest.json"),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    report = []
    for entry in manifest["datasets"]:
        path = Path(entry["dataset"])
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256(path)
        if digest != entry["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {path}")
        with path.open() as source:
            header = source.readline().split()
            if len(header) != 2:
                raise ValueError(f"invalid header in {path}")
            capacity, known_cut = map(int, header)
            if capacity != entry["capacity_spins"] or known_cut != 0:
                raise ValueError(f"manifest/header mismatch for {path}")
            pairs = 0
            while True:
                forward = source.readline()
                if not forward:
                    break
                reverse = source.readline()
                if not reverse:
                    raise ValueError(f"unpaired final record in {path}")
                first = tuple(map(int, forward.split()))
                second = tuple(map(int, reverse.split()))
                if len(first) != 3 or len(second) != 3:
                    raise ValueError(f"malformed edge pair in {path}")
                u, v, weight = first
                rv, ru, reverse_weight = second
                if (u, v, weight) != (ru, rv, reverse_weight):
                    raise ValueError(f"non-symmetric adjacent records in {path}")
                if not (1 <= u <= entry["problem_vertices"]):
                    raise ValueError(f"edge reaches padding/outside graph in {path}")
                if not (1 <= v <= entry["problem_vertices"]):
                    raise ValueError(f"edge reaches padding/outside graph in {path}")
                if u == v or not -128 <= weight <= 127 or weight == 0:
                    raise ValueError(f"invalid edge in {path}")
                pairs += 1
        if pairs != entry["undirected_edges"]:
            raise ValueError(
                f"edge-count mismatch for {path}: {pairs} != "
                f"{entry['undirected_edges']}"
            )
        geometry = entry["geometry"]
        implemented = (
            geometry["mesh_x"] * geometry["mesh_y"]
            * geometry["h0_per_h1"] * geometry["cores_per_h0"] * 32
        )
        if implemented != capacity:
            raise ValueError(f"geometry does not implement {capacity} spins")
        report.append({
            "name": entry["name"],
            "path": str(path),
            "sha256": digest,
            "capacity_spins": capacity,
            "undirected_edges": pairs,
            "status": "PASS",
        })
        print(
            f"PASS {entry['name']} spins={capacity} edges={pairs}", flush=True
        )
    output = args.report or args.manifest.with_name("validation_report.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"report={output} datasets={len(report)}")


if __name__ == "__main__":
    main()
