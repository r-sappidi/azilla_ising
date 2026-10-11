#!/usr/bin/env python3
"""Complete original/counter-core v3 comparisons, including internal events."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def serial(value):
    if isinstance(value,dict):return {str(k):serial(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [serial(v) for v in value]
    return value


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cross-only',action='store_true')
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    snap=ROOT/'results/matched_dense_exact_v3_20260907/snapshot'
    sys.path.insert(0,str(snap/'model'))
    from azilla_cycle_model import exact_events,core_iteration_pipeline,core_full_events
    from azilla_cycle_model.core_iteration import CoreIteration
    from azilla_cycle_model.shared_fetch_double_cli import run_full_cores
    from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset
    from azilla_cycle_model.performance import PerformanceConfig
    candidate=ROOT/'model/azilla_cycle_model/fast_core.py'
    spec=importlib.util.spec_from_file_location('azilla_cycle_model.fast_core',candidate)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    files=[candidate,ROOT/'model/azilla_cycle_model/fast_shared_fetch_cli.py',Path(__file__),
           *sorted((snap/'model/azilla_cycle_model').glob('*.py')),snap/'ramulator.yaml',
           ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so']
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    cases=[('full_h1_dense1024',1024,Geometry(1,1,8,256),0),
           ('two_h1_dense512',512,Geometry(2,1,2,4),0),
           ('two_h1_dense512_stall7',512,Geometry(2,1,2,4),7),
           ('two_h1_sparse512',512,Geometry(2,1,2,4),0)]
    if a.cross_only:
        cases=[('two_h1_dense512_stall13',512,Geometry(2,1,2,4),13),
               ('two_h1_sparse512_stall7',512,Geometry(2,1,2,4),7),
               ('four_h1_dense512',512,Geometry(2,2,1,4),0)]
    rows=[]
    for label,n,g,stall in cases:
        pairs={(i,j) for i in range(n//32) for j in range(i+1,n//32)
               if 'sparse' not in label or (i+j)%3==0}
        ds=BlockOccupancyDataset(g.spin_count,0,pairs)
        dataset=(ROOT/'results/matched_dense_exact_v3_20260907/points/1024/cir/implicit/timing_input.txt'
                 if g.node_count==1 else ROOT/'results/dense_multi_h1_vcs_v2_20260908/snapshot/tb/datasets/dense_occupancy_512.txt')
        runs={}
        for name,core in [('original',CoreIteration),('candidate',mod.FastCoreIteration)]:
            core_iteration_pipeline.CoreIteration=core
            def run(model,records,initialization,**kwargs):
                return run_full_cores(model,records,initialization,
                    arithmetic_fixture=dict(timing_only=True,stall_period=stall),**kwargs)
            core_full_events.run_full_cores=run
            m=exact_events.RamulatorEventPerformanceModel(g,ds,dataset_path=str(dataset),
                ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                ramulator_config=str(snap/'ramulator.yaml'),config=PerformanceConfig(
                    execution_mode='cores-only',timing_only=True,h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4,max_cycles=200000),
                mem_lanes=16,ticks_per_cycle=40,idle_tick_modulus=None)
            events={k:[] for k in ('core','memory','state')}
            m.core_event_observer=lambda *r:events['core'].append(r)
            m.core_memory_observer=lambda *r:events['memory'].append(r)
            m.core_state_observer=lambda *r:events['state'].append(r)
            start=time.monotonic()
            try:
                result=m.run();elapsed=time.monotonic()-start
                assert result.core_pipeline_contract['fetch_policy']=='single_fetch_two_slot_cir_routes_v3'
                runs[name]=dict(result=asdict(result),transfers=m.transfers,events=events,wall_seconds=elapsed)
            finally:m.close()
            (a.output/f'{label}_{name}.json').write_text(json.dumps(serial(runs[name]))+'\n')
            print(label,name,result.total_cycles,round(elapsed,3),flush=True)
        checks={k:runs['original'][k]==runs['candidate'][k] for k in ('result','transfers','events')}
        rows.append(dict(case=label,passed=all(checks.values()),checks=checks,
            original_seconds=runs['original']['wall_seconds'],candidate_seconds=runs['candidate']['wall_seconds'],
            speedup=runs['original']['wall_seconds']/runs['candidate']['wall_seconds']))
        unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
        passed=all(r['passed'] for r in rows) and unchanged
        report=dict(status=('pass' if len(rows)==len(cases) else 'running') if passed else 'fail',
                    cases=rows,sources_unchanged=unchanged,source_sha256=hashes,
                    scope='Timing-only v3 model equivalence at listed cases; not large-geometry RTL certification')
        (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
        if not passed:raise SystemExit(1)


if __name__=='__main__':main()
