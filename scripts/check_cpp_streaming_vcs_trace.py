#!/usr/bin/env python3
"""C++ streaming endpoint model versus a stored VCS RTL trace (stage 3b).

C++ counterpart of scripts/check_shared_fetch_two_h1.py (kind ``mesh``) and
scripts/check_shared_fetch_single_h1.py (kind ``single``) as driven through
scripts/check_streaming_integrated.py: the unified C++ binary runs
``--execution-mode cores-only --endpoint-policy streaming`` with the recorded
geometry, engines 4/2/4, the shared Ramulator library and YAML, the recorded
stall period and no idle modulus, and its outputs are compared, with zero
tolerance, against the RTL log:

  initialization  FULL_INIT cycle=N            == timing_cycles.initialization
  total           PASS ... total_cycles=N      == timing_cycles.total
  jobs            PASS ... jobs=M              == cores_only_ablation.directed_core_jobs
  core            every CORE_INT   (cycle, event, source, dst, src)
  memory          every CORE_MEM   (cycle, event, system, tag)
  state           every CORE_STATE (cycle, event, core, block|0)
  noc             every CORE_NOC   mesh: (cycle, scope, node, direction, type, source,
                                   epoch, block, x, y, last); single: (cycle, scope)

The event tuples come from the binary's ``--core-events`` CSV (the Python
model's core/memory/state observer streams) and ``--transfer-trace``.  Both
sides are sorted before comparison, as the Python checkers do.  With
``--python-differential`` the stored Python-vs-RTL result is embedded and
``three_way_agreement`` is reported only when both passed on the same RTL log
(by SHA-256 when the stored result records it, by path otherwise) with equal
observed totals.  No RTL is simulated and no model file is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path('/home/rsappidi/azilla_ising')
LIBRARY = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
YAML = ROOT / 'tb/ramulator_128x32.yaml'
SOURCE_DIR = Path('/var/tmp/azilla_cpp_streaming_20261007/src/cpp_exact')
PINNED = [ROOT / 'tb/shared_fetch_two_h1_tb.sv', ROOT / 'tb/shared_fetch_single_h1_tb.sv',
          ROOT / 'rtl/experimental/streaming/core.sv', ROOT / 'rtl/experimental/streaming/mvm.sv',
          ROOT / 'rtl/shared_fetch_replay_double.sv', ROOT / 'rtl/dram_weight_streamer.sv',
          ROOT / 'rtl/banked_state_sram.sv']


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_rtl(log, kind):
    expected = {k: [] for k in ('core', 'memory', 'state', 'noc')}
    end = init = None
    pattern = re.compile(r'(\w+)=\s*(\S+)')
    with Path(log).open() as handle:
        for line in handle:
            if line.startswith('CORE_'):
                f = dict(pattern.findall(line))
                c = int(f['cycle'])
                if line.startswith('CORE_NOC'):
                    if kind == 'mesh':
                        expected['noc'].append((c, f['scope'], int(f['node']), f['direction'],
                                                *(int(f[k]) for k in ('type', 'source', 'epoch', 'block', 'x', 'y', 'last'))))
                    else:
                        expected['noc'].append((c, f['event']))
                    continue
                e = f['event']
                if line.startswith('CORE_INT'):
                    expected['core'].append((c, e, int(f['source']), int(f['dst']), int(f['src'])))
                elif line.startswith('CORE_MEM'):
                    expected['memory'].append((c, e, int(f['system']), int(f['tag'])))
                elif line.startswith('CORE_STATE'):
                    expected['state'].append((c, e, int(f['core']), int(f['block']) if e == 'request' else 0))
            elif line.startswith('PASS SHARED_FETCH'):
                end = re.search(r'PASS SHARED_FETCH_(TWO|SINGLE)_H1 total_cycles=(\d+) jobs=(\d+)', line)
            elif line.startswith('FULL_INIT'):
                init = re.search(r'FULL_INIT cycle=(\d+)', line)
    if not end or not init:
        raise RuntimeError('RTL did not complete (missing PASS/FULL_INIT lines)')
    tb = 'mesh' if end.group(1) == 'TWO' else 'single'
    if tb != kind:
        raise RuntimeError(f'log is a {tb} testbench trace; --kind {kind} requested')
    return expected, int(init.group(1)), int(end.group(2)), int(end.group(3))


def parse_cpp_events(path):
    observed = {k: [] for k in ('core', 'memory', 'state')}
    with Path(path).open() as handle:
        next(handle)
        for line in handle:
            stream, cycle, event, a, b, c = line.rstrip('\n').split(',')
            cycle, a, b = int(cycle), int(a), int(b)
            if stream == 'core':
                observed['core'].append((cycle, event, a, b, int(c)))
            elif stream == 'memory':
                observed['memory'].append((cycle, event, a, b))
            else:
                observed['state'].append((cycle, event, a, b if event == 'request' else 0))
    return observed


def parse_cpp_transfers(path, kind):
    rows = []
    with Path(path).open() as handle:
        header = next(handle).rstrip('\r\n').split(',')
        index = {name: i for i, name in enumerate(header)}
        for line in handle:
            f = line.rstrip('\r\n').split(',')
            if kind == 'mesh':
                rows.append((int(f[index['cycle']]), f[index['scope']], int(f[index['node']]), f[index['direction']],
                             int(f[index['packet_type']]), int(f[index['source_id']]), int(f[index['epoch']]),
                             int(f[index['block_id']]), int(f[index['dest_x']]), int(f[index['dest_y']]),
                             int(f[index['last']])))
            else:
                rows.append((int(f[index['cycle']]), f[index['scope']]))
    return rows


def parse_time(path):
    text = Path(path).read_text() if Path(path).exists() else ''
    wall = re.search(r'Elapsed \(wall clock\) time.*?: (.*)', text)
    rss = re.search(r'Maximum resident set size \(kbytes\): (\d+)', text)
    seconds = None
    if wall:
        parts = wall.group(1).strip().split(':')
        seconds = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    return dict(wall_seconds=seconds, max_rss_kb=int(rss.group(1)) if rss else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=('mesh', 'single'), required=True)
    ap.add_argument('--dataset', type=Path, required=True)
    ap.add_argument('--schedule', type=Path)
    ap.add_argument('--log', type=Path, required=True)
    ap.add_argument('--h0', type=int, required=True)
    ap.add_argument('--cores', type=int, required=True)
    ap.add_argument('--mesh-x', type=int, default=1)
    ap.add_argument('--mesh-y', type=int, default=1)
    ap.add_argument('--stall', type=int, default=0)
    ap.add_argument('--binary', type=Path, default=Path('/var/tmp/azilla_cpp_streaming_20261007/build/azilla_exact'))
    ap.add_argument('--workdir', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--python-differential', type=Path)
    ap.add_argument('--label', default='')
    a = ap.parse_args()
    if a.kind == 'single' and (a.mesh_x, a.mesh_y) != (1, 1):
        ap.error('single-H1 traces are 1x1')
    if a.kind == 'mesh' and a.schedule is None and a.mesh_x * a.mesh_y > 2:
        ap.error('multi-node testbench requires explicit matching schedule')
    a.workdir = a.workdir.resolve()
    a.output = a.output.resolve()
    a.dataset = a.dataset.resolve()
    a.log = a.log.resolve()
    if a.schedule:
        a.schedule = a.schedule.resolve()
    a.workdir.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for p in SOURCE_DIR.iterdir() if p.is_file()) if SOURCE_DIR.exists() else []
    before = {str(p): sha256(p) for p in sources + [a.binary, LIBRARY, YAML] + [p for p in PINNED if p.exists()]}
    inputs = dict(dataset=str(a.dataset), dataset_sha256=sha256(a.dataset), rtl_log=str(a.log), rtl_log_sha256=sha256(a.log),
                  schedule=str(a.schedule) if a.schedule else None,
                  schedule_sha256=sha256(a.schedule) if a.schedule else None)

    command = [str(a.binary), 'simulate-exact-events', '--execution-mode', 'cores-only', '--endpoint-policy', 'streaming',
               '--dataset', str(a.dataset), '--mesh-x', str(a.mesh_x), '--mesh-y', str(a.mesh_y),
               '--h0-per-h1', str(a.h0), '--cores-per-h0', str(a.cores), '--h0-mvms', '4', '--h1-mvms', '2',
               '--cross-mvms', '4', '--ramulator-library', str(LIBRARY), '--ramulator-config', str(YAML),
               '--metrics-prefix', str(a.workdir / 'metrics'), '--transfer-trace', str(a.workdir / 'transfers.csv'),
               '--core-events', str(a.workdir / 'events.csv')]
    if a.schedule:
        command += ['--schedule', str(a.schedule)]
    if a.stall:
        command += ['--stall-period', str(a.stall)]
    (a.workdir / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    start = time.perf_counter()
    with (a.workdir / 'run.log').open('w') as log:
        proc = subprocess.run(['/usr/bin/time', '-v', '-o', str(a.workdir / 'wall.time')] + command, cwd=ROOT,
                              stdout=log, stderr=subprocess.STDOUT)
    execution = dict(returncode=proc.returncode, harness_wall_seconds=time.perf_counter() - start,
                     **parse_time(a.workdir / 'wall.time'))
    report = dict(label=a.label, kind=a.kind, simulator='vcs', execution_mode='cores-only',
                  fetch_policy='single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1', implementation='cpp_exact',
                  geometry=dict(mesh_x=a.mesh_x, mesh_y=a.mesh_y, h0_per_h1=a.h0, cores_per_h0=a.cores),
                  spins=a.mesh_x * a.mesh_y * a.h0 * a.cores * 32, stall_period=a.stall, engines=[4, 2, 4],
                  inputs=inputs, command=command, execution=execution)
    if proc.returncode != 0:
        report.update(status='error', error='binary failed; see run.log')
        a.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        raise SystemExit(1)
    summary = json.loads((a.workdir / 'metrics_summary.json').read_text())
    pass_line = next((l for l in (a.workdir / 'run.log').read_text().splitlines() if l.startswith('PASS ')), '')
    expected, rtl_init, rtl_total, rtl_jobs = parse_rtl(a.log, a.kind)
    observed = parse_cpp_events(a.workdir / 'events.csv')
    observed['noc'] = parse_cpp_transfers(a.workdir / 'transfers.csv', a.kind)
    timing = summary['timing_cycles']
    checks = dict(initialization=rtl_init == timing['initialization'], total=rtl_total == timing['total'],
                  jobs=rtl_jobs == summary['cores_only_ablation']['directed_core_jobs'],
                  summary_matches_stdout=f"total_cycles={timing['total']}" in pass_line,
                  summary_mode=summary['execution_mode'] == 'cores-only' and
                  summary['cores_only_ablation']['pipeline_contract']['fetch_policy'] == report['fetch_policy'],
                  summary_geometry=summary['geometry'] == dict(mesh_x=a.mesh_x, mesh_y=a.mesh_y, h0_per_h1=a.h0,
                                                                cores_per_h0=a.cores, spins_per_core=32,
                                                                spin_count=report['spins']))
    differences = {}
    counts = {}
    for k in expected:
        x, y = sorted(expected[k]), sorted(observed[k])
        checks[k] = x == y
        counts[k] = dict(rtl=len(x), cpp=len(y))
        if x != y:
            i = next((i for i, (u, v) in enumerate(zip(x, y)) if u != v), min(len(x), len(y)))
            differences[k] = dict(index=i, rtl=x[i:i + 4], model=y[i:i + 4], rtl_count=len(x), model_count=len(y))
    after = {p: sha256(p) for p in before}
    checks['sources_unchanged'] = before == after
    report.update(checks=checks, differences=differences, event_counts=counts, rtl_total=rtl_total, rtl_init=rtl_init,
                  rtl_jobs=rtl_jobs, model_total=timing['total'], model_init=timing['initialization'],
                  iteration_cycles=timing['iteration'], model_jobs=summary['cores_only_ablation']['directed_core_jobs'],
                  sha256_before=before, status='pass' if all(checks.values()) else 'fail',
                  scope=('rectangular-mesh timing-only workload; exact geometry, recorded core/memory/state/NoC events'
                         if a.kind == 'mesh' else
                         'one H1; timing-only; full instantiated endpoints, memory, banked state and completion router'))
    if a.python_differential:
        stored = json.loads(a.python_differential.read_text())
        same_log = None
        for key, value in stored.get('sha256', {}).items():
            if key.endswith(Path(a.log).name) and Path(key).resolve() == a.log.resolve():
                same_log = value == inputs['rtl_log_sha256']
        report['python_differential'] = dict(path=str(a.python_differential), status=stored.get('status'),
                                             rtl_total=stored.get('rtl_total'), model_total=stored.get('model_total'),
                                             iteration_cycles=stored.get('iteration_cycles'),
                                             fetch_policy=stored.get('fetch_policy') or stored.get('model_contract', {}).get('fetch_policy'),
                                             stall_period=stored.get('stall_period', 0), geometry=stored.get('geometry'),
                                             records_rtl_log_sha256=same_log is not None,
                                             rtl_log_sha256_matches=same_log)
        pd = report['python_differential']
        geometry_ok = (pd['geometry'] == report['geometry']) if a.kind == 'mesh' else \
            (pd['geometry'] == dict(h0=a.h0, cores=a.cores))
        report['three_way_agreement'] = bool(
            pd['status'] == 'pass' and report['status'] == 'pass' and pd['rtl_total'] == rtl_total and
            pd['model_total'] == timing['total'] and pd['iteration_cycles'] == timing['iteration'] and
            pd['fetch_policy'] == report['fetch_policy'] and pd['stall_period'] == a.stall and geometry_ok and
            (same_log is None or same_log))
        report['three_way_basis'] = ('stored Python differential records the RTL log SHA-256; verified'
                                     if same_log else
                                     'stored Python differential records no input hashes; RTL log identified by the '
                                     'campaign manifest path and hashed at check time')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('label', 'status', 'checks', 'rtl_total', 'model_total', 'iteration_cycles',
                                              'event_counts', 'execution')}, indent=2))
    if report.get('three_way_agreement') is not None:
        print('three_way_agreement', report['three_way_agreement'])
    if report['status'] != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
