#!/usr/bin/env python3
"""Diagnose saved degree32 failure; no production/reference/RTL modifications."""
import argparse
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import numpy as np
from run_provisional_exact_matrix import ROOT,digest,write


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();out=a.output.resolve();out.relative_to(ROOT/'results');out.mkdir(parents=True,exist_ok=False)
    prior=ROOT/'results/paper_validation_20260907/quality_tuning_diagnostic_v1'
    graph_path=prior/'dense_degree_holdout.npz';z=np.load(graph_path)
    n=int(z['spins']);r=z['rows'];c=z['columns'];j=z['couplings'];degree=np.bincount(r,minlength=n)
    assert n==1024 and len(r)==32768 and np.all(j==-1)
    keys=r*n+c;reverse=c*n+r
    assert len(np.unique(keys))==len(keys) and np.array_equal(np.sort(keys),np.sort(reverse))
    assert np.all(r!=c)
    spec=importlib.util.spec_from_file_location('frozen_reference',prior/'reference.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    neighbors=[c[r==i] for i in range(n)]
    positive=r<c
    sourcehashes={str(p):digest(p) for p in (graph_path,prior/'reference.py')}
    write(out/'plan.json',dict(scope='post-hoc diagnosis, not untouched heldout evaluation',
        graph=sourcehashes,spins=n,edges=len(r)//2,min_degree=int(degree.min()),
        max_degree=int(degree.max()),mean_degree=float(degree.mean()),
        noise='same per-block RTL-compatible LFSR; no RTL changes',
        configurations=[(8,16),(16,16),(24,16),(32,16),(8,0),(8,32)],seeds=[200,201,202]))
    shutil.copy2(Path(__file__),out/'runner_source.py')
    summary=[]
    for inertia,amplitude in [(8,16),(16,16),(24,16),(32,16),(8,0),(8,32)]:
        for seed in (200,201,202):
            state,lf=ref.initial_state(n,seed);prev2=None;hist=[];best=0;beststep=0;firstuniform=None
            saved=list(csv.DictReader((prior/'held_out/selected/dense_degree_holdout'/f'seed_{seed}'/'trajectory.csv').open())) if (inertia,amplitude)==(8,16) else None
            for step in range(1001):
                # Independent objective: count each undirected edge once.
                cut=int(np.count_nonzero(state[r[positive]]!=state[c[positive]]))
                if cut>best:best,beststep=cut,step
                plus=int(state.sum())
                if plus in (0,n) and firstuniform is None:firstuniform=step
                if saved:assert cut==int(saved[step]['cut']),('saved replay mismatch',seed,step)
                if step==1000:
                    hist.append((step,cut,plus,0,0,0));break
                signs=state.astype(np.int64)*2-1
                # True integer accumulation independent of floating bincount.
                coupling=np.zeros(n,dtype=np.int64);np.add.at(coupling,r,j*signs[c])
                assert np.array_equal(coupling,np.bincount(r,weights=j*signs[c],minlength=n).astype(np.int64))
                if seed==200 and step in (0,1,2,499,500,998,999):
                    scalar=np.array([-sum(int(signs[v]) for v in neighbors[i]) for i in range(n)])
                    assert np.array_equal(coupling,scalar)
                amp=amplitude*(999-step)//999
                # Independent scalar LFSR stepping and per-spin bit extraction.
                scalar_lf=np.array([((int(v)<<1)|(((int(v)>>31)^(int(v)>>21)^(int(v)>>1)^int(v))&1))&0xffffffff for v in lf],dtype=np.uint64)
                lf=ref.lfsr_step(lf);assert np.array_equal(lf,scalar_lf)
                noise=np.array([amp*(2*((int(lf[i//32])>>(i%32))&1)-1) for i in range(n)],dtype=np.int64)
                field=inertia*signs+coupling+noise
                assert abs(field).max()<2**31
                nxt=field>=0;flips=int(np.count_nonzero(nxt!=state))
                repeat2=prev2 is not None and np.array_equal(nxt,prev2) and flips>0
                if saved and step+1<1001:assert flips==int(saved[step+1]['flipped_spins'])
                hist.append((step,cut,plus,flips,int(repeat2),amp))
                prev2,state=state,nxt
            name=f'a{inertia}_n{amplitude}_seed{seed}'
            with (out/(name+'.csv')).open('x',newline='') as f:
                w=csv.writer(f);w.writerow(['iteration','cut','plus_spins','next_flips','next_repeat_period2','next_noise']);w.writerows(hist)
            result=dict(name=name,a=inertia,noise=amplitude,seed=seed,best_cut=best,best_iteration=beststep,
                final_cut=hist[-1][1],first_uniform_iteration=firstuniform,
                full_flip_steps=sum(t[3]==n for t in hist),
                saved_cut_and_flip_replay=saved is not None,
                uniform_noise_free_two_cycle_proven=bool(np.all(degree>inertia)))
            summary.append(result);print(json.dumps(result),flush=True)
    assert all(digest(Path(p))==sha for p,sha in sourcehashes.items())
    write(out/'summary.json',summary)
    write(out/'complete.json',dict(status='pass',independent_integer_equations=True,
        interpretation='Uniform states form an exact noise-free two-cycle whenever every degree exceeds a (b=1).'))


if __name__=='__main__':main()
