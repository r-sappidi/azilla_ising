#!/usr/bin/env python3
"""Isolated watchdog-only change; retain the original full event differential."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'results/dense_vcs_validation_v3_20260908'
OUT = ROOT / 'results/dense_core16k_timeout_retry_20260908'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(p)


def main():
    OUT.mkdir(exist_ok=False)
    save(OUT / 'status.json', {'status': 'preparing'})
    snap = OUT / 'snapshot'
    # Retain all frozen experimental inputs and model/RTL except watchdog code.
    for directory in ('model', 'rtl', 'tb', 'scripts'):
        shutil.copytree(BASE / 'snapshot' / directory, snap / directory,
                        symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
    (snap / 'third_party').symlink_to(ROOT / 'third_party', target_is_directory=True)
    (snap / 'build').mkdir()
    (snap / 'build/cycle_model_ramulator').symlink_to(
        ROOT / 'build/cycle_model_ramulator', target_is_directory=True)
    tb = 'tb/shared_fetch_single_h1_tb.sv'
    old = (snap / tb).read_text()
    expected = old.replace(
        '  integer cycle=0,completed=0,total_jobs=0,phase=0,offset=0,init_core=0,init_row=0,quiet=0,delay_count=0;',
        '  integer cycle=0,completed=0,total_jobs=0,phase=0,offset=0,init_core=0,init_row=0,quiet=0,delay_count=0;\n  integer max_cycles=2000000;').replace(
        'if(cycle>2000000) $fatal(1,"timeout");',
        'if(cycle>max_cycles) $fatal(1,"timeout max_cycles=%0d completed=%0d/%0d",max_cycles,completed,2*total_jobs);').replace(
        '    pair_t pair;', '    pair_t pair;\n'
        '    void\'($value$plusargs("MAX_CYCLES=%d",max_cycles));\n'
        '    if(max_cycles<=0) $fatal(1,"MAX_CYCLES must be positive");\n'
        '    $display("TESTBENCH_MAX_CYCLES=%0d",max_cycles);')
    assert expected != old and (ROOT / tb).read_text() == expected
    runner = 'scripts/run_shared_fetch_single_h1.sh'
    old_runner = (snap / runner).read_text()
    expected_runner = old_runner.replace(
        '+DATASET="${DATASET:?DATASET required}" >',
        '+DATASET="${DATASET:?DATASET required}" +MAX_CYCLES="${MAX_CYCLES:-2000000}" >')
    assert expected_runner != old_runner and (ROOT / runner).read_text() == expected_runner
    for name in (tb, runner):
        shutil.copy2(ROOT / name, snap / name)
    sources = {str(p): sha(p) for directory in ('model', 'rtl', 'tb', 'scripts')
               for p in (snap / directory).rglob('*') if p.is_file() and not p.is_symlink()}
    for lib in ('build/cycle_model_ramulator/libazilla_ramulator.so', 'third_party/ramulator2/libramulator.so'):
        sources[str(ROOT / lib)] = sha(ROOT / lib)
    save(OUT / 'manifest.json', {'sources': sources, 'original': str(BASE),
        'changed_files': [tb, runner], 'change': 'watchdog plusarg only',
        'max_cycles': 20000000, 'geometry': [1, 1, 8, 256],
        'engines': [4, 2, 4], 'paper_ready': False})
    env = dict(os.environ, MAX_CYCLES='20000000', H0_COUNT='8', CORES_PER_H0='256',
               PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    for n in (1024, 16384):
        case = OUT / str(n)
        case.mkdir()
        dataset = snap / f'tb/datasets/dense_occupancy_{n}.txt'
        save(OUT / 'status.json', {'status': 'running', 'problem_spins': n,
                                 'phase': 'build-smoke' if n == 1024 else 'rtl-then-differential'})
        if n == 1024:
            commands = [('launcher', ['bash', runner])]
        else:
            commands = [('rtl', [str(OUT / '1024/build/simv'), f'+DATASET={dataset}', '+MAX_CYCLES=20000000']),
                ('differential', ['/home/rsappidi/miniforge3/envs/azilla/bin/python3',
                    'scripts/check_shared_fetch_single_h1.py', '--dataset', str(dataset),
                    '--log', str(case / 'rtl.log'), '--output', str(case / 'differential.json'),
                    '--h0', '8', '--cores', '256'])]
        for tag, cmd in commands:
            save(case / f'{tag}.command.json', cmd)
            with (case / f'{tag}.log').open('x') as log:
                subprocess.run(['/usr/bin/time', '-v', '-o', str(case / f'{tag}.time'), *cmd],
                    cwd=snap, env=env | {'OUTPUT_ROOT': str(case), 'DATASET': str(dataset)},
                    stdout=log, stderr=subprocess.STDOUT, check=True)
        d = json.loads((case / 'differential.json').read_text())
        assert d['status'] == 'pass' and all(d['checks'].values())
        assert all(sha(p) == h for p, h in sources.items())
        save(case / 'release_check.json', {'status': 'pass', 'sources_unchanged': True})
    save(OUT / 'status.json', {'status': 'complete', 'passed': [1024, 16384]})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if OUT.exists():
            save(OUT / 'status.json', {'status': 'failed', 'error': repr(exc)})
        raise
