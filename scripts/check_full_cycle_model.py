#!/usr/bin/env python3
"""Compare the Python full-system smoke run with prebuilt RTL executables."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )
    return result.stdout


def require(pattern: str, text: str, label: str) -> tuple[int, ...]:
    match = re.search(pattern, text)
    if match is None:
        raise RuntimeError(f"could not parse {label}\n{text}")
    return tuple(map(int, match.groups()))


def python_result(mode: str, args) -> tuple[tuple[int, ...], tuple[int, ...]]:
    command = [
        sys.executable, "-m", "azilla_cycle_model.cli", f"simulate-{mode}",
        "--dataset", "tb/datasets/g256_smoke.txt",
        "--mesh-x", "2", "--mesh-y", "1",
        "--h0-per-h1", "2", "--cores-per-h0", "2",
    ]
    if mode == "ramulator":
        command += [
            "--ramulator-library", args.ramulator_library,
            "--ramulator-config", args.ramulator_config,
        ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "model")
    output = run(command, env)
    timing = require(
        rf"PASS mode={mode} spins=256 initialization_cycles=(\d+) total_cycles=(\d+)",
        output, f"Python {mode} timing",
    ) + require(r"iteration=0 cycles=(\d+)", output, "Python iteration")
    noc = require(
        r"noc injected_flits=(\d+) ejected_flits=(\d+) physical_link_flits=(\d+) injection_stalls=(\d+)",
        output, f"Python {mode} NoC",
    )
    return timing, noc


def rtl_result(binary: str, mode: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    output = run([
        binary, "+DATASET=g256_smoke.txt",
        f"+NOC_STATS_FILE=build/full_cycle_model_{mode}.csv",
    ])
    if "iteration=0 PASS" not in output or "PASS: 1 iteration(s)" not in output:
        raise RuntimeError(f"RTL {mode} functional check did not pass\n{output}")
    initialization = require(
        r"initialization complete at cycle (\d+)", output, "RTL initialization"
    )[0]
    total = require(r"total_cycles=(\d+)", output, "RTL total")[0]
    window, injected, ejected, links = require(
        r"NOC summary window_cycles=(\d+) injected_flits=(\d+) ejected_flits=(\d+) physical_link_flits=(\d+)",
        output, f"RTL {mode} NoC",
    )
    csv_text = (ROOT / f"build/full_cycle_model_{mode}.csv").read_text()
    injection_stalls = sum(
        int(line.split(",")[7]) for line in csv_text.splitlines()
        if line.startswith("inject,")
    )
    return (initialization, total, window), (
        injected, ejected, links, injection_stalls
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--direct-rtl",
        default="build/ising_mesh_floo_2x1_h02_c2_e1-1-1_f4_s1_r0_i1_t0/Vising_mesh_tb",
    )
    parser.add_argument(
        "--ramulator-rtl",
        default="build/ising_mesh_floo_2x1_h02_c2_e1-1-1_f4_s1_r1_i1_t0/Vising_mesh_tb",
    )
    parser.add_argument(
        "--ramulator-library",
        default="build/cycle_model_ramulator/libazilla_ramulator.so",
    )
    parser.add_argument(
        "--ramulator-config", default="tb/ramulator_128x32.yaml"
    )
    args = parser.parse_args()
    for mode, binary in (("direct", args.direct_rtl),
                         ("ramulator", args.ramulator_rtl)):
        if not (ROOT / binary).exists():
            raise FileNotFoundError(
                f"missing {binary}; build the matching tb floo-mesh configuration"
            )
        python_timing, python_noc = python_result(mode, args)
        rtl_timing, rtl_noc = rtl_result(binary, mode)
        if python_timing != rtl_timing or python_noc != rtl_noc:
            raise AssertionError(
                f"{mode} mismatch: Python timing/noc={python_timing}/{python_noc}; "
                f"RTL={rtl_timing}/{rtl_noc}"
            )
        print(f"PASS full-system {mode} timing={python_timing} noc={python_noc}")


if __name__ == "__main__":
    main()
