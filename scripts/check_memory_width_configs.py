#!/usr/bin/env python3
"""Standalone request-conservation smoke; does not certify accelerator timing."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from azilla_cycle_model.ramulator import RamulatorBackend
from run_memory_width_sweep import digest, dump


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--configs-manifest", type=Path, required=True)
    parser.add_argument("--library", type=Path, default=ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    rows = []
    for config in json.loads(args.configs_manifest.read_text())["configs"]:
        if digest(config["path"]) != config["sha256"]:
            raise ValueError("config changed")
        with RamulatorBackend(args.library, config["path"], ROOT / "tb/datasets/g256_smoke.txt", 1, 8, timing_only=True) as backend:
            sent = 0
            completed = set()
            # One 32-byte request every opportunity: same 16 request lanes
            # and 40 DRAM ticks per accelerator cycle at every width.
            for cycle in range(10000):
                for _ in range(16):
                    if sent < 1024 and backend.send(0, sent * 32, sent):
                        sent += 1
                    else:
                        break
                backend.tick(40)
                while (response := backend.pop(0)) is not None:
                    if response.tag in completed or not 0 <= response.tag < sent:
                        raise AssertionError("duplicate/invalid completion")
                    completed.add(response.tag)
                if len(completed) == 1024:
                    break
            stats = backend.stats(0)
            assert sent == len(completed) == stats.accepted == stats.completed == 1024
            assert stats.outstanding == 0
            rows.append({"width": config["width"], "cycles": cycle + 1,
                         "accepted": stats.accepted, "completed": stats.completed,
                         "rejected": stats.rejected, "config_sha256": config["sha256"]})
    dump(args.output, {"status": "pass", "scope": "standalone memory request conservation only; not integrated RTL validation",
                       "library_sha256": digest(args.library), "results": rows})
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
