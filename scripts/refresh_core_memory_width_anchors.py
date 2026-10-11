#!/usr/bin/env python3
"""Replay existing VCS width fixtures with current model, preserving provenance."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependencies():
    package = ROOT / "model/azilla_cycle_model"
    pending = [package / f"{name}.py" for name in (
        "__init__", "core_iteration_pipeline", "core_iteration_driver", "ramulator", "workload", "fixed", "hierarchy")]
    files = set()
    while pending:
        path = pending.pop()
        if path in files:
            continue
        files.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                pending.append(package / (node.module.replace(".", "/") + ".py"))
    files.update(ROOT / "scripts" / name for name in (
        "check_core_iteration_pipeline.py", "generate_core_iteration_dataset.py"))
    files.add(ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so")
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(files)}


def main():
    source = ROOT / "results/paper_validation_20260907/core_memory_width_e4_j2_v1"
    output = ROOT / "results/paper_validation_20260907/core_memory_width_e4_j2_nocfix_v2"
    # Any changed RTL/testbench source requires regenerating the oracle.
    for line in (source / "source_before.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if name.startswith(("rtl/", "tb/")) and digest(ROOT / name) != expected:
            raise RuntimeError(f"RTL fixture source changed: {name}")
    before = dependencies()
    output.mkdir(exist_ok=False)
    (output / "model_before.json").write_text(json.dumps(before, indent=2) + "\n")
    evidence = {}
    for width in (32, 64):
        run = output / f"x{width}"
        run.mkdir()
        for name in ("dataset.txt", "config_binary.sha256", *[f"stall{s}.log" for s in (0, 7, 13)],
                     *[f"arithmetic_stall{s}.log" for s in (0, 7, 13)]):
            fixture = source / f"x{width}" / name
            evidence[str(fixture.relative_to(ROOT))] = digest(fixture)
            shutil.copy2(fixture, run / name)
        config = ROOT / f"results/memory_width_prepare_20260907_v1/ramulator_x{width}_32gbps.yaml"
        expected = (source / f"x{width}/config_binary.sha256").read_text().splitlines()[0].split()[0]
        assert digest(config) == expected
        for stall in (0, 7, 13):
            for compressed in (False, True):
                prefix = "independent_compressed" if compressed else "independent"
                command = [sys.executable, "scripts/check_core_iteration_pipeline.py", "--root", str(run),
                           "--stall", str(stall), "--engines", "4", "--jobs", "2", "--independent", "--config", str(config)]
                if compressed:
                    command.append("--compress-idle")
                with (run / f"{prefix}_stall{stall}.log").open("w") as log:
                    subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    after = dependencies()
    (output / "model_after.json").write_text(json.dumps(after, indent=2) + "\n")
    (output / "fixture_sha256.json").write_text(json.dumps(evidence, indent=2) + "\n")
    assert before == after, "actual model dependencies changed during comparisons"
    print("PASS all 12 scoped local-state-snapshot comparisons; source hashes unchanged")


if __name__ == "__main__":
    main()
