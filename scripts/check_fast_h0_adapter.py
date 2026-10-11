#!/usr/bin/env python3
"""Isolated complete-run differential and wall-time benchmark, no live edits."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cross-only',action='store_true')
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    snap=ROOT/'results/matched_dense_exact_v3_20260907/snapshot'
    sys.path.insert(0,str(snap/'model'))
    from azilla_cycle_model import exact_events
    from azilla_cycle_model.adapters import H0Adapter
    from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset,ScheduledBlock
    from azilla_cycle_model.performance import PerformanceConfig
    candidate=ROOT/'model/azilla_cycle_model/fast_adapters.py'
    spec=importlib.util.spec_from_file_location('azilla_cycle_model.fast_adapters',candidate)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [candidate,*sorted((snap/'model/azilla_cycle_model').glob('*.py')),
                             snap/'ramulator.yaml',Path(__file__)]}
    results=[]
    cases=[('full_h1_1024',1024,Geometry(1,1,8,256),False),
           ('two_h1_512',512,Geometry(2,1,2,4),False),
           ('two_h1_512_reordered',512,Geometry(2,1,2,4),True)]
    if a.cross_only:
        cases=[('two_h1_sparse512_reordered',512,Geometry(2,1,2,4),True),
               ('four_h1_dense512',512,Geometry(2,2,1,4),False),
               ('four_h1_dense512_reordered',512,Geometry(2,2,1,4),True)]
    for label,n,g,reordered in cases:
        pairs={(i,j) for i in range(n//32) for j in range(i+1,n//32)
               if 'sparse' not in label or (i+j)%3==0}
        ds=BlockOccupancyDataset(g.spin_count,0,pairs)
        records=[ScheduledBlock(*pair) for pair in sorted(pairs)]
        if reordered:random.Random(908).shuffle(records)
        dataset=(ROOT/'results/matched_dense_exact_v3_20260907/points/1024/cir/implicit/timing_input.txt'
                 if g.node_count==1 else
                 ROOT/'results/dense_multi_h1_vcs_v2_20260908/snapshot/tb/datasets/dense_occupancy_512.txt')
        runs={}
        for name,adapter in [('original',H0Adapter),('candidate',mod.FastH0Adapter)]:
            exact_events.H0Adapter=adapter
            m=exact_events.RamulatorEventPerformanceModel(g,ds,dataset_path=str(dataset),
                ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                ramulator_config=str(snap/'ramulator.yaml'),
                config=PerformanceConfig(execution_mode='cir',timing_only=True,
                    h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4,max_cycles=100000),
                mem_lanes=16,ticks_per_cycle=40,idle_tick_modulus=None)
            start=time.monotonic()
            try:
                r=m.run(schedule=records)
                elapsed=time.monotonic()-start
                runs[name]=dict(result=asdict(r),transfers=m.transfers,wall_seconds=elapsed)
            finally:m.close()
            (a.output/f'{label}_{name}.json').write_text(json.dumps(runs[name],indent=2)+'\n')
            print(label,name,'cycles',r.total_cycles,'wall',round(elapsed,3),flush=True)
        checks={key:runs['original'][key]==runs['candidate'][key] for key in ('result','transfers')}
        entry=dict(case=label,checks=checks,passed=all(checks.values()),
                   original_seconds=runs['original']['wall_seconds'],candidate_seconds=runs['candidate']['wall_seconds'],
                   speedup=runs['original']['wall_seconds']/runs['candidate']['wall_seconds'])
        results.append(entry);print(json.dumps(entry),flush=True)
        unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in source_hashes.items())
        report=dict(status='pass' if all(x['passed'] for x in results) and unchanged and len(results)==len(cases)
                    else 'running' if all(x['passed'] for x in results) and unchanged else 'fail',
                    cases=results,sources_unchanged=unchanged,source_sha256=source_hashes,
                    scope='Complete CIR exact-event equivalence; all result fields and accepted transfer timestamps. Not a new RTL certificate or 64K-active validation.')
        (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        if not entry['passed'] or not unchanged:raise SystemExit(1)


if __name__=='__main__':main()
