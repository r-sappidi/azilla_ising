#!/usr/bin/env python3
"""Instance-tuned Gset search: every trajectory is exactly 1000 updates."""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from datetime import datetime, timezone
import numpy as np

ROOT = Path(os.environ.get('AZILLA_REPOSITORY_ROOT', Path(__file__).resolve().parents[1]))
OUT = ROOT / 'results/gset_instance_tuning_20260908'
OLD = ROOT / 'results/retuned_quality_v2_20260908'
REFERENCES = {'gset_g21':931, 'gset_g32':1410, 'gset_g35':7687,
              'gset_g37':7691, 'gset_g47':6657, 'gset_g70':9594, 'gset_g81':14060}
TRAIN = [61000, 61001]
EVAL = list(range(62000, 62008))

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp'); tmp.write_text(json.dumps(d, indent=2)+'\n'); tmp.replace(p)
def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def schedule(p):
    values=[]
    for i in range(1000):
        hold=p.get('hold',0); end=p.get('end',999)
        numerator=max(0,end-max(i,hold)); denominator=end-hold
        if p.get('shape','linear')=='quadratic':
            amp=p['noise']*numerator*numerator//(denominator*denominator)
        else: amp=p['noise']*numerator//denominator
        values.append((p['a'],p['b'],amp))
    assert len(values)==1000 and all(0<=a<=127 and 1<=b<=127 and 0<=n<=127 for a,b,n in values)
    return values

@lru_cache(None)
def helpers():
    return (module('gset_helper',OUT/'snapshot/runner.py'),
            module('gset_reference',OUT/'snapshot/reference.py'))
@lru_cache(None)
def graph(path):return helpers()[1].load_dataset(Path(path))

def worker_signals():
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

