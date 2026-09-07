#!/usr/bin/env python3
"""Fail-closed, resumable continuation into a new immutable exact-model snapshot.

Never signals old launchers/children or writes their results. Requires a separate
validated gate and an inventory captured after launcher quiescence.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(os.environ.get('AZILLA_REPOSITORY_ROOT', Path(__file__).resolve().parents[1])).resolve()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def save(path, value):
    """Atomic generated metadata; callers distinguish immutable/updated files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.tmp.{os.getpid()}')
    with temp.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')
    os.replace(temp, path)


def signature(point):
    return dict(mode=point['mode'], geometry=point['geometry'],
                engines=point['engines'],
                ramulator_config_sha256=point['ramulator_config_sha256'])


def minimum_matrix_points(points):
    """Select the preregistered 12 cells, independent of measured outcomes."""
    expected = {f'{family}_n{size}{suffix}/{mode}'
                for family, suffix in (('toroidal_d4', ''), ('community_d16', '_s1'), ('uniform_d16', '_s1'))
                for size in (65536, 131072) for mode in ('cir', 'cores-only')}
    selected = [entry for entry in points if entry[0] == 'provisional_exact_matrix_v1'
                and entry[3]['relative'] in expected]
    if len(selected) != 12 or {e[3]['relative'] for e in selected} != expected:
        raise RuntimeError('minimum matrix inventory must contain exactly all twelve cells')
    for _, _, _, point in selected:
        g = point['geometry']
        if point['engines'] != [4, 2, 4] or g['cores_per_h0'] != 256 or g['h0_per_h1'] != 8:
            raise RuntimeError('minimum matrix provisioning mismatch')
        size = 65536 if '_n65536' in point['relative'] else 131072
        if (g['mesh_x'], g['mesh_y']) != ((1, 1) if size == 65536 else (2, 1)):
            raise RuntimeError('minimum matrix mesh mismatch')
    return selected


def signature_key(value):
    return json.dumps({k: value[k] for k in
                       ('mode', 'geometry', 'engines', 'ramulator_config_sha256')},
                      sort_keys=True)


def verify_gate(path):
    gate = json.loads(Path(path).read_text())
    if gate.get('status') != 'pass' or not gate.get('evidence'):
        raise RuntimeError('missing passing validation evidence')
    for label in ('baseline', 'optimized'):
        snapshot = Path(gate[label + '_snapshot']).resolve()
        files = gate[label + '_files']
        if not files:
            raise RuntimeError('empty snapshot hash inventory')
        for relative, expected in files.items():
            candidate = (snapshot / relative).resolve()
            candidate.relative_to(snapshot)
            if digest(candidate) != expected:
                raise RuntimeError('validation source hash changed: ' + str(candidate))
        actual = {str(p.relative_to(snapshot)) for p in (snapshot / 'model/azilla_cycle_model').glob('*.py')}
        recorded = {p for p in files if p.startswith('model/azilla_cycle_model/') and p.endswith('.py')}
        if actual != recorded:
            raise RuntimeError('snapshot Python inventory differs from gate')
    for entry in gate['evidence']:
        if digest(entry['path']) != entry['sha256']:
            raise RuntimeError('validation evidence hash changed')
    if not gate.get('covered_signatures'):
        raise RuntimeError('empty validated configuration envelope')
    return gate


def live_prefixes():
    found = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
            if any(m in argv for m in ('azilla_cycle_model.cli', 'azilla_cycle_model.fast_cli')) and '--metrics-prefix' in argv:
                prefix = argv[argv.index('--metrics-prefix') + 1]
                found.setdefault(prefix, []).append(int(proc.name))
        except (OSError, UnicodeError, ValueError):
            continue
    return found


def verify_launcher_quiescence(handoff):
    for launcher in handoff['launchers']:
        proc = Path('/proc') / str(launcher['pid'])
        if proc.exists():
            stat = proc.joinpath('stat').read_text().split()
            if stat[21] == str(launcher['start_ticks']) and stat[2] not in ('T', 't', 'Z'):
                raise RuntimeError('old launcher is not quiescent')


