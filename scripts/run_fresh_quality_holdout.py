#!/usr/bin/env python3
"""Frozen training-winner quality holdout; no retuning or hardware timing claims."""
import argparse,csv,importlib.util,json,os,shutil,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import numpy as np

ROOT=Path(os.environ.get('AZILLA_REPOSITORY_ROOT',Path.cwd())).resolve()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    with Path(p).open('x') as f:json.dump(v,f,indent=2)
def load_module(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def frozen(out):
    return load_module('held_reference',out/'snapshot/reference.py'),load_module('held_baseline',out/'snapshot/baseline_runner.py')
def schedule(w):return [(w['a'],w['b'],w['noise']*(999-i)//999) for i in range(1000)]
def objective(state,r,c,w):
    keep=r<c
    return int(w[keep][state[r[keep]]!=state[c[keep]]].sum(dtype=np.int64))

def simulate(ref,graph,sched,seed,out):
    n,_,r,c,j=graph;w=-j;state,lf=ref.initial_state(n,seed);initial=state.copy();best_state=state.copy()
    best=objective(state,r,c,w);best_i=0;trace=[(0,best,0,0,0)]
    old2=None;started=time.monotonic()
    absolute=np.bincount(r,weights=np.abs(j),minlength=n).astype(np.int64)
    assert max(a+b*int(absolute.max())+amp for a,b,amp in sched)<2**31
    for i,(a,b,amp) in enumerate(sched,1):
        signs=state.astype(np.int64)*2-1
        field=a*signs+b*np.bincount(r,weights=j*signs[c],minlength=n).astype(np.int64)
        lf=ref.lfsr_step(lf)
        if amp:field+=amp*(((lf[:,None]>>np.arange(32,dtype=np.uint64))&1).reshape(-1).astype(np.int64)*2-1)
        nxt=field>=0;flips=int(np.count_nonzero(nxt!=state));repeat=old2 is not None and np.array_equal(nxt,old2) and flips>0
        old2,state=state,nxt;cut=ref.cut_value(state,r,c,w)
        if cut>best:best,best_i,best_state=cut,i,state.copy()
        trace.append((i,cut,flips,int(repeat),amp))
    assert objective(best_state,r,c,w)==best and objective(state,r,c,w)==trace[-1][1]
    assert best==max(t[1] for t in trace) and best_i==next(t[0] for t in trace if t[1]==best)
    out.mkdir(parents=True,exist_ok=False)
    with (out/'trajectory.csv').open('x',newline='') as f:
        wr=csv.writer(f);wr.writerow(['iteration','cut','flipped_spins','spin_period_two','noise']);wr.writerows(trace)
    np.savez_compressed(out/'states.npz',initial=initial,best=best_state,final=state,final_lfsr=lf)
    result=dict(seed=seed,spins=n,iterations=1000,initial_cut=trace[0][1],best_cut=best,best_iteration=best_i,
        final_cut=trace[-1][1],zero_cut_iterations=sum(t[1]==0 for t in trace[1:]),
        spin_period_two_observations=sum(t[3] for t in trace),wall_seconds=time.monotonic()-started,
        independent_best_final_objective_pass=True,known_optimum=None,algorithmic_reference_only=True)
    save(out/'result.json',result);return result

def write_graph(path,n,edges,sign):
    with path.open('x') as f:
        f.write(f'{n} 0\n')
        for a,b,w in edges:f.write(f'{a+1} {b+1} {sign*w}\n{b+1} {a+1} {sign*w}\n')
def generate(n,degree,seed,community=False):
    rng=np.random.default_rng(seed);count=n*degree//2;keys=np.empty(0,dtype=np.int64)
    while len(keys)<count:
        batch=max(4096,int((count-len(keys))*1.2));a=rng.integers(n,size=batch);b=rng.integers(n,size=batch)
        if community:
            local=rng.random(batch)<.8;b[local]=(a[local]//(n//8))*(n//8)+rng.integers(n//8,size=int(local.sum()))
        keep=a!=b;a,b=a[keep],b[keep]
        keys=np.unique(np.concatenate([keys,np.minimum(a,b)*n+np.maximum(a,b)]))
    if len(keys)>count:keys=np.sort(rng.choice(keys,count,replace=False))
    return np.column_stack((keys//n,keys%n,np.ones(count,dtype=np.int64)))

def prepare(out):
    out.mkdir(parents=True,exist_ok=False);snap=out/'snapshot';snap.mkdir()
    training=ROOT/'results/paper_validation_20260907/quality_tuning_diagnostic_v1'
    winner=json.loads((training/'winner.json').read_text());scores=json.loads((training/'training_scores.json').read_text())
    assert winner==max(scores,key=lambda d:d['score']) and winner['kind']=='linear'
    for src,name in [(training/'reference.py','reference.py'),(training/'winner.json','winner.json'),
        (training/'training_scores.json','training_scores.json'),(training/'plan.json','training_plan.json'),
        (ROOT/'scripts/run_quality_local_baselines.py','baseline_runner.py'),
        (ROOT/'scripts/maxcut_local_baseline.cpp','baseline.cpp'),(Path(__file__),'runner.py')]:shutil.copy2(src,snap/name)
    subprocess.run(['g++','-O3','-std=c++17',str(snap/'baseline.cpp'),'-o',str(snap/'baseline')],check=True)
    sched=schedule(winner)
    with (snap/'schedule.csv').open('x',newline='') as f:
        wr=csv.writer(f);wr.writerow(['iteration','coeff_a','coeff_b','noise_amplitude']);wr.writerows((i,*s) for i,s in enumerate(sched))
    ref,baseline=frozen(out)
    smoke=out/'smoke';smoke.mkdir();rng=np.random.default_rng(7719)
    edges=np.array([(a,b,int(rng.choice([-3,-1,1,2]))) for a in range(32) for b in range(a+1,32)],dtype=np.int64)
    write_graph(smoke/'objective.txt',32,edges,1);write_graph(smoke/'couplings.txt',32,edges,-1)
    graph=ref.load_dataset(smoke/'couplings.txt');simulate(ref,graph,sched,41000,smoke/'observed')
    save(smoke/'reference_manifest.json',dict(datasets=[dict(name='smoke',dataset=str(smoke/'couplings.txt'),cut_weight_mode='negated',generated=True)]))
    with (smoke/'reference.log').open('x') as log:
        subprocess.run([sys.executable,str(snap/'reference.py'),'--manifest',str(smoke/'reference_manifest.json'),
            '--output-dir',str(smoke/'reference'),'--schedule',str(snap/'schedule.csv'),'--seeds','41000'],stdout=log,stderr=subprocess.STDOUT,check=True)
    ours=list(csv.DictReader((smoke/'observed/trajectory.csv').open()));expected=list(csv.DictReader((smoke/'reference/smoke/seed_41000/cut_trajectory.csv').open()))
    assert [r['cut'] for r in ours]==[r['cut'] for r in expected]
    baseline.execute(snap/'baseline',ref,smoke/'objective.txt',smoke/'cpu',41000,100)
    save(smoke/'audit.json',dict(status='pass',reference_trajectory_iterations=1000,
        best_final_objective=True,signed_cpu_every_flip_checks=True))
    datasets=[];data=out/'datasets';data.mkdir()
    published=json.loads((ROOT/'results/paper_benchmarks/datasets/manifest.json').read_text())['datasets']
    selected={'gset_g21','gset_g32','gset_g35','gset_g37','gset_g47','gset_g70','gset_g81'}
    for d in published:
        if d['name'] not in selected:continue
        source=ROOT/d['dataset'];assert sha(source)==d['sha256']
        n,_,r,c,w=ref.load_dataset(source);edges=np.column_stack((r[r<c],c[r<c],w[r<c]))
        directory=data/d['name'];directory.mkdir();write_graph(directory/'objective.txt',n,edges,1);write_graph(directory/'couplings.txt',n,edges,-1)
        datasets.append(dict(name=d['name'],spins=n,family='Gset',graph_seed=None,original_source=str(source),original_sha256=sha(source),
            scope='excluded from parameter training; previously inspected broad baseline, not blind unseen graph',objective=str(directory/'objective.txt'),couplings=str(directory/'couplings.txt')))
    for family,sizes,degrees in [('uniform',[1024,4096,16384,65536],[16,32]),('community',[4096,16384],[16])]:
        for n in sizes:
            for degree in degrees:
                for seed in (202609071,202609072):
                    edges=generate(n,degree,seed,family=='community');name=f'{family}_d{degree}_n{n}_s{seed}'
                    directory=data/name;directory.mkdir();write_graph(directory/'objective.txt',n,edges,1);write_graph(directory/'couplings.txt',n,edges,-1)
                    datasets.append(dict(name=name,spins=n,family=family,degree=degree,graph_seed=seed,
                        scope='fresh graph instance; training-frozen schedule; no outcome-based parameter changes',
                        realized_community_local_fraction=float(np.mean(edges[:,0]//(n//8)==edges[:,1]//(n//8))),
                        objective=str(directory/'objective.txt'),couplings=str(directory/'couplings.txt')))
    for d in datasets:
        d['objective_sha256']=sha(d['objective']);d['couplings_sha256']=sha(d['couplings'])
    files={str(p):sha(p) for p in snap.iterdir() if p.is_file()}
    for p in snap.iterdir():p.chmod(0o555 if p.name=='baseline' else 0o444)
    save(out/'manifest.json',dict(status='frozen-before-evaluation',schedule=winner,iterations=1000,solver_seeds=list(range(41000,41008)),
        workers=2,threads_per_worker=1,datasets=datasets,files=files,trial_count=len(datasets)*8,
        selection='exact prior training winner only: G14/G19/uniform_train; no G22/G31/degree32 diagnosis retuning',
        CPU_baseline='same initial state; shuffled greedy pass and sequential positive-gain local search, max100sweeps',
        limitations=['Algorithmic-reference solution quality only, no hardware runtime or large RTL validation.',
                    'Initial and noise stream share the solver seed; graph seeds are separate.',
                    'No known-optimum percentages without independently verified reference values.',
                    'All failures/collapses retained; no selecting parameters on this holdout.']))
    print('PREPARED',len(datasets),'datasets',len(datasets)*8,'matched trials',flush=True)

def run(out):
    manifest=json.loads((out/'manifest.json').read_text());ref,baseline=frozen(out);sched=schedule(manifest['schedule'])
    def verify():assert all(sha(p)==h for p,h in manifest['files'].items())
    verify()
    def one(d):
        try:
            assert sha(d['objective'])==d['objective_sha256'] and sha(d['couplings'])==d['couplings_sha256']
            graph=ref.load_dataset(Path(d['couplings']));n,_,r,c,j=graph
            keys=r*n+c;ix=np.argsort(keys);rev=np.argsort(c*n+r)
            assert len(np.unique(keys))==len(keys) and np.array_equal(keys[ix],(c*n+r)[rev]) and np.array_equal(j[ix],j[rev])
            assert np.all((-128<=j)&(j<=127)) and not np.any(r==c)
            results=[]
            for seed in manifest['solver_seeds']:
                directory=out/'trials'/d['name']/f'seed_{seed}';directory.mkdir(parents=True,exist_ok=False)
                result=simulate(ref,graph,sched,seed,directory/'ising')
                cpu=baseline.execute(out/'snapshot/baseline',ref,Path(d['objective']),directory/'cpu',seed,100)
                assert result['initial_cut']==cpu['initial_cut']
                states=np.load(directory/'ising/states.npz');initial=np.loadtxt(directory/'cpu/initial.txt',dtype=np.int64)
                assert np.array_equal(states['initial'].astype(np.int64)*2-1,initial)
                matched=dict(dataset=d['name'],**result,local_search_cut=cpu['local_search_cut'],greedy_cut=cpu['greedy_one_pass_cut'],
                    local_search_converged=cpu['converged_one_flip_local_optimum'],ratio_to_local_search=result['best_cut']/cpu['local_search_cut'] if cpu['local_search_cut'] else None)
                save(directory/'matched_result.json',matched);results.append(matched)
                print('DONE',d['name'],seed,'best',result['best_cut'],'local',cpu['local_search_cut'],flush=True)
            verify();assert sha(d['objective'])==d['objective_sha256'] and sha(d['couplings'])==d['couplings_sha256']
            save(out/'trials'/d['name']/'audit.json',dict(status='pass',trials=8,iterations=1000,paired_initial_states=True,independent_objectives=True))
            return results
        except Exception as error:
            fail=out/(d['name']+'_failure.json')
            save(fail,dict(status='fail',error=repr(error)));raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        groups=list(pool.map(one,manifest['datasets']))
    verify();all_results=[r for g in groups for r in g]
    save(out/'results.json',all_results);save(out/'complete.json',dict(status='complete',matched_trials=len(all_results),checks='pass',hardware_timing=False))

def main():
    if not __debug__:raise RuntimeError('assertions required')
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--run',action='store_true');args=ap.parse_args()
    out=args.output.resolve();out.relative_to(ROOT/'results')
    if args.run:run(out)
    else:prepare(out)
if __name__=='__main__':main()
