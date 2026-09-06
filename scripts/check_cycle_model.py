#!/usr/bin/env python3
"""Differentially compare Python component traces with their RTL oracle."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "model"
sys.path.insert(0, str(MODEL_ROOT))

from azilla_cycle_model.compute import (  # noqa: E402
    SpinCore,
    SymmetricMVM,
    SynchronousBlockSram,
)
from azilla_cycle_model.fixed import pack_lanes  # noqa: E402
from azilla_cycle_model.hierarchy import DmaCommand, HierarchyNode  # noqa: E402
from azilla_cycle_model.noc import EAST, Flit, FlooRouter  # noqa: E402


def python_trace() -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    matrix = [[((row * 7 + column * 3) % 17) - 8
               for column in range(32)] for row in range(32)]
    sram = SynchronousBlockSram()
    sram.rows[0] = [pack_lanes(row, 8) for row in matrix]
    mvm = SymmetricMVM()
    trace: list[tuple[int, int, int]] = []
    cycle = 0
    start = True
    while True:
        old_data = sram.read_data
        old_address = mvm.weight_row
        mvm.tick(start=start, state_a=0xA5A55A5A, state_b=0x13579BDF,
                 weight_data=old_data)
        sram.tick(read_slot=0, read_row=old_address)
        trace.append((cycle, mvm.weight_row, int(mvm.done)))
        if mvm.done:
            break
        start = False
        cycle += 1
    results = [(index, mvm.result_a[index], mvm.result_b[index])
               for index in range(32)]
    return trace, results


def rtl_trace(build: bool) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    executable = ROOT / "build/cycle_model_compute_trace_tb/Vcycle_model_compute_trace_tb"
    if build or not executable.exists():
        subprocess.run(
            ["make", "-C", "tb", "cycle-model-compute-trace-build"],
            cwd=ROOT,
            check=True,
        )
    completed = subprocess.run(
        [str(executable)], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    trace: list[tuple[int, int, int]] = []
    results: list[tuple[int, int, int]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["TRACE"]:
            trace.append(tuple(map(int, fields[1:4])))
        elif fields[:1] == ["RESULT"]:
            results.append(tuple(map(int, fields[1:4])))
    if not trace or len(results) != 32:
        raise RuntimeError("RTL oracle did not emit a complete trace")
    return trace, results


CORE_STATE_NUMBER = {
    SpinCore.RESET: 0,
    SpinCore.INIT: 1,
    SpinCore.IDLE: 2,
    SpinCore.ACCUMULATE: 3,
    SpinCore.FINALIZE: 4,
    SpinCore.WAIT_COMMIT: 5,
    SpinCore.COMMIT: 6,
    SpinCore.DONE: 7,
}


def _core_record(cycle: int, core: SpinCore) -> tuple[int, ...]:
    outputs = core.outputs()
    return (
        cycle,
        CORE_STATE_NUMBER[core.core_state],
        int(outputs.init_done),
        int(outputs.weight_init_ready),
        int(outputs.h0_partial_ready),
        int(outputs.ext_partial_ready),
        int(outputs.iter_done),
        outputs.state_current,
        outputs.state_next,
        int(core.local_mvm.done),
        core.h0_partial_beat_count,
        core.ext_partial_beat_count,
    )


def python_spin_core_trace() -> tuple[list[tuple[int, ...]], list[tuple[int, int]]]:
    core = SpinCore()
    trace: list[tuple[int, ...]] = []
    cycle = 0

    def step(**signals: object) -> None:
        nonlocal cycle
        defaults = dict(
            noise_seed=0x12345678, coeff_a=3, coeff_b=-2,
            noise_amplitude=5, init_state=0xA55AA55A,
        )
        defaults.update(signals)
        core.tick(**defaults)
        trace.append(_core_record(cycle, core))
        cycle += 1

    step(rst=True)
    step(rst=True)
    step(init_start=True)
    for row in range(32):
        values = [((row * 5 + column * 11) % 19) - 9
                  for column in range(32)]
        step(weight_init_valid=True, weight_init_data=pack_lanes(values, 8))
    step()
    step(iter_start=True)
    for beat in range(4):
        base = beat * 8
        step(
            h0_partial_valid=True,
            h0_partial_data=pack_lanes(
                [base + lane - 16 for lane in range(8)], 32
            ),
            ext_partial_valid=True,
            ext_partial_data=pack_lanes(
                [-2 * (base + lane - 16) for lane in range(8)], 32
            ),
        )
    step(partials_done=True)
    while not core.outputs().iter_done:
        step()
    totals = list(enumerate(core.accumulator_total))
    step(commit=True)
    step()
    return trace, totals


def rtl_spin_core_trace(build: bool) -> tuple[list[tuple[int, ...]], list[tuple[int, int]]]:
    executable = ROOT / "build/cycle_model_spin_core_trace_tb/Vcycle_model_spin_core_trace_tb"
    if build or not executable.exists():
        subprocess.run(
            ["make", "-C", "tb", "cycle-model-spin-core-trace-build"],
            cwd=ROOT,
            check=True,
        )
    completed = subprocess.run(
        [str(executable)], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    trace: list[tuple[int, ...]] = []
    totals: list[tuple[int, int]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["CORE"]:
            trace.append(tuple(map(int, fields[1:])))
        elif fields[:1] == ["TOTAL"]:
            totals.append(tuple(map(int, fields[1:3])))
    if not trace or len(totals) != 32:
        raise RuntimeError("spin-core RTL oracle did not emit a complete trace")
    return trace, totals


NODE_STATE_NUMBER = {
    HierarchyNode.RESET: 0,
    HierarchyNode.IDLE: 1,
    HierarchyNode.RUN: 2,
    HierarchyNode.DONE: 3,
}
ENGINE_STATE_NUMBER = {"IDLE": 0, "FETCH": 1, "START": 2, "COMPUTE": 3}
OUTPUT_STATE_NUMBER = {"OUTPUT_IDLE": 0, "SEND_A": 1, "SEND_B": 2}


def _hierarchy_record(cycle: int, node: HierarchyNode) -> tuple[int, ...]:
    outputs = node.outputs()
    engine = node.engines[0]
    partial = outputs.partials[0]
    slot_bits = int(engine.slot_valid[0]) | (int(engine.slot_valid[1]) << 1)
    result_bits = int(engine.result_valid[0]) | (int(engine.result_valid[1]) << 1)
    reserved_bits = int(engine.result_reserved[0]) | (int(engine.result_reserved[1]) << 1)
    return (
        cycle, NODE_STATE_NUMBER[node.node_state], int(outputs.iter_done),
        int(outputs.state_ready), int(outputs.command_ready[0]),
        int(outputs.weight_ready[0]), int(partial.valid), partial.block_id,
        int(partial.last), partial.data,
        ENGINE_STATE_NUMBER[engine.state], OUTPUT_STATE_NUMBER[engine.output_state],
        slot_bits, result_bits, reserved_bits, engine.partial_beat,
    )


def python_hierarchy_trace() -> list[tuple[int, ...]]:
    node = HierarchyNode(state_entry_count=2, mvm_count=1)
    trace: list[tuple[int, ...]] = []
    cycle = 0

    def step(**signals: object) -> None:
        nonlocal cycle
        node.tick(**signals)
        trace.append(_hierarchy_record(cycle, node))
        cycle += 1

    step(rst=True)
    step(rst=True)
    step()
    step(state_valid=True, state_index=0, state_data=0xFFFFFFFF)
    step(state_valid=True, state_index=1, state_data=0)
    step(iter_start=True)
    step(commands=[DmaCommand(0, 1, 10, 20)])
    for row in range(32):
        values = [1 if row == column else 0 for column in range(32)]
        step(weight_valid=[True], weight_data=[pack_lanes(values, 8)],
             partial_ready=[True])
    step(schedule_done=True, partial_ready=[True])
    while not node.outputs().iter_done:
        step(partial_ready=[True])
    return trace


def rtl_hierarchy_trace(build: bool) -> list[tuple[int, ...]]:
    executable = ROOT / "build/cycle_model_hierarchy_trace_tb/Vcycle_model_hierarchy_trace_tb"
    if build or not executable.exists():
        subprocess.run(
            ["make", "-C", "tb", "cycle-model-hierarchy-trace-build"],
            cwd=ROOT, check=True,
        )
    completed = subprocess.run(
        [str(executable)], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    trace: list[tuple[int, ...]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["HN"]:
            payload = fields[1:]
            values = [int(field, 16) if index == 9 else int(field)
                      for index, field in enumerate(payload)]
            trace.append(tuple(values))
    if not trace:
        raise RuntimeError("hierarchy RTL oracle did not emit a trace")
    return trace


def python_router_trace() -> list[tuple[int, ...]]:
    router = FlooRouter(0, 0, fifo_depth=4)
    local_index = 0
    north_index = 0
    trace: list[tuple[int, ...]] = []

    def step(cycle: int, *, rst: bool, enabled: bool, east_ready: bool) -> None:
        nonlocal local_index, north_index
        inputs: list[Flit | None] = [None] * 5
        if enabled and local_index < 5:
            inputs[0] = Flit(
                data=10 + local_index, dest_x=1, dest_y=0,
                last=local_index in (3, 4),
            )
        if enabled and north_index < 5:
            inputs[4] = Flit(
                data=30 + north_index, dest_x=1, dest_y=0,
                last=north_index in (0, 4),
            )
        ready = [True] * 5
        ready[EAST] = east_ready
        before = router.tick(inputs, ready, rst=rst)
        if not rst:
            if inputs[0] is not None and before.input_ready[0]:
                local_index += 1
            if inputs[4] is not None and before.input_ready[4]:
                north_index += 1
        after = router.outputs()
        ready_mask = sum(int(value) << index
                         for index, value in enumerate(after.input_ready))
        valid_mask = sum(int(value is not None) << index
                         for index, value in enumerate(after.output_flits))
        east = after.output_flits[EAST]
        trace.append((
            cycle, ready_mask, valid_mask, int(east is not None),
            0 if east is None else east.data & 0xFFFFFFFF,
            0 if east is None else int(east.last), int(east_ready),
        ))

    step(0, rst=True, enabled=False, east_ready=True)
    step(1, rst=True, enabled=False, east_ready=True)
    for cycle in range(2, 8):
        step(cycle, rst=False, enabled=True, east_ready=False)
    for cycle in range(8, 28):
        step(cycle, rst=False, enabled=True, east_ready=True)
    return trace


def rtl_router_trace(build: bool) -> list[tuple[int, ...]]:
    executable = ROOT / "build/cycle_model_router_trace_tb/Vcycle_model_router_trace_tb"
    if build or not executable.exists():
        subprocess.run(
            ["make", "-C", "tb", "cycle-model-router-trace-build"],
            cwd=ROOT, check=True,
        )
    completed = subprocess.run(
        [str(executable)], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    trace = [tuple(map(int, line.split()[1:]))
             for line in completed.stdout.splitlines()
             if line.startswith("RTR ")]
    if not trace:
        raise RuntimeError("router RTL oracle did not emit a trace")
    return trace


def compare(name: str, expected: object, actual: object) -> None:
    if expected == actual:
        print(f"PASS {name}")
        return
    if isinstance(expected, list) and isinstance(actual, list):
        for index, (lhs, rhs) in enumerate(zip(expected, actual)):
            if lhs != rhs:
                raise AssertionError(
                    f"{name} first mismatch at record {index}: "
                    f"python={lhs!r} rtl={rhs!r}"
                )
        raise AssertionError(
            f"{name} length mismatch: python={len(expected)} rtl={len(actual)}"
        )
    raise AssertionError(f"{name} mismatch: python={expected!r} rtl={actual!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    py_trace, py_results = python_trace()
    sv_trace, sv_results = rtl_trace(args.rebuild)
    compare("symmetric_mvm cycle trace", py_trace, sv_trace)
    compare("symmetric_mvm arithmetic", py_results, sv_results)
    py_core_trace, py_totals = python_spin_core_trace()
    sv_core_trace, sv_totals = rtl_spin_core_trace(args.rebuild)
    compare("spin_core cycle trace", py_core_trace, sv_core_trace)
    compare("spin_core arithmetic", py_totals, sv_totals)
    compare("hierarchy_node cycle/arithmetic trace",
            python_hierarchy_trace(), rtl_hierarchy_trace(args.rebuild))
    compare("FlooNoC router cycle trace",
            python_router_trace(), rtl_router_trace(args.rebuild))


if __name__ == "__main__":
    main()
