#!/usr/bin/env python3
"""Frozen-schedule held-out quality coverage; separate from timing evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from run_provisional_exact_matrix import ROOT, digest, write


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    snapshot = out / 'snapshot'
    snapshot.mkdir()
    runner = snapshot / 'run_reference_solution_quality.py'
    shutil.copy2(ROOT / 'scripts/run_reference_solution_quality.py', runner)
    winner = json.loads((ROOT / 'results/cir_evaluation/solution_quality/schedule_sweep_20260904/winner.json').read_text())
    schedule = snapshot / 'schedule.csv'
    shutil.copy2(ROOT / winner['schedule'], schedule)
    hashes = {str(p): digest(p) for p in (runner, schedule)}
    for p in (runner, schedule): p.chmod(0o444)
    published = json.loads((ROOT / 'results/paper_benchmarks/datasets/manifest.json').read_text())['datasets']
    canonical = json.loads((ROOT / 'results/paper_canonical/datasets/manifest.json').read_text())['datasets']
    groups = [
        [d for d in published if d['family'] == 'published-gset'],
        [d for d in canonical if d['capacity_spins'] in (65536, 131072, 262144)
         and d['seed'] in (None, 1) and d['family'] in
         ('canonical-toroidal-d4', 'canonical-uniform-d16', 'canonical-community-d16')]]
    if not groups[0] or len(groups[1]) != 9:
        raise RuntimeError('unexpected dataset coverage')
    write(out / 'campaign.json', dict(status='algorithmic-reference-only',
        schedule_selection='frozen earlier using planted 16K training; no retuning here',
        schedule=winner, files=hashes, datasets=groups,
        published_solver_seeds=list(range(30)), large_solver_seeds=list(range(10)),
        objective='signed weighted MaxCut; J=-original graph weights',
        known_optimum=False, note='No optimality percentages without verified bounds; '
        'solver seed changes both initial spins and noise stream, as in prior reference.'))
    shutil.copy2(Path(__file__), out / 'runner_source.py')

    def run_group(index):
        seeds = 30 if index == 0 else 10
        for d in groups[index]:
            point = out / d['name']
            point.mkdir()
            try:
                source = ROOT / d['dataset']
                if digest(source) != d['sha256']:
                    raise RuntimeError('source dataset hash mismatch')
                # Keep topology/vertex ordering, explicitly encode MaxCut sign.
                dataset = point / 'maxcut_couplings.txt'
                with source.open() as inp, dataset.open('x') as dest:
                    spins, _ = map(int, inp.readline().split())
                    dest.write(f'{spins} 0\n')
                    for line in inp:
                        if not line.strip(): continue
                        a, b, w = map(int, line.split())
                        if not -128 <= -w <= 127:
                            raise ValueError('negated coupling exceeds int8')
                        dest.write(f'{a} {b} {-w}\n')
                entry = dict(d, dataset=str(dataset), generated=True,
                    cut_weight_mode='negated', original_sha256=d['sha256'],
                    sha256=digest(dataset), known_maxcut=None)
                manifest = point / 'manifest.json'
                write(manifest, dict(datasets=[entry]))
                cmd = [sys.executable, str(runner), '--manifest', str(manifest),
                    '--output-dir', str(point / 'trials'), '--schedule', str(schedule),
                    '--seeds', ','.join(map(str, range(seeds)))]
                write(point / 'command.json', cmd)
                print(f'START {d["name"]} seeds={seeds}', flush=True)
                env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                           PYTHONDONTWRITEBYTECODE='1')
                with (point / 'run.log').open('x') as log:
                    subprocess.run(['/usr/bin/time', '-v', '-o', str(point / 'wall.time'),
                        *cmd], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                rows = list(csv.DictReader((point / 'trials/summary.csv').open()))
                assert len(rows) == seeds
                for row in rows:
                    path = point / 'trials' / d['name'] / f'seed_{row["seed"]}' / 'cut_trajectory.csv'
                    trajectory = list(csv.DictReader(path.open()))
                    assert len(trajectory) == 1001
                    assert int(row['best_cut']) == max(int(t['cut']) for t in trajectory)
                    assert int(row['known_cut']) == 0
                assert digest(source) == d['sha256']
                assert digest(dataset) == entry['sha256']
                assert all(digest(Path(p)) == h for p, h in hashes.items())
                write(point / 'audit.json', dict(status='trajectory-consistency-pass',
                    accuracy_scope='algorithmic-reference-only', known_optimum=False,
                    seeds=seeds, iterations=1000))
                print(f'COLLECTED {d["name"]}', flush=True)
            except Exception as error:
                write(point / 'failure.json', dict(error=repr(error)))
                raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_group, i) for i in range(2)]
        for future in futures: future.result()
    write(out / 'complete.json', dict(status='collected', rtl_verification=False))


if __name__ == '__main__':
    main()
