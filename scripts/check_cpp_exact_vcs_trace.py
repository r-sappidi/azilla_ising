#!/usr/bin/env python3
"""Fail-closed CIR exact-event differential: C++ port against one stored VCS trace.

This is the C++ counterpart of ``scripts/check_cir_exact_vcs_trace.py``. It
applies the same three comparisons to ``build/cpp_exact/azilla_exact_cir``:

* phase timing (initialization / iteration / total cycles) parsed from the
  VCS log (``initialization complete at cycle N`` and ``total_cycles=N``);
* every accepted NoC transfer record (cycle, scope, node, direction,
  packet_type, source_id, epoch, block_id, dest_x, dest_y, last) from the
  RTL event CSV versus the C++ ``--transfer-trace`` CSV, as sorted tuples;
* aggregate NoC totals (injected/ejected/physical-link flits and
  injection/ejection/link stall cycles) from the RTL NoC statistics CSV
  versus the C++ ``metrics_summary.json`` counters.

Zero tolerance. The model is driven exactly as the Python checker drives the
Python model: ``--execution-mode cir``, the supplied geometry and engine
counts, the shared Ramulator library/YAML, no idle-tick modulus, default FIFO
depth and baseline inter-H1 link settings. Optionally the stored Python
differential JSON is embedded so RTL = Python = C++ agreement is explicit.
This is evidence for the supplied geometry/dataset/schedule only.

Opt-in extensions (default behaviour unchanged; all recorded in the output):
``--binary-arg`` passes extra flags to the binary (e.g. ``--dense-all-blocks 1``
for header-only dense occupancy); ``--reuse-cpp-run`` compares a C++ run that
already exists in ``--workdir`` (its command.json must match what this script
would run) instead of executing the binary again; ``--streaming-transfers``
compares the transfer traces cycle group by cycle group in bounded memory
(exactly equivalent to comparing the globally sorted lists because both files
are cycle non-decreasing, which is verified) and reads ``.gz`` traces.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_exact_event_model import rtl_transfers  # noqa: E402
from check_16k_cycle_model import rtl_noc_totals  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time_v(path: Path) -> dict:
    """Parse ``/usr/bin/time -v`` output into wall seconds and peak RSS."""
    info = {}
    if not path.exists():
        return info
    for line in path.read_text().splitlines():
        if "Elapsed (wall clock) time" in line:
            # "... (h:mm:ss or m:ss): 1:02.34"
            raw = line.split("): ", 1)[-1].strip()
            try:
                seconds = 0.0
                for part in raw.split(":"):
                    seconds = seconds * 60 + float(part)
                info["wall_seconds"] = round(seconds, 3)
            except ValueError:
                info["wall_raw"] = raw
        elif "Maximum resident set size" in line:
            info["max_rss_kb"] = int(line.rsplit(":", 1)[-1].strip())
        elif "User time (seconds)" in line:
            info["user_seconds"] = float(line.rsplit(":", 1)[-1].strip())
        elif "System time (seconds)" in line:
            info["system_seconds"] = float(line.rsplit(":", 1)[-1].strip())
        elif "Exit status" in line:
            info["exit_status"] = int(line.rsplit(":", 1)[-1].strip())
    return info


TRANSFER_COLUMNS = ("cycle", "scope", "node", "direction", "packet_type", "source_id", "epoch",
                    "block_id", "dest_x", "dest_y", "last")


def open_text(path: Path):
    """Text handle for a plain or gzip-compressed CSV."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", newline="")
    return path.open(newline="")


