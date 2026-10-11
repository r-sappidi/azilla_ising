#!/usr/bin/env python3
"""One-factor provisioning sweep against the frozen 4/2/4 baseline campaign.

Seed-one screening only. No automatic validation or finalist promotion.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from run_provisional_exact_matrix import ROOT, digest, write


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    baseline = args.baseline.resolve()
    manifest = json.loads((baseline / 'manifest.json').read_text())
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    snapshot = baseline / 'snapshot'
    configs = [(4, 2, 8), (4, 2, 2), (4, 4, 4), (4, 1, 4),
               (8, 2, 4), (2, 2, 4)]
    datasets = [d for d in manifest['datasets'] if d['seed'] in (None, 1)]
    datasets.sort(key=lambda d: (
        {131072: 0, 65536: 1, 262144: 2}[d['capacity_spins']],
        0 if 'toroidal' in d['name'] else 1 if 'community' in d['name'] else 2))
    if len(datasets) != 9:
        raise RuntimeError('expected nine seed-one/fixed datasets')
    hashes = manifest['files']
    library = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
    config = snapshot / 'ramulator_128x32.yaml'
    write(out / 'manifest.json', dict(status='provisional-provisioning-screen',
        baseline=str(baseline), baseline_manifest_sha256=digest(baseline / 'manifest.json'),
        datasets=datasets, configs=configs, modes=['cores-only', 'cir'], points=108,
        files=hashes, baseline_config=[4, 2, 4],
        note='Reuse baseline points; exact events only; seed-one screen, not final matrix. '
             'In cores-only these counts provision hierarchy memory streamers, not extra core MVMs. '
             'No change to geometry, interface widths, or 32 spins/core.'))
    shutil.copy2(Path(__file__), out / 'runner_source.py')

    def verify():
        for name, sha in hashes.items():
            if digest(Path(name)) != sha:
                raise RuntimeError(f'frozen dependency changed: {name}')

    def run_mode(mode):
        for d in datasets:
            for engines in configs:
                point = out / d['name'] / ('e' + '-'.join(map(str, engines))) / mode
                point.mkdir(parents=True, exist_ok=False)
                try:
                    verify()
                    dataset = ROOT / d['dataset']
                    if digest(dataset) != d['sha256']:
                        raise RuntimeError('canonical dataset hash mismatch')
                    command = [sys.executable, '-m', 'azilla_cycle_model.cli',
                        'simulate-exact-events', '--dataset', str(dataset),
                        '--execution-mode', mode, '--ramulator-library', str(library),
                        '--ramulator-config', str(config),
                        '--metrics-prefix', str(point / 'metrics')]
                    for level, count in zip(('h0', 'h1', 'cross'), engines):
                        command.extend([f'--{level}-mvms', str(count)])
                    for key, value in d['geometry'].items():
                        command.extend(['--' + key.replace('_', '-'), str(value)])
                    write(point / 'command.json', command)
                    print(f'START {d["name"]} {engines} {mode}', flush=True)
                    env = dict(os.environ, PYTHONPATH=str(snapshot / 'model'),
                               PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1',
                               OPENBLAS_NUM_THREADS='1')
                    with (point / 'run.log').open('x') as log:
                        subprocess.run(['/usr/bin/time', '-v', '-o', str(point / 'wall.time'),
                            *command], cwd=snapshot, env=env, stdout=log,
                            stderr=subprocess.STDOUT, check=True)
                    data = json.loads((point / 'metrics_summary.json').read_text())
                    assert data['execution_mode'] == mode
                    assert data['accuracy'] == 'cycle-structured-unverified'
                    assert data['timing_cycles']['iteration'] > 0
                    assert data['noc']['injected_flits'] == data['noc']['ejected_flits']
                    for dram in data['dram_systems']:
                        assert dram['accepted_requests'] == dram['completed_requests']
                        assert dram['outstanding_requests'] == 0
                    if mode == 'cores-only':
                        assert data['cores_only_ablation']['pipeline_contract']['model'] == 'full_single_mvm_iteration_v1'
                    verify()
                    assert digest(dataset) == d['sha256']
                    write(point / 'audit.json', dict(status='provisional-conservation-pass',
                        paper_ready=False, geometry_rtl_verified=False))
                    print(f'COLLECTED {d["name"]} {engines} {mode}', flush=True)
                except Exception as error:
                    write(point / 'failure.json', dict(error=repr(error), paper_ready=False))
                    raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_mode, mode) for mode in ('cores-only', 'cir')]
        for future in futures:
            future.result()
    write(out / 'collection_complete.json', dict(points=108, paper_ready=False))


if __name__ == '__main__':
    main()
