#!/usr/bin/env python3
"""Gated 512K/1M exact-model extension; no new RTL certification is issued."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

from run_fast_exact_continuation import audit_metrics, digest, save, verify_gate

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'results/paper_validation_20260907'
OUT = ROOT / 'results/paper_deadline_20260907/scale_extension_v1'


def python_hashes(path):
    return {str(p.relative_to(path)): digest(p) for p in sorted(path.rglob('*.py'))}


def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'results/paper_canonical/datasets/manifest.json'
    rows = [r for r in json.loads(source.read_text())['datasets']
            if r['capacity_spins'] in (524288, 1048576) and r['seed'] in (None, 1)
            and r['family'] in ('canonical-toroidal-d4', 'canonical-community-d16', 'canonical-uniform-d16')]
    assert len(rows) == 6
    for row in rows:
        assert digest(ROOT / row['dataset']) == row['sha256']
    oracle = BASE / 'provisional_exact_matrix_v1/snapshot'
    optimized = BASE / 'fast_exact_candidate_v2'
    config = oracle / 'ramulator_128x32.yaml'
    library = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
    signatures = []
    for n in (524288, 1048576):
        geom = next(r['geometry'] for r in rows if r['capacity_spins'] == n)
        for mode in ('cir', 'cores-only'):
            signatures.append(dict(mode=mode, geometry=geom, engines=[4, 2, 4],
                ramulator_config=str(config), ramulator_config_sha256=digest(config)))
    checker = ROOT / 'scripts/check_fast_exact_windows.py'
    save(OUT / 'envelope.json', dict(oracle_snapshot=str(oracle), required_signatures=signatures,
        scope='Additional sampled optimization equivalence, not large RTL verification'))
    save(OUT / 'manifest.json', dict(status='prepared-gated-stretch', datasets=rows,
        oracle=str(oracle), optimized=str(optimized),
        oracle_hashes=python_hashes(oracle/'model'), optimized_hashes=python_hashes(optimized/'model'),
        source_manifest=str(source), source_manifest_sha256=digest(source),
        checker=str(checker), checker_sha256=digest(checker),
        library=str(library), library_sha256=digest(library), signatures=signatures,
        primary_gate=str(optimized/'approved_gate_refresh0_v2.json'),
        runner_sha256=digest(__file__), memory_limit_gib=20,
        deadline_utc='2026-09-10T11:59:00Z', paper_ready=False,
        note='12 paired-family/mode/size points, identity mapping, fixed H1; optional beyond the core 256K paper scope.'))
    print('PREPARED six canonical datasets, four validation signatures, twelve timing points', flush=True)


def sources(manifest):
    assert digest(__file__) == manifest['runner_sha256']
    assert digest(manifest['checker']) == manifest['checker_sha256']
    assert digest(manifest['library']) == manifest['library_sha256']
    for name in ('oracle', 'optimized'):
        assert python_hashes(Path(manifest[name])/'model') == manifest[name+'_hashes']


def validate(index):
    m = json.loads((OUT/'manifest.json').read_text()); sources(m)
    dest = OUT/f'validation_{index}'
    cmd = [sys.executable, m['checker'], '--envelope', str(OUT/'envelope.json'),
        '--optimized-snapshot', str(Path(m['optimized'])/'model'), '--indices', str(index),
        '--cycles', '2000', '--library', m['library'], '--output', str(dest)]
    with (OUT/f'validation_{index}.log').open('x') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    sources(m)
    summary = json.loads((dest/'summary.json').read_text())
    assert len(summary) == 1 and summary[0]['index'] == index
    assert summary[0]['status'] == 'pass' and all(summary[0]['checks'].values())
    assert summary[0]['signature'] == m['signatures'][index]
    checks = {str(p): digest(p) for p in dest.rglob('*') if p.is_file()}
    save(OUT/f'approved_{index}.json', dict(status='pass', files=checks,
        signature=m['signatures'][index], scope='Window equivalence only; not an RTL certificate'))
    print('PASS validation', index, flush=True)


def wait_gate(m, index):
    while True:
        primary = Path(m['primary_gate']); local = OUT/f'approved_{index}.json'
        if primary.exists() and local.exists():
            g = verify_gate(primary)
            assert g['baseline_snapshot'] == m['oracle'] and g['optimized_snapshot'] == m['optimized']
            approved = json.loads(local.read_text())
            assert approved['status'] == 'pass' and approved['signature'] == m['signatures'][index]
            assert all(digest(p) == sha for p, sha in approved['files'].items())
            return
        if (OUT/f'validation_{index}_failure.json').exists():
            raise RuntimeError('Required scale-window validation failed')
        status = BASE/'fast_handoff_supervisor_v3/supervisor_status.json'
        if status.exists() and json.loads(status.read_text())['status'] == 'blocked':
            raise RuntimeError('Primary validation/handoff is blocked; manual review required')
        time.sleep(60)


def campaign(family, mode):
    m = json.loads((OUT/'manifest.json').read_text())
    rows = sorted([r for r in m['datasets'] if r['name'].startswith(family+'_')],
                  key=lambda r:r['capacity_spins'])
    assert len(rows) == 2
    for row in rows:
        index = next(i for i,s in enumerate(m['signatures'])
                     if s['geometry'] == row['geometry'] and s['mode'] == mode)
        print('WAIT gated point', row['name'], mode, flush=True)
        wait_gate(m, index); sources(m)
        assert digest(ROOT/row['dataset']) == row['sha256']
        sig = m['signatures'][index]
        assert digest(sig['ramulator_config']) == sig['ramulator_config_sha256']
        dest = OUT/'timing'/row['name']/mode; dest.mkdir(parents=True, exist_ok=False)
        cmd = [sys.executable, '-m', 'azilla_cycle_model.fast_cli', 'simulate-exact-events',
            '--dataset', str(ROOT/row['dataset']), '--execution-mode', mode,
            '--ramulator-library', m['library'], '--ramulator-config', sig['ramulator_config'],
            '--idle-refresh-period-ticks', '0', '--metrics-prefix', str(dest/'metrics')]
        for key, value in row['geometry'].items(): cmd += ['--'+key.replace('_','-'), str(value)]
        for level, count in zip(('h0','h1','cross'), sig['engines']): cmd += ['--'+level+'-mvms', str(count)]
        save(dest/'command.json', cmd)
        save(dest/'provenance.json', dict(signature=sig, dataset_sha256=row['sha256'],
            primary_gate_sha256=digest(m['primary_gate']), window_gate_sha256=digest(OUT/f'approved_{index}.json'),
            snapshot=m['optimized'], paper_ready=False, identity_mapping=True))
        while True:
            avail = next(int(x.split()[1]) for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
            if avail > 24*1024*1024: break
            time.sleep(60)
        env = dict(os.environ, PYTHONPATH=str(Path(m['optimized'])/'model'),
            PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        env.pop('PYTHONOPTIMIZE', None)
        def bound_memory():
            bound = m['memory_limit_gib']*1024**3
            resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        print('START', row['name'], mode, flush=True)
        with (dest/'run.log').open('x') as log:
            subprocess.run(['/usr/bin/time','-v','-o',str(dest/'wall.time'),*cmd],
                cwd=m['optimized'], env=env, stdout=log, stderr=subprocess.STDOUT,
                preexec_fn=bound_memory, check=True)
        sources(m); assert digest(ROOT/row['dataset']) == row['sha256']
        files = audit_metrics(dest, mode, dict(**sig))
        save(dest/'audit.json', dict(status='provisional-conservation-pass', files=files,
            paper_ready=False, geometry_rtl_verified=False, signature=sig))
        print('COLLECTED', row['name'], mode, flush=True)


if __name__ == '__main__':
    if not __debug__: raise RuntimeError('Assertions must be enabled')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare','validate','campaign'])
    p.add_argument('--index', type=int, choices=range(4))
    p.add_argument('--family', choices=['toroidal','community','uniform'])
    p.add_argument('--mode', choices=['cir','cores-only'])
    a=p.parse_args()
    try:
        if a.action=='prepare': prepare()
        elif a.action=='validate':
            if a.index is None: p.error('--index required')
            validate(a.index)
        else:
            if a.family is None or a.mode is None: p.error('--family and --mode required')
            campaign(a.family,a.mode)
    except Exception as error:
        if OUT.exists():
            name=f'validation_{a.index}_failure.json' if a.action=='validate' else f'{a.action}_{a.family}_{a.mode}_failure.json'
            save(OUT/name, dict(error=repr(error), paper_ready=False))
        raise
