#!/usr/bin/env python3
"""G37/G81 seed-robust refinement; exactly 1000 iterations per trial."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import numpy as np

ROOT=Path(os.environ.get('AZILLA_REPOSITORY_ROOT',Path(__file__).resolve().parents[1]))
OUT=ROOT/'results/gset_robust_tuning_20260908'
OLD=ROOT/'results/gset_instance_tuning_20260908'
TRAIN=list(range(71000,71006))
CONFIRM=list(range(72000,72006))
EVALUATE=list(range(73000,73016))
ENGINE=None


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(d,indent=2)+'\n');tmp.replace(p)
def initialize():
    global ENGINE
    spec=importlib.util.spec_from_file_location('robust_trial_engine',OUT/'snapshot/tuning.py')
    ENGINE=importlib.util.module_from_spec(spec);spec.loader.exec_module(ENGINE)
    ENGINE.OUT=OUT;ENGINE.worker_signals()
def trial(task):return ENGINE.trial(task)
def pid(p):return hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()[:16]
def ranked(rows):
    groups={}
    for r in rows:groups.setdefault(r['parameter_id'],[]).append(r)
    return sorted(groups.values(),key=lambda g:(-float(np.mean([r['best_cut'] for r in g])),
        -float(np.median([r['best_cut'] for r in g])),-min(r['best_cut'] for r in g),g[0]['parameter_id']))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--resume',action='store_true');a=ap.parse_args()
    OUT.mkdir(exist_ok=a.resume);snap=OUT/'snapshot'
    if not a.resume:
        snap.mkdir()
        for name in ('runner.py','reference.py'):shutil.copy2(OLD/'snapshot'/name,snap/name)
        shutil.copy2(ROOT/'scripts/run_gset_instance_tuning.py',snap/'tuning.py')
        shutil.copy2(__file__,snap/'campaign.py')
    old=json.loads((OLD/'manifest.json').read_text())
    ds=[d for d in old['datasets'] if d['name'] in ('gset_g37','gset_g81')]
    previous=json.loads((OLD/'winners.json').read_text())['parameters']
    profiles=[dict(shape='linear',hold=h,end=e) for h,e in
              ((0,249),(0,499),(0,749),(0,999),(249,749),(249,999),(499,999))]
    profiles.append(dict(shape='quadratic',hold=0,end=999))
    candidates={}
    for d in ds:
        name=d['name'];oldp=previous[name]
        av=(23,25,27) if name=='gset_g37' else (2,3,4)
        bv=(1,2,3) if name=='gset_g37' else (3,4,5)
        nv=(24,32,48) if name=='gset_g37' else (6,8,12)
        grid=[dict(a=x,b=y,noise=z,**profile) for x in av for y in bv for z in nv for profile in profiles]
        # Keep earlier successful settings and the global reference eligible.
        oldrows=[]
        for stage in ('coarse','refine'):
            for f in (OLD/stage/name).glob('*/seed_*/audit.json'):oldrows.append(json.loads(f.read_text()))
        grid += [g[0]['parameters'] for g in ranked(oldrows)[:8]]
        grid += [oldp,dict(a=16,b=1,noise=32,shape='linear',hold=0,end=999)]
        candidates[name]=list({pid(p):p for p in grid}.values())
    sources={str(p):sha(p) for p in snap.iterdir() if p.is_file()}
    plan=dict(datasets=ds,candidates=candidates,training_seeds=TRAIN,confirmation_seeds=CONFIRM,
        evaluation_seeds=EVALUATE,iterations=1000,workers=4,sources=sources,
        selection='Mean training best-cut, then median, minimum, parameter ID; top six receive six more training seeds; select on all twelve seeds.',
        evaluation='Freeze one winner per graph before16 fresh seeds; also evaluate previous winner on identical fresh seeds. Never retune from evaluation.',
        previous_parameters={d['name']:previous[d['name']] for d in ds},
        scope='Per-instance algorithmic-reference tuning; not RTL timing or a global held-out benchmark.',
        constraints='No trajectory over1000updates; same arithmetic/noise implementation; no postprocessing.',
        reference_sources=old['reference_sources'],reference_cuts={d['name']:old['reference_cuts'][d['name']] for d in ds})
    if a.resume:assert json.loads((OUT/'manifest.json').read_text())==plan
    else:save(OUT/'manifest.json',plan)
    initialize()
    def interrupted(signum,frame):
        save(OUT/'status.json',dict(status='interrupted',signal=signum))
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGINT,interrupted)
    signal.signal(signal.SIGTERM,interrupted)
    # The scheduling implementation is unchanged; validate every candidate's
    # ranges/endpoints before launching costly trials.
    for grid in candidates.values():
        for p in grid:
            s=ENGINE.schedule(p)
            assert len(s)==1000 and s[-1][2]==0 and all(s[i][2]>=s[i+1][2] for i in range(999))
    rows=[]
    def execute(stage,tasks):
        save(OUT/'status.json',dict(status=stage,completed=0,total=len(tasks)))
        with ProcessPoolExecutor(max_workers=4,initializer=initialize) as pool:
            futures=[pool.submit(trial,t) for t in tasks]
            for i,f in enumerate(as_completed(futures),1):
                r=f.result();rows.append(r)
                save(OUT/'status.json',dict(status=stage,completed=i,total=len(tasks),
                    dataset=r['dataset'],last_cut=r['best_cut']))
                print(stage,i,len(tasks),r['dataset'],r['best_cut'],flush=True)
        assert all(sha(p)==h for p,h in sources.items())
    execute('screen',[('screen',d,p,s) for d in ds for p in candidates[d['name']] for s in TRAIN])
    finalists={d['name']:[g[0]['parameters'] for g in ranked([r for r in rows if r['dataset']==d['name']])[:6]] for d in ds}
    save(OUT/'finalists.json',finalists)
    execute('confirmation',[('confirmation',d,p,s) for d in ds for p in finalists[d['name']] for s in CONFIRM])
    winners={}
    for d in ds:
        eligible={pid(p) for p in finalists[d['name']]}
        groups=ranked([r for r in rows if r['dataset']==d['name'] and r['parameter_id'] in eligible])
        assert all(len(g)==12 for g in groups)
        winners[d['name']]=groups[0][0]['parameters']
    frozen=dict(status='frozen-before-evaluation',parameters=winners)
    if (OUT/'winners.json').exists():assert json.loads((OUT/'winners.json').read_text())==frozen
    else:save(OUT/'winners.json',frozen)
    execute('evaluation',[('evaluation',d,winners[d['name']],s) for d in ds for s in EVALUATE])
    execute('previous_baseline',[('previous_baseline',d,previous[d['name']],s) for d in ds for s in EVALUATE])
    summary=[]
    for d in ds:
        for stage in ('evaluation','previous_baseline'):
            rr=[r for r in rows if r['dataset']==d['name'] and r['stage']==stage]
            assert sorted(r['seed'] for r in rr)==EVALUATE
            cuts=[r['best_cut'] for r in rr];reference=rr[0]['reference_cut']
            summary.append(dict(dataset=d['name'],stage=stage,best=max(cuts),mean=float(np.mean(cuts)),
                median=float(np.median(cuts)),minimum=min(cuts),best_ratio=max(cuts)/reference,
                mean_ratio=float(np.mean(cuts))/reference,hits99=sum(r['target_met'] for r in rr),seeds=16,
                parameters=rr[0]['parameters']))
    save(OUT/'summary.json',summary)
    save(OUT/'status.json',dict(status='complete',trials=len(rows),summary=summary))


if __name__=='__main__':
    try:main()
    except BaseException as e:
        if OUT.exists():save(OUT/'status.json',dict(status='interrupted' if isinstance(e,KeyboardInterrupt) else 'failed',error=repr(e)))
        raise
