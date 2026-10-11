#!/usr/bin/env python3
"""Train-only schedule sweep followed by a frozen, fresh-instance evaluation."""
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/retuned_quality_v2_20260908'
OLD = ROOT / 'results/paper_deadline_20260907/quality_holdout_v1'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_suffix('.tmp')
    t.write_text(json.dumps(value, indent=2) + '\n')
    t.replace(p)


def load(name, p):
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    OUT.mkdir(exist_ok=False)
    snap = OUT / 'snapshot'
    snap.mkdir()
    for name in ('reference.py', 'baseline_runner.py', 'baseline.cpp', 'baseline', 'runner.py'):
        shutil.copy2(OLD / 'snapshot' / name, snap / name)
    shutil.copy2(__file__, snap / 'retuned_runner.py')
    helper = load('quality_helper', snap / 'runner.py')
    ref = load('quality_reference', snap / 'reference.py')
    baseline = load('quality_baseline', snap / 'baseline_runner.py')
    files = {str(p): sha(p) for p in snap.iterdir() if p.is_file()}
    candidates = [dict(a=a, b=1, noise=noise, kind='linear')
                  for a in (8, 16, 32) for noise in (8, 16, 32)]
    families = [('uniform', 16), ('uniform', 32), ('community', 16)]
    sizes = [1024, 4096, 16384, 65536, 131072, 262144]
    plan = dict(candidates=candidates, iterations=1000, workers=2,
        training_graph_seed=2026090801, training_solver_seeds=[51000, 51001],
        evaluation_graph_seeds=[2026090811, 2026090812], solver_seeds=[52000,52001,52002,52003],
        families=families, sizes=sizes, files=files,
        selection='Global training geometric mean best-cut / paired local-search cut; ties retain first candidate.',
        disclosure='Retuning motivated by prior degree32 holdout failure. New synthetic instances are held out; Gsets are previously inspected retests.',
        reference='simple local search, not optimum or SOTA', hardware_timing=False)
    save(OUT / 'plan.json', plan)
    def guard():
        assert all(sha(p) == h for p, h in files.items())
    def generated(family, degree, n, seed, directory):
        directory.mkdir(parents=True)
        edges = helper.generate(n, degree, seed, family == 'community')
        helper.write_graph(directory / 'objective.txt', n, edges, 1)
        helper.write_graph(directory / 'couplings.txt', n, edges, -1)
        d = dict(name=directory.name, family=family, degree=degree, spins=n, graph_seed=seed,
                 scope='fresh synthetic instance; global training-frozen schedule',
                 objective=str(directory / 'objective.txt'), couplings=str(directory / 'couplings.txt'))
        d.update(objective_sha256=sha(d['objective']), couplings_sha256=sha(d['couplings']))
        return d
    save(OUT / 'status.json', dict(status='preparing-training', candidates=9))
    training = [generated(f,d,1024,2026090801,OUT/'training_inputs'/f'{f}_d{d}') for f,d in families]
    g14 = ROOT / 'results/paper_validation_20260907/broad_solution_quality_v1/gset_g14/maxcut_couplings.txt'
    n,_,r,c,j = ref.load_dataset(g14)
    folder = OUT / 'training_inputs/gset_g14'
    folder.mkdir()
    shutil.copy2(g14, folder / 'couplings.txt')
    helper.write_graph(folder / 'objective.txt', n, np.column_stack((r[r<c],c[r<c],-j[r<c])), 1)
    training.append(dict(name='gset_g14',couplings=str(folder/'couplings.txt'),objective=str(folder/'objective.txt')))
    save(OUT / 'training_inputs.json', training)
    def train(d):
        graph = ref.load_dataset(Path(d['couplings']))
        rows = []
        for seed in plan['training_solver_seeds']:
            base = OUT/'training'/d['name']/f'seed_{seed}'
            base.mkdir(parents=True)
            cpu = baseline.execute(snap/'baseline',ref,Path(d['objective']),base/'cpu',seed,100)
            for k,w in enumerate(candidates):
                result = helper.simulate(ref,graph,helper.schedule(w),seed,base/f'candidate_{k}')
                assert result['initial_cut'] == cpu['initial_cut']
                rows.append(dict(dataset=d['name'],candidate=k,seed=seed,
                    best_cut=result['best_cut'],final_cut=result['final_cut'],
                    ratio=result['best_cut']/cpu['local_search_cut']))
                print('TRAIN',d['name'],seed,k,result['best_cut'],flush=True)
        return rows
    save(OUT/'status.json',dict(status='training',trials=72))
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=[r for group in pool.map(train,training) for r in group]
    scores=[float(np.exp(np.mean(np.log([r['ratio'] for r in rows if r['candidate']==k])))) for k in range(9)]
    winner=candidates[int(np.argmax(scores))]
    save(OUT/'training_results.json',rows)
    save(OUT/'winner.json',dict(schedule=winner,scores=scores,selection=plan['selection']))
    guard()
    # Independent full-reference trajectory check before any holdout result.
    smoke=OUT/'smoke';smoke.mkdir()
    edges=np.array([(a,b,(-3,-1,1,2)[(a+b)%4]) for a in range(32) for b in range(a+1,32)])
    helper.write_graph(smoke/'couplings.txt',32,edges,-1)
    observed=helper.simulate(ref,ref.load_dataset(smoke/'couplings.txt'),helper.schedule(winner),53000,smoke/'observed')
    save(smoke/'manifest.json',dict(datasets=[dict(name='smoke',dataset=str(smoke/'couplings.txt'),cut_weight_mode='negated',generated=True)]))
    with (smoke/'schedule.csv').open('x',newline='') as f:
        w=csv.writer(f);w.writerow(['iteration','coeff_a','coeff_b','noise_amplitude'])
        w.writerows((i,*s) for i,s in enumerate(helper.schedule(winner)))
    with (smoke/'reference.log').open('x') as log:
        subprocess.run([sys.executable,str(snap/'reference.py'),'--manifest',str(smoke/'manifest.json'),
            '--output-dir',str(smoke/'reference'),'--schedule',str(smoke/'schedule.csv'),'--seeds','53000'],stdout=log,stderr=subprocess.STDOUT,check=True)
    ours=list(csv.DictReader((smoke/'observed/trajectory.csv').open()))
    expected=list(csv.DictReader((smoke/'reference/smoke/seed_53000/cut_trajectory.csv').open()))
    assert [x['cut'] for x in ours]==[x['cut'] for x in expected]
    save(smoke/'audit.json',dict(status='pass',iterations=1000,signed_reference_trajectory=True))
    save(OUT/'status.json',dict(status='preparing-evaluation',winner=winner))
    datasets=[]
    for n in sizes:
        for family,degree in families:
            for seed in plan['evaluation_graph_seeds']:
                datasets.append(generated(family,degree,n,seed,OUT/'datasets'/f'{family}_d{degree}_n{n}_s{seed}'))
    for d in json.loads((OLD/'manifest.json').read_text())['datasets']:
        if d['family']=='Gset':
            assert sha(d['couplings'])==d['couplings_sha256'] and sha(d['objective'])==d['objective_sha256']
            datasets.append(dict(d,scope='previously inspected benchmark retest; excluded from current training'))
    manifest=dict(plan,datasets=datasets,schedule=winner,trial_count=len(datasets)*4)
    save(OUT/'manifest.json',manifest)
    def evaluate(d):
        graph=ref.load_dataset(Path(d['couplings']))
        rows=[]
        for seed in plan['solver_seeds']:
            folder=OUT/'trials'/d['name']/f'seed_{seed}';folder.mkdir(parents=True)
            result=helper.simulate(ref,graph,helper.schedule(winner),seed,folder/'ising')
            cpu=baseline.execute(snap/'baseline',ref,Path(d['objective']),folder/'cpu',seed,100)
            state=np.load(folder/'ising/states.npz')
            assert np.array_equal(state['initial'].astype(np.int64)*2-1,np.loadtxt(folder/'cpu/initial.txt',dtype=np.int64))
            assert result['initial_cut']==cpu['initial_cut']
            row=dict(dataset=d['name'],**result,local_search_cut=cpu['local_search_cut'],
                greedy_cut=cpu['greedy_one_pass_cut'],local_search_converged=cpu['converged_one_flip_local_optimum'],
                ratio_to_local_search=result['best_cut']/cpu['local_search_cut'] if cpu['local_search_cut'] else None)
            save(folder/'matched_result.json',row);rows.append(row)
            print('EVALUATED',d['name'],seed,result['best_cut'],flush=True)
        guard()
        assert sha(d['couplings'])==d['couplings_sha256'] and sha(d['objective'])==d['objective_sha256']
        save(OUT/'trials'/d['name']/'audit.json',dict(status='pass',trials=4,independent_objectives=True,paired_initial_states=True))
        return rows
    save(OUT/'status.json',dict(status='evaluating',trial_count=manifest['trial_count'],winner=winner))
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=[r for group in pool.map(evaluate,datasets) for r in group]
    save(OUT/'results.json',rows)
    save(OUT/'complete.json',dict(status='complete',matched_trials=len(rows),checks='pass',hardware_timing=False))
    save(OUT/'status.json',dict(status='complete',matched_trials=len(rows)))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        if OUT.exists():save(OUT/'failure.json',dict(status='failed',error=repr(exc)))
        raise
