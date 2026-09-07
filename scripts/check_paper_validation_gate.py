#!/usr/bin/env python3
"""Check source-bound integrated validation evidence before a paper run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CHECKS = ("integrated_timing", "backpressure", "exact_event_equivalence")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def validate_certificate(path: Path, mode: str, config: Path) -> list[str]:
    errors = []
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return ["certificate must be an object"]
    except (OSError, ValueError) as error:
        return [f"missing or invalid certificate: {error}"]
    for key, expected in (("status", "pass"), ("simulator", "vcs"),
                          ("execution_mode", mode)):
        if data.get(key) != expected:
            errors.append(f"{key} must equal {expected!r}")
    checks = data.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    for check in REQUIRED_CHECKS:
        if checks.get(check) is not True:
            errors.append(f"missing passing check: {check}")
    for field in ("source_sha256", "evidence_sha256"):
        hashes = data.get(field)
        if not isinstance(hashes, dict) or not hashes:
            errors.append(f"missing nonempty {field}")
            continue
        if field == "source_sha256":
            # A certificate must bind the model and RTL sources, including
            # the actual shared library used for live memory timing.
            required = {str(p.relative_to(ROOT)) for base, suffix in
                        ((ROOT / "model/azilla_cycle_model", "*.py"),
                         (ROOT / "rtl", "*.sv")) for p in base.glob(suffix)}
            required.update({"tb/ramulator_dpi.cpp",
                             "build/cycle_model_ramulator/libazilla_ramulator.so"})
            missing = required - hashes.keys()
            if missing:
                errors.append(f"source coverage missing {len(missing)} required files")
        for name, expected in hashes.items():
            try:
                if digest(ROOT / name) != expected:
                    errors.append(f"stale {field}: {name}")
            except (OSError, TypeError) as error:
                errors.append(f"unreadable {field} {name}: {error}")
    try:
        if data.get("ramulator_config_sha256") != digest(config):
            errors.append("Ramulator configuration does not match validation")
    except OSError as error:
        errors.append(f"cannot read Ramulator configuration: {error}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--mode", choices=("cir", "cores-only"), required=True)
    parser.add_argument("--ramulator-config", type=Path, required=True)
    args = parser.parse_args()
    errors = validate_certificate(args.certificate, args.mode, args.ramulator_config)
    if errors:
        print("BLOCKED: " + "; ".join(errors))
        raise SystemExit(1)
    print(f"PASS source-bound {args.mode} validation gate")


if __name__ == "__main__":
    main()
