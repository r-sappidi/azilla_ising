#!/usr/bin/env python3
"""Run frozen mapping artifacts only through exact-event Ramulator timing."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_point(manifest: Path, output: Path) -> dict:
    artifact = manifest.parent
    dataset = artifact / "dataset.txt"
    if not dataset.is_file():
        raise FileNotFoundError(dataset)
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "metrics_summary.json"
    if not summary.is_file():
        command = [
            "python3", "-m", "azilla_cycle_model.cli",
            "simulate-mapped-exact-events", "--artifact", str(artifact),
            "--dataset", str(dataset), "--h0-mvms", "4", "--h1-mvms", "2",
            "--cross-mvms", "4", "--execution-mode", "cores-only",
            "--ramulator-library",
            "build/cycle_model_ramulator/libazilla_ramulator.so",
            "--ramulator-config", "tb/ramulator_128x32.yaml",
            "--idle-refresh-period-ticks", "7600",
            "--metrics-prefix", str(output / "metrics"),
            "--transfer-trace", str(output / "transfers.csv"),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = "model"
        env["LD_LIBRARY_PATH"] = (
            "third_party/ramulator2:" + env.get("LD_LIBRARY_PATH", "")
        )
        with (output / "run.log").open("w") as log, \
             (output / "wall.time").open("w") as wall:
            subprocess.run(["/usr/bin/time", "-v", "-o", "/dev/stderr", *command],
                           cwd=ROOT, env=env, stdout=log, stderr=wall, check=True)
    data = json.loads(summary.read_text())
    if data.get("accuracy") != "cycle-structured-unverified":
        raise RuntimeError(f"unexpected accuracy in {summary}")
    timing = data["timing_cycles"]
    return {
        "dataset": artifact.parent.name, "mapping": artifact.name,
        "execution_mode": data["execution_mode"],
        "spins": data["geometry"]["spin_count"],
        "iteration_cycles": timing["iteration"], "total_cycles": timing["total"],
        "physical_link_flits": data["noc"]["physical_link_flits"],
        "average_hops": data["noc"]["average_hops"],
        "link_stalls": data["noc"]["link_stalls"],
        "accuracy": data["accuracy"],
        "summary": str(summary.resolve().relative_to(ROOT)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name-pattern", default=r".*_n65536(?:_s1)?$")
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    pattern = re.compile(args.name_pattern)
    manifests = [path for path in args.artifact_root.glob("*/*/manifest.json")
                 if pattern.fullmatch(path.parent.parent.name)]
    if not manifests:
        raise SystemExit("no matching mapping artifacts")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(run_point, manifest,
                        args.output_dir / manifest.parent.parent.name /
                        manifest.parent.name): manifest
            for manifest in manifests
        }
        for future in as_completed(futures):
            row = future.result(); rows.append(row)
            print(f"PASS {row['dataset']} {row['mapping']} "
                  f"cycles={row['iteration_cycles']}", flush=True)
    rows.sort(key=lambda row: (row["spins"], row["dataset"], row["mapping"]))
    with (args.output_dir / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(f"PASS points={len(rows)}")


if __name__ == "__main__":
    main()