def verify_dependencies(manifest, point):
    for name, sha in manifest['files'].items():
        if digest(name) != sha:
            raise RuntimeError('original dependency changed: ' + name)
    if digest(ROOT / point['dataset']) != point['dataset_sha256']:
        raise RuntimeError('canonical dataset hash changed')
    if digest(point['ramulator_config']) != point['ramulator_config_sha256']:
        raise RuntimeError('memory config hash changed')


def audit_metrics(directory, mode, point=None):
    summary = directory / 'metrics_summary.json'
    data = json.loads(summary.read_text())
    assert data['execution_mode'] == mode
    assert data['accuracy'] == 'cycle-structured-unverified'
    assert data['timing_cycles']['iteration'] > 0
    assert data['noc']['injected_flits'] == data['noc']['ejected_flits']
    if point:
        assert all(data['geometry'][k] == v for k, v in point['geometry'].items())
        assert data['geometry']['spins_per_core'] == 32
    for dram in data['dram_systems']:
        assert dram['accepted_requests'] == dram['completed_requests']
        assert dram['outstanding_requests'] == 0
        assert dram['accepted_requests'] == dram['scheduled_jobs'] * 32
        if point:
            assert dram['engines'] == dict(zip(('h0','h1','cross'),point['engines']))[dram['level']]
    if mode == 'cores-only':
        assert data['cores_only_ablation']['pipeline_contract']['model'] == 'full_single_mvm_iteration_v1'
    required = [summary, directory / 'metrics_noc.csv', directory / 'metrics_nodes.csv',
                directory / 'metrics_dram.csv', directory / 'command.json', directory / 'wall.time']
    if 'Exit status: 0' not in (directory / 'wall.time').read_text():
        raise RuntimeError('missing successful timed-process exit status')
    return {str(p): digest(p) for p in required}


