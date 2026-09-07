#!/usr/bin/env python3
"""Compare the production-bound cores-only wrapper with complete VCS epochs."""
import argparse
import hashlib
import json
import re
import sys
from contextlib import ExitStack
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
import azilla_cycle_model.shared_fetch_events as shared_events
from azilla_cycle_model.shared_fetch_events import run_full_cores
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset,ScheduledBlock
from azilla_cycle_model.fixed import pack_lanes
from azilla_cycle_model.ramulator import RamulatorBackend
from generate_core_iteration_dataset import coupling
from check_core_iteration_pipeline import fields


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--stall',type=int,default=0)
    ap.add_argument('--engines',type=int,default=1)
    ap.add_argument('--h0-engines',type=int)
    ap.add_argument('--h1-engines',type=int)
    ap.add_argument('--cross-engines',type=int)
    ap.add_argument('--timing-only',action='store_true')
    ap.add_argument('--repeat',type=int,default=1,
                    help='test-only repeated canonical jobs; not a valid unique graph schedule')
    ap.add_argument('--double-buffer',action='store_true')
    ap.add_argument('--config',type=Path,default=Path('tb/ramulator_128x32.yaml'))
    args=ap.parse_args()
    if args.repeat < 1:ap.error('--repeat must be positive')
    log=args.root/f'stall{args.stall}.log'
    text=log.read_text();expected=[];memory=[];state=[];results={};debug={}
    for line in text.splitlines():
        if not line.startswith('CORE_'):continue
        f=fields(line)
        if line.startswith('CORE_INT'):
            expected.append((int(f['cycle']),f['event'],int(f['source']),int(f['dst']),int(f['src'])))
        elif line.startswith('CORE_MEM'):
            memory.append((int(f['cycle']),f['event'],int(f['system']),int(f['tag'])))
        elif line.startswith('CORE_STATE'):
            state.append((int(f['cycle']),f['event'],int(f['core']),
                          int(f['block']) if f['event']=='request' else int(f['value'],16)))
        elif line.startswith('CORE_RESULT'):
            results[int(f['iteration']),int(f['core'])]=int(f['state'],16)
        elif line.startswith('CORE_DEBUG'):
            debug[int(f['cycle']),int(f['core'])]=f
    total=re.search(r'PASS CORE_ITERATION_PUBLICATION cycles=(\d+) jobs=(\d+)',text)
    if not total or int(total[2])!=12*args.repeat:ap.error('unexpected fixture completion count')
    observed=[];observed_memory=[];observed_state=[];first_debug=[]
    source_index={0:0,4:1,6:2}
    model=RamulatorEventPerformanceModel(Geometry(2,1,2,2),BlockOccupancyDataset.load(args.root/'dataset.txt'),
        dataset_path=str(args.root/'dataset.txt'),ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(args.config),config=PerformanceConfig(execution_mode='cores-only',
            h0_mvm_count=args.h0_engines or args.engines,
            h1_mvm_count=args.h1_engines or args.engines,
            cross_mvm_count=args.cross_engines or args.engines,timing_only=True))
    # Test-only payload backend: production remains timing-only, while this
    # wrapper check independently verifies its identical controller arithmetic.
    if not args.timing_only:
        model.backend.finalize()
        model.backend=RamulatorBackend(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',
            args.config,args.root/'dataset.txt',8,8,timing_only=False)
    model.core_event_observer=lambda c,e,s,a,b:observed.append((c,e,source_index[s],a,b))
    model.core_memory_observer=lambda *row:observed_memory.append(row)
    model.core_state_observer=lambda *row:observed_state.append(row)
    cs=['RESET','INIT','IDLE','ACCUMULATE','FINALIZE','WAIT_COMMIT','COMMIT','DONE']
    js=['IDLE','STATE_REQ','STATE_WAIT','LOAD','RUN','RETIRE']
    def observe_debug(p):
        for dst,c in p.cores.items():
            f=debug.get((p.cycle,dst))
            if f is None:
                first_debug.append(dict(cycle=p.cycle,core=dst,missing=True));return
            out=c.outputs()
            values=dict(core_state=cs.index(c.core_state),job_state=js.index(c.off_state),
                job_ready=int(out.job_ready),weight_ready=int(out.weight_ready),diagonal_done=int(c.diagonal_captured),
                mvm_done=int(c.local_mvm.done),source=c.source,transpose=int(c.transpose),weight_beat=c.off_beat,
                state=out.state_current)
            wanted={k:int(f[k],16 if k=='state' else 10) for k in values}
            if 'x' not in f['next']:values['next']=out.state_next;wanted['next']=int(f['next'],16)
            if args.timing_only:
                for k in ('state','next'):
                    values.pop(k,None);wanted.pop(k,None)
            if values!=wanted and not first_debug:
                first_debug.append(dict(cycle=p.cycle,core=dst,model=values,rtl=wanted))
    model.core_debug_observer=observe_debug
    fixture=dict(initial_states={c:0xa5a55a5a^(0x01010101*c) for c in range(8)},
        noise_seeds={c:c+1 for c in range(8)},stall_period=args.stall,timing_only=args.timing_only,
        diagonal_rows={c:[pack_lanes([coupling(c*32+r,c*32+col) for col in range(32)],8)
                          for r in range(32)] for c in range(8)})
    try:
        # Explicit test-only command replay: validate unique graph scheduling
        # normally, then repeat each source queue to saturate its lanes. No
        # production duplicate validation or resource handshake is weakened.
        def repeated(compiler, names):
            def compile_replay(*a, **kw):
                schedule=compiler(*a, **kw)
                for name in names:
                    for queue in getattr(schedule,name):
                        original=list(queue)
                        for _ in range(args.repeat-1):queue.extend(original)
                return schedule
            return compile_replay
        with ExitStack() as stack:
            if args.double_buffer:
                from azilla_cycle_model.shared_fetch_double import DoubleFetchPipeline
                stack.enter_context(patch.object(shared_events,'CoreIterationPipeline',DoubleFetchPipeline))
            if args.repeat>1:
                for name,groups in [('compile_schedule',('h0','h1','cross')),
                                    ('compile_cores_only_schedule',('h0',))]:
                    stack.enter_context(patch.object(shared_events,name,
                        repeated(getattr(shared_events,name),groups)))
            result=run_full_cores(model,[ScheduledBlock(0,b) for b in (1,2,4)],35,
                                  iterations=2,arithmetic_fixture=fixture)
    finally:model.close()
    observed_results={(i,c):v for i,states in enumerate(model.core_final_states) for c,v in states.items()}
    if args.timing_only:
        state=[r if r[1]=='request' else r[:3]+(0,) for r in state]
        observed_state=[r if r[1]=='request' else r[:3]+(0,) for r in observed_state]
    checks=dict(total_cycles=result.total_cycles==int(total[1]),core_debug=not first_debug,
                core_events=sorted(expected)==sorted(observed),memory=sorted(memory)==sorted(observed_memory),
                state=sorted(state)==sorted(observed_state),next_states=results==observed_results)
    if args.timing_only:del checks['next_states']
    report=dict(matched=all(checks.values()),checks=checks,scope='full wrapper; 256 spins; two iterations; specified engines/config',
        rtl_total=int(total[1]),model_total=result.total_cycles,first_debug=first_debug[:1],
        contract=result.core_pipeline_contract,
        wrapper_sha256=hashlib.sha256((ROOT/'model/azilla_cycle_model/shared_fetch_events.py').read_bytes()).hexdigest())
    report['arithmetic_evaluated']=not args.timing_only
    report['repeated_jobs_per_source']=args.repeat
    report['graph_case']=args.repeat==1
    report['double_buffer']=args.double_buffer
    for label,a,b in [('core_events',expected,observed),('memory',memory,observed_memory),('state',state,observed_state)]:
        a,b=sorted(a),sorted(b)
        if a!=b:
            i=next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
            report['first_'+label]=dict(index=i,rtl=a[i:i+2],model=b[i:i+2])
    prefix='wrapper_timing' if args.timing_only else 'wrapper'
    (args.root/f'{prefix}_stall{args.stall}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['matched']:raise SystemExit(1)


if __name__=='__main__':main()
