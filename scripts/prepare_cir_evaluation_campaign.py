#!/usr/bin/env python3
"""Emit the frozen CIR-ablation and fixed-H1 design-space experiment matrix.

This script deliberately does not run a simulator.  It creates a reviewable,
versioned contract that launchers can consume after both execution modes are
available.  Every DSE organization retains 2,048 cores (65,536 spins) per H1.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


SPINS_PER_CORE = 32
CORES_PER_H1 = 2048
REFERENCE_H0S = 8
REFERENCE_CORES_PER_H0 = 256
REFERENCE_H0_MVMS = 4
REFERENCE_H1_MVMS = 2
REFERENCE_CROSS_MVMS = 4
REFERENCE_MVMS_PER_H1 = 38


def comma_ints(text: str) -> list[int]:
    return sorted({int(value) for value in text.split(",") if value.strip()})


def mesh_shapes(nodes: int) -> list[tuple[int, int]]:
    return [
        (x, nodes // x)
        for x in range(1, nodes + 1)
        if nodes % x == 0 and x <= nodes // x
    ]


def fixed_budget_allocations(h0s: int) -> list[tuple[int, int, int]]:
    """Useful allocations with exactly 38 MVMs/H1, including the reference."""
    allocations = []
    for h0_mvms in (1, 2, 4, 8, 16):
        for h1_mvms in (1, 2, 4, 6, 8, 12, 16):
            cross_mvms = REFERENCE_MVMS_PER_H1 - h0s * h0_mvms - h1_mvms
            if cross_mvms in (1, 2, 4, 6, 8, 12, 16):
                allocations.append((h0_mvms, h1_mvms, cross_mvms))
    return allocations


def row(
    phase: str, spins: int, mesh: tuple[int, int], h0s: int,
    engines: tuple[int, int, int], execution_mode: str, fidelity: str,
    workload: str, replicate: int = 0,
) -> dict[str, object]:
    h0_mvms, h1_mvms, cross_mvms = engines
    return {
        "phase": phase,
        "workload": workload,
        "spins": spins,
        "mesh_x": mesh[0],
        "mesh_y": mesh[1],
        "h1_nodes": mesh[0] * mesh[1],
        "h0_per_h1": h0s,
        "cores_per_h0": CORES_PER_H1 // h0s,
        "spins_per_core": SPINS_PER_CORE,
        "h0_mvms": h0_mvms,
        "h1_mvms": h1_mvms,
        "cross_mvms": cross_mvms,
        "mvms_per_h1": h0s * h0_mvms + h1_mvms + cross_mvms,
        "execution_mode": execution_mode,
        "fidelity": fidelity,
        "iterations": 1 if workload == "dense-block-stress" else 10,
        "replicate": replicate,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/cir_evaluation/campaign"),
    )
    parser.add_argument(
        "--sizes", type=comma_ints,
        default=comma_ints("65536,131072,262144"),
    )
    parser.add_argument(
        "--h0-counts", type=comma_ints, default=comma_ints("2,4,8,16")
    )
    args = parser.parse_args()

    if any(spins % (CORES_PER_H1 * SPINS_PER_CORE) for spins in args.sizes):
        parser.error("sizes must be multiples of the fixed 65,536-spin H1")
    if any(CORES_PER_H1 % h0s for h0s in args.h0_counts):
        parser.error("each H0 count must divide 2,048 cores/H1")

    rows: list[dict[str, object]] = []
    reference = (
        REFERENCE_H0_MVMS, REFERENCE_H1_MVMS, REFERENCE_CROSS_MVMS
    )
    for spins in args.sizes:
        nodes = spins // (CORES_PER_H1 * SPINS_PER_CORE)
        shapes = mesh_shapes(nodes)
        balanced = shapes[-1]

        # Broad, resource-constrained screening.  Exact finalists are chosen
        # only after calibrated results are available.
        for h0s in args.h0_counts:
            for engines in fixed_budget_allocations(h0s):
                rows.append(row(
                    "dense-dse-screen", spins, balanced, h0s, engines,
                    "cir", "calibrated-event", "dense-block-stress",
                ))

        # Mesh-shape sensitivity for the proposed fixed H1.
        for mesh in shapes:
            rows.append(row(
                "mesh-shape-screen", spins, mesh, REFERENCE_H0S, reference,
                "cir", "calibrated-event", "dense-block-stress",
            ))

        # CIR ablation uses identical geometry, mapping, memory and links.
        for mode in ("cir", "cores-only"):
            rows.append(row(
                "cir-ablation-screen", spins, balanced, REFERENCE_H0S,
                reference, mode, "calibrated-event", "dense-block-stress",
            ))
            rows.append(row(
                "cir-ablation-sparse", spins, balanced, REFERENCE_H0S,
                reference, mode, "calibrated-event", "sparse-suite",
            ))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "experiment_matrix.csv"
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    contract = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "planned; exact-event and RTL finalists selected from screening",
        "baseline": {
            "name": "cores-only destination-stationary",
            "definition": (
                "Each core computes every contribution to its 32 owned spins. "
                "Off-diagonal symmetric blocks are evaluated once per endpoint; "
                "weights are stored in directed destination order; remote state "
                "may be shared within an H1; no partial vectors leave a core."
            ),
            "fairness": (
                "Same core/H0/H1 geometry, graph placement, memory technology and "
                "aggregate bandwidth, link parameters, arithmetic, and iterations."
            ),
        },
        "fixed_h1": {
            "spins": CORES_PER_H1 * SPINS_PER_CORE,
            "cores": CORES_PER_H1,
            "reference": {
                "h0_per_h1": REFERENCE_H0S,
                "cores_per_h0": REFERENCE_CORES_PER_H0,
                "h0_mvms": REFERENCE_H0_MVMS,
                "h1_mvms": REFERENCE_H1_MVMS,
                "cross_mvms": REFERENCE_CROSS_MVMS,
                "mvms_per_h1": REFERENCE_MVMS_PER_H1,
            },
        },
        "selection": {
            "constraint": "38 shared CIR MVM engines per H1 during primary DSE",
            "finalists": (
                "Pareto frontier over iteration cycles, physical-link flits, "
                "p95/max link utilization, stalls, memory demand, and load balance"
            ),
            "confirmation": "exact-event/Ramulator, then representative VCS RTL",
        },
        "matrix": str(csv_path),
    }
    (args.output_dir / "campaign_contract.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n"
    )
    schedule_dir = args.output_dir / "candidate_schedules"
    schedule_dir.mkdir(exist_ok=True)
    schedules = {
        "constant_a3_b1_n3.csv": [(3, 1, 3)] * 100,
        "noise_decay_a3_b1_n8_to_0.csv": [
            (3, 1, max(0, 8 - (9 * iteration // 100)))
            for iteration in range(100)
        ],
        "a_ramp_noise_decay.csv": [
            (iteration * 4 // 100, 1, max(0, 8 - (9 * iteration // 100)))
            for iteration in range(100)
        ],
        "slow_a_ramp_noise_decay_500.csv": [
            (iteration * 8 // 500, 1, max(0, 16 - (17 * iteration // 500)))
            for iteration in range(500)
        ],
        "slow_noise_decay_a0_500.csv": [
            (0, 1, max(0, 16 - (17 * iteration // 500)))
            for iteration in range(500)
        ],
        "cyclic_ramp_noise_500.csv": [
            (
                (iteration % 100) * 8 // 100,
                1,
                max(0, 16 - (17 * (iteration % 100) // 100)),
            )
            for iteration in range(500)
        ],
    }
    for name, values in schedules.items():
        with (schedule_dir / name).open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("iteration", "coeff_a", "coeff_b", "noise_amplitude"))
            writer.writerows(
                (iteration, *coefficients)
                for iteration, coefficients in enumerate(values)
            )
    print(f"rows={len(rows)} matrix={csv_path}")


if __name__ == "__main__":
    main()
