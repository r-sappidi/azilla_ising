#!/usr/bin/env python3
"""Frozen-source exact sensitivities, explicitly outside certification gates.

This runner collects provisional projections, never passes/changes the separate
VCS certificate gates. Baseline points are referenced rather than duplicated.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from run_provisional_exact_matrix import ROOT, digest, write


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kind', choices=['width', 'density', 'geometry'], required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--baseline', type=Path, required=True)
    args = ap.parse_args()
    baseline = args.baseline.resolve()
    bm = json.loads((baseline / 'manifest.json').read_text())
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    snapshot = baseline / 'snapshot'
    hashes = dict(bm['files'])
    base_config = snapshot / 'ramulator_128x32.yaml'
    datasets = [d for d in bm['datasets'] if d['seed'] in (None, 1)]
    datasets.sort(key=lambda d: ({131072:0,65536:1,262144:2}[d['capacity_spins']],
                                 0 if 'toroidal' in d['name'] else 1, d['name']))
    points = []
    if args.kind == 'density':
        canonical = json.loads((ROOT / 'results/paper_canonical/datasets/manifest.json').read_text())
        datasets = [d for d in canonical['datasets'] if d['name'] in
                    [f'uniform_d{degree}_n131072_s1' for degree in (4,8,32,64)]]
        if len(datasets) != 4: raise RuntimeError('missing density inputs')
        for d in datasets:
            points.append((d, 'fixed_4-2-4', d['geometry'], 4, base_config))
    elif args.kind == 'geometry':
        for d in datasets:
            for h0, cores in ((4,512),(16,128)):
                for label, engines in (('fixed_per_h0',4), ('iso_total_h0_mvms',32//h0)):
                    g = dict(d['geometry'], h0_per_h1=h0, cores_per_h0=cores)
                    assert g['mesh_x']*g['mesh_y']*h0*cores*32 == d['capacity_spins']
                    points.append((d,f'h{h0}_c{cores}_{label}',g,engines,base_config))
    else:
        config_manifest = json.loads((ROOT / 'results/memory_width_prepare_20260907_v1/configs.json').read_text())
        for item in config_manifest['configs']:
            if item['width'] == 128: continue
            source = Path(item['path'])
            assert digest(source) == item['sha256']
            target = out / source.name
            shutil.copy2(source,target); target.chmod(0o444)
            hashes[str(target)] = digest(target)
            for d in datasets:
                points.append((d,f'x{item["width"]}',d['geometry'],4,target))
    plan = []
    for d,label,g,h0_engines,config in points:
        for mode in ('cores-only','cir'):
            plan.append(dict(dataset=d,variant=label,geometry=g,h0_mvms=h0_engines,
                h1_mvms=2,cross_mvms=4,config=str(config),mode=mode))
    write(out / 'manifest.json',dict(status='provisional-not-certified',kind=args.kind,
        baseline=str(baseline),files=hashes,points=plan,
        caveats=['Seed-one screening; not final multi-seed comparisons.',
        'Width scales controller/channel count, banks, queues, capacity; not pure serialization or vendor GDDR7.',
        'Geometry changes memory-interface count, even for equal MVM count.',
        'Density uses uniform d4/8/32/64; existing d16 and community/toroidal baseline supply locality contrast.']))
    shutil.copy2(Path(__file__),out/'runner_source.py')
    for point in plan:
        d=point['dataset'];mode=point['mode']
        path=out/d['name']/point['variant']/mode
        path.mkdir(parents=True,exist_ok=False)
        try:
            # Avoid launching a new large process under memory pressure.
            while True:
                mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
                if int(mem['MemAvailable'].split()[0]) > 16*1024*1024: break
                time.sleep(30)
            assert all(digest(Path(p))==h for p,h in hashes.items())
            dataset=ROOT/d['dataset'];assert digest(dataset)==d['sha256']
            cmd=[sys.executable,'-m','azilla_cycle_model.cli','simulate-exact-events',
                '--dataset',str(dataset),'--execution-mode',mode,
                '--ramulator-library',str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                '--ramulator-config',point['config'],'--metrics-prefix',str(path/'metrics')]
            for level in ('h0','h1','cross'):cmd.extend([f'--{level}-mvms',str(point[level+'_mvms'])])
            for key,value in point['geometry'].items():cmd.extend(['--'+key.replace('_','-'),str(value)])
            write(path/'command.json',cmd)
            print(f'START {d["name"]} {point["variant"]} {mode}',flush=True)
            env=dict(os.environ,PYTHONPATH=str(snapshot/'model'),PYTHONDONTWRITEBYTECODE='1',
                     OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
            with (path/'run.log').open('x') as log:
                subprocess.run(['/usr/bin/time','-v','-o',str(path/'wall.time'),*cmd],
                    cwd=snapshot,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            data=json.loads((path/'metrics_summary.json').read_text())
            assert data['execution_mode']==mode
            assert data['accuracy']=='cycle-structured-unverified'
            assert data['noc']['injected_flits']==data['noc']['ejected_flits']
            assert data['timing_cycles']['iteration']>0
            for dram in data['dram_systems']:
                assert dram['accepted_requests']==dram['completed_requests'] and dram['outstanding_requests']==0
            if mode=='cores-only':assert data['cores_only_ablation']['pipeline_contract']['model']=='full_single_mvm_iteration_v1'
            assert digest(dataset)==d['sha256']
            assert all(digest(Path(p))==h for p,h in hashes.items())
            write(path/'audit.json',dict(status='provisional-conservation-pass',paper_ready=False))
            print(f'COLLECTED {d["name"]} {point["variant"]} {mode}',flush=True)
        except Exception as error:
            write(path/'failure.json',dict(error=repr(error)));raise
    write(out/'complete.json',dict(points=len(plan),paper_ready=False))


if __name__=='__main__':main()
