#!/usr/bin/env python3
"""Paired dense occupancy timing; frozen exact models, never calibrated timing.

Dense mathematical weights are -1 except the zero diagonal. Timing payloads
are elided, not represented as a sparse graph with one edge per block.
Small explicit complete graphs check that this encoding preserves timing.
"""
import argparse
import concurrent.futures
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'single_fetch_two_slot_cir_routes_v3'
SIZES = (1024,4096,8192,16384,32768,65536,131072,262144)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def save(p, value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(p)

def worker(out,n,mode,encoding,smoke):
    manifest=json.loads((out/'manifest.json').read_text())
    assert all(sha(p)==v for p,v in manifest['sources'].items())
    sys.path.insert(0,str(out/'snapshot/model'))
    from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset
    from azilla_cycle_model.performance import PerformanceConfig
    from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
    from azilla_cycle_model.metrics import write_exact_event_metrics
    if mode=='cores-only':
        from azilla_cycle_model import core_full_events
        from azilla_cycle_model.shared_fetch_double_cli import run_full_cores
        core_full_events.run_full_cores=run_full_cores
    g=Geometry(2,1,1,2) if smoke else Geometry(2 if n>=131072 else 1,2 if n>=262144 else 1,8,256)
    case=out/('smoke' if smoke else 'points')/str(n)/mode/encoding
    case.mkdir(parents=True,exist_ok=False)
    dataset=case/'timing_input.txt'
    with dataset.open('x') as f:
        f.write(f'{g.spin_count} 0\n')
        if encoding=='explicit':
            assert smoke
            for i in range(n):
                for j in range(n):
                    if i!=j:f.write(f'{i+1} {j+1} -1\n')
    blocks=n//32;expected=blocks*(blocks-1)//2
    pairs={(a,b) for a in range(blocks) for b in range(a+1,blocks)}
    if encoding=='explicit':
        ds=BlockOccupancyDataset.load(dataset)
        assert ds.active_block_pairs()==pairs
    else:ds=BlockOccupancyDataset(g.spin_count,0,pairs)
    save(case/'input.json',dict(problem_spins=n,allocated_spins=g.spin_count,
        geometry=asdict(g),unordered_blocks=expected,undirected_edges=n*(n-1)//2,
        mapping='identity, first N vertices active; remaining capacity isolated',
        workload='complete graph, J=-1 off diagonal; timing-only payload elision',
        diagonal='local block timing retained; padding cores remain instantiated',
        dataset_file_role='DPI header only for implicit occupancy; NOT an arithmetic dataset',
        fetch_policy=POLICY if mode=='cores-only' else 'cir',
        schedule_definition='all unordered pairs 0 <= a < b < N/32; default owner allocator',
        paper_ready=False,geometry_rtl_verified=False))
    cfg=PerformanceConfig(execution_mode=mode,timing_only=True,h0_mvm_count=4,
        h1_mvm_count=2,cross_mvm_count=4,max_cycles=1_000_000_000)
    m=RamulatorEventPerformanceModel(g,ds,dataset_path=str(dataset),
        ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(out/'snapshot/ramulator.yaml'),config=cfg,
        mem_lanes=16,ticks_per_cycle=40,idle_tick_modulus=None)
    began=time.monotonic()
    try:r=m.run()
    finally:m.close()
    assert sum(d.scheduled_jobs for d in r.dram)==expected
    assert sum(d.accepted_requests for d in r.dram)==32*expected
    assert all(d.accepted_requests==d.completed_requests and not d.outstanding_requests for d in r.dram)
    assert r.counters.injected_flits==r.counters.ejected_flits
    if mode=='cores-only':
        assert r.core_pipeline_contract['fetch_policy']==POLICY
        assert r.weight_block_reads==expected and r.directed_core_jobs==2*expected
    paths=write_exact_event_metrics(case/'metrics',r,g)
    assert all(sha(p)==v for p,v in manifest['sources'].items())
    save(case/'audit.json',dict(status='conservation-pass',problem_spins=n,
        allocated_spins=g.spin_count,mode=mode,canonical_blocks=expected,
        initialization_cycles=r.initialization_cycles,iteration_cycles=r.iteration_cycles,
        total_cycles=r.total_cycles,wall_seconds=time.monotonic()-began,
        files={str(p):sha(p) for p in paths.values()},
        transfer_sha256=hashlib.sha256(repr(m.transfers).encode()).hexdigest() if smoke else None,
        accuracy=r.accuracy,paper_ready=False,geometry_rtl_verified=False))
    print('PASS',n,mode,encoding,r.iteration_cycles,flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--worker',action='store_true');ap.add_argument('--size',type=int)
    ap.add_argument('--mode',choices=('cir','cores-only'));ap.add_argument('--encoding',default='implicit')
    ap.add_argument('--smoke',action='store_true');a=ap.parse_args();out=a.output.resolve()
    if a.worker:return worker(out,a.size,a.mode,a.encoding,a.smoke)
    out.relative_to(ROOT/'results');out.mkdir(parents=True,exist_ok=False)
    base=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
    for count in (1,4,16):
        for stall in (0,7,13):
            d=json.loads((base/f'shared_repeat{count}/wrapper_stall{stall}.json').read_text())
            assert d['matched'] and d['double_buffer'] and all(d['checks'].values())
    frozen=json.loads((base/'manifest.json').read_text())
    assert all(sha(base/'snapshot'/p)==v for p,v in frozen['sources'].items())
    shutil.copytree(base/'snapshot/model',out/'snapshot/model',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(base/'snapshot/tb/ramulator_128x32.yaml',out/'snapshot/ramulator.yaml')
    shutil.copy2(__file__,out/'runner.py')
    sources={str(p):sha(p) for p in (out/'snapshot').rglob('*') if p.is_file()}
    for p in (out/'runner.py',ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',ROOT/'third_party/ramulator2/libramulator.so'):
        sources[str(p)]=sha(p)
    # Frozen runner is launched with an explicit repository root through argv environment.
    save(out/'manifest.json',dict(sources=sources,sizes=SIZES,modes=['cir','cores-only'],
        workers=8,policy=POLICY,engine_counts=[4,2,4],memory_width=128,
        baseline_validation=str(base),dense_encoding_gate='pending',
        scope='one complete dense iteration; exact timing projection, not dense RTL certification',
        old_dense_run='PID73614 and serial launcher260860 terminated; artifacts retained'))
    env=dict(os.environ,AZILLA_DENSE_REPOSITORY=str(ROOT),OMP_NUM_THREADS='1',
        OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    def launch(n,mode,encoding='implicit',smoke=False):
        cmd=[sys.executable,str(out/'runner.py'),'--output',str(out),'--worker',
             '--size',str(n),'--mode',mode,'--encoding',encoding]+(['--smoke'] if smoke else [])
        tag=f'{"smoke" if smoke else "dense"}_{n}_{mode}_{encoding}'
        save(out/'commands'/f'{tag}.json',cmd)
        with (out/f'{tag}.log').open('x') as log:
            p=subprocess.Popen(['/usr/bin/time','-v','-o',str(out/f'{tag}.time'),*cmd],
                cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            save(out/'status'/f'{tag}.json',dict(status='running',pid=p.pid,command=cmd))
            rc=p.wait()
        save(out/'status'/f'{tag}.json',dict(status='pass' if rc==0 else 'fail',returncode=rc))
        if rc:raise RuntimeError(f'{tag} failed, rc={rc}')
    save(out/'status.json',dict(status='dense-encoding-gate',completed=0,total=16))
    for mode in ('cir','cores-only'):
        for encoding in ('explicit','implicit'):launch(128,mode,encoding,True)
        p=out/'smoke/128'/mode
        left=json.loads((p/'explicit/audit.json').read_text());right=json.loads((p/'implicit/audit.json').read_text())
        for k in ('initialization_cycles','iteration_cycles','total_cycles','canonical_blocks','transfer_sha256'):
            assert left[k]==right[k],(mode,k)
    save(out/'encoding_gate.json',dict(status='pass',spins=128,geometry='2H1 x 1H0 x 2cores',
        checks='complete explicit graph vs implicit dense occupancy: cycles, transfers, request conservation',
        scope='software encoding equivalence; not new RTL certification'))
    lock=threading.Lock();large=threading.Semaphore(1);completed=[];reservations={}
    def point(n,mode):
        key=f'{n}_{mode}';estimate=2*1024**3+(n//32)*(n//32-1)//2*2000
        token=large if n>=131072 else threading.Semaphore(1)
        with token:
            while True:
                available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
                with lock:
                    # Conservative reservations avoid simultaneous startup overcommit.
                    if available-sum(reservations.values())>estimate+24*1024**3:
                        reservations[key]=estimate;break
                save(out/'status'/f'admission_{key}.json',dict(status='waiting-memory',estimated_bytes=estimate))
                time.sleep(30)
            try:
                launch(n,mode)
                with lock:
                    completed.append(key);save(out/'status.json',dict(status='running',completed=completed,total=16))
            except Exception as e:
                save(out/'status'/f'failure_{key}.json',dict(error=repr(e)))
                raise
            finally:
                with lock:reservations.pop(key)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(point,n,mode) for n in SIZES for mode in ('cir','cores-only')]
        for f in concurrent.futures.as_completed(futures):f.result()
    save(out/'status.json',dict(status='complete',completed=completed,total=16,paper_ready=False))

if __name__=='__main__':
    if os.environ.get('AZILLA_DENSE_REPOSITORY'):ROOT=Path(os.environ['AZILLA_DENSE_REPOSITORY'])
    main()
