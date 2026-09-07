#!/usr/bin/env python3
"""Full single-H1 endpoint differential; no trace-driven model stimulus."""
import argparse,hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset
from azilla_cycle_model import core_full_events
from azilla_cycle_model.shared_fetch_double_cli import run_full_cores

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',type=Path,required=True)
    ap.add_argument('--log',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--h0',type=int,required=True);ap.add_argument('--cores',type=int,required=True)
    a=ap.parse_args();text=a.log.read_text();expected={k:[] for k in ('core','memory','state','noc')}
    for line in text.splitlines():
        if not line.startswith('CORE_'):continue
        f=dict(re.findall(r'(\w+)=\s*(\S+)',line));c=int(f['cycle']);e=f['event']
        if line.startswith('CORE_INT'):expected['core'].append((c,e,int(f['source']),int(f['dst']),int(f['src'])))
        elif line.startswith('CORE_MEM'):expected['memory'].append((c,e,int(f['system']),int(f['tag'])))
        elif line.startswith('CORE_STATE'):expected['state'].append((c,e,int(f['core']),int(f['block']) if e=='request' else 0))
        elif line.startswith('CORE_NOC'):expected['noc'].append((c,e))
    end=re.search(r'PASS SHARED_FETCH_SINGLE_H1 total_cycles=(\d+) jobs=(\d+)',text)
    init=re.search(r'FULL_INIT cycle=(\d+)',text)
    if not end or not init:raise RuntimeError('RTL did not complete')
    observed={k:[] for k in expected}
    core_full_events.run_full_cores=run_full_cores
    g=Geometry(1,1,a.h0,a.cores)
    m=RamulatorEventPerformanceModel(g,BlockOccupancyDataset.load(a.dataset),dataset_path=str(a.dataset),
        ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(ROOT/'tb/ramulator_128x32.yaml'),
        config=PerformanceConfig(execution_mode='cores-only',timing_only=True,h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4))
    m.core_event_observer=lambda *r:observed['core'].append(r)
    m.core_memory_observer=lambda *r:observed['memory'].append(r)
    m.core_state_observer=lambda c,e,k,v:observed['state'].append((c,e,k,v if e=='request' else 0))
    try:r=m.run()
    finally:m.close()
    observed['noc']=[(t[0],t[1]) for t in m.transfers]
    checks=dict(initialization=int(init[1])==r.initialization_cycles,total=int(end[1])==r.total_cycles,
                jobs=int(end[2])==r.directed_core_jobs)
    differences={}
    for k in expected:
        x,y=sorted(expected[k]),sorted(observed[k]);checks[k]=x==y
        if x!=y:
            i=next((i for i,(u,v) in enumerate(zip(x,y)) if u!=v),min(len(x),len(y)))
            differences[k]=dict(index=i,rtl=x[i:i+3],model=y[i:i+3],rtl_count=len(x),model_count=len(y))
    report=dict(status='pass' if all(checks.values()) else 'fail',checks=checks,differences=differences,
        geometry=vars(g) if hasattr(g,'__dict__') else dict(h0=a.h0,cores=a.cores),spins=g.spin_count,
        rtl_total=int(end[1]),model_total=r.total_cycles,iteration_cycles=r.iteration_cycles,
        scope='one H1; timing-only; full instantiated endpoints, memory, banked state and completion router',
        model_contract=r.core_pipeline_contract,
        sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.dataset,a.log,ROOT/'tb/shared_fetch_single_h1_tb.sv')})
    a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not all(checks.values()):raise SystemExit(1)
if __name__=='__main__':main()
