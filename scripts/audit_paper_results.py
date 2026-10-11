#!/usr/bin/env python3
"""Build a fail-closed inventory of results eligible for paper figures."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/paper_final_20260906/audit"
EXACT = ROOT / "results/paper_final_20260906/exact_execution_modes"
QUALITY = ROOT / (
    "results/cir_evaluation/solution_quality/schedule_sweep_20260904/"
    "held_out_scaling/summary.csv"
)
PERFORMANCE_HOLD = ROOT / "results/paper_validation_20260906/performance_validation_hold.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        # Do not leave a previously accepted table behind when validation is
        # revoked. Raw experiment outputs are retained in their own folders.
        path.write_text("")
        return
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def exact_rows() -> tuple[list[dict], list[dict]]:
    accepted, rejected = [], []
    anchor = ROOT / "results/paper_validation_20260906/cores_only_corrected/PASS"
    for summary in sorted(EXACT.glob("*/*/metrics_summary.json")):
        data = json.loads(summary.read_text())
        mode = data.get("execution_mode", summary.parent.name)
        companions = [summary.parent / f"metrics_{kind}.csv"
                      for kind in ("noc", "nodes", "dram")]
        reasons = []
        if PERFORMANCE_HOLD.is_file():
            hold = json.loads(PERFORMANCE_HOLD.read_text())
            reasons.extend(hold.get("blocked_modes", {}).get(mode, []))
        if mode != "cores-only":
            reasons.append("CIR differential unresolved")
        if data.get("accuracy") != "cycle-structured-unverified":
            reasons.append(f"unexpected accuracy={data.get('accuracy')}")
        if not anchor.is_file():
            reasons.append("missing cores-only RTL differential anchor")
        if any(not path.is_file() for path in companions):
            reasons.append("missing metric companion")
        timing = data.get("timing_cycles", {})
        if not all(int(timing.get(key, 0)) > 0
                   for key in ("initialization", "iteration", "total")):
            reasons.append("incomplete timing")
        geometry = data.get("geometry", {})
        dram = data.get("dram_systems", [])
        noc = data.get("noc", {})
        accepted_requests = sum(int(row["accepted_requests"]) for row in dram)
        rejected_requests = sum(int(row["rejected_requests"]) for row in dram)
        row = {
            "dataset": summary.parent.parent.name,
            "execution_mode": mode,
            "spins": geometry.get("spin_count", 0),
            "mesh_x": geometry.get("mesh_x", 0),
            "mesh_y": geometry.get("mesh_y", 0),
            "h0_per_h1": geometry.get("h0_per_h1", 0),
            "cores_per_h0": geometry.get("cores_per_h0", 0),
            "initialization_cycles": timing.get("initialization", 0),
            "iteration_cycles": timing.get("iteration", 0),
            "total_cycles": timing.get("total", 0),
            "injected_flits": noc.get("injected_flits", 0),
            "physical_link_flits": noc.get("physical_link_flits", 0),
            "average_hops": noc.get("average_hops", 0),
            "injection_stalls": noc.get("injection_stalls", 0),
            "ejection_stalls": noc.get("ejection_stalls", 0),
            "link_stalls": noc.get("link_stalls", 0),
            "dram_accepted": accepted_requests,
            "dram_rejected": rejected_requests,
            "dram_rejection_fraction": (
                rejected_requests / (accepted_requests + rejected_requests)
                if accepted_requests + rejected_requests else 0
            ),
            "accuracy": data.get("accuracy"),
            "rtl_anchor": str(anchor.relative_to(ROOT)),
            "summary": str(summary.relative_to(ROOT)),
            "summary_sha256": digest(summary),
        }
        if reasons:
            rejected.append({**row, "rejection_reason": "; ".join(reasons)})
        else:
            accepted.append(row)
    return accepted, rejected


def quality_rows() -> tuple[list[dict], list[str]]:
    if not QUALITY.is_file():
        return [], ["missing held-out solution-quality summary"]
    rows = list(csv.DictReader(QUALITY.open(newline="")))
    reasons = []
    required = {16384, 65536, 262144, 1048576}
    sizes = {int(row["spins"]) for row in rows}
    if not required.issubset(sizes):
        reasons.append(f"missing sizes {sorted(required - sizes)}")
    counts = Counter(int(row["spins"]) for row in rows)
    if any(counts[size] < 5 for size in required if size in counts):
        reasons.append("fewer than five seeds at a required size")
    normalized = [{
        "dataset": row["dataset"], "spins": int(row["spins"]),
        "seed": int(row["seed"]), "iterations": int(row["iterations"]),
        "known_cut": int(row["known_cut"]), "best_cut": int(row["best_cut"]),
        "fraction_of_known_cut": 1.0 - float(row["gap_fraction"]),
        "best_iteration": int(row["best_iteration"]),
        "algorithm": row["algorithm"], "schedule": row["schedule"],
        "accuracy_scope": "algorithmic-reference-aligned",
        "source_sha256": digest(QUALITY),
    } for row in rows]
    return normalized, reasons


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    accepted, rejected = exact_rows()
    quality, quality_reasons = quality_rows()
    write_csv(OUT / "exact_cores_only_accepted.csv", accepted)
    write_csv(OUT / "exact_rejected.csv", rejected)
    write_csv(OUT / "solution_quality_accepted.csv", quality)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    by_size = defaultdict(set)
    for row in accepted:
        family = row["dataset"].split("_n", 1)[0].rsplit("_s", 1)[0]
        by_size[int(row["spins"])].add(family)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "policy": "exact-event only; calibrated results prohibited",
        "accepted_exact_cores_only": len(accepted),
        "rejected_exact_points": len(rejected),
        "solution_quality_trials": len(quality),
        "solution_quality_reasons": quality_reasons,
        "families_by_spin_count": {
            str(size): sorted(values) for size, values in sorted(by_size.items())
        },
        "performance_matrix_complete": all(
            len(by_size[size]) >= 3 for size in (65536, 131072, 262144)
        ),
        "limitations": [
            "Large geometries are cycle-structured simulator results, not full RTL runs.",
            "CIR and hybrid points are excluded until the CIR differential passes.",
            "No calibrated result is eligible for a paper performance figure.",
        ],
    }
    (OUT / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