def iter_transfer_groups(path: Path):
    """Yield (cycle, sorted accepted-transfer tuples at that cycle) in file order.

    The tuple is the one ``rtl_transfers`` builds. The cycle column must be
    non-decreasing (RTL events are written as they occur; the C++ streaming
    sink writes in generation order); otherwise this raises, because the
    group-wise comparison would no longer equal a global sort.
    """
    with open_text(path) as handle:
        reader = csv.reader(handle)
        header = next(reader)
        index = {name: header.index(name) for name in ("event",) + TRANSFER_COLUMNS}
        ev, cy, sc, no, di, pt, si, ep, bi, dx, dy, la = (index[n] for n in ("event",) + TRANSFER_COLUMNS)
        current = None
        group = []
        for row in reader:
            if not row or row[ev] != "accept":
                continue
            cycle = int(row[cy])
            if current is None:
                current = cycle
            elif cycle != current:
                if cycle < current:
                    raise ValueError(f"{path}: cycle {cycle} after {current}; transfer trace is not cycle-monotonic")
                group.sort()
                yield current, group
                current = cycle
                group = []
            group.append((cycle, row[sc], int(row[no]), row[di], int(row[pt]), int(row[si]), int(row[ep]),
                          int(row[bi]), int(row[dx]), int(row[dy]), int(row[la])))
        if group:
            group.sort()
            yield current, group


