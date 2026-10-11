#!/usr/bin/env python3
"""Build and run dense RTL winner configurations with wall-time records."""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def timed(command: list[str], log: Path, timing: Path, env: dict[str, str]) -> tuple[int, float]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log.open("w") as output:
        output.write("command=" + subprocess.list2cmdline(command) + "\n")
        output.flush()
        result = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(timing), *command],
            cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            check=False,
        )
    return result.returncode, time.perf_counter() - started


def verilator_build(
    row: dict[str, str], jobs: int, timing_only: bool
) -> tuple[list[str], Path]:
    config = config_name(row, timing_only)
    binary = ROOT / "build" / f"ising_mesh_floo_{config}" / "Vising_mesh_tb"
    command = [
        "make", "-C", "tb", "floo-mesh-build",
        f"MESH_X_COUNT={row['mesh_x']}", f"MESH_Y_COUNT={row['mesh_y']}",
        f"H0_COUNT={row['h0_per_h1']}", f"CORES_PER_H0={row['cores_per_h0']}",
        f"H0_MVM_COUNT={row['h0_mvms']}", f"H1_MVM_COUNT={row['h1_mvms']}",
        f"CROSS_MVM_COUNT={row['cross_mvms']}", "FIFO_DEPTH=4",
        "SKIP_ZERO_BLOCKS=0", "USE_RAMULATOR=1", "ITERATION_COUNT=1",
        f"TIMING_ONLY={int(timing_only)}", f"VERILATOR_JOBS={jobs}",
        "VERILATOR_OUTPUT_SPLIT=20000",
    ]
    return command, binary


def vcs_build(
    row: dict[str, str], jobs: int, env: dict[str, str], timing_only: bool,
    *, iterations: int = 1, skip_zero_blocks: bool = False,
) -> tuple[list[str], Path]:
    config = config_name(row, timing_only, iterations, skip_zero_blocks)
    build_dir = ROOT / "build" / f"ising_mesh_vcs_{config}"
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / "simv"
    compiler_root = Path("/home/rsappidi/miniforge3/envs/azilla/bin")
    pvalue = [
        ("MESH_X_COUNT", row["mesh_x"]), ("MESH_Y_COUNT", row["mesh_y"]),
        ("H0_COUNT", row["h0_per_h1"]), ("CORES_PER_H0", row["cores_per_h0"]),
        ("H0_MVM_COUNT", row["h0_mvms"]), ("H1_MVM_COUNT", row["h1_mvms"]),
        ("CROSS_MVM_COUNT", row["cross_mvms"]), ("FIFO_DEPTH", "4"),
        ("SKIP_ZERO_BLOCKS", str(int(skip_zero_blocks))), ("USE_RAMULATOR", "1"),
        ("MEM_PIN_COUNT", "128"), ("MEM_PIN_GBPS", "32"),
        ("MEM_LANES", "16"), ("ITERATION_COUNT", str(iterations)),
    ]
    sources = [
        "rtl/ising_pkg.sv", "rtl/j_block_sram.sv", "rtl/banked_state_sram.sv", "rtl/mvm.sv",
        "rtl/symmetric_mvm.sv", "rtl/core.sv", "rtl/hierarchy_node.sv",
        "rtl/h0_adapter.sv", "rtl/h0_tile.sv", "rtl/h1_child_adapter.sv",
        "rtl/h1_tile.sv", "rtl/h1_noc_adapter.sv", "rtl/cross_h1_node.sv",
        "rtl/azilla_floo_router.sv", "rtl/top_node.sv", "rtl/mesh_h1_tile.sv",
        "rtl/ising_mesh.sv", "rtl/dram_weight_streamer.sv",
        "tb/ramulator_dpi_bridge.sv", "tb/ramulator_node_frontend.sv",
        "tb/ising_mesh_tb.sv", "tb/ramulator_dpi.cpp",
    ]
    vcs = str(Path(env["VCS_HOME"]) / "bin" / "vcs")
    command = [
        vcs, "-full64", "-sverilog", "-j", str(jobs),
        "-cpp", str(compiler_root / "x86_64-conda-linux-gnu-g++"),
        "-cc", str(compiler_root / "x86_64-conda-linux-gnu-gcc"),
        "-assert", "svaext", "-assert", "disable_cover", "-timescale=1ns/1ps",
        *(["+define+AZILLA_TIMING_ONLY"] if timing_only else []),
        "-top", "ising_mesh_tb",
        f"-Mdir={build_dir / 'csrc'}", "-o", str(binary),
        *[f"-pvalue+ising_mesh_tb.{key}={value}" for key, value in pvalue],
        "-CFLAGS", f"-std=c++20 -I{ROOT / 'third_party/ramulator2/src'}",
        "-LDFLAGS", f"-no-pie -L{ROOT / 'third_party/ramulator2'} -lramulator "
                     f"-Wl,-rpath,{ROOT / 'third_party/ramulator2'}",
        "-f", "rtl/floo_router_files.f", *sources,
    ]
    return command, binary


