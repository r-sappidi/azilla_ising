#!/usr/bin/env python3
"""Collect a frozen, explicitly provisional exact-event comparison matrix.

Collection never promotes validation status. Large geometries remain projections.
Each mode stops its queue at the first failed run or conservation check.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    snapshot = out / 'snapshot'
    hashes = {}
    for source in sorted((ROOT / 'model/azilla_cycle_model').glob('*.py')):
        target = snapshot / 'model/azilla_cycle_model' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        before = digest(source)
        shutil.copy2(source, target)
        if digest(target) != before or digest(source) != before:
            raise RuntimeError('source changed while snapshotting')
        target.chmod(0o444)
        hashes[str(target)] = before
    manifest_path = ROOT / 'results/paper_canonical/datasets/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    selected = [d for d in manifest['datasets']
                if d['capacity_spins'] in (65536, 131072, 262144)
                and d['family'] in ('canonical-toroidal-d4',
                                    'canonical-uniform-d16', 'canonical-community-d16')]
    if len(selected) != 21:
        raise RuntimeError(f'expected 21 datasets, found {len(selected)}')
    selected.sort(key=lambda d: (d['capacity_spins'],
        0 if 'toroidal' in d['name'] else 1 if 'community' in d['name'] else 2, d['name']))
    library = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
    config = ROOT / 'tb/ramulator_128x32.yaml'
    hashes[str(library)] = digest(library)
    config_copy = snapshot / config.name
    shutil.copy2(config, config_copy)
    hashes[str(config_copy)] = digest(config_copy)
    shutil.copy2(Path(__file__), out / 'runner_source.py')
    write(out / 'manifest.json', dict(status='provisional-pending-validation-review',
        created_utc=datetime.now(timezone.utc).isoformat(),
        datasets=selected, files=hashes, modes=['cores-only', 'cir'],
        mapping='identity', h0_mvms=4, h1_mvms=2, cross_mvms=4,
        dataset_manifest_sha256=digest(manifest_path),
        note='Not RTL certification; no automatic paper-ready promotion.'))

    def run_mode(mode):
        for d in selected:
            point = out / d['name'] / mode
            point.mkdir(parents=True, exist_ok=False)
            try:
                for name, sha in hashes.items():
                    if digest(Path(name)) != sha:
                        raise RuntimeError(f'frozen input changed: {name}')
                dataset = ROOT / d['dataset']
                if digest(dataset) != d['sha256']:
                    raise RuntimeError('dataset differs from canonical manifest')
                command = [sys.executable, '-m', 'azilla_cycle_model.cli',
                    'simulate-exact-events', '--dataset', str(dataset),
                    '--h0-mvms', '4', '--h1-mvms', '2', '--cross-mvms', '4',
                    '--execution-mode', mode, '--ramulator-library', str(library),
                    '--ramulator-config', str(config_copy),
                    '--metrics-prefix', str(point / 'metrics')]
                for key, value in d['geometry'].items():
                    command.extend(['--' + key.replace('_', '-'), str(value)])
                write(point / 'command.json', command)
                print(f'START {d["name"]} {mode}', flush=True)
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
                assert digest(dataset) == d['sha256']
                assert all(digest(Path(n)) == sha for n, sha in hashes.items())
                write(point / 'audit.json', dict(status='provisional-conservation-pass',
                    paper_ready=False, geometry_rtl_verified=False))
                print(f'COLLECTED {d["name"]} {mode}', flush=True)
            except Exception as error:
                write(point / 'failure.json', dict(error=repr(error), paper_ready=False))
                raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_mode, mode) for mode in ('cores-only', 'cir')]
        for future in futures:
            future.result()
    write(out / 'collection_complete.json', dict(points=42, paper_ready=False))


if __name__ == '__main__':
    main()
