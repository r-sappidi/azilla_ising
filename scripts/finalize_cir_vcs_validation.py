#!/usr/bin/env python3
"""Create the fail-closed CIR VCS differential validation manifest."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/paper_validation_20260906"
LOGS = ROOT / "logs/paper_final_20260906"


def require(path: Path, phrase: str) -> None:
    text = path.read_text()
    if phrase not in text:
        raise AssertionError(f"{path} lacks required evidence: {phrase}")


def main() -> None:
    require(LOGS / "cir_16k_vcs_arithmetic.log", "PASS 16K arithmetic")
    require(LOGS / "cir_16k_vcs_timing.log", "PASS 16K Ramulator differential")
    require(LOGS / "cir_16k_vcs_exact_event.log", "PASS exact-events g16384")
    trace = ROOT / "build/full_cycle_model_16k_vcs_events.csv"
    if not trace.is_file() or trace.stat().st_size == 0:
        raise AssertionError("missing VCS transfer trace")
    manifest = {
        "schema": "azilla-cir-vcs-validation-v1",
        "simulator": "Synopsys VCS M-2017.03-SP2-4",
        "geometry": {"spins": 16384, "mesh_x": 4, "mesh_y": 4,
                     "h0_per_h1": 2, "cores_per_h0": 16,
                     "h0_mvms": 1, "h1_mvms": 1, "cross_mvms": 16},
        "coverage": ["arithmetic-versus-Octave-equation-reference",
                     "fully-cycle-stepped-Python-versus-VCS",
                     "exact-event-Python-versus-VCS",
                     "accepted-transfer-trace-and-NoC-counter-equality"],
        "accuracy_label": "rtl-differential",
        "limitations": [
            "The differential applies to the stated 16K geometry and memory configuration.",
            "Larger geometries are RTL-validated-model projections, not direct RTL verification."
        ],
        "event_trace": str(trace.relative_to(ROOT)),
    }
    (OUT / "cir_vcs_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUT / "PASS").touch()
    print("PASS finalized CIR VCS differential validation")


if __name__ == "__main__":
    main()
