#!/usr/bin/env python3
"""Versioned shared-fetch and constrained-link followups; no old result writes."""
import argparse,concurrent.futures,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/shared_fetch_followups_v1'
BASE=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1'
MINIMUM=ROOT/'results/paper_deadline_20260907/minimum_matrix_v1/timing'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2)+'\n');t.replace(p)
def prepare():
    OUT.mkdir(exist_ok=False)
    snap=OUT/'snapshot/model/azilla_cycle_model';snap.mkdir(parents=True)
    # Freeze public project code only; never copy licensed PDK or tool files.
    for p in (ROOT/'model/azilla_cycle_model').glob('*.py'):shutil.copy2(p,snap/p.name)
    evidence=[]
    for folder in ('full_retry','sram_candidate'):
        for stall in (0,7,13):
            p=ROOT/f'results/shared_fetch_validation_v1/{folder}/wrapper_stall{stall}.json'
            d=json.loads(p.read_text());assert d['matched'] and all(d['checks'].values())
            evidence.append(p)
    manifest=json.loads((BASE/'manifest.json').read_text())
    datasets=[d for d in manifest['datasets'] if d['capacity_spins'] in (65536,131072) and d['seed'] in (None,1)]
    assert len(datasets)==6
    files=list(snap.glob('*.py'))+evidence+[BASE/'manifest.json',Path(__file__),
        BASE/'snapshot/ramulator_128x32.yaml',ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so']
    files += [ROOT/d['dataset'] for d in datasets]
    save(OUT/'manifest.json',dict(datasets=datasets,files={str(p):sha(p) for p in files},
        scope='256-spin representative RTL validation; larger runs are unverified-geometry projections',
        shared_fetch='single-fetch two-unicast; extra1KiB register buffer/source lane',
        paper_ready=False,accelerator_cycle_ns=10))
def guard(m):
    assert all(sha(p)==s for p,s in m['files'].items()),'frozen input/source changed'
def minimum_complete():return len(list(MINIMUM.rglob('audit.json'))) == 12
def run(kind):
    m=json.loads((OUT/'manifest.json').read_text());guard(m)
    if kind=='links':
        while not minimum_complete():time.sleep(30)
    points=[(d,mode) for d in m['datasets']
            if kind=='shared' or (d['capacity_spins']==131072 and d['seed']==1)
            for mode in (('cores-only',) if kind=='shared' else ('cir','cores-only'))]
    def point(item):
        d,mode=item
        if kind=='shared' and d['capacity_spins']==131072:
            while not minimum_complete():time.sleep(30)
        directory=OUT/kind/d['name']/mode
        if (directory/'audit.json').exists():return
        directory.mkdir(parents=True,exist_ok=False);guard(m)
        source=OUT/'snapshot/model' if kind=='shared' else BASE/'snapshot/model'
        module='azilla_cycle_model.shared_fetch_cli' if kind=='shared' else 'azilla_cycle_model.cli'
        cmd=[sys.executable,'-m',module,'simulate-exact-events','--execution-mode',mode,
             '--dataset',str(ROOT/d['dataset']),'--ramulator-library',str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
             '--ramulator-config',str(BASE/'snapshot/ramulator_128x32.yaml'),
             '--h0-mvms','4','--h1-mvms','2','--cross-mvms','4','--idle-refresh-period-ticks','0',
             '--metrics-prefix',str(directory/'metrics')]
        for k,v in d['geometry'].items():cmd+=['--'+k.replace('_','-'),str(v)]
        if kind=='links':cmd+=['--link-latency-cycles','8','--link-flit-interval-cycles','2','--link-max-inflight-flits','4','--link-credit-return-cycles','8']
        save(directory/'command.json',cmd)
        env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        with (directory/'run.log').open('w') as log:
            result=subprocess.run(['/usr/bin/time','-v','-o',str(directory/'wall.time'),*cmd],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            save(directory/'failure.json',dict(returncode=result.returncode));raise RuntimeError(str(directory))
        data=json.loads((directory/'metrics_summary.json').read_text());guard(m)
        assert data['execution_mode']==mode and data['timing_cycles']['iteration']>0
        assert data['noc']['injected_flits']==data['noc']['ejected_flits']
        assert all(x['accepted_requests']==x['completed_requests']==32*x['scheduled_jobs'] and not x['outstanding_requests'] for x in data['dram_systems'])
        if kind=='shared':
            assert data['cores_only_ablation']['pipeline_contract']['fetch_policy']=='single_fetch_two_unicast_v1'
        files={str(p):sha(p) for p in directory.iterdir() if p.is_file()}
        save(directory/'audit.json',dict(status='conservation-pass',files=files,paper_ready=False,
            geometry_rtl_verified=False,accuracy=data['accuracy']))
        print('PASS',kind,d['name'],mode,flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(point,points))
    save(OUT/(kind+'_complete.json'),dict(status='complete',points=len(points),paper_ready=False))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','shared','links']);a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:
        try:run(a.action)
        except Exception as e:
            save(OUT/(a.action+'_failure.json'),dict(error=repr(e)));raise
