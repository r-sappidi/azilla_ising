#!/usr/bin/env python3
"""Measure node throughput and rank dense Azilla hierarchy configurations.

Only one hierarchy node is elaborated per measurement. Exact dense interaction
counts project its measured H0, H1, and cross-H1 service rates to large systems.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERF_PREFIX = "PERF_CSV,"


def integer_list(text: str) -> list[int]:
    values = sorted({int(value) for value in text.split(",")})
    if not values or any(value < 1 for value in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def parse_perf_line(output: str) -> dict[str, str]:
    lines = [line for line in output.splitlines() if line.startswith(PERF_PREFIX)]
    if len(lines) != 1:
        raise RuntimeError(f"expected one {PERF_PREFIX} line, found {len(lines)}")
    fields = {}
    for item in lines[0][len(PERF_PREFIX):].split(","):
        key, value = item.split("=", 1)
        fields[key] = value
    return fields


def run_measurements(args: argparse.Namespace, output_dir: Path) -> list[dict[str, str]]:
    results = []
    for mvm_count in args.mvm_counts:
        for output_lanes in args.output_lanes:
            if output_lanes > mvm_count:
                continue
            label = f"m{mvm_count}_o{output_lanes}"
            command = [
                "make", "-C", "tb", "ramulator-node-perf-test",
                f"PERF_MVM_COUNT={mvm_count}",
                f"PERF_OUTPUT_LANES={output_lanes}",
                f"PERF_WORK_BLOCKS={args.work_blocks}",
                f"PERF_TOTAL_BLOCKS={args.total_spins // 32}",
                f"PERF_MEM_LANES={args.mem_lanes}",
                f"PERF_MAX_OUTSTANDING={args.max_outstanding}",
                f"PERF_DATASET={args.dataset}",
            ]
            print(f"[measure] {label}", flush=True)
            completed = subprocess.run(
                command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False)
            log_path = output_dir / f"{label}.log"
            log_path.write_text(completed.stdout)
            if completed.returncode:
                raise RuntimeError(f"benchmark {label} failed; see {log_path}")
            row = parse_perf_line(completed.stdout)
            row["log"] = str(log_path.relative_to(ROOT))
            results.append(row)
    return results


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise RuntimeError(f"no rows to write to {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_measurements(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def choose2(value: int) -> int:
    return value * (value - 1) // 2


def mesh_shape(node_count: int) -> tuple[int, int]:
    height = math.isqrt(node_count)
    while node_count % height:
        height -= 1
    return node_count // height, height


def estimate_configurations(
    args: argparse.Namespace, measurements: list[dict[str, str]]
) -> list[dict[str, object]]:
    rates = {
        (int(row["mvm_count"]), int(row["output_lanes"])):
            float(row["blocks_per_cycle"])
        for row in measurements
    }
    node_choices = sorted(rates)
    total_blocks = args.total_spins // 32
    rows: list[dict[str, object]] = []

    for h1_count in args.h1_counts:
        for h0_per_h1 in args.h0_counts:
            denominator = h1_count * h0_per_h1
            if total_blocks % denominator:
                continue
            cores_per_h0 = total_blocks // denominator
            if cores_per_h0 > args.max_cores_per_h0 or cores_per_h0 & (cores_per_h0 - 1):
                continue
            interface_count = h1_count * (h0_per_h1 + 2)
            if interface_count > args.max_memory_interfaces:
                continue

            blocks_per_h1 = h0_per_h1 * cores_per_h0
            h0_work = choose2(cores_per_h0)
            h1_work = choose2(blocks_per_h1) - h0_per_h1 * choose2(cores_per_h0)
            cross_total = choose2(total_blocks) - h1_count * choose2(blocks_per_h1)
            cross_work = math.ceil(cross_total / h1_count)

            for h0_choice, h1_choice, cross_choice in itertools.product(
                node_choices, repeat=3
            ):
                h0_mvm, h0_lanes = h0_choice
                h1_mvm, h1_lanes = h1_choice
                cross_mvm, cross_lanes = cross_choice
                total_mvms = h1_count * (
                    h0_per_h1 * h0_mvm + h1_mvm + cross_mvm)
                total_output_lanes = h1_count * (
                    h0_per_h1 * h0_lanes + h1_lanes + cross_lanes)
                if (total_mvms > args.max_total_mvms or
                        total_output_lanes > args.max_total_output_lanes):
                    continue

                h0_cycles = math.ceil(h0_work / rates[h0_choice]) if h0_work else 0
                h1_cycles = math.ceil(h1_work / rates[h1_choice]) if h1_work else 0
                cross_cycles = math.ceil(cross_work / rates[cross_choice]) if cross_work else 0
                estimated_cycles = max(32, h0_cycles, h1_cycles, cross_cycles)
                bottleneck = max(
                    (("core", 32), ("h0", h0_cycles), ("h1", h1_cycles),
                     ("cross", cross_cycles)), key=lambda item: item[1])[0]
                mesh_x, mesh_y = mesh_shape(h1_count)
                rows.append({
                    "estimated_cycles": estimated_cycles, "bottleneck": bottleneck,
                    "mesh_x": mesh_x, "mesh_y": mesh_y, "h1_count": h1_count,
                    "h0_per_h1": h0_per_h1, "cores_per_h0": cores_per_h0,
                    "h0_mvm": h0_mvm, "h0_output_lanes": h0_lanes,
                    "h1_mvm": h1_mvm, "h1_output_lanes": h1_lanes,
                    "cross_mvm": cross_mvm, "cross_output_lanes": cross_lanes,
                    "h0_cycles": h0_cycles, "h1_cycles": h1_cycles,
                    "cross_cycles": cross_cycles, "memory_interfaces": interface_count,
                    "total_mvms": total_mvms, "total_output_lanes": total_output_lanes,
                })

    rows.sort(key=lambda row: (
        row["estimated_cycles"], row["total_mvms"],
        row["total_output_lanes"], row["memory_interfaces"]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mvm-counts", type=integer_list, default=integer_list("1,2,4,8,16"))
    parser.add_argument("--output-lanes", type=integer_list, default=integer_list("1,2,4"))
    parser.add_argument("--h1-counts", type=integer_list, default=integer_list("4,8,16,32"))
    parser.add_argument("--h0-counts", type=integer_list, default=integer_list("2,4,8,16"))
    parser.add_argument("--work-blocks", type=int, default=256)
    parser.add_argument("--total-spins", type=int, default=65536)
    parser.add_argument("--dataset", default="g65536_kings.txt")
    parser.add_argument("--mem-lanes", type=int, default=16)
    parser.add_argument("--max-outstanding", type=int, default=64)
    parser.add_argument("--max-cores-per-h0", type=int, default=32)
    parser.add_argument("--max-memory-interfaces", type=int, default=96)
    parser.add_argument("--max-total-mvms", type=int, default=384)
    parser.add_argument("--max-total-output-lanes", type=int, default=384)
    parser.add_argument("--output-dir", type=Path, default=Path("build/ramulator_perf_sweep"))
    parser.add_argument("--analyze-only", action="store_true",
                        help="reuse microbenchmarks.csv instead of running RTL")
    args = parser.parse_args()

    if args.total_spins % 32:
        parser.error("--total-spins must be divisible by 32")
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    measurement_path = output_dir / "microbenchmarks.csv"
    if args.analyze_only:
        measurements = read_measurements(measurement_path)
    else:
        measurements = run_measurements(args, output_dir)
        write_csv(measurement_path, measurements)

    configurations = estimate_configurations(args, measurements)
    if not configurations:
        raise RuntimeError("no geometry satisfies the requested resource limits")
    ranking_path = output_dir / "ranked_configurations.csv"
    write_csv(ranking_path, configurations)
    best = configurations[0]
    print("\nBest dense projection within the supplied resource limits:")
    print(f"  mesh={best['mesh_x']}x{best['mesh_y']}, H0/H1={best['h0_per_h1']}, "
          f"cores/H0={best['cores_per_h0']}")
    print(f"  H0 MVM/lanes={best['h0_mvm']}/{best['h0_output_lanes']}, "
          f"H1={best['h1_mvm']}/{best['h1_output_lanes']}, "
          f"cross={best['cross_mvm']}/{best['cross_output_lanes']}")
    print(f"  estimated cycles={best['estimated_cycles']}, "
          f"bottleneck={best['bottleneck']}, interfaces={best['memory_interfaces']}, "
          f"MVMs={best['total_mvms']}, output lanes={best['total_output_lanes']}")
    baseline = next((row for row in configurations if
                     row["h1_count"] == 16 and row["h0_per_h1"] == 4 and
                     row["cores_per_h0"] == 32 and
                     row["h0_mvm"] == row["h1_mvm"] == row["cross_mvm"] == 4 and
                     row["h0_output_lanes"] == row["h1_output_lanes"] ==
                     row["cross_output_lanes"] == 1), None)
    if baseline:
        speedup = baseline["estimated_cycles"] / best["estimated_cycles"]
        print(f"  projected speedup over 4/4/4 MVM, one-lane baseline: {speedup:.3f}x")
    print(f"Measurements: {measurement_path}")
    print(f"Full ranking: {ranking_path}")
    print("Projection excludes mesh hop/contention latency, scheduler overhead, and CDC/PHY effects.")


if __name__ == "__main__":
    main()
