#!/usr/bin/env python3
"""Exact-equivalence harness: Python exact-event model (oracle) vs C++ port.

Runs ``azilla_cycle_model.cli simulate-exact-events --execution-mode cir``
and ``build/cpp_exact/azilla_exact_cir`` on identical inputs, then compares
every field of metrics_summary.json, every cell of the NoC/node/DRAM CSV
tables and every accepted NoC transfer record.  No tolerance: any mismatch
fails the case.  Wall time and peak RSS are recorded for both sides with
``/usr/bin/time -v``.

This is optimization/port equivalence only; it does not extend RTL coverage.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = '/home/rsappidi/miniforge3/envs/azilla/bin/python3'
CPP = ROOT / 'build/cpp_exact/azilla_exact_cir'
LIBRARY = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
YAML = ROOT / 'tb/ramulator_128x32.yaml'
PORTED_SOURCES = [
    'adapters.py', 'compute.py', 'config.py', 'cross.py', 'events.py',
    'exact_events.py', 'hierarchy.py', 'memory.py', 'metrics.py', 'noc.py',
    'performance.py', 'ramulator.py', 'scheduler.py', 'workload.py', 'cli.py',
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate_dataset(family, blocks, path):
    """Same block-occupancy fixtures as scripts/check_fast_exact_model.py plus
    the (i+j)%3 sparse family from check_fast_shared_fetch.py."""
    pairs = []
    for a in range(blocks):
        for b in range(a + 1, blocks):
            if (family == 'dense' or
                    (family == 'sparse' and (b == a + 1 or b == (a + blocks // 2) % blocks)) or
                    (family == 'uneven' and (a == 0 or (a % 7 == 0 and b % 3 == 0))) or
                    (family == 'bank-conflict' and a % 8 == 0 and b % 8 == 0) or
                    (family == 'mod3' and (a + b) % 3 == 0)):
                pairs.append((a, b))
    with path.open('w') as handle:
        handle.write(f'{blocks * 32} 0\n')
        for a, b in pairs:
            u, v = a * 32 + 1, b * 32 + 1
            handle.write(f'{u} {v} -1\n{v} {u} -1\n')
    return len(pairs)


def cases():
    """(name, dataset spec, geometry, engines, idle_refresh_ticks)."""
    result = [
        dict(name='smoke256_cir', dataset=str(ROOT / 'tb/datasets/g256_smoke.txt'),
             geometry=(2, 1, 2, 2), engines=(1, 1, 1), idle=7600),
        dict(name='single_sparse_cir', family='sparse', geometry=(1, 1, 2, 8), engines=(1, 1, 1), idle=7600),
        dict(name='single_dense_cir', family='dense', geometry=(1, 1, 2, 4), engines=(2, 2, 2), idle=7600),
        dict(name='two_uneven_cir', family='uneven', geometry=(2, 1, 2, 8), engines=(4, 2, 4), idle=7600),
        dict(name='four_conflict_cir', family='bank-conflict', geometry=(2, 2, 2, 8), engines=(8, 8, 8), idle=7600),
        dict(name='sixteen_sparse_cir', family='sparse', geometry=(4, 4, 2, 2), engines=(16, 16, 16), idle=7600),
        dict(name='two_dense_cir', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4), idle=7600),
        dict(name='two_h1_dense512_cir', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4), idle=0),
        dict(name='two_h1_sparse512_cir', family='mod3', geometry=(2, 1, 2, 4), engines=(4, 2, 4), idle=0),
        dict(name='four_h1_dense512_cir', family='dense', geometry=(2, 2, 1, 4), engines=(4, 2, 4), idle=0),
        dict(name='full_h1_dense1024_cir', family='dense', geometry=(1, 1, 8, 4), engines=(4, 2, 4), idle=0),
        # Explicit --schedule file with cross-H1 owner overrides ((a+b) % nodes).
        dict(name='four_h1_dense512_schedule_cir', family='dense', geometry=(2, 2, 1, 4), engines=(4, 2, 4), idle=7600,
             schedule='owner-mod'),
        # Nondefault inter-H1 link timing and package chiplet latency
        # (parameterized-interconnect-projection paths).
        dict(name='two_dense_links_cir', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4), idle=7600,
             extra=['--link-latency-cycles', '3', '--link-flit-interval-cycles', '2',
                    '--link-max-inflight-flits', '3', '--link-credit-return-cycles', '2',
                    '--chiplet-link-latency-cycles', '5']),
        dict(name='sixteen_sparse_links_cir', family='sparse', geometry=(4, 4, 2, 2), engines=(4, 2, 4), idle=0,
             extra=['--link-latency-cycles', '2', '--link-flit-interval-cycles', '1',
                    '--link-max-inflight-flits', '2', '--link-credit-return-cycles', '1',
                    '--chiplet-link-latency-cycles', '3']),
        dict(name='kings16k_chiplet_cir', dataset=str(ROOT / 'tb/datasets/g16384_kings.txt'),
             geometry=(4, 4, 2, 16), engines=(4, 2, 4), idle=7600,
             extra=['--chiplet-link-latency-cycles', '7']),
        dict(name='kings16k_cir', dataset=str(ROOT / 'tb/datasets/g16384_kings.txt'),
             geometry=(4, 4, 2, 16), engines=(4, 2, 4), idle=7600),
        dict(name='torus64k_cir',
             dataset=str(ROOT / 'results/paper_canonical/datasets/prepared/toroidal_d4_n65536.txt'),
             geometry=(1, 1, 8, 256), engines=(4, 2, 4), idle=7600),
        dict(name='community128k_cir',
             dataset=str(ROOT / 'results/paper_canonical/datasets/prepared/community_d16_n131072_s1.txt'),
             geometry=(2, 1, 8, 256), engines=(4, 2, 4), idle=0,
             yaml=str(ROOT / 'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot/ramulator_128x32.yaml'),
             reference=str(ROOT / 'results/cir_matched_all_campaigns_v3_active/points/'
                           'provisional_exact_matrix_v1/community_d16_n131072_s1/cir'),
             transfers=False),
    ]
    return result


def write_schedule(case, dataset, path):
    """Default sorted active pairs, with cross-H1 owners forced to (a+b) % nodes."""
    x, y, h, c = case['geometry']
    nodes, blocks_per_h1 = x * y, h * c
    pairs = set()
    with Path(dataset).open() as handle:
        next(handle)
        for line in handle:
            u, v, w = map(int, line.split())
            a, b = (u - 1) // 32, (v - 1) // 32
            if w != 0 and a != b:
                pairs.add((min(a, b), max(a, b)))
    with path.open('w') as handle:
        handle.write('# block_a block_b owner\n')
        for a, b in sorted(pairs):
            owner = (a + b) % nodes if a // blocks_per_h1 != b // blocks_per_h1 else -1
            handle.write(f'{a} {b} {owner}\n')


def command_args(case, dataset, yaml, schedule=None):
    x, y, h, c = case['geometry']
    h0, h1, cross = case['engines']
    args = ['simulate-exact-events', '--execution-mode', 'cir', '--dataset', str(dataset),
            '--mesh-x', str(x), '--mesh-y', str(y), '--h0-per-h1', str(h), '--cores-per-h0', str(c),
            '--h0-mvms', str(h0), '--h1-mvms', str(h1), '--cross-mvms', str(cross),
            '--ramulator-library', str(LIBRARY), '--ramulator-config', str(yaml)]
    if case['idle']:
        args += ['--idle-refresh-period-ticks', str(case['idle'])]
    args += list(case.get('extra', []))
    if schedule is not None:
        args += ['--schedule', str(schedule)]
    return args


def parse_time(path):
    text = Path(path).read_text() if Path(path).exists() else ''
    wall = re.search(r'Elapsed \(wall clock\) time.*?: (.*)', text)
    rss = re.search(r'Maximum resident set size \(kbytes\): (\d+)', text)
    seconds = None
    if wall:
        parts = wall.group(1).strip().split(':')
        seconds = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    return dict(wall_seconds=seconds, max_rss_kb=int(rss.group(1)) if rss else None)


def run_side(label, executable, args, target, transfers=True):
    target.mkdir(parents=True, exist_ok=True)
    command = list(executable) + args + ['--metrics-prefix', str(target / 'metrics')]
    if transfers:
        command += ['--transfer-trace', str(target / 'transfers.csv')]
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


def compare_dirs(oracle_dir, cpp_dir, transfers=True):
    names = ['metrics_summary.json', 'metrics_noc.csv', 'metrics_nodes.csv', 'metrics_dram.csv']
    if transfers:
        names.append('transfers.csv')
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
    parser.add_argument('--output', type=Path, default=ROOT / 'results/cpp_exact_port_20261006')
    parser.add_argument('--filter', default='', help='substring of case names to run')
    parser.add_argument('--only', nargs='*', default=None, help='exact case names to run')
    parser.add_argument('--skip-python', action='store_true',
                        help='reuse an existing python/ directory for each case instead of rerunning')
    parser.add_argument('--skip-cpp', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--rebuild-report', action='store_true',
                        help='rebuild comparison_report.json from cases/*/comparison.json without running')
    parser.add_argument('--binary', type=Path, default=None,
                        help='C++ binary to test instead of build/cpp_exact/azilla_exact_cir (recorded in the report)')
    args = parser.parse_args()
    global CPP
    if args.binary is not None:
        CPP = args.binary.resolve()
    selected = [c for c in cases() if (args.only is None or c['name'] in args.only) and args.filter in c['name']]
    if args.list:
        for c in selected:
            print(c['name'])
        return
    out = args.output
    (out / 'cases').mkdir(parents=True, exist_ok=True)
    report_path = out / 'comparison_report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else dict(cases={})
    report.update(
        updated_utc=datetime.now(timezone.utc).isoformat(),
        scope='C++ port of the Python exact-event cir model; complete-run equivalence, not RTL certification',
        python_sources_sha256={name: sha256(ROOT / 'model/azilla_cycle_model' / name) for name in PORTED_SOURCES},
        cpp_sources_sha256={p.name: sha256(p) for p in sorted((ROOT / 'model/cpp_exact').iterdir()) if p.is_file()},
        harness_sha256=sha256(Path(__file__)),
        ramulator_library_sha256=sha256(LIBRARY),
        ramulator_config_sha256=sha256(YAML),
        cpp_binary_sha256=sha256(CPP) if CPP.exists() else None,
    )

    def save():
        # Merge with per-case files so concurrent harness processes cannot
        # overwrite each other's results.
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
        if 'dataset' in case:
            dataset = Path(case['dataset'])
        else:
            dataset = case_dir / 'dataset.txt'
            if not dataset.exists():
                x, y, h, c = case['geometry']
                generate_dataset(case['family'], x * y * h * c, dataset)
        if not dataset.exists():
            report['cases'][name] = dict(case=case, status='missing-dataset')
            save()
            print('MISSING', name, dataset, flush=True)
            continue
        yaml = Path(case.get('yaml', YAML))
        schedule = None
        if case.get('schedule'):
            schedule = case_dir / 'schedule.txt'
            if not schedule.exists():
                write_schedule(case, dataset, schedule)
        cargs = command_args(case, dataset, yaml, schedule)
        item = dict(case=case, dataset=str(dataset), dataset_sha256=sha256(dataset),
                    ramulator_config=str(yaml), ramulator_config_sha256=sha256(yaml),
                    cpp_binary_sha256=sha256(CPP) if CPP.exists() else None,
                    cpp_sources_sha256=report['cpp_sources_sha256'],
                    python_sources_sha256=report['python_sources_sha256'], execution=[])
        transfers = case.get('transfers', True)
        oracle_dir = case_dir / 'python'
        if case.get('reference'):
            oracle_dir = Path(case['reference'])
            item['oracle'] = dict(source='existing-reference', path=str(oracle_dir),
                                  note='Python side not rerun; compared against saved campaign metrics')
            ref_time = parse_time(oracle_dir / 'wall.time')
            item['execution'].append(dict(label='python-reference', **ref_time))
        elif args.skip_python:
            item['oracle'] = dict(source='existing-python-dir', path=str(oracle_dir))
            item['execution'].append(dict(label='python-existing', **parse_time(oracle_dir / 'wall.time')))
        else:
            item['execution'].append(run_side('python', [PYTHON, '-m', 'azilla_cycle_model.cli'], cargs,
                                              oracle_dir, transfers))
        cpp_dir = case_dir / 'cpp'
        if not args.skip_cpp:
            item['execution'].append(run_side('cpp', [str(CPP)], cargs, cpp_dir, transfers))
        if any(r.get('returncode') for r in item['execution']):
            item['status'] = 'execution-failure'
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
        passed, fields = compare_dirs(oracle_dir, cpp_dir, transfers)
        item['fields'] = fields
        item['status'] = 'pass' if passed else 'fail'
        py = next((r for r in item['execution'] if r['label'].startswith('python')), None)
        cpp = next((r for r in item['execution'] if r['label'] == 'cpp'), None)
        if py and cpp and py.get('wall_seconds') and cpp.get('wall_seconds'):
            item['speedup'] = py['wall_seconds'] / cpp['wall_seconds']
        (case_dir / 'comparison.json').write_text(json.dumps(item, indent=2) + '\n')
        report['cases'][name] = item
        save()
        print(item['status'].upper(), name, 'speedup', round(item.get('speedup', 0), 2),
              'byte_identical', all(f.get('byte_identical') for f in fields.values()), flush=True)


if __name__ == '__main__':
    main()