def ensure_mapping(original, output, point, baseline_snapshot):
    """Reuse a checked artifact, or run exactly the frozen lossless mapping recipe."""
    old = original / point['relative'].rsplit('/', 1)[0]
    if (old / 'mapping_audit.json').exists():
        audit = json.loads((old / 'mapping_audit.json').read_text())
        assert audit['lossless'] and audit['exact_block_set'] and audit['inverse_permutation_edges_verified']
        assert all(digest(p) == sha for p, sha in audit['files'].items())
        return old
    directory = output / 'artifacts' / point['relative'].rsplit('/', 1)[0]
    if (directory / 'mapping_audit.json').exists():
        audit = json.loads((directory / 'mapping_audit.json').read_text())
        assert all(digest(p) == sha for p, sha in audit['files'].items())
        return directory
    if directory.exists():
        raise RuntimeError('partial mapping artifact needs manual review')
    directory.mkdir(parents=True)
    bundle = original / 'compression_mapping'
    sys.path[:0] = [str(bundle), str(baseline_snapshot / 'model')]
    import numpy as np
    import torch
    import azilla_integration.pipeline as mapper
    from azilla_cycle_model.mapping_adapter import artifact_from_mapping, load_sparse_dataset_arrays, write_permuted_dataset
    from azilla_cycle_model.workload import Geometry
    assert Path(mapper.__file__).resolve().is_relative_to(bundle.resolve())
    torch.set_num_threads(1)
    dataset = ROOT / point['dataset']
    n, cut, src, dst, weights = load_sparse_dataset_arrays(dataset)
    geometry = Geometry(**point['geometry'])
    cfg = mapper.CompileConfig(mesh_x=geometry.mesh_x, mesh_y=geometry.mesh_y,
        blocks_per_node=geometry.h0_per_h1 * geometry.cores_per_h0,
        lossy_weight_budget=0.0, seed=1)
    policy = point['relative'].split('/')[-2]
    start = time.monotonic()
    if policy == 'compression_mapping_lossless':
        keep = src <= dst
        mapped = mapper.compile_mapping(torch.from_numpy(src[keep]), torch.from_numpy(dst[keep]),
                                        torch.from_numpy(weights[keep]), n, cfg)
        assert mapped.dropped_blocks == 0 and mapped.dropped_weight_fraction == 0
        perm, inv, pairs = mapped.permutation, mapped.inverse_permutation, mapped.block_pairs
    else:
        assert policy in ('identity', 'random')
        perm = np.arange(n) if policy == 'identity' else np.random.default_rng(1).permutation(n)
        inv = np.empty_like(perm); inv[perm] = np.arange(n)
        a, b = np.minimum(inv[src], inv[dst]) // 32, np.maximum(inv[src], inv[dst]) // 32
        ids = np.unique(a[a != b] * geometry.total_blocks + b[a != b])
        pairs = np.column_stack((ids // geometry.total_blocks, ids % geometry.total_blocks))
    a, b = np.minimum(inv[src], inv[dst]) // 32, np.maximum(inv[src], inv[dst]) // 32
    expected = np.unique(a[a != b] * geometry.total_blocks + b[a != b])
    assert np.array_equal(np.sort(pairs[:, 0] * geometry.total_blocks + pairs[:, 1]), expected)
    artifact = artifact_from_mapping(geometry, perm, inv, pairs, known_cut=cut,
        owner_assigner=lambda geom, coords: mapper.endpoint_owners(coords, cfg),
        metadata=dict(policy=policy, seed=1, source_sha256=point['dataset_sha256'],
                      lossless=True, owner_policy='endpoint-local-common-control'))
    artifact.save(directory)
    write_permuted_dataset(dataset, directory / 'dataset.txt', artifact)
    _, mc, ms, md, mw = load_sparse_dataset_arrays(directory / 'dataset.txt')
    assert mc == cut and np.array_equal(ms, inv[src]) and np.array_equal(md, inv[dst]) and np.array_equal(mw, weights)
    hashes = {str(p): digest(p) for p in directory.iterdir() if p.is_file()}
    save(directory / 'mapping_audit.json', dict(lossless=True, exact_block_set=True,
         inverse_permutation_edges_verified=True, files=hashes,
         occupied_blocks=len(pairs), wall_seconds=time.monotonic() - start))
    return directory


def main():
    if not __debug__:
        raise RuntimeError('do not disable assertion-based conservation checks')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inventory', type=Path, required=True)
    ap.add_argument('--gate', type=Path, required=True)
    ap.add_argument('--handoff', type=Path, required=True,
                    help='Root-issued JSON confirming old launcher quiescence')
    ap.add_argument('--original-root', type=Path, default=ROOT / 'results/paper_validation_20260907')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--worker-index', type=int, default=0)
    ap.add_argument('--worker-count', type=int, default=1)
    ap.add_argument('--poll-seconds', type=int, default=30)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--minimum-matrix-only', action='store_true')
    args = ap.parse_args()
    assert 0 <= args.worker_index < args.worker_count and 1 <= args.poll_seconds <= 60
    gate = verify_gate(args.gate)
    inventory = json.loads(args.inventory.read_text())
    handoff = json.loads(args.handoff.read_text())
    if handoff.get('status') != 'launchers-quiescent' or handoff.get('inventory_sha256') != digest(args.inventory):
        raise RuntimeError('handoff does not authorize this exact inventory')
    if handoff.get('gate_sha256') != digest(args.gate):
        raise RuntimeError('handoff gate mismatch')
    verify_launcher_quiescence(handoff)
    covered = {signature_key(s) for s in gate['covered_signatures']}
    points = []
    for campaign in inventory['campaigns']:
        original = args.original_root.resolve() / campaign['campaign']
        if digest(original / 'manifest.json') != campaign['manifest_sha256']:
            raise RuntimeError('original campaign manifest changed')
        manifest = json.loads((original / 'manifest.json').read_text())
        oracle = Path(gate['baseline_snapshot']).resolve()
        for source, expected in manifest['files'].items():
            if '/model/azilla_cycle_model/' in source and source.endswith('.py'):
                counterpart = oracle / 'model/azilla_cycle_model' / Path(source).name
                if digest(counterpart) != expected:
                    raise RuntimeError('gate oracle differs from original campaign model')
        for point in campaign['points']:
            points.append((campaign['campaign'], original, manifest, point))
    if args.minimum_matrix_only:
        points = minimum_matrix_points(points)
    assigned = points[args.worker_index::args.worker_count]
    if args.dry_run:
        missing=[p for _,_,_,p in assigned if p['status']=='unstarted' and signature_key(p) not in covered]
        if missing:
            raise RuntimeError(f'{len(missing)} unstarted points outside validated envelope')
        print(json.dumps(dict(assigned=len(assigned), eligible=sum(signature_key(p) in covered for _, _, _, p in assigned)), indent=2))
        return
    output = args.output.resolve(); output.relative_to(ROOT / 'results')
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / f'worker{args.worker_index}.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run_identity = dict(inventory_sha256=digest(args.inventory), gate_sha256=digest(args.gate),
                        handoff_sha256=digest(args.handoff), worker_count=args.worker_count,
                        runner_sha256=digest(__file__), minimum_matrix_only=args.minimum_matrix_only)
    identity_path = output / 'continuation_identity.json'
    if identity_path.exists():
        assert json.loads(identity_path.read_text()) == run_identity
    else:
        save(identity_path, run_identity)
    def record_audit(path, value):
        save(path, value)
        merged = []
        for campaign_name, _, _, item in assigned:
            audit = output / campaign_name / item['relative'] / 'audit.json'
            if audit.exists():
                merged.append(dict(campaign=campaign_name, point=item['relative'],
                                   audit=str(audit), **json.loads(audit.read_text())))
        save(output / f'merged_provenance_worker{args.worker_index}.json', merged)
    pending = assigned[:]
    while pending:
        deferred = []
        for name, original, manifest, point in pending:
            destination = output / name / point['relative']
            old = original / point['relative']
            if (destination / 'audit.json').exists():
                prior = json.loads((destination / 'audit.json').read_text())
                assert all(digest(p) == sha for p, sha in prior['files'].items())
                continue
            if str(old / 'metrics') in live_prefixes():
                deferred.append((name, original, manifest, point)); continue
            verify_gate(args.gate)
            verify_launcher_quiescence(handoff)
            verify_dependencies(manifest, point)
            if (old / 'failure.json').exists():
                raise RuntimeError('original failed point requires review: ' + str(old))
            if (old / 'metrics_summary.json').exists():
                if name == 'mapping_ablation_exact_v1':
                    ma = json.loads((old.parent / 'mapping_audit.json').read_text())
                    assert ma['lossless'] and ma['exact_block_set'] and ma['inverse_permutation_edges_verified']
                    assert all(digest(p) == sha for p, sha in ma['files'].items())
                files = audit_metrics(old, point['mode'], point)
                record_audit(destination / 'audit.json', dict(status='adopted-provisional-conservation-pass',
                     paper_ready=False, geometry_rtl_verified=False, source='frozen-original',
                     original=str(old), files=files, signature=signature(point)))
                print('ADOPT', name, point['relative'], flush=True); continue
            if (old / 'command.json').exists():
                raise RuntimeError('old partial point requires review: ' + str(old))
            if signature_key(point) not in covered:
                raise RuntimeError('configuration outside validated envelope: ' + str(signature(point)))
            if (destination / 'command.json').exists():
                if str(destination / 'metrics') in live_prefixes():
                    deferred.append((name, original, manifest, point)); continue
                if (destination / 'metrics_summary.json').exists():
                    files = audit_metrics(destination, point['mode'], point)
                    record_audit(destination / 'audit.json', dict(status='optimized-provisional-conservation-pass',
                         paper_ready=False, geometry_rtl_verified=False, files=files,
                         gate_sha256=digest(args.gate), signature=signature(point)))
                    continue
                raise RuntimeError('optimized partial point requires review: ' + str(destination))
            destination.mkdir(parents=True, exist_ok=True)
            dataset = ROOT / point['dataset']
            mapped = name == 'mapping_ablation_exact_v1'
            artifact = None
            if mapped:
                locks = output / 'mapping_locks'; locks.mkdir(exist_ok=True)
                lock_name = hashlib.sha256(point['relative'].rsplit('/', 1)[0].encode()).hexdigest()
                with (locks / lock_name).open('a') as mapping_lock:
                    fcntl.flock(mapping_lock, fcntl.LOCK_EX)
                    artifact = ensure_mapping(original, output / name, point, Path(gate['baseline_snapshot']))
            if mapped:
                dataset = artifact / 'dataset.txt'
            cmd = [args.python, '-m', 'azilla_cycle_model.fast_cli',
                   'simulate-mapped-exact-events' if mapped else 'simulate-exact-events',
                   '--dataset', str(dataset), '--execution-mode', point['mode'],
                   '--ramulator-library', str(ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'),
                   '--ramulator-config', point['ramulator_config'], '--metrics-prefix', str(destination / 'metrics')]
            if mapped:
                cmd += ['--artifact', str(artifact)]
            else:
                for key, val in point['geometry'].items():
                    cmd += ['--' + key.replace('_', '-'), str(val)]
            for level, count in zip(('h0', 'h1', 'cross'), point['engines']):
                cmd += [f'--{level}-mvms', str(count)]
            snapshot = Path(gate['optimized_snapshot'])
            env = dict(os.environ, PYTHONPATH=str(snapshot / 'model'), PYTHONDONTWRITEBYTECODE='1',
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            env.pop('PYTHONOPTIMIZE', None)
            save(destination / 'command.json', cmd)
            inputs = {str(dataset): digest(dataset)}
            if artifact:
                inputs.update(json.loads((artifact / 'mapping_audit.json').read_text())['files'])
            save(destination / 'provenance.json', dict(source='optimized', signature=signature(point),
                 source_snapshot=str(snapshot), gate_sha256=digest(args.gate), inputs=inputs,
                 original_campaign=str(original), environment={k:env[k] for k in ('PYTHONPATH','PYTHONDONTWRITEBYTECODE','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS')}, command=cmd))
            print('START', name, point['relative'], flush=True)
            try:
                while True:
                    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                                     if line.startswith('MemAvailable:'))
                    if available > 16 * 1024 * 1024:
                        break
                    time.sleep(args.poll_seconds)
                verify_gate(args.gate); verify_launcher_quiescence(handoff)
                with (destination / 'run.log').open('x') as log:
                    subprocess.run(['/usr/bin/time', '-v', '-o', str(destination / 'wall.time'), *cmd],
                                   cwd=snapshot, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                verify_gate(args.gate); verify_dependencies(manifest, point)
                assert all(digest(p) == sha for p, sha in inputs.items())
                files = audit_metrics(destination, point['mode'], point)
                record_audit(destination / 'audit.json', dict(status='optimized-provisional-conservation-pass',
                     paper_ready=False, geometry_rtl_verified=False, files=files,
                     gate_sha256=digest(args.gate), signature=signature(point)))
                print('COLLECTED', name, point['relative'], flush=True)
            except Exception as error:
                save(destination / 'failure.json', dict(error=repr(error), paper_ready=False))
                raise
        merged = []
        for name, _, _, point in assigned:
            audit = output / name / point['relative'] / 'audit.json'
            if audit.exists():
                merged.append(dict(campaign=name, point=point['relative'], audit=str(audit), **json.loads(audit.read_text())))
        save(output / f'merged_provenance_worker{args.worker_index}.json', merged)
        pending = deferred
        if pending:
            print('WAIT original/optimized active children', len(pending), flush=True)
            time.sleep(args.poll_seconds)
    save(output / f'worker{args.worker_index}_complete.json', dict(points=len(assigned), paper_ready=False))


if __name__ == '__main__':
    main()
