#!/usr/bin/env python3
"""Strict streaming regression on completed RTL gate evidence, unique outputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from evaluate_streaming_endpoints import command, save, ROOT


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--rtl',type=Path,required=True)
    ap.add_argument('--workloads',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--wait-for-rtl',action='store_true')
    a=ap.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.nice(15)
    assert json.loads((a.rtl/'status.json').read_text())['status']=='rtl_gate_complete'
    if a.wait_for_rtl:
        while True:
            gate=json.loads((a.workloads/'status.json').read_text())['status']
            if gate=='pass':break
            if gate!='running':raise RuntimeError(f'RTL gate failed: {gate}')
            save(out/'status.json',dict(status='waiting_for_rtl',gate=str(a.workloads)))
            time.sleep(15)
    assert json.loads((a.workloads/'status.json').read_text())['status']=='pass'
    tasks=[]
    for jobs in (2,8,32):
        case=out/f'jobs{jobs}';case.mkdir()
        shutil.copy2(a.rtl/'dataset.txt',case/'dataset.txt')
        for stall in (0,7,13):
            shutil.copy2(a.rtl/f'jobs{jobs}/streaming/stall{stall}.log',case/f'stall{stall}.log')
            tasks.append((f'jobs{jobs}_stall{stall}',[sys.executable,'scripts/check_streaming_integrated.py',
                'shared','--root',str(case),'--stall',str(stall),'--engines','4','--h0-engines','4',
                '--h1-engines','2','--cross-engines','4','--repeat',str(jobs//2),'--double-buffer']))
    for mx,my,cores in ((2,1,8),(2,2,4),(4,2,4)):
        tag=f'mesh{mx}x{my}_c{cores}';base=a.workloads/tag
        for family in ('dense','sparse','hotspot'):
            for stall in (0,7):
                name=f'{tag}_{family}_stall{stall}'
                tasks.append((name,[sys.executable,'scripts/check_streaming_integrated.py','mesh',
                    '--dataset',str(base/f'{family}.txt'),'--schedule',str(base/f'{family}.schedule'),
                    '--log',str(base/'streaming'/f'{family}_stall{stall}.log'),
                    '--output',str(out/f'{name}.json'),'--h0','2','--cores',str(cores),
                    '--mesh-x',str(mx),'--mesh-y',str(my),'--stall',str(stall)]))
    sources=list((ROOT/'model/azilla_cycle_model').glob('*.py'))+list((ROOT/'rtl/experimental/streaming').glob('*.sv'))
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    save(out/'manifest.json',dict(sources=hashes,tasks=tasks))
    status=dict(status='running',total=len(tasks),passed=[],failed=[])
    save(out/'status.json',status)
    def run(task):
        name,args=task;command(args,out/f'{name}.log');return name
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(run,t):t[0] for t in tasks}
        for f in as_completed(futures):
            try:status['passed'].append(f.result())
            except Exception as e:status['failed'].append(dict(case=futures[f],error=str(e)))
            save(out/'status.json',status)
    status['sources_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    status['status']='pass' if not status['failed'] and status['sources_unchanged'] else 'fail'
    save(out/'status.json',status)
    if status['status']!='pass':raise SystemExit(1)


if __name__=='__main__':main()