def trial(task):
    stage,d,p,seed=task
    helper,ref=helpers(); ident=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()[:16]
    dest=OUT/stage/d['name']/ident/f'seed_{seed}'
    g=graph(d['couplings'])
    reused=False
    if dest.exists():
        required=['audit.json','result.json','states.npz','trajectory.csv']
        if all((dest/f).is_file() for f in required):
            prior=json.loads((dest/'audit.json').read_text())
            assert prior['parameters']==p and prior['seed']==seed and prior['dataset']==d['name'] and prior['stage']==stage
            result=json.loads((dest/'result.json').read_text());reused=True
        else:
            archive=dest.with_name(dest.name+'.interrupted-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
            dest.rename(archive)  # Preserve incomplete attempts, never overwrite them.
    if not reused:result=helper.simulate(ref,g,schedule(p),seed,dest)
    # Recompute objectives directly from the independent objective-weight file.
    n,_,r,c,w=graph(d['objective']); keep=r<c
    states=np.load(dest/'states.npz')
    assert result['seed']==seed and result['iterations']==1000
    assert np.array_equal(states['initial'],ref.initial_state(n,seed)[0])
    with (dest/'trajectory.csv').open() as f:trajectory=list(csv.DictReader(f))
    cuts=[int(x['cut']) for x in trajectory]
    assert len(cuts)==1001 and max(cuts)==result['best_cut'] and cuts[-1]==result['final_cut']
    assert cuts[0]==result['initial_cut'] and cuts.index(max(cuts))==result['best_iteration']
    assert [int(x['noise']) for x in trajectory[1:]]==[x[2] for x in schedule(p)]
    for field,key in [('initial','initial_cut'),('best','best_cut'),('final','final_cut')]:
        assert len(states[field])==n
        cut=int(w[keep][states[field][r[keep]]!=states[field][c[keep]]].sum(dtype=np.int64))
        assert cut==result[key],(d['name'],field,cut,result[key])
    assert sha(d['objective'])==d['objective_sha256'] and sha(d['couplings'])==d['couplings_sha256']
    row=dict(dataset=d['name'],stage=stage,parameters=p,parameter_id=ident,
             reference_cut=REFERENCES[d['name']],target_cut=math.ceil(.99*REFERENCES[d['name']]),
             **result)
    row['reference_ratio']=result['best_cut']/row['reference_cut']
    row['target_met']=result['best_cut']>=row['target_cut']
    row['saved_objective_audit']='pass'
    if reused:assert prior==row,'Saved audit changed during resume verification'
    else:save(dest/'audit.json',row)
    return row

def rank(rows):
    groups={}
    for r in rows:groups.setdefault(r['parameter_id'],[]).append(r)
    return sorted(groups.values(),key=lambda g:(-max(r['best_cut'] for r in g),
                  -sum(r['best_cut'] for r in g)/len(g),g[0]['parameter_id']))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--resume',action='store_true')
    resume=parser.parse_args().resume
    OUT.mkdir(exist_ok=resume);snap=OUT/'snapshot'
    if not resume:
        snap.mkdir()
        for name in ('reference.py','runner.py'):shutil.copy2(OLD/'snapshot'/name,snap/name)
        shutil.copy2(__file__,snap/'campaign.py')
    datasets=[d for d in json.loads((OLD/'manifest.json').read_text())['datasets'] if d['name'] in REFERENCES]
    assert {d['name'] for d in datasets}==set(REFERENCES)
    files={str(p):sha(p) for p in snap.iterdir() if p.is_file()}
    grid=[dict(a=a,b=b,noise=noise,shape='linear',hold=0,end=999)
          for a in (0,1,2,3,4,6,8,12,16,24,32) for b in (1,2,4) for noise in (0,2,4,8,16,32,64)]
    profiles=[dict(shape='linear',hold=0,end=e) for e in (249,499,749,999)]
    profiles += [dict(shape='linear',hold=249,end=999),dict(shape='quadratic',hold=0,end=999)]
    plan=dict(iterations=1000,workers=4,training_seeds=TRAIN,evaluation_seeds=EVAL,
        coarse_candidates=grid,refinement_profiles=profiles,datasets=datasets,sources=files,
        reference_cuts=REFERENCES,reference_sources=[
        'https://leria-info.univ-angers.fr/~jinkao.hao/papers/MaHaoAOR2016.pdf',
        'https://leria-info.univ-angers.fr/~jinkao.hao/papers/GoudetGoeffonHaoCOR2024.pdf',
        'https://arxiv.org/abs/2505.18508'],
        selection='Per-instance maximum training cut; mean training cut then parameter ID break ties. Top six refined; one winner frozen before eight evaluation seeds.',
        scope='Instance-tuned algorithmic reference, not global held-out quality or hardware time-to-solution.',
        target='At least 99% of the declared published reference cut, not an optimality certificate.',
        constraints='No trial above1000iterations; no local-search postprocessing; no evaluation-seed-based retuning.')
    if resume:
        previous=json.loads((OUT/'manifest.json').read_text())
        assert previous==plan,'Frozen experiment plan or source hashes changed'
    else:save(OUT/'manifest.json',plan)
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    run_dir=OUT/'launches'/run_id;run_dir.mkdir(parents=True)
    shutil.copy2(__file__,run_dir/'campaign.py')
    save(run_dir/'manifest.json',dict(resume=resume,script_sha256=sha(__file__),
        experiment_manifest_sha256=sha(OUT/'manifest.json')))
    def interrupted(signum,frame):
        save(OUT/'status.json',dict(status='interrupted',signal=signum,run_id=run_id))
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGINT,interrupted);signal.signal(signal.SIGTERM,interrupted)
    # Validate each schedule shape against the independent reference entry point.
    helper,ref=helpers();smoke=OUT/'smoke';smoke.mkdir(exist_ok=resume)
    edges=np.array([(a,b,(-3,-1,1,2)[(a+b)%4]) for a in range(32) for b in range(a+1,32)])
    if not resume:
        helper.write_graph(smoke/'couplings.txt',32,edges,-1)
        save(smoke/'manifest.json',dict(datasets=[dict(name='smoke',dataset=str(smoke/'couplings.txt'),cut_weight_mode='negated',generated=True)]))
    save(OUT/'status.json',dict(status='reference-smokes',profiles=len(profiles)))
    for i,profile in enumerate(profiles):
        p=dict(a=4,b=2,noise=16,**profile);case=smoke/f'profile{i}'
        if resume:
            with (case/'schedule.csv').open() as f:
                stored=[(int(x['coeff_a']),int(x['coeff_b']),int(x['noise_amplitude'])) for x in csv.DictReader(f)]
            assert stored==schedule(p)
            with (case/'observed/trajectory.csv').open() as f:observed=[int(x['cut']) for x in csv.DictReader(f)]
            with (case/'reference/smoke/seed_63000/cut_trajectory.csv').open() as f:expected=[int(x['cut']) for x in csv.DictReader(f)]
            assert len(observed)==1001 and observed==expected
            continue
        case.mkdir()
        helper.simulate(ref,ref.load_dataset(smoke/'couplings.txt'),schedule(p),63000,case/'observed')
        with (case/'schedule.csv').open('x',newline='') as f:
            w=csv.writer(f);w.writerow(['iteration','coeff_a','coeff_b','noise_amplitude']);w.writerows((j,*v) for j,v in enumerate(schedule(p)))
        with (case/'reference.log').open('x') as log:
            subprocess.run([sys.executable,str(snap/'reference.py'),'--manifest',str(smoke/'manifest.json'),
                '--output-dir',str(case/'reference'),'--schedule',str(case/'schedule.csv'),'--seeds','63000'],stdout=log,stderr=subprocess.STDOUT,check=True)
        def cuts(path):
            with path.open() as f:return [int(x['cut']) for x in csv.DictReader(f)]
        assert cuts(case/'observed/trajectory.csv')==cuts(case/'reference/smoke/seed_63000/cut_trajectory.csv')
    smoke_pass=dict(status='pass',profiles=6,iterations_each=1000)
    if resume:assert json.loads((smoke/'audit.json').read_text())==smoke_pass
    else:save(smoke/'audit.json',smoke_pass)
    allrows=[]
    def execute(stage,tasks):
        save(OUT/'status.json',dict(status=stage,completed=0,total=len(tasks)))
        with ProcessPoolExecutor(max_workers=4,initializer=worker_signals) as pool:
            for i,row in enumerate(pool.map(trial,tasks,chunksize=1),1):
                allrows.append(row)
                with (run_dir/'trials.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
                best={}
                for name in REFERENCES:
                    r=[r for r in allrows if r['dataset']==name]
                    if r:best[name]=max(x['best_cut'] for x in r)
                save(OUT/'status.json',dict(status=stage,completed=i,total=len(tasks),best_seen=best))
                print(stage,i,len(tasks),row['dataset'],row['best_cut'],row['reference_ratio'],flush=True)
        assert all(sha(p)==h for p,h in files.items())
    execute('coarse', [('coarse',d,p,seed) for p in grid for d in datasets for seed in TRAIN])
    tasks=[]
    for d in datasets:
        candidates={}
        for group in rank([r for r in allrows if r['dataset']==d['name']])[:6]:
            top=group[0]['parameters']
            for a in sorted({max(0,top['a']-1),top['a'],min(127,top['a']+1)}):
                for profile in profiles:
                    p=dict(a=a,b=top['b'],noise=top['noise'],**profile)
                    candidates[json.dumps(p,sort_keys=True)]=p
        tasks += [('refine',d,p,seed) for p in candidates.values() for seed in TRAIN]
    execute('refine',tasks)
    winners={d['name']:rank([r for r in allrows if r['dataset']==d['name']])[0][0]['parameters'] for d in datasets}
    frozen_winners=dict(status='frozen-before-fresh-seed-evaluation',parameters=winners)
    if (OUT/'winners.json').exists():assert json.loads((OUT/'winners.json').read_text())==frozen_winners
    else:save(OUT/'winners.json',frozen_winners)
    execute('evaluation',[('evaluation',d,winners[d['name']],seed) for d in datasets for seed in EVAL])
    summary=[]
    for d in datasets:
        train=[r for r in allrows if r['dataset']==d['name'] and r['stage']!='evaluation']
        test=[r for r in allrows if r['dataset']==d['name'] and r['stage']=='evaluation']
        assert sorted(r['seed'] for r in test)==EVAL
        summary.append(dict(dataset=d['name'],reference_cut=REFERENCES[d['name']],
            training_best=max(r['best_cut'] for r in train),evaluation_best=max(r['best_cut'] for r in test),
            evaluation_mean=float(np.mean([r['best_cut'] for r in test])),evaluation_hits=sum(r['target_met'] for r in test),
            evaluation_trials=len(test),evaluation_best_ratio=max(r['reference_ratio'] for r in test),parameters=winners[d['name']]))
    save(OUT/'summary.json',summary)
    save(OUT/'status.json',dict(status='complete',trials=len(allrows),all_evaluation_targets_met=all(r['evaluation_hits']>0 for r in summary)))

if __name__=='__main__':
    try:main()
    except BaseException as e:
        if OUT.exists():
            save(OUT/'status.json',dict(status='interrupted' if isinstance(e,KeyboardInterrupt) else 'failed',error=repr(e)))
            if not isinstance(e,KeyboardInterrupt):save(OUT/'failure.json',dict(error=repr(e)))
        raise
