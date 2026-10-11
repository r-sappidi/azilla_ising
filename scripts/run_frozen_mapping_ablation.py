#!/usr/bin/env python3
"""Lossless user compression_mapping ablation, frozen exact-event models only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()

def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--source', type=Path, default=ROOT / 'results/paper_validation_20260907/provisional_exact_matrix_v1')
    args = ap.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve()
    base = json.loads((source / 'manifest.json').read_text())
    snapshot = source / 'snapshot'
    bundle = out / 'compression_mapping'
    shutil.copytree(ROOT / 'compression_mapping', bundle,
                    ignore=shutil.ignore_patterns('__pycache__', '.git', '*.pyc'))
    hashes = dict(base['files'])
    for path in bundle.rglob('*'):
        if path.is_file():
            hashes[str(path)] = sha(path)
            path.chmod(0o444)
    for path in (ROOT / 'third_party/ramulator2').glob('*.so*'):
        if path.is_file():
            hashes[str(path.resolve())] = sha(path)
    sys.path[:0] = [str(bundle), str(snapshot / 'model')]
    import numpy as np
    import torch
    import azilla_integration.pipeline as user_pipeline
    from azilla_cycle_model.mapping_adapter import artifact_from_mapping, load_sparse_dataset_arrays, write_permuted_dataset
    from azilla_cycle_model.workload import Geometry
    assert Path(user_pipeline.__file__).resolve().is_relative_to(bundle)
    torch.set_num_threads(1)
    datasets = [d for d in base['datasets'] if d.get('seed') in (None, 1)]
    datasets.sort(key=lambda d: (d['capacity_spins'], 'toroidal' not in d['name'], d['name']))
    save(out / 'manifest.json', dict(status='provisional-pending-validation-review',
        source=str(source), files=hashes, datasets=datasets,
        policies=['identity', 'random', 'compression_mapping_lossless'],
        modes=['cores-only', 'cir'], seed=1, workers=1,
        note='Same endpoint-local owner policy for all mappings; no lossy pruning. Mapping quality separated from owner scheduling. 64K has one H1, hence no cross-H1 traffic.',
        h0_mvms=4, h1_mvms=2, cross_mvms=4))
    shutil.copy2(__file__, out / 'runner_source.py')
    def check():
        for path, value in hashes.items():
            if sha(path) != value:
                raise RuntimeError('frozen dependency changed: ' + path)
    for d in datasets:
        check()
        dataset = ROOT / d['dataset']
        assert sha(dataset) == d['sha256']
        n, cut, src, dst, weights = load_sparse_dataset_arrays(dataset)
        g = Geometry(**d['geometry'])
        config = user_pipeline.CompileConfig(mesh_x=g.mesh_x, mesh_y=g.mesh_y,
            blocks_per_node=g.h0_per_h1*g.cores_per_h0, lossy_weight_budget=0.0, seed=1)
        for policy in ('identity', 'random', 'compression_mapping_lossless'):
            directory = out / d['name'] / policy
            directory.mkdir(parents=True)
            start = time.monotonic()
            print('MAP', d['name'], policy, flush=True)
            if policy == 'compression_mapping_lossless':
                # Canonical files are symmetric. Supply each undirected edge once.
                keep = src <= dst
                mapping = user_pipeline.compile_mapping(torch.from_numpy(src[keep]),
                    torch.from_numpy(dst[keep]), torch.from_numpy(weights[keep]), n, config)
                assert mapping.dropped_blocks == 0 and mapping.dropped_weight_fraction == 0
                perm, inv, pairs = mapping.permutation, mapping.inverse_permutation, mapping.block_pairs
            else:
                perm = np.arange(n) if policy == 'identity' else np.random.default_rng(1).permutation(n)
                inv = np.empty_like(perm); inv[perm] = np.arange(n)
                a, b = np.minimum(inv[src], inv[dst])//32, np.maximum(inv[src], inv[dst])//32
                ids = np.unique(a[a != b]*g.total_blocks + b[a != b])
                pairs = np.column_stack((ids//g.total_blocks, ids%g.total_blocks))
            # Independently recover the exact occupied block set after permutation.
            a, b = np.minimum(inv[src], inv[dst])//32, np.maximum(inv[src], inv[dst])//32
            expected = np.unique(a[a != b]*g.total_blocks + b[a != b])
            assert np.array_equal(np.sort(pairs[:,0]*g.total_blocks+pairs[:,1]), expected)
            artifact = artifact_from_mapping(g, perm, inv, pairs, known_cut=cut,
                owner_assigner=lambda geom, coords: user_pipeline.endpoint_owners(coords, config),
                metadata=dict(policy=policy, seed=1, source_sha256=d['sha256'], lossless=True,
                              owner_policy='endpoint-local-common-control'))
            artifact.save(directory)
            write_permuted_dataset(dataset, directory/'dataset.txt', artifact)
            _, mapped_cut, ms, md, mw = load_sparse_dataset_arrays(directory/'dataset.txt')
            assert mapped_cut == cut and np.array_equal(ms, inv[src]) and np.array_equal(md, inv[dst]) and np.array_equal(mw, weights)
            inputs = {str(p): sha(p) for p in directory.iterdir() if p.is_file()}
            save(directory/'mapping_audit.json', dict(lossless=True, exact_block_set=True,
                inverse_permutation_edges_verified=True, wall_seconds=time.monotonic()-start,
                files=inputs, occupied_blocks=len(pairs)))
            for mode in ('cores-only', 'cir'):
                check()
                point = directory / mode; point.mkdir()
                command = [sys.executable, '-m', 'azilla_cycle_model.cli',
                    'simulate-mapped-exact-events', '--artifact', str(directory),
                    '--dataset', str(directory/'dataset.txt'), '--h0-mvms','4','--h1-mvms','2',
                    '--cross-mvms','4','--execution-mode',mode,'--ramulator-library',
                    str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                    '--ramulator-config', str(snapshot/'ramulator_128x32.yaml'),
                    '--metrics-prefix',str(point/'metrics')]
                save(point/'command.json', command)
                print('START', d['name'], policy, mode, flush=True)
                env = dict(os.environ, PYTHONPATH=str(snapshot/'model'), PYTHONDONTWRITEBYTECODE='1',
                           OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
                try:
                    with (point/'run.log').open('x') as log:
                        subprocess.run(['/usr/bin/time','-v','-o',str(point/'wall.time'),*command],
                            cwd=snapshot, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                    data = json.loads((point/'metrics_summary.json').read_text())
                    assert data['accuracy'] == 'cycle-structured-unverified' and data['execution_mode'] == mode
                    assert data['noc']['injected_flits'] == data['noc']['ejected_flits']
                    assert data['timing_cycles']['iteration'] > 0
                    for dram in data['dram_systems']:
                        assert dram['accepted_requests'] == dram['completed_requests'] and dram['outstanding_requests'] == 0
                    if mode == 'cores-only':
                        assert data['cores_only_ablation']['pipeline_contract']['model'] == 'full_single_mvm_iteration_v1'
                    check()
                    assert all(sha(p) == value for p,value in inputs.items())
                    save(point/'audit.json',dict(status='provisional-conservation-pass', paper_ready=False,
                        source_and_artifact_hashes_unchanged=True, geometry_rtl_verified=False))
                    print('COLLECTED',d['name'],policy,mode,flush=True)
                except Exception as error:
                    save(point/'failure.json',dict(error=repr(error),paper_ready=False))
                    raise
    save(out/'collection_complete.json',dict(points=len(datasets)*6,paper_ready=False))

if __name__ == '__main__':
    main()
