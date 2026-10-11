#!/usr/bin/env python3
"""Reuse a passing, frozen two-H1 VCS executable for backpressure checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'results/dense_multi_h1_vcs_v2_20260908'
OUT = ROOT / 'results/dense_two_h1_backpressure_20260908'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def main():
    OUT.mkdir(exist_ok=False)
    save(OUT / 'status.json', {'status': 'waiting', 'gate': str(BASE / 'status.json')})
    while not (BASE / 'status.json').exists():
        for mode in ('cir', 'cores-only'):
            p = BASE / f'{mode}_status.json'
            if p.exists() and json.loads(p.read_text())['status'] == 'failed':
                raise RuntimeError(f'{mode} prerequisite failed')
        time.sleep(30)
    assert json.loads((BASE / 'status.json').read_text())['status'] == 'complete'
    snap = BASE / 'snapshot'
    hashes = json.loads((BASE / 'manifest.json').read_text())['sources']
    binary = BASE / 'cores-only/2048/build/simv'
    hashes[str(binary)] = hashlib.sha256(binary.read_bytes()).hexdigest()
    save(OUT / 'manifest.json', {'sources': hashes, 'spins': 2048,
         'geometry': [2, 1, 2, 16], 'stall_periods': [7, 13],
         'scope': 'cores-only two-H1 timing and event differential; not arithmetic'})
    def verify():
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                   for p, h in hashes.items())
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               VCS_HOME='/home/hwpeng/vcs', VCS_ARCH_OVERRIDE='linux',
               VCS_TARGET_ARCH='linux64', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    for stall in (7, 13):
        verify()
        case = OUT / f'stall_{stall}'
        case.mkdir()
        save(OUT / 'status.json', {'status': 'running', 'stall_period': stall})
        dataset = snap / 'tb/datasets/dense_occupancy_2048.txt'
        commands = [('rtl', [str(binary), f'+DATASET={dataset}', f'+STALL_PERIOD={stall}']),
            ('differential', ['/home/rsappidi/miniforge3/envs/azilla/bin/python3',
              'scripts/check_shared_fetch_two_h1.py', '--dataset', str(dataset),
              '--log', str(case / 'rtl.log'), '--output', str(case / 'differential.json'),
              '--h0', '2', '--cores', '16', '--stall', str(stall)])]
        for tag, cmd in commands:
            save(case / f'{tag}.command.json', cmd)
            with (case / f'{tag}.log').open('x') as log:
                subprocess.run(['/usr/bin/time', '-v', '-o', str(case / f'{tag}.time'), *cmd],
                               cwd=snap, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        report = json.loads((case / 'differential.json').read_text())
        assert report['status'] == 'pass' and all(report['checks'].values())
        verify()
    save(OUT / 'status.json', {'status': 'complete', 'passed_stall_periods': [7, 13]})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if OUT.exists():
            save(OUT / 'status.json', {'status': 'failed', 'error': repr(exc)})
        raise
