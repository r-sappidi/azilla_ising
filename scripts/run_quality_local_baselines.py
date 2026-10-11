#!/usr/bin/env python3
"""Matched-input simple CPU quality baselines; not accelerator timing or SOTA."""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def write(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2)+'\n')

def execute(binary, ref, dataset, point, seed, sweeps):
    n,_,rows,cols,w = ref.load_dataset(dataset)
    if np.any(rows==cols): raise ValueError('self edges unsupported')
    # Reject asymmetric or duplicate records: objective counts each edge twice.
    keys=rows.astype(np.int64)*n+cols
    ix=np.argsort(keys); reverse=np.argsort(cols.astype(np.int64)*n+rows)
    if len(np.unique(keys)) != len(keys) or not np.array_equal(keys[ix],(cols*n+rows)[reverse]) or not np.array_equal(w[ix],w[reverse]):
        raise ValueError('expected unique symmetric directed records')
    initial,_=ref.initial_state(n,seed)
    signs=initial.astype(np.int64)*2-1
    point.mkdir(parents=True,exist_ok=False)
    np.savetxt(point/'initial.txt',signs,fmt='%d')
    np.savetxt(point/'order.txt',np.random.default_rng(seed).permutation(n),fmt='%d')
    cmd=[str(binary),str(dataset),str(point/'initial.txt'),str(point/'order.txt'),str(sweeps),str(point/'final.txt')]
    write(point/'command.json',cmd)
    t=time.perf_counter()
    with (point/'trajectory.csv').open('x') as f:
        subprocess.run(['/usr/bin/time','-v','-o',str(point/'wall.time'),*cmd],stdout=f,check=True)
    wall=time.perf_counter()-t
    trajectory=list(csv.DictReader((point/'trajectory.csv').open()))
    scores=[int(r['cut']) for r in trajectory]
    final=np.loadtxt(point/'final.txt',dtype=np.int64)
    assert scores[0]==ref.cut_value(initial,rows,cols,w)
    assert scores[-1]==ref.cut_value(final>0,rows,cols,w)
    assert all(a<=b for a,b in zip(scores,scores[1:]))
    gains=np.bincount(rows,weights=w*final[rows]*final[cols],minlength=n).astype(np.int64)
    converged=not np.any(gains>0)
    if int(trajectory[-1]['flips'])==0: assert converged
    result=dict(seed=seed,spins=n,initial_cut=scores[0],greedy_one_pass_cut=scores[1],
        local_search_cut=scores[-1],sweeps=len(trajectory)-1,sweep_cap=sweeps,
        converged_one_flip_local_optimum=converged,terminated_by='no_positive_gain' if int(trajectory[-1]['flips'])==0 else 'sweep_cap',
        local_search_cpu_wall_seconds=float(trajectory[-1]['wall_seconds']),
        greedy_cpu_wall_seconds=float(trajectory[1]['wall_seconds']),process_wall_seconds=wall,
        objective='signed weighted MaxCut: original w = -Ising J',known_optimum=False,
        dataset_sha256=digest(dataset),initial_sha256=digest(point/'initial.txt'),order_sha256=digest(point/'order.txt'),
        audit='initial/final objective, monotonicity, final gains checked',hardware_timing=False)
    write(point/'result.json',result)
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--campaign',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--sweeps',type=int,default=100)
    ap.add_argument('--smoke-only',action='store_true')
    a=ap.parse_args(); out=a.output.resolve(); out.relative_to(ROOT/'results')
    out.mkdir(parents=True,exist_ok=False); snap=out/'snapshot'; snap.mkdir()
    campaign=json.loads(a.campaign.read_text())
    ref_path=next(Path(p) for p in campaign['files'] if p.endswith('run_reference_solution_quality.py'))
    assert digest(ref_path)==campaign['files'][str(ref_path)]
    for src,name in [(Path(__file__),'runner.py'),(ROOT/'scripts/maxcut_local_baseline.cpp','baseline.cpp'),(ref_path,'reference.py'),(a.campaign,'quality_campaign.json')]:
        shutil.copy2(src,snap/name)
    spec=importlib.util.spec_from_file_location('quality_reference',snap/'reference.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    binary=snap/'baseline'
    compile_cmd=['g++','-O3','-std=c++17',str(snap/'baseline.cpp'),'-o',str(binary)]
    subprocess.run(compile_cmd,check=True)
    hashes={str(p):digest(p) for p in snap.iterdir() if p.is_file()}
    write(out/'campaign.json',dict(source=a.campaign.as_posix(),hashes=hashes,compile=compile_cmd,
        compiler=subprocess.check_output(['g++','--version'],text=True),numpy=np.__version__,
        scope='20 identical datasets; Gset 30 seeds; large canonical 10 seeds',
        policies=['one shuffled positive-gain pass','sequential positive-gain local search to convergence or 100 passes'],
        seed_note='exact same reference initial_state; independent NumPy PCG64 visit ordering from same seed',
        quality_only=True,hardware_or_sota_comparison=False))
    # Signed dense random tests exercise all incremental gains after every flip.
    smoke=out/'smoke';smoke.mkdir()
    for seed in range(5):
        graph=smoke/f'graph{seed}.txt';rng=np.random.default_rng(seed)
        mat=rng.integers(-4,5,size=(32,32));mat=np.triu(mat,1);mat+=mat.T
        with graph.open('x') as f:
            f.write('32 0\n')
            for i,j in zip(*np.nonzero(mat)): f.write(f'{i+1} {j+1} {mat[i,j]}\n')
        execute(binary,ref,graph,smoke/f'seed{seed}',seed,a.sweeps)
    write(out/'smoke_pass.json',dict(tests=5,audit='every-flip independent full objective and all gains; final Python check'))
    if a.smoke_only: return
    results=[]
    for group_index,group in enumerate(campaign['datasets']):
        seeds=campaign['published_solver_seeds'] if group_index==0 else campaign['large_solver_seeds']
        for d in group:
            source=ROOT/d['dataset'];assert digest(source)==d['sha256']
            for seed in seeds:
                r=execute(binary,ref,source,out/d['name']/f'seed_{seed}',seed,a.sweeps)
                r['dataset']=d['name'];results.append(r)
                with (out/'summary.csv').open('w') as f:
                    writer=csv.DictWriter(f,fieldnames=list(r));writer.writeheader();writer.writerows(results)
                print(f"DONE {d['name']} seed={seed} local={r['local_search_cut']} converged={r['converged_one_flip_local_optimum']}",flush=True)
            assert digest(source)==d['sha256']
    assert all(digest(p)==h for p,h in hashes.items())
    write(out/'complete.json',dict(trials=len(results),quality_only=True,hashes_unchanged=True))

if __name__=='__main__': main()
