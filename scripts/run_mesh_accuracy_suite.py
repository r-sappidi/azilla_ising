#!/usr/bin/env python3
"""Run isolated, fail-closed VCS/exact-model timing validation cases.

Each child freezes the campaign model and copies current verification fixtures.
Failures remain failures; independent cases continue, with no automatic repair.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    cases = [
        ('owner_zero_regression', 2, 2, 1024, 'dense', 'zero', 0, 1),
        ('distributed_dense', 2, 2, 1024, 'dense', 'distributed', 0, 1),
        ('distributed_sparse_s1', 2, 2, 1024, 'sparse', 'distributed', 0, 1),
        ('distributed_sparse_s2', 2, 2, 1024, 'sparse', 'distributed', 0, 2),
        ('distributed_dense_stall7', 2, 2, 1024, 'dense', 'distributed', 7, 1),
        ('distributed_dense_stall13', 2, 2, 1024, 'dense', 'distributed', 13, 1),
        ('rectangular_sparse', 3, 2, 1536, 'sparse', 'distributed', 0, 3),
        ('larger_dense', 2, 2, 2048, 'dense', 'distributed', 0, 1),
    ]
    state = dict(status='running', scope='small multi-H1 timing only; 4/2/4 engines; baseline memory; one iteration',
                 excludes=['arithmetic', 'multi-iteration', 'full nominal multi-H1 geometry',
                           'memory-width sensitivity', 'physical link projections'], cases=[])

    def save():
        state['updated_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tmp = out / 'status.tmp'
        tmp.write_text(json.dumps(state, indent=2) + '\n')
        tmp.replace(out / 'status.json')

    for name, mx, my, size, workload, owner, stall, seed in cases:
        command = [sys.executable, str(ROOT / 'scripts/queue_dense_multi_h1_vcs.py'),
                   '--output', str(out / name), '--mesh-x', str(mx), '--mesh-y', str(my),
                   '--sizes', str(size), '--workload', workload, '--owner-policy', owner,
                   '--core-stall', str(stall), '--seed', str(seed)]
        state['cases'].append(dict(name=name, status='queued', command=command))
    save()
    for case in state['cases']:
        case['status'] = 'running'
        save()
        with (out / (case['name'] + '.log')).open('x') as log:
            try:
                result = subprocess.run(case['command'], cwd=ROOT, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=6*3600,
                                        env=dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1'))
                case['returncode'] = result.returncode
                case['status'] = 'pass' if result.returncode == 0 else 'fail'
            except subprocess.TimeoutExpired:
                case['status'] = 'timeout'
                # Do not overlap subsequent work with potentially surviving descendants.
                state['status'] = 'blocked-timeout'
                save()
                return 1
        save()
    state['status'] = 'pass' if all(c['status'] == 'pass' for c in state['cases']) else 'failed-cases'
    save()
    return 0 if state['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