def compare_transfer_streams(expected_path: Path, observed_path: Path) -> dict:
    """Bounded-memory equivalent of ``sorted(rtl) == sorted(model)``.

    Returns counts, equality and the first differing record (index in the
    globally sorted order) when they differ.
    """
    expected_groups = iter_transfer_groups(expected_path)
    observed_groups = iter_transfer_groups(observed_path)
    n_expected = n_observed = 0
    equal = True
    first = None
    sentinel = (None, [])
    while True:
        e = next(expected_groups, sentinel)
        o = next(observed_groups, sentinel)
        if e is sentinel and o is sentinel:
            break
        e_cycle, e_group = e
        o_cycle, o_group = o
        if equal and (e_cycle != o_cycle or e_group != o_group):
            equal = False
            if e_cycle == o_cycle:
                i = next((i for i, (a, b) in enumerate(zip(e_group, o_group)) if a != b),
                         min(len(e_group), len(o_group)))
                first = dict(index=n_expected + i, rtl=e_group[i:i + 1], model=o_group[i:i + 1])
            elif e_cycle is None or (o_cycle is not None and o_cycle < e_cycle):
                first = dict(index=n_observed, rtl=[], model=o_group[:1])
            else:
                first = dict(index=n_expected, rtl=e_group[:1], model=[])
        n_expected += len(e_group)
        n_observed += len(o_group)
        if not equal:
            # Keep counting both files so the totals are reported; groups no
            # longer need to be compared.
            for cycle, group in expected_groups:
                n_expected += len(group)
            for cycle, group in observed_groups:
                n_observed += len(group)
            break
    return dict(equal=equal, expected=n_expected, observed=n_observed, first_difference=first)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--rtl-log", required=True, type=Path)
    parser.add_argument("--rtl-events", required=True, type=Path)
    parser.add_argument("--rtl-noc", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="comparison JSON; refuses to overwrite")
    parser.add_argument("--workdir", required=True, type=Path,
                        help="directory receiving the C++ metrics, transfer trace, log and timing")
    parser.add_argument("--mesh-x", type=int, default=4)
    parser.add_argument("--mesh-y", type=int, default=4)
    parser.add_argument("--h0-per-h1", type=int, default=2)
    parser.add_argument("--cores-per-h0", type=int, default=16)
    parser.add_argument("--h0-mvms", type=int, default=4)
    parser.add_argument("--h1-mvms", type=int, default=2)
    parser.add_argument("--cross-mvms", type=int, default=4)
    parser.add_argument("--binary", type=Path, default=Path("build/cpp_exact/azilla_exact_cir"))
    parser.add_argument("--library", type=Path,
                        default=Path("build/cycle_model_ramulator/libazilla_ramulator.so"))
    parser.add_argument("--config", type=Path, default=Path("tb/ramulator_128x32.yaml"))
    parser.add_argument("--python-differential", type=Path,
                        help="stored scripts/check_cir_exact_vcs_trace.py JSON for this trace; "
                             "embedded and cross-checked for the three-way statement")
    parser.add_argument("--label", default=None, help="free-form trace label recorded in the output")
    parser.add_argument("--no-time", action="store_true", help="do not wrap the binary in /usr/bin/time -v")
    parser.add_argument("--binary-arg", action="append", default=[],
                        help="extra argument appended to the C++ command (repeatable; e.g. --binary-arg=--dense-all-blocks --binary-arg=1)")
    parser.add_argument("--reuse-cpp-run", action="store_true",
                        help="do not run the binary; compare the run already in --workdir (command.json must match)")
    parser.add_argument("--streaming-transfers", action="store_true",
                        help="compare transfer traces cycle group by cycle group in bounded memory; accepts .gz inputs")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite existing evidence")
    for p in (args.dataset, args.rtl_log, args.rtl_events, args.rtl_noc, args.binary, args.library, args.config):
        if not p.exists():
            parser.error(f"missing input {p}")
    if args.schedule and not args.schedule.exists():
        parser.error(f"missing schedule {args.schedule}")

    # Sources whose hashes are recorded and re-checked after the run: the C++
    # port, the binary, the shared Ramulator library/YAML, and the testbench
    # files that the Python checker also pins (the RTL trace was produced by
    # them; this does not re-run them).
    source_paths = sorted((ROOT / "model/cpp_exact").glob("*.cpp")) + sorted((ROOT / "model/cpp_exact").glob("*.hpp"))
    source_paths += [ROOT / "model/cpp_exact/CMakeLists.txt", ROOT / "tb/ising_mesh_tb.sv",
                     ROOT / "tb/ramulator_node_frontend.sv", ROOT / "tb/ramulator_dpi_bridge.sv",
                     ROOT / "tb/ramulator_dpi.cpp"]
    source_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in source_paths if p.exists()}
    binary_sha = sha256(args.binary)
    library_sha = sha256(args.library)
    config_sha = sha256(args.config)

    log = args.rtl_log.read_text(errors="replace")
    init = re.search(r"initialization complete at cycle (\d+)", log)
    total = re.search(r"total_cycles=(\d+)", log)
    if not init or not total or "VCS" not in log:
        parser.error("missing VCS phase timing in log")
    expected_timing = (int(init[1]), int(total[1]) - int(init[1]), int(total[1]))

    args.workdir.mkdir(parents=True, exist_ok=True)
    metrics_prefix = args.workdir / "metrics"
    transfer_path = args.workdir / "transfers.csv"
    run_log = args.workdir / "run.log"
    time_path = args.workdir / "wall.time"
    if not args.reuse_cpp_run:
        for stale in (transfer_path, run_log, time_path, *args.workdir.glob("metrics_*")):
            if stale.exists():
                parser.error(f"workdir already holds {stale}; use a fresh directory")
    command = [str(args.binary), "simulate-exact-events", "--execution-mode", "cir",
               "--dataset", str(args.dataset),
               "--mesh-x", str(args.mesh_x), "--mesh-y", str(args.mesh_y),
               "--h0-per-h1", str(args.h0_per_h1), "--cores-per-h0", str(args.cores_per_h0),
               "--h0-mvms", str(args.h0_mvms), "--h1-mvms", str(args.h1_mvms),
               "--cross-mvms", str(args.cross_mvms),
               "--ramulator-library", str(args.library), "--ramulator-config", str(args.config),
               "--metrics-prefix", str(metrics_prefix), "--transfer-trace", str(transfer_path)]
    if args.schedule:
        command += ["--schedule", str(args.schedule)]
    command += list(args.binary_arg)
    reuse_info = None
    if args.reuse_cpp_run:
        # Compare a C++ run that already exists in the workdir. Fail closed:
        # its recorded command must be this script's command up to the
        # transfer-trace flag spelling (the streaming sink writes the same
        # rows in generation order) and the trace file location, which may
        # have been gzip-compressed since.
        command_path = args.workdir / "command.json"
        if not command_path.exists():
            parser.error(f"--reuse-cpp-run: {command_path} missing")
        recorded = json.loads(command_path.read_text())

        def normalise(cmd):
            pairs = {}
            positional = []
            i = 0
            while i < len(cmd):
                if cmd[i].startswith("--") and i + 1 < len(cmd):
                    pairs[cmd[i]] = cmd[i + 1]
                    i += 2
                else:
                    positional.append(cmd[i])
                    i += 1
            trace = pairs.pop("--transfer-trace", None) or pairs.pop("--transfer-trace-stream", None)
            pairs.pop("--metrics-prefix", None)
            # Path-valued inputs compare by resolved location (the binary runs with cwd=ROOT).
            for key in ("--dataset", "--schedule", "--ramulator-library", "--ramulator-config"):
                if key in pairs:
                    pairs[key] = str((ROOT / pairs[key]).resolve())
            positional = [str((ROOT / positional[0]).resolve())] + positional[1:] if positional else positional
            return positional, pairs, trace

        want_pos, want_pairs, _ = normalise(command)
        have_pos, have_pairs, have_trace = normalise(recorded)
        recorded_prefix = dict(zip(recorded, recorded[1:])).get("--metrics-prefix")
        if recorded_prefix:
            metrics_prefix = Path(recorded_prefix)
        if have_trace:
            transfer_path = Path(have_trace)
            if not transfer_path.exists() and Path(have_trace + ".gz").exists():
                transfer_path = Path(have_trace + ".gz")
        reuse_info = dict(command_path=str(command_path), recorded_command=recorded,
                          command_matches=(want_pos == have_pos and want_pairs == have_pairs),
                          transfer_trace=str(transfer_path), metrics_prefix=str(metrics_prefix),
                          streamed_trace=("--transfer-trace-stream" in recorded))
        if not reuse_info["command_matches"]:
            reuse_info["expected_command"] = command
        run_record = args.workdir / "cpp_run.json"
        if run_record.exists():
            rec = json.loads(run_record.read_text())
            reuse_info["run_record"] = rec
            reuse_info["binary_sha256_at_run"] = rec.get("binary_sha256")
            reuse_info["binary_unchanged_since_run"] = rec.get("binary_sha256") == binary_sha
        stdout = run_log.read_text(errors="replace") if run_log.exists() else ""
        timing_info = parse_time_v(time_path)
        timing_info["reused"] = True
        exit_status = timing_info.get("exit_status")
        if exit_status is None:
            exit_status = 0 if re.search(r"^PASS mode=exact-events", stdout, re.M) else None
        timing_info["returncode"] = exit_status
        checks = dict(binary_exit=exit_status == 0 and bool(re.search(r"^PASS mode=exact-events", stdout, re.M)),
                      reused_command_matches=reuse_info["command_matches"])
        if "binary_unchanged_since_run" in reuse_info:
            checks["binary_unchanged_since_run"] = reuse_info["binary_unchanged_since_run"]
        class _Proc:
            returncode = 0 if checks["binary_exit"] else 1
        proc = _Proc()
    else:
        wrapped = command if (args.no_time or not Path("/usr/bin/time").exists()) else \
            ["/usr/bin/time", "-v", "-o", str(time_path), *command]
        (args.workdir / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        started = time.time()
        with run_log.open("w") as handle:
            proc = subprocess.run(wrapped, stdout=handle, stderr=subprocess.STDOUT, cwd=str(ROOT))
        harness_wall = time.time() - started
        stdout = run_log.read_text(errors="replace")
        timing_info = parse_time_v(time_path)
        timing_info.setdefault("wall_seconds", round(harness_wall, 3))
        timing_info["harness_wall_seconds"] = round(harness_wall, 3)
        timing_info["returncode"] = proc.returncode
        checks = dict(binary_exit=proc.returncode == 0)
    observed_timing = None
    noc = None
    observed = []
    summary = None
    summary_path = Path(str(metrics_prefix) + "_summary.json")
    if proc.returncode == 0 and summary_path.exists() and transfer_path.exists():
        summary = json.loads(summary_path.read_text())
        t = summary["timing_cycles"]
        observed_timing = (int(t["initialization"]), int(t["iteration"]), int(t["total"]))
        n = summary["noc"]
        noc = (int(n["injected_flits"]), int(n["ejected_flits"]), int(n["physical_link_flits"]),
               int(n["injection_stalls"]), int(n["ejection_stalls"]), int(n["link_stalls"]))
        # The C++ transfer trace uses the RTL event schema, so the same parser
        # that reads the VCS trace reads it; both are sorted identically.
        if not args.streaming_transfers:
            observed = rtl_transfers(transfer_path)
        pass_line = re.search(r"initialization_cycles=(\d+) iteration_cycles=(\d+) total_cycles=(\d+)", stdout)
        checks["stdout_matches_summary"] = bool(pass_line) and tuple(int(x) for x in pass_line.groups()) == observed_timing
        checks["summary_geometry"] = summary.get("geometry", {}).get("spin_count") == \
            args.mesh_x * args.mesh_y * args.h0_per_h1 * args.cores_per_h0 * 32 and \
            summary.get("execution_mode") == "cir"
    stream_result = None
    if args.streaming_transfers:
        expected = []
        if summary is not None and transfer_path.exists():
            stream_result = compare_transfer_streams(args.rtl_events, transfer_path)
        expected_count = stream_result["expected"] if stream_result else \
            sum(len(g) for _, g in iter_transfer_groups(args.rtl_events))
        observed_count = stream_result["observed"] if stream_result else 0
    else:
        expected = rtl_transfers(args.rtl_events)
        expected_count, observed_count = len(expected), len(observed)
    expected_noc = rtl_noc_totals(args.rtl_noc)
    checks["timing"] = observed_timing == expected_timing
    if args.streaming_transfers:
        checks["transfers"] = bool(stream_result) and stream_result["equal"] and stream_result["observed"] > 0
    else:
        checks["transfers"] = bool(observed) and observed == expected
    checks["noc"] = noc == expected_noc
    checks["sources_unchanged"] = all(sha256(ROOT / p) == h for p, h in source_hashes.items()) and \
        sha256(args.binary) == binary_sha and sha256(args.library) == library_sha and \
        sha256(args.config) == config_sha

    evidence = dict(status="pass" if all(checks.values()) else "fail", checks=checks,
                    label=args.label, simulator="vcs", execution_mode="cir",
                    implementation="cpp_exact", binary=str(args.binary), binary_sha256=binary_sha,
                    source_sha256=source_hashes,
                    geometry=dict(mesh_x=args.mesh_x, mesh_y=args.mesh_y,
                                  h0_per_h1=args.h0_per_h1, cores_per_h0=args.cores_per_h0,
                                  spins=args.mesh_x * args.mesh_y * args.h0_per_h1 * args.cores_per_h0 * 32),
                    mvms=dict(h0=args.h0_mvms, h1=args.h1_mvms, cross=args.cross_mvms),
                    scope="CIR timing-only; supplied geometry, dataset and schedule only; "
                          "stored VCS trace, no new RTL simulation",
                    expected_timing=list(expected_timing),
                    observed_timing=list(observed_timing) if observed_timing else None,
                    expected_noc=list(expected_noc), observed_noc=list(noc) if noc else None,
                    expected_transfers=expected_count, observed_transfers=observed_count,
                    transfer_comparison="streaming-cycle-groups" if args.streaming_transfers else "sorted-lists",
                    cpp_run_reused=reuse_info, cpp_execution=timing_info, cpp_command=command,
                    cpp_outputs={k: str(v) for k, v in dict(summary=summary_path, transfers=transfer_path,
                                                            run_log=run_log, time=time_path).items()},
                    command=sys.argv,
                    sha256={str(p): sha256(p) for p in (args.dataset, args.rtl_log, args.rtl_events,
                                                        args.rtl_noc, args.library, args.config)})
    if args.schedule:
        evidence["sha256"][str(args.schedule)] = sha256(args.schedule)
    if summary is not None:
        evidence["cpp_output_sha256"] = {p.name: sha256(p) for p in sorted(args.workdir.glob("metrics_*"))}
        evidence["cpp_output_sha256"]["transfers.csv"] = sha256(transfer_path)
        evidence["scheduled_jobs"] = summary.get("scheduled_jobs")
    if not checks["transfers"] and observed:
        i = next((i for i, (a, b) in enumerate(zip(expected, observed)) if a != b),
                 min(len(expected), len(observed)))
        evidence["first_difference"] = dict(index=i, rtl=expected[i:i + 1], model=observed[i:i + 1])
    if stream_result is not None and not stream_result["equal"]:
        evidence["first_difference"] = stream_result["first_difference"]
    if proc.returncode != 0:
        evidence["cpp_stderr_tail"] = stdout[-2000:]

    # Three-way statement: embed the stored Python-vs-RTL result and verify it
    # refers to the same RTL trace, dataset and schedule by SHA-256.
    if args.python_differential:
        py = json.loads(args.python_differential.read_text())
        py_sha = py.get("sha256", {})
        rtl_hashes_here = {sha256(args.rtl_events), sha256(args.rtl_noc)}
        py_hash_values = set(py_sha.values())
        same_trace = rtl_hashes_here <= py_hash_values
        same_dataset = sha256(args.dataset) in py_hash_values
        same_schedule = (sha256(args.schedule) in py_hash_values) if args.schedule else \
            not any(k.endswith("schedule.txt") or "schedule" in Path(k).name for k in py_sha)
        same_geometry = py.get("geometry") == dict(mesh_x=args.mesh_x, mesh_y=args.mesh_y,
                                                   h0_per_h1=args.h0_per_h1, cores_per_h0=args.cores_per_h0) \
            and py.get("mvms") == evidence["mvms"]
        evidence["python_reference"] = dict(
            path=str(args.python_differential), status=py.get("status"), checks=py.get("checks"),
            expected_timing=py.get("expected_timing"), observed_timing=py.get("observed_timing"),
            expected_noc=py.get("expected_noc"), observed_noc=py.get("observed_noc"),
            expected_transfers=py.get("expected_transfers"), observed_transfers=py.get("observed_transfers"),
            same_rtl_trace=same_trace, same_dataset=same_dataset, same_schedule=same_schedule,
            same_geometry=same_geometry,
            library_sha256_matches=py_sha.get("build/cycle_model_ramulator/libazilla_ramulator.so",
                                              py_sha.get(str(args.library))) == library_sha,
            config_sha256_matches=py_sha.get("tb/ramulator_128x32.yaml", py_sha.get(str(args.config))) == config_sha)
        evidence["three_way_agreement"] = bool(
            evidence["status"] == "pass" and py.get("status") == "pass" and same_trace and same_dataset
            and same_schedule and same_geometry and list(py.get("observed_timing") or []) == list(observed_timing or [])
            and list(py.get("observed_noc") or []) == list(noc or [])
            and py.get("observed_transfers") == observed_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({k: evidence[k] for k in ("status", "checks", "label", "geometry", "expected_timing",
                                                 "observed_timing", "expected_noc", "observed_noc",
                                                 "expected_transfers", "observed_transfers", "cpp_execution")
                      if k in evidence} | {"three_way_agreement": evidence.get("three_way_agreement")},
                     indent=2), flush=True)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
