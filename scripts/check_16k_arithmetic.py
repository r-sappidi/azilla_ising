#!/usr/bin/env python3
"""Three-way 16K arithmetic comparison: Python, RTL, and MATLAB/Octave."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from azilla_cycle_model.performance import PerformanceConfig, RamulatorPerformanceModel
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )
    return completed.stdout


def parse_golden(path: Path, spin_count: int) -> tuple[int, ...]:
    tokens = path.read_text().split()
    if len(tokens) != spin_count + 2:
        raise ValueError(
            f"golden file has {len(tokens) - 2} states, expected {spin_count}"
        )
    if tuple(map(int, tokens[:2])) != (spin_count, 1):
        raise ValueError(f"golden header mismatch in {path}")
    states = tuple(map(int, tokens[2:]))
    if any(state not in (0, 1) for state in states):
        raise ValueError("golden states must be zero or one")
    return states


def parse_rtl_states(path: Path, spin_count: int) -> tuple[int, ...]:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "iteration spin state":
        raise ValueError(f"invalid RTL state dump header in {path}")
    states = [-1] * spin_count
    for line in lines[1:]:
        iteration, spin, state = map(int, line.split())
        if iteration != 0 or not 0 <= spin < spin_count or state not in (0, 1):
            raise ValueError(f"invalid RTL state record: {line}")
        if states[spin] != -1:
            raise ValueError(f"duplicate RTL state for spin {spin}")
        states[spin] = state
    if any(state == -1 for state in states):
        raise ValueError("RTL state dump is incomplete")
    return tuple(states)


def expand_python_states(words: tuple[int, ...], spin_count: int) -> tuple[int, ...]:
    states = tuple(
        (word >> bit) & 1
        for word in words
        for bit in range(32)
    )
    if len(states) != spin_count:
        raise ValueError(
            f"Python emitted {len(states)} spin states, expected {spin_count}"
        )
    return states


def assert_states(label: str, expected: tuple[int, ...],
                  observed: tuple[int, ...]) -> None:
    if expected == observed:
        return
    mismatches = [
        index for index, pair in enumerate(zip(expected, observed))
        if pair[0] != pair[1]
    ]
    first = mismatches[0]
    raise AssertionError(
        f"{label}: {len(mismatches)} mismatches; first spin {first}: "
        f"expected={expected[first]} observed={observed[first]}"
    )


def timing(output: str) -> tuple[int, int]:
    initialization = re.search(
        r"initialization complete at cycle (\d+)", output
    )
    total = re.search(r"total_cycles=(\d+)", output)
    if initialization is None or total is None:
        raise RuntimeError(f"could not parse RTL timing\n{output}")
    return int(initialization.group(1)), int(total.group(1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="tb/datasets/g16384_kings.txt")
    parser.add_argument(
        "--rtl",
        default=("build/ising_mesh_floo_4x4_h02_c16_e1-1-16_"
                 "f4_s1_r1_i1_t0/Vising_mesh_tb"),
    )
    parser.add_argument(
        "--octave", default=".tools/octave/bin/octave-cli"
    )
    parser.add_argument(
        "--golden", default="build/g16384_matlab_golden.txt"
    )
    parser.add_argument(
        "--rtl-states", default="build/g16384_rtl_states.txt"
    )
    parser.add_argument(
        "--python-states", default="build/g16384_python_states.txt"
    )
    parser.add_argument(
        "--ramulator-library",
        default="build/cycle_model_ramulator/libazilla_ramulator.so",
    )
    parser.add_argument("--ramulator-config", default="tb/ramulator_128x32.yaml")
    parser.add_argument("--reuse-golden", action="store_true")
    parser.add_argument("--reuse-rtl", action="store_true")
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    golden_path = ROOT / args.golden
    rtl_states_path = ROOT / args.rtl_states
    python_states_path = ROOT / args.python_states
    golden_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.reuse_golden:
        octave_path = (ROOT / args.octave).resolve()
        octave_env = dict(os.environ)
        # Conda's Octave package needs its relocatable prefix when invoked
        # without `conda activate`; system Octave ignores this convention.
        if ".tools/octave" in args.octave:
            octave_env["OCTAVE_HOME"] = str(octave_path.parents[1])
        octave_eval = (
            "addpath('scripts'); "
            f"generate_matlab_golden('{dataset_path}', '{golden_path}', 1, 0, 1, 0);"
        )
        octave_output = run(
            [str(octave_path), "--quiet", "--eval", octave_eval], env=octave_env
        )
        if "MATLAB_GOLDEN" not in octave_output:
            raise RuntimeError(f"Octave did not report golden completion\n{octave_output}")

    dataset = IsingDataset.load(dataset_path)
    matlab_states = parse_golden(golden_path, dataset.spin_count)

    rtl_output = ""
    if not args.reuse_rtl:
        env = dict(os.environ)
        ramulator_dir = str(ROOT / "third_party/ramulator2")
        env["LD_LIBRARY_PATH"] = (
            ramulator_dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        )
        rtl_output = run([
            str(ROOT / args.rtl), "+DATASET=g16384_kings.txt",
            f"+MATLAB_GOLDEN={golden_path}",
            f"+DUMP_STATES={rtl_states_path}",
            "+NOC_STATS_FILE=build/g16384_arithmetic_rtl_noc.csv",
        ], env=env)
        if "iteration=0 PASS" not in rtl_output or "PASS: 1 iteration(s)" not in rtl_output:
            raise AssertionError(f"RTL arithmetic check did not pass\n{rtl_output}")

    rtl_states = parse_rtl_states(rtl_states_path, dataset.spin_count)
    assert_states("RTL vs MATLAB", matlab_states, rtl_states)

    geometry = Geometry(4, 4, 2, 16)
    schedule = [
        ScheduledBlock(a, b) for a, b in sorted(dataset.active_block_pairs())
    ]
    model = RamulatorPerformanceModel(
        geometry, dataset,
        ramulator_library=str(ROOT / args.ramulator_library),
        ramulator_config=str(ROOT / args.ramulator_config),
        dataset_path=str(dataset_path), mem_lanes=16, ticks_per_cycle=40,
        config=PerformanceConfig(
            h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=16,
            fifo_depth=4, timing_only=False, max_cycles=25_000,
        ),
    )
    try:
        result = model.run(schedule=schedule, sparse=True)
    finally:
        model.close()

    python_states = expand_python_states(
        result.iterations[0].states, dataset.spin_count
    )
    python_states_path.write_text(
        "iteration spin state\n" + "".join(
            f"0 {spin} {state}\n" for spin, state in enumerate(python_states)
        )
    )
    assert_states("Python vs MATLAB", matlab_states, python_states)
    assert_states("Python vs RTL", rtl_states, python_states)

    rtl_timing = timing(rtl_output) if rtl_output else (16_899, 18_720)
    python_timing = result.initialization_cycles, result.total_cycles
    if rtl_timing != python_timing:
        raise AssertionError(
            f"timing mismatch RTL={rtl_timing} Python={python_timing}"
        )

    print(
        "PASS 16K arithmetic Python=RTL=MATLAB-compatible-reference "
        f"spins={dataset.spin_count} ones={sum(python_states)} "
        f"initialization_cycles={result.initialization_cycles} "
        f"iteration_cycles={result.iterations[0].cycles} "
        f"total_cycles={result.total_cycles}"
    )


if __name__ == "__main__":
    main()
