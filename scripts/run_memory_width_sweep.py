#!/usr/bin/env python3
"""Prepare projected GDDR width configs; run only with current VCS certificates."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (32, 64, 128)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configurations(base):
    controllers = base["memory_system"]["controllers"]
    if len(controllers) != 8 or any(c != controllers[0] for c in controllers):
        raise ValueError("expected eight identical reference controllers")
    dram = controllers[0]["dram"]
    if (dram["impl"], dram["channel_width"], dram["org"]["dq"], dram["timing"][0]) != ("GDDR6", 16, 16, 32000):
        raise ValueError("expected projected GDDR6-derived x16 at 32000 Mb/s")
    result = {}
    for width in WIDTHS:
        config = copy.deepcopy(base)
        config["memory_system"]["controllers"] = copy.deepcopy(controllers[:width // 16])
        result[width] = config
    return result


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def prepare(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    base = yaml.safe_load(args.base.read_text())
    rows = []
    for width, config in configurations(base).items():
        path = out / f"ramulator_x{width}_32gbps.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        rows.append({"width": width, "channels": width // 16,
                     "raw_GB_per_s": width * 4, "path": str(path),
                     "sha256": digest(path)})
    dump(out / "configs.json", {"schema": 1, "base": str(args.base.resolve()),
         "base_sha256": digest(args.base), "configs": rows,
         "mem_lanes": 16, "ticks_per_cycle": 40,
         "interpretation": "projected GDDR6-derived channel-count sensitivity; aggregate banks, queues and capacity scale with width; not vendor GDDR7"})
    print(out / "configs.json")


def run(args):
    from check_paper_validation_gate import validate_certificate
    plan = json.loads(args.plan.read_text())
    configs = json.loads(Path(plan["configs_manifest"]).read_text())
    if configs["mem_lanes"] != 16 or configs["ticks_per_cycle"] != 40:
        raise ValueError("unexpected bridge parameters")
    library = Path(plan["ramulator_library"]).resolve()
    if digest(library) != plan["ramulator_library_sha256"]:
        raise ValueError("Ramulator library changed")
    if digest(library) != digest(ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so"):
        raise ValueError("library differs from the library covered by the validation gate")
    errors = []
    for row in configs["configs"]:
        if digest(row["path"]) != row["sha256"]:
            errors.append(f"changed x{row['width']} config")
        for mode in ("cir", "cores-only"):
            cert = plan["certificates"][str(row["width"])][mode]
            errors.extend(validate_certificate(Path(cert), mode, Path(row["path"])))
    if errors:
        raise RuntimeError("validation gate closed:\n" + "\n".join(errors))
    if not plan["workloads"]:
        raise ValueError("empty workload matrix")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "plan.json", plan)
    for workload in plan["workloads"]:
        name = workload["name"]
        if not name or Path(name).name != name:
            raise ValueError("workload name must be a filename component")
        for path, expected in workload["input_sha256"].items():
            if digest(path) != expected:
                raise ValueError(f"changed workload input {path}")
        extra = workload["arguments"]
        for option in ("--dataset", "--schedule"):
            if option in extra:
                source = extra[extra.index(option) + 1]
                if source not in workload["input_sha256"]:
                    raise ValueError(f"missing input hash for {option}")
        if "--dataset" not in extra or any(a.startswith("--dataset=") or a.startswith("--schedule=") for a in extra):
            raise ValueError("supply --dataset and optional --schedule as separate arguments")
        forbidden = ("--execution-mode", "--ramulator-config", "--ramulator-library", "--metrics-prefix", "--mem-lanes", "--ticks-per-cycle")
        if any(arg.split("=")[0] in forbidden for arg in extra):
            raise ValueError("workload overrides controlled sweep option")
        for row in configs["configs"]:
            for mode in ("cir", "cores-only"):
                cert = plan["certificates"][str(row["width"])][mode]
                errors = validate_certificate(Path(cert), mode, Path(row["path"]))
                if errors or digest(library) != plan["ramulator_library_sha256"]:
                    raise RuntimeError(f"validation changed: {errors}")
                run_dir = out / f"{name}_x{row['width']}_{mode}"
                run_dir.mkdir(exist_ok=False)
                command = [sys.executable, "-m", "azilla_cycle_model.cli", "simulate-exact-events",
                    *extra, "--execution-mode", mode, "--ramulator-library", str(library),
                    "--ramulator-config", row["path"], "--mem-lanes", "16", "--ticks-per-cycle", "40",
                    "--metrics-prefix", str(run_dir / "metrics")]
                record = {"command": command, "certificate": cert, "config": row,
                          "start_unix": time.time(), "status": "running"}
                dump(run_dir / "run.json", record)
                with (run_dir / "run.log").open("w") as log:
                    result = subprocess.run(["/usr/bin/time", "-v", "-o", str(run_dir / "time.txt"), *command],
                        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "model")}, stdout=log, stderr=subprocess.STDOUT)
                record.update(returncode=result.returncode, wall_seconds=time.time() - record["start_unix"],
                              status="completed-unreviewed" if result.returncode == 0 else "failed")
                dump(run_dir / "run.json", record)
                if result.returncode:
                    raise RuntimeError(f"failed {run_dir}; sweep stopped")


def main():
    parser = argparse.ArgumentParser(__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--base", type=Path, default=ROOT / "tb/ramulator_128x32.yaml")
    prep.add_argument("--output", type=Path, required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)(args)


if __name__ == "__main__":
    main()