def config_name(
    row: dict[str, str], timing_only: bool = True, iterations: int = 1,
    skip_zero_blocks: bool = False,
) -> str:
    return (f"{row['mesh_x']}x{row['mesh_y']}_h0{row['h0_per_h1']}_"
            f"c{row['cores_per_h0']}_e{row['h0_mvms']}-{row['h1_mvms']}-"
            f"{row['cross_mvms']}_f4_s{int(skip_zero_blocks)}_r1_"
            f"i{iterations}_t{int(timing_only)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", choices=("verilator", "vcs"), required=True)
    parser.add_argument("--winners", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument(
        "--arithmetic", action="store_true",
        help="enable arithmetic payload evaluation and final-state comparison",
    )
    args = parser.parse_args()
    timing_only = not args.arithmetic
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.winners.open(newline="") as source:
        rows = list(csv.DictReader(source))

    env = os.environ.copy()
    if args.simulator == "vcs":
        env.setdefault("VCS_HOME", "/home/hwpeng/vcs")
        env["VCS_ARCH_OVERRIDE"] = "linux"
        env["VCS_TARGET_ARCH"] = "linux64"
        env["PATH"] = f"{env['VCS_HOME']}/bin:/usr/bin:/bin"

    summary: list[dict[str, object]] = []
    dataset_dir = ROOT / "results" / "dense_config_search" / "rtl_datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        spins = int(row["spins"])
        dataset = dataset_dir / f"dense_{spins}_header.txt"
        dataset.write_text(f"{spins} 0\n")
        relative_dataset = Path("../../results/dense_config_search/rtl_datasets") / dataset.name
        if args.simulator == "verilator":
            build_command, binary = verilator_build(row, args.jobs, timing_only)
        else:
            build_command, binary = vcs_build(row, args.jobs, env, timing_only)
        label = f"{spins}_{config_name(row, timing_only)}"
        build_rc, build_wall = timed(
            build_command, args.output_dir / f"build_{label}.log",
            args.output_dir / f"build_{label}.time", env,
        )
        sim_rc, sim_wall = -1, 0.0
        noc_stats = (args.output_dir / f"noc_stats_{label}.csv").resolve()
        internal_stats = (args.output_dir / f"internal_stats_{label}.csv").resolve()
        if build_rc == 0 and binary.is_file():
            sim_rc, sim_wall = timed(
                [str(binary), f"+DATASET={relative_dataset}",
                 f"+NOC_STATS_FILE={noc_stats}",
                 f"+INTERNAL_STATS_FILE={internal_stats}"],
                args.output_dir / f"sim_{label}.log",
                args.output_dir / f"sim_{label}.time", env,
            )
        summary.append({
            "simulator": args.simulator, "spins": spins,
            "mode": "timing-only" if timing_only else "arithmetic",
            "config": config_name(row, timing_only),
            "build_wall_seconds": f"{build_wall:.3f}", "build_exit": build_rc,
            "simulation_wall_seconds": f"{sim_wall:.3f}", "simulation_exit": sim_rc,
            "binary": str(binary.relative_to(ROOT)), "noc_stats": str(noc_stats),
            "internal_stats": str(internal_stats),
        })
        with (args.output_dir / "summary.csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(summary[0]))
            writer.writeheader(); writer.writerows(summary)
        if build_rc or sim_rc:
            raise SystemExit(build_rc or sim_rc)


if __name__ == "__main__":
    main()
