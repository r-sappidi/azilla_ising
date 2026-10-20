#!/usr/bin/env python3
"""Exact-equivalence harness: Python streaming endpoint model (oracle) vs C++ port.

Stage 3 counterpart of scripts/check_cpp_exact_model.py.  Runs
``python3 -m azilla_cycle_model.streaming_cli ... --execution-mode cores-only``
(or ``shared_fetch_double_cli`` for the buffered-v3 policy) and the unified
C++ binary ``azilla_exact --execution-mode cores-only --endpoint-policy ...``
on identical inputs, then compares, with zero tolerance, every field of
metrics_summary.json, every cell of the NoC/node/DRAM CSV tables, every
accepted NoC transfer record and (when requested) the core/memory/state
observer event streams.  Wall time and peak RSS come from ``/usr/bin/time -v``.

Cases whose Python side already exists (the streaming endpoint matrix cells)
are compared against the stored metrics without rerunning Python; their
command.json arguments are copied exactly (``--metrics-prefix`` replaced).

Cases with a nonzero stall period or with event-stream comparison use
scripts/run_streaming_oracle.py, a wrapper around the unchanged streaming_cli
that only installs the checkers' ``arithmetic_fixture`` and observers.

This is optimization/port equivalence only; it does not extend RTL coverage.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path('/home/rsappidi/azilla_ising')
HERE = Path(__file__).resolve().parent
PYTHON = '/home/rsappidi/miniforge3/envs/azilla/bin/python3'
DEFAULT_CPP = Path('/var/tmp/azilla_cpp_streaming_20261007/build/azilla_exact')
LIBRARY = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
YAML = ROOT / 'tb/ramulator_128x32.yaml'
ORACLE_WRAPPER = HERE / 'run_streaming_oracle.py'
PORTED_SOURCES = [
    'streaming_cli.py', 'streaming_core.py', 'shared_fetch_events.py', 'shared_fetch_double.py',
    'shared_fetch.py', 'shared_fetch_double_cli.py', 'core_iteration_pipeline.py', 'core_iteration.py',
    'core_iteration_driver.py', 'core_full_controller.py', 'core_state_bank.py',
    'core_state_publication.py', 'core_full_events.py', 'compute.py', 'memory.py', 'noc.py',
    'scheduler.py', 'workload.py', 'exact_events.py', 'metrics.py', 'performance.py', 'ramulator.py',
    'mapping_adapter.py', 'cli.py',
]
STAGE1_CASES = ROOT / 'results/cpp_exact_port_20261006/cases'
WORKLOADS = ROOT / 'results/streaming_workloads_20261004_v1'
NOMINAL = ROOT / 'results/streaming_nominal_20261004_v1'
MATRIX = ROOT / 'results/streaming_endpoint_matrix_20261005_v1/points'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cases():
    """Each case: name, args (shared CLI arguments without metrics/trace
    outputs), policy, optional stall, events flag, optional reference dir."""
    result = []

    def add(name, dataset, geometry, engines=(4, 2, 4), policy='streaming', stall=0, schedule=None,
            events=False, yaml=YAML, extra=(), group='small'):
        x, y, h, c = geometry
        h0, h1, cross = engines
        args = ['simulate-exact-events', '--execution-mode', 'cores-only', '--dataset', str(dataset),
                '--mesh-x', str(x), '--mesh-y', str(y), '--h0-per-h1', str(h), '--cores-per-h0', str(c),
                '--h0-mvms', str(h0), '--h1-mvms', str(h1), '--cross-mvms', str(cross),
                '--ramulator-library', str(LIBRARY), '--ramulator-config', str(yaml)] + list(extra)
        if schedule is not None:
            args += ['--schedule', str(schedule)]
        result.append(dict(name=name, args=args, policy=policy, stall=stall, events=events, group=group,
                           geometry=geometry, engines=engines, dataset=str(dataset),
                           schedule=str(schedule) if schedule else None, yaml=str(yaml)))

    # Stage-1 small datasets (same fixtures), streaming policy, plus a few buffered-v3.
    add('smoke256_stream', ROOT / 'tb/datasets/g256_smoke.txt', (2, 1, 2, 2), events=True)
    add('smoke256_stream_e111', ROOT / 'tb/datasets/g256_smoke.txt', (2, 1, 2, 2), engines=(1, 1, 1))
    add('smoke256_buffered', ROOT / 'tb/datasets/g256_smoke.txt', (2, 1, 2, 2), policy='buffered-v3', events=True)
    add('single_sparse_stream', STAGE1_CASES / 'single_sparse_cir/dataset.txt', (1, 1, 2, 8), engines=(1, 1, 1))
    add('single_dense_stream', STAGE1_CASES / 'single_dense_cir/dataset.txt', (1, 1, 2, 4), engines=(2, 2, 2))
    add('two_uneven_stream', STAGE1_CASES / 'two_uneven_cir/dataset.txt', (2, 1, 2, 8), events=True)
    add('four_conflict_stream', STAGE1_CASES / 'four_conflict_cir/dataset.txt', (2, 2, 2, 8), engines=(8, 8, 8))
    add('sixteen_sparse_stream', STAGE1_CASES / 'sixteen_sparse_cir/dataset.txt', (4, 4, 2, 2), engines=(16, 16, 16))
    add('two_dense_stream', STAGE1_CASES / 'two_dense_cir/dataset.txt', (2, 1, 2, 4))
    add('two_dense_buffered', STAGE1_CASES / 'two_dense_cir/dataset.txt', (2, 1, 2, 4), policy='buffered-v3')
    add('two_h1_sparse512_stream', STAGE1_CASES / 'two_h1_sparse512_cir/dataset.txt', (2, 1, 2, 4))
    add('four_h1_dense512_stream', STAGE1_CASES / 'four_h1_dense512_cir/dataset.txt', (2, 2, 1, 4))
    add('four_h1_dense512_schedule_stream', STAGE1_CASES / 'four_h1_dense512_schedule_cir/dataset.txt', (2, 2, 1, 4),
        schedule=STAGE1_CASES / 'four_h1_dense512_schedule_cir/schedule.txt')
    add('full_h1_dense1024_stream', STAGE1_CASES / 'full_h1_dense1024_cir/dataset.txt', (1, 1, 8, 4))
    add('two_dense_links_stream', STAGE1_CASES / 'two_dense_cir/dataset.txt', (2, 1, 2, 4),
        extra=['--link-latency-cycles', '3', '--link-flit-interval-cycles', '2',
               '--link-max-inflight-flits', '3', '--link-credit-return-cycles', '2'])
    add('sixteen_sparse_links_stream', STAGE1_CASES / 'sixteen_sparse_cir/dataset.txt', (4, 4, 2, 2),
        extra=['--link-latency-cycles', '2', '--link-flit-interval-cycles', '1',
               '--link-max-inflight-flits', '2', '--link-credit-return-cycles', '1'])
    add('kings16k_stream', ROOT / 'tb/datasets/g16384_kings.txt', (4, 4, 2, 16), group='medium')
    # The 18 timing workloads of validate_streaming_campaign.py (RTL-matched inputs).
    for mx, my, cores in ((2, 1, 8), (2, 2, 4), (4, 2, 4)):
        tag = f'mesh{mx}x{my}_c{cores}'
        for family in ('dense', 'sparse', 'hotspot'):
            for stall in (0, 7):
                add(f'wl_{tag}_{family}_stall{stall}', WORKLOADS / tag / f'{family}.txt', (mx, my, 2, cores),
                    stall=stall, schedule=WORKLOADS / tag / f'{family}.schedule', events=True, group='workload')
    # Nominal single-H1 64K toroidal and the 128K two-H1 hierarchy stress (stall 0/7).
    add('nominal_toroidal_65536', NOMINAL / 'datasets/toroidal_65536.txt', (1, 1, 8, 256), events=True, group='nominal')
    for stall in (0, 7):
        add(f'nominal_hierarchy_stress_131072_stall{stall}', NOMINAL / 'datasets/hierarchy_stress_131072.txt',
            (2, 1, 8, 256), stall=stall, schedule=NOMINAL / 'datasets/hierarchy_stress_131072.schedule',
            events=True, group='nominal')
    # Buffered-v3 cross-check on one workload (policy fell out of the port).
    add('wl_mesh2x2_c4_sparse_stall0_buffered', WORKLOADS / 'mesh2x2_c4/sparse.txt', (2, 2, 2, 4),
        policy='buffered-v3', schedule=WORKLOADS / 'mesh2x2_c4/sparse.schedule', events=True, group='workload')
    # Completed streaming matrix cells: compare against stored Python metrics.
    for command_json in sorted(MATRIX.glob('*/*/*/cores-only/command.json')) + \
            sorted(MATRIX.glob('*/*/*/*/cores-only/command.json')):
        cell = command_json.parent
        if not (cell / 'metrics_summary.json').exists():
            continue
        rel = cell.parent.relative_to(MATRIX)
        name = 'matrix_' + '__'.join(rel.parts[1:])
        command = json.loads(command_json.read_text())
        assert command[2] == 'azilla_cycle_model.streaming_cli', command
        args = command[3:]
        index = args.index('--metrics-prefix')
        del args[index:index + 2]
        result.append(dict(name=name, args=args, policy='streaming', stall=0, events=False, group='matrix',
                           reference=str(cell), transfers=False, mapped=args[0] == 'simulate-mapped-exact-events'))
    return result


def parse_time(path):
    text = Path(path).read_text() if Path(path).exists() else ''
    wall = re.search(r'Elapsed \(wall clock\) time.*?: (.*)', text)
    rss = re.search(r'Maximum resident set size \(kbytes\): (\d+)', text)
    seconds = None
    if wall:
        parts = wall.group(1).strip().split(':')
        seconds = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    return dict(wall_seconds=seconds, max_rss_kb=int(rss.group(1)) if rss else None)


def run_side(label, executable, args, target, transfers=True, events=False):
    target.mkdir(parents=True, exist_ok=True)
    command = list(executable) + args + ['--metrics-prefix', str(target / 'metrics')]
    if transfers:
        command += ['--transfer-trace', str(target / 'transfers.csv')]
    if events:
        command += ['--core-events', str(target / 'events.csv')]
    full = ['/usr/bin/time', '-v', '-o', str(target / 'wall.time')] + command
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'model'), PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
    (target / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    start = time.perf_counter()
    with (target / 'run.log').open('w') as log:
        process = subprocess.run(full, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    record = dict(label=label, returncode=process.returncode, harness_wall_seconds=time.perf_counter() - start)
    record.update(parse_time(target / 'wall.time'))
    return record


def json_differences(a, b, path='$', out=None):
    out = [] if out is None else out
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))
                                   and not isinstance(a, bool) and not isinstance(b, bool)):
        out.append(dict(path=path, oracle=a, cpp=b, kind='type'))
    elif isinstance(a, dict):
        if a.keys() != b.keys():
            out.append(dict(path=path, oracle_keys=sorted(a), cpp_keys=sorted(b), kind='keys'))
        for key in sorted(a.keys() & b.keys()):
            json_differences(a[key], b[key], f'{path}.{key}', out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(dict(path=path, oracle_length=len(a), cpp_length=len(b), kind='length'))
        for index, (left, right) in enumerate(zip(a, b)):
            json_differences(left, right, f'{path}[{index}]', out)
    elif a != b:
        out.append(dict(path=path, oracle=a, cpp=b, kind='value'))
    return out


def cell(value):
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def csv_differences(a_text, b_text, name):
    import csv
    import io
    a_rows = list(csv.reader(io.StringIO(a_text)))
    b_rows = list(csv.reader(io.StringIO(b_text)))
    out = []
    if a_rows[0] != b_rows[0]:
        out.append(dict(path=f'{name}:header', oracle=a_rows[0], cpp=b_rows[0]))
    if len(a_rows) != len(b_rows):
        out.append(dict(path=f'{name}:rows', oracle_length=len(a_rows), cpp_length=len(b_rows)))
    for r, (left, right) in enumerate(zip(a_rows[1:], b_rows[1:]), 1):
        if len(left) != len(right):
            out.append(dict(path=f'{name}:row{r}', oracle=left, cpp=right))
            continue
        for c, (u, v) in enumerate(zip(left, right)):
            if cell(u) != cell(v):
                column = a_rows[0][c] if c < len(a_rows[0]) else c
                out.append(dict(path=f'{name}:row{r}.{column}', oracle=u, cpp=v))
                if len(out) > 200:
                    out.append(dict(path=f'{name}', note='difference list truncated'))
                    return out
    return out


def event_differences(a_text, b_text):
    """Event streams: byte identity reported; pass requires equal sorted multisets."""
    a_rows = sorted(a_text.splitlines()[1:])
    b_rows = sorted(b_text.splitlines()[1:])
    out = []
    if a_rows != b_rows:
        index = next((i for i, (x, y) in enumerate(zip(a_rows, b_rows)) if x != y), min(len(a_rows), len(b_rows)))
        out.append(dict(path='events:sorted', index=index, oracle=a_rows[index:index + 3], cpp=b_rows[index:index + 3],
                        oracle_count=len(a_rows), cpp_count=len(b_rows)))
    return out


def compare_dirs(oracle_dir, cpp_dir, transfers=True, events=False):
    names = ['metrics_summary.json', 'metrics_noc.csv', 'metrics_nodes.csv', 'metrics_dram.csv']
    if transfers:
        names.append('transfers.csv')
    if events:
        names.append('events.csv')
    fields = {}
    passed = True
    for name in names:
        left, right = Path(oracle_dir) / name, Path(cpp_dir) / name
        entry = dict(oracle_exists=left.exists(), cpp_exists=right.exists())
        if not (left.exists() and right.exists()):
            entry['status'] = 'missing'
            passed = False
            fields[name] = entry
            continue
        a, b = left.read_bytes(), right.read_bytes()
        entry['byte_identical'] = a == b
        entry['oracle_sha256'] = hashlib.sha256(a).hexdigest()
        entry['cpp_sha256'] = hashlib.sha256(b).hexdigest()
        if name.endswith('.json'):
            diffs = json_differences(json.loads(a), json.loads(b))
        elif name == 'events.csv':
            diffs = event_differences(a.decode(), b.decode())
            entry['rows'] = a.count(b'\n') - 1
        else:
            diffs = csv_differences(a.decode(), b.decode(), name)
        entry['differences'] = diffs
        entry['status'] = 'pass' if not diffs else 'fail'
        if diffs:
            passed = False
        fields[name] = entry
    return passed, fields


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('/var/tmp/azilla_cpp_streaming_20261007/results/oracle'))
    parser.add_argument('--filter', default='', help='substring of case names to run')
    parser.add_argument('--only', nargs='*', default=None, help='exact case names to run')
    parser.add_argument('--group', default=None, help='small | medium | workload | nominal | matrix')
    parser.add_argument('--skip-python', action='store_true',
                        help='reuse an existing python/ directory for each case instead of rerunning')
    parser.add_argument('--skip-cpp', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--rebuild-report', action='store_true')
    parser.add_argument('--binary', type=Path, default=DEFAULT_CPP)
    parser.add_argument('--cpp-library', type=Path, default=None,
                        help='Ramulator bridge library for the C++ side only (e.g. libazilla_ramulator_mt.so); '
                             'the oracle keeps build/cycle_model_ramulator/libazilla_ramulator.so')
    parser.add_argument('--cpp-arg', action='append', default=[],
                        help='extra argument appended to the C++ command only (repeatable, e.g. '
                             '--cpp-arg=--dram-threads --cpp-arg=4)')
    args = parser.parse_args()
    cpp = args.binary.resolve()
    cpp_library = args.cpp_library.resolve() if args.cpp_library is not None else None
    selected = [c for c in cases() if (args.only is None or c['name'] in args.only) and args.filter in c['name']
                and (args.group is None or c['group'] == args.group)]
    if args.list:
        for c in selected:
            print(c['group'], c['name'])
        return
    out = args.output
    (out / 'cases').mkdir(parents=True, exist_ok=True)
    report_path = out / 'comparison_report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else dict(cases={})
    cpp_sources = sorted(p for p in (DEFAULT_CPP.parent.parent / 'src/cpp_exact').iterdir() if p.is_file()) \
        if (DEFAULT_CPP.parent.parent / 'src/cpp_exact').exists() else []
    report.update(
        updated_utc=datetime.now(timezone.utc).isoformat(),
        scope='C++ port of the Python streaming endpoint (cores-only) model; complete-run equivalence, not RTL certification',
        python_sources_sha256={name: sha256(ROOT / 'model/azilla_cycle_model' / name) for name in PORTED_SOURCES},
        cpp_sources_sha256={p.name: sha256(p) for p in cpp_sources},
        harness_sha256=sha256(Path(__file__)),
        oracle_wrapper_sha256=sha256(ORACLE_WRAPPER),
        ramulator_library_sha256=sha256(LIBRARY),
        ramulator_config_sha256=sha256(YAML),
        cpp_ramulator_library=str(cpp_library) if cpp_library is not None else str(LIBRARY),
        cpp_ramulator_library_sha256=sha256(cpp_library) if cpp_library is not None else sha256(LIBRARY),
        cpp_extra_args=list(args.cpp_arg),
        cpp_binary=str(cpp),
        cpp_binary_sha256=sha256(cpp) if cpp.exists() else None,
    )

    def save():
        for comparison in sorted((out / 'cases').glob('*/comparison.json')):
            item = json.loads(comparison.read_text())
            report['cases'].setdefault(comparison.parent.name, item)
        report['cases'] = dict(sorted(report['cases'].items()))
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    if args.rebuild_report:
        report['cases'] = {}
        save()
        print('rebuilt', report_path, len(report['cases']), 'cases')
        return
    save()
    for case in selected:
        name = case['name']
        case_dir = out / 'cases' / name
        case_dir.mkdir(parents=True, exist_ok=True)
        transfers = case.get('transfers', True)
        events = case['events']
        inputs = {}
        for flag in ('--dataset', '--schedule', '--ramulator-config', '--artifact'):
            if flag in case['args']:
                value = case['args'][case['args'].index(flag) + 1]
                path = Path(value)
                if path.is_dir():
                    inputs[flag] = {str(p.relative_to(path)): sha256(p) for p in sorted(path.iterdir()) if p.is_file()}
                else:
                    inputs[flag] = sha256(path) if path.exists() else None
        item = dict(case=case, inputs_sha256=inputs, cpp_binary_sha256=report['cpp_binary_sha256'],
                    cpp_sources_sha256=report['cpp_sources_sha256'],
                    python_sources_sha256=report['python_sources_sha256'], execution=[])
        oracle_dir = case_dir / 'python'
        use_wrapper = case['stall'] != 0 or events or case['policy'] != 'streaming'
        python_args = list(case['args'])
        cpp_args = list(case['args']) + ['--endpoint-policy', case['policy']]
        if case['stall']:
            cpp_args += ['--stall-period', str(case['stall'])]
        if cpp_library is not None:
            cpp_args[cpp_args.index('--ramulator-library') + 1] = str(cpp_library)
        cpp_args += list(args.cpp_arg)
        item['cpp_ramulator_library'] = report['cpp_ramulator_library']
        item['cpp_ramulator_library_sha256'] = report['cpp_ramulator_library_sha256']
        item['cpp_extra_args'] = list(args.cpp_arg)
        if case.get('reference'):
            oracle_dir = Path(case['reference'])
            item['oracle'] = dict(source='existing-reference', path=str(oracle_dir),
                                  note='Python side not rerun; compared against the stored streaming matrix metrics')
            item['execution'].append(dict(label='python-reference', **parse_time(oracle_dir / 'wall.time')))
        elif args.skip_python and (oracle_dir / 'metrics_summary.json').exists():
            item['oracle'] = dict(source='existing-python-dir', path=str(oracle_dir))
            item['execution'].append(dict(label='python-existing', **parse_time(oracle_dir / 'wall.time')))
        else:
            if use_wrapper:
                executable = [PYTHON, str(ORACLE_WRAPPER)]
                python_args += ['--endpoint-policy', case['policy']]
                if case['stall']:
                    python_args += ['--stall-period', str(case['stall'])]
                item['oracle'] = dict(source='run_streaming_oracle.py wrapper around streaming_cli/shared_fetch_double_cli',
                                      stall_period=case['stall'], events=events)
            else:
                executable = [PYTHON, '-m', 'azilla_cycle_model.streaming_cli']
                item['oracle'] = dict(source='azilla_cycle_model.streaming_cli')
            item['execution'].append(run_side('python', executable, python_args, oracle_dir, transfers, events))
        cpp_dir = case_dir / 'cpp'
        if not args.skip_cpp:
            item['execution'].append(run_side('cpp', [str(cpp)], cpp_args, cpp_dir, transfers, events))
        if any(r.get('returncode') for r in item['execution']):
            item['status'] = 'execution-failure'
            (case_dir / 'comparison.json').write_text(json.dumps(item, indent=2) + '\n')
            report['cases'][name] = item
            save()
            print('EXECUTION-FAILURE', name, flush=True)
            continue
        if not (oracle_dir / 'metrics_summary.json').exists():
            item['status'] = 'oracle-pending'
            report['cases'][name] = item
            save()
            print('ORACLE-PENDING', name, flush=True)
            continue
        passed, fields = compare_dirs(oracle_dir, cpp_dir, transfers, events)
        item['fields'] = fields
        item['status'] = 'pass' if passed else 'fail'
        for side in ('python', 'cpp'):
            log = (oracle_dir if side == 'python' else cpp_dir) / 'run.log'
            if log.exists():
                first = log.read_text().splitlines()
                item[f'{side}_pass_line'] = next((l for l in first if l.startswith('PASS ')), None)
        py = next((r for r in item['execution'] if r['label'].startswith('python')), None)
        cpp_record = next((r for r in item['execution'] if r['label'] == 'cpp'), None)
        if py and cpp_record and py.get('wall_seconds') and cpp_record.get('wall_seconds'):
            item['speedup'] = py['wall_seconds'] / cpp_record['wall_seconds']
        (case_dir / 'comparison.json').write_text(json.dumps(item, indent=2) + '\n')
        report['cases'][name] = item
        save()
        print(item['status'].upper(), name, 'speedup', round(item.get('speedup', 0), 2),
              'byte_identical', all(f.get('byte_identical') for f in fields.values()), flush=True)


if __name__ == '__main__':
    main()
