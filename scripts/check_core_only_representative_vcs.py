#!/usr/bin/env python3
"""Audit representative cores-only VCS RTL logs against timing invariants."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from azilla_cycle_model.events import EventCompressedMesh, PacketRelease
from azilla_cycle_model.noc import InterconnectConfig

PATTERN = re.compile(
    r"CORE_REP_SUMMARY seed=(\d+) stall_cycles=(\d+) cycles=(\d+) "
    r"injected=(\d+) ejected=(\d+) link_flits=(\d+) "
    r"blocked_ejection=(\d+) first0=(\d+) last0=(\d+)"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    replay = EventCompressedMesh(2, 1, 4, InterconnectConfig()).replay(
        [PacketRelease(0, 0, 1, 0, 0), PacketRelease(0, 1, 0, 0, 1)],
        drain_cycles=0,
    )
    if (replay.end_cycle, replay.injected_flits, replay.ejected_flits,
            replay.physical_link_flits) != (3, 2, 2, 2):
        raise AssertionError(f"Python real-router model changed: {replay}")
    for path in sorted(args.log_dir.glob("seed*_stall*.log")):
        text = path.read_text()
        match = PATTERN.search(text)
        if match is None or "PASS: cores-only representative VCS RTL" not in text:
            raise AssertionError(f"incomplete or failed VCS log: {path}")
        seed, stall, cycles, injected, ejected, links, blocked, first, last = map(
            int, match.groups()
        )
        delay = max(stall - 2, 0)
        # Two state flits plus two complete 32-flit weight streams leave the
        # canonical cross-H1 owner. One state and one weight copy cross the
        # physical link; the other copies eject locally at the owner node.
        expected = (
            104 + delay, 66, 66, 34, 2 * delay,
            3 + delay, 103 + delay,
        )
        observed = (cycles, injected, ejected, links, blocked, first, last)
        if observed != expected:
            raise AssertionError(
                f"{path}: timing/traffic mismatch expected={expected} observed={observed}"
            )
        # This span includes the serialized canonical weight delivery to both
        # endpoints followed by directed arithmetic.
        if last - first + 1 != 101:
            raise AssertionError(f"{path}: canonical delivery/compute span changed")
        if stall == 0 and first != replay.end_cycle:
            raise AssertionError(
                f"{path}: RTL/model router completion mismatch "
                f"RTL={first} Python={replay.end_cycle}"
            )
        rows.append(dict(seed=seed, stall_cycles=stall, total_cycles=cycles,
                         injected_flits=injected, ejected_flits=ejected,
                         physical_link_flits=links, blocked_ejection_cycles=blocked,
                         first_job_cycle=first, last_job_cycle=last,
                         directed_compute_latency=last-first+1,
                         simulator="VCS", status="rtl-differential"))
    if len(rows) != 9:
        raise AssertionError(f"expected 9 representative cases, found {len(rows)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(f"PASS cores-only representative VCS cases={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
