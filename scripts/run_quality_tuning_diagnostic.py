#!/usr/bin/env python3
"""Small training-only schedule selection with held-out evaluation and repeats.

Does not change RTL, existing campaigns, update equation, or reference source.
Repeated spin patterns under noise are diagnostics, not full dynamical cycles.
"""
import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import shutil
import time
import numpy as np
from run_provisional_exact_matrix import ROOT, digest, write


def schedule(a,b,n,kind):
    return [(a,b,n*(999-i)//999 if kind=='linear' else
             n if i<500 else n*(999-i)//499) for i in range(1000)]


def simulate(ref, graph, sched, seed, output=None):
    n,r,c,j=graph
    state,lfsr=ref.initial_state(n,seed)
    old2=None;best=ref.cut_value(state,r,c,-j);best_i=0
    trace=[(0,best,0,0,0,0)]
    stable=two=maxstable=maxtwo=0
    start=time.perf_counter()
    for step,(a,b,noise) in enumerate(sched,1):
        signs=state.astype(np.int64)*2-1
        field=a*signs+b*np.bincount(r,weights=j*signs[c],minlength=n).astype(np.int64)
        lfsr=ref.lfsr_step(lfsr)
        if noise:
            bits=np.arange(32,dtype=np.uint64)
            field+=noise*(((lfsr[:,None]>>bits)&1).reshape(-1).astype(np.int64)*2-1)
        nxt=field>=0
        flips=int(np.count_nonzero(nxt!=state))
        repeat2=old2 is not None and np.array_equal(nxt,old2) and flips>0
        # Only score consecutive stationary/period-two episodes when noise=0.
        stable=stable+1 if noise==0 and flips==0 else 0
        two=two+1 if noise==0 and repeat2 else 0
        maxstable=max(maxstable,stable);maxtwo=max(maxtwo,two)
        old2,state=state,nxt
        cut=ref.cut_value(state,r,c,-j)
        if cut>best:best,best_i=cut,step
        trace.append((step,cut,flips,int(repeat2),noise,stable))
    result=dict(seed=seed,best_cut=best,best_iteration=best_i,final_cut=trace[-1][1],
        initial_cut=trace[0][1],longest_zero_noise_fixed_episode=maxstable,
        longest_zero_noise_nontrivial_period2_episode=maxtwo,
        spin_pattern_period2_observations=sum(t[3] for t in trace),
        zero_cut_steps=sum(t[1]==0 for t in trace[1:]),wall_seconds=time.perf_counter()-start)
    if output:
        output.mkdir(parents=True,exist_ok=False)
        with (output/'trajectory.csv').open('x',newline='') as f:
            w=csv.writer(f);w.writerow(['iteration','cut','flipped_spins','spin_repeat_period2','noise_amplitude','zero_noise_fixed_episode']);w.writerows(trace)
        write(output/'result.json',result)
        np.save(output/'final_state.npy',np.packbits(state))
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output.resolve();out.relative_to(ROOT/'results');out.mkdir(parents=True,exist_ok=False)
    refpath=out/'reference.py';shutil.copy2(ROOT/'scripts/run_reference_solution_quality.py',refpath)
    spec=importlib.util.spec_from_file_location('frozen_quality_reference',refpath)
    ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    shutil.copy2(Path(__file__),out/'runner_source.py')
    # Independent scalar/dense smoke, signed weights and deterministic noise.
    rr=np.array([0,1,1,2,2,3]);cc=np.array([1,0,2,1,3,2]);jj=np.array([-1,-1,2,2,-3,-3])
    st,lf=ref.initial_state(32,7);dense=np.zeros((32,32),dtype=np.int64);dense[rr,cc]=jj
    for a,b,amp in [(2,1,4)]*8:
        sg=st.astype(np.int64)*2-1;lf=ref.lfsr_step(lf)
        nv=np.array([amp*(2*((int(lf[k//32])>>(k%32))&1)-1) for k in range(32)])
        sparse=a*sg+b*np.bincount(rr,weights=jj*sg[cc],minlength=32).astype(np.int64)+nv
        assert np.array_equal(sparse,a*sg+b*dense.dot(sg)+nv)
        st=sparse>=0
        assert ref.cut_value(st,rr,cc,-jj)==sum(-int(jj[k]) for k in range(0,6,2) if st[rr[k]]!=st[cc[k]])
    write(out/'smoke.json',dict(status='pass',steps=8,signed_dense_equation_and_cut=True))
    inputs={};graphs={}
    quality=ROOT/'results/paper_validation_20260907/broad_solution_quality_v1'
    for name in ['gset_g14','gset_g19','gset_g22','gset_g31']:
        p=quality/name/'maxcut_couplings.txt';meta=json.loads((quality/name/'manifest.json').read_text())['datasets'][0]
        assert digest(p)==meta['sha256']
        n,_,r,c,j=ref.load_dataset(p);graphs[name]=(n,r,c,j);inputs[str(p)]=digest(p)
    # Fresh graph instances to avoid treating previously inspected Gsets as blind tests.
    for name,degree,seed in [('uniform_train',16,91001),('uniform_holdout',16,91002),('dense_degree_holdout',32,91003)]:
        rng=np.random.default_rng(seed);edges=set();n=1024
        while len(edges)<n*degree//2:
            a,b=map(int,rng.integers(n,size=2))
            if a!=b:edges.add((min(a,b),max(a,b)))
        pairs=sorted(edges);r=np.array([x for a,b in pairs for x in (a,b)]);c=np.array([x for a,b in pairs for x in (b,a)]);j=-np.ones(len(r),dtype=np.int64)
        graphs[name]=(n,r,c,j)
        p=out/(name+'.npz');np.savez(p,rows=r,columns=c,couplings=j,spins=n,graph_seed=seed);inputs[str(p)]=digest(p)
    train=['gset_g14','gset_g19','uniform_train']
    held=['gset_g22','gset_g31','uniform_holdout','dense_degree_holdout']
    configs=[(a,b,n,k) for (a,b),n,k in itertools.product([(0,1),(1,2),(2,1),(4,1),(8,1),(16,1),(32,1)],(0,4,16),('linear','hold-half'))]
    write(out/'plan.json',dict(training=train,held_out=held,training_seeds=[100,101,102],
        held_out_seeds=list(range(200,210)),configs=configs,iterations=1000,inputs=inputs,
        selection='mean improvement over initial random cut, normalized by sum positive objective weights; training only',
        caveats=['Gset performance was previously inspected; fresh generated holdouts are independent graph instances.',
                 'Ratio to positive-edge sum is normalization, not optimality or accuracy.',
                 'No large retuned campaign auto-launch; no RTL modifications.']))
    scored=[]
    for a,b,n,k in configs:
        label=f'a{a}_b{b}_n{n}_{k}';sched=schedule(a,b,n,k);scores=[]
        for name in train:
            graph=graphs[name];bound=int(np.maximum(-graph[3],0).sum()//2)
            for seed in (100,101,102):
                result=simulate(ref,graph,sched,seed,out/'training'/label/name/f'seed_{seed}')
                scores.append((result['best_cut']-result['initial_cut'])/bound)
        scored.append(dict(label=label,a=a,b=b,noise=n,kind=k,score=float(np.mean(scores))))
        print(f'TRAIN {label} score={scored[-1]["score"]:.6f}',flush=True)
    winner=max(scored,key=lambda d:d['score']);write(out/'training_scores.json',scored);write(out/'winner.json',winner)
    print('FROZEN '+winner['label'],flush=True)
    for label,sched in [('selected',schedule(winner['a'],winner['b'],winner['noise'],winner['kind'])),('original',schedule(2,1,4,'hold-half'))]:
        for name in held:
            for seed in range(200,210):simulate(ref,graphs[name],sched,seed,out/'held_out'/label/name/f'seed_{seed}')
            print(f'HELD_OUT {label} {name}',flush=True)
    assert all(digest(Path(p))==h for p,h in inputs.items())
    write(out/'complete.json',dict(status='complete',training_trials=len(configs)*9,held_out_trials=80,rtl_verification=False))


if __name__=='__main__':main()
