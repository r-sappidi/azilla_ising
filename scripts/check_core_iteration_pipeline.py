#!/usr/bin/env python3
"""Full-core/Ramulator/router differential with identical external TB phases.

External reset/init/publish/start/done/commit stimulus is replayed, not inferred
from observed datapath timing. Independent phase-controller validation follows.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.core_iteration_pipeline import CoreIterationPipeline
from azilla_cycle_model.hierarchy import DmaCommand
from azilla_cycle_model.fixed import pack_lanes
from azilla_cycle_model.ramulator import RamulatorBackend
from azilla_cycle_model.workload import Geometry
from generate_core_iteration_dataset import coupling


def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)',line.strip()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--stall',type=int,default=0)
    parser.add_argument('--engines',type=int,default=1)
    parser.add_argument('--h0-engines',type=int)
    parser.add_argument('--h1-engines',type=int)
    parser.add_argument('--cross-engines',type=int)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--independent',action='store_true',
                        help='derive all controller phases without RTL timing input')
    parser.add_argument('--compress-idle',action='store_true')
    parser.add_argument('--tag',default='',help='unique evidence suffix for a new model revision')
    parser.add_argument('--full-publication',action='store_true')
    parser.add_argument('--config',type=Path,default=Path('tb/ramulator_128x32.yaml'))
    args=parser.parse_args()
    if args.full_publication and not args.independent:
        parser.error('full publication requires independent controller')
    source_files=[ROOT/'model/azilla_cycle_model'/name for name in (
        'core_iteration.py','core_iteration_pipeline.py','core_iteration_driver.py',
        'core_state_bank.py','compute.py','fixed.py','config.py','memory.py','noc.py','ramulator.py',
        'core_full_controller.py','core_state_publication.py')]
    source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    text=(args.root/f'stall{args.stall}.log').read_text()
    phases={};debug={};rtl_events=[];rtl_memory=[];rtl_state=[];rtl_local=[];rtl_control=[]
    for line in text.splitlines():
        if not line.startswith(('CORE_','PUB_LOCAL','CONTROL_NET')):continue
        f=fields(line);cycle=int(f.get('cycle',0))
        if line.startswith('CORE_PHASE'):
            phases[cycle]={k:int(v) for k,v in f.items()}
        elif line.startswith('CORE_DEBUG'):
            debug[cycle,int(f['core'])]=f
        elif line.startswith('CORE_INT'):
            rtl_events.append((cycle,f['event'],int(f['source']),int(f['dst']),int(f['src'])))
        elif line.startswith('CORE_MEM'):
            rtl_memory.append((cycle,f['event'],int(f['system']),int(f['tag'])))
        elif line.startswith('CORE_STATE'):
            rtl_state.append((cycle,f['event'],int(f['core']),
                              int(f['block']) if f['event']=='request' else int(f['value'],16)))
        elif line.startswith('PUB_LOCAL'):
            rtl_local.append((cycle,f['event'],int(f['h1']),int(f['h0']),int(f['block']),int(f['data'],16)))
        elif line.startswith('CONTROL_NET'):
            rtl_control.append((cycle,f['scope'],int(f['node']),int(f['type']),int(f['block']),int(f['data'],16),int(f['epoch'])))
    if not debug or 'PASS CORE_ITERATION_' not in text:
        raise RuntimeError('missing successful VCS debug trace')
    events=[];memory=[];state=[];local=[];control=[];source_index={0:0,4:1,6:2}
    backend=RamulatorBackend(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',
                             args.config,args.root/'dataset.txt',8,8,timing_only=False)
    pipeline=CoreIterationPipeline(Geometry(2,1,2,2),backend,{s:[] for s in source_index},
        {0:args.h0_engines or args.engines,4:args.h1_engines or args.engines,
         6:args.cross_engines or args.engines},stall_period=args.stall,use_mesh=True,
        compress_idle=args.compress_idle,
        observer=lambda c,e,s,a,b: events.append((c,e,source_index[s],a,b)),
        memory_observer=lambda c,e,s,t:memory.append((c,e,s,t)))
    pipeline.state_observer=lambda *row:state.append(row)
    pipeline.noc_observer=lambda c,s,n,d,f,a:control.append((c,s,n,f.packet_type,f.block_id,f.data,f.epoch)) if a and f.packet_type!=3 else None
    driver=None
    if args.independent:
        from azilla_cycle_model.core_iteration_driver import CoreIterationDriver
        driver_class=CoreIterationDriver
        driver_extra={}
        if args.full_publication:
            from azilla_cycle_model.core_full_controller import CoreFullController
            driver_class=CoreFullController
            driver_extra['records']=[(0,1),(0,2),(0,4)]
        driver=driver_class(pipeline,
            {s:[DmaCommand(0,b,0,b),DmaCommand(b,0,b,0)]*(args.jobs//2)
             for s,b in ((0,1),(4,2),(6,4))},
            diagonal_rows={c:[pack_lanes([coupling(c*32+r,c*32+col) for col in range(32)],8)
                              for r in range(32)] for c in range(8)},
            initial_states={c:0xa5a55a5a^(0x01010101*c) for c in range(8)},
            noise_seeds={c:c+1 for c in range(8)},**driver_extra)
        if args.full_publication:driver.local_observer=lambda *row:local.append(row)
    first_debug=None;dispatch=False
    core_states=['RESET','INIT','IDLE','ACCUMULATE','FINALIZE','WAIT_COMMIT','COMMIT','DONE']
    job_states=['IDLE','STATE_REQ','STATE_WAIT','LOAD','RUN','RETIRE']
    try:
        for cycle in range(max(c for c,_ in debug)+1):
            phase=phases.get(cycle,{})
            for dst,core in pipeline.cores.items():
                f=debug[cycle,dst];o=core.outputs()
                observed=dict(core_state=core_states.index(core.core_state),
                    job_state=job_states.index(core.off_state),job_ready=int(o.job_ready),
                    weight_ready=int(o.weight_ready),diagonal_done=int(core.diagonal_captured),
                    mvm_done=int(core.local_mvm.done),source=core.source,transpose=int(core.transpose),
                    weight_beat=core.off_beat,state=o.state_current)
                expected={k:int(f[k],16 if k=='state' else 10) for k in observed}
                if 'x' not in f['next']:
                    observed['next']=o.state_next;expected['next']=int(f['next'],16)
                if observed!=expected and first_debug is None:
                    first_debug=dict(cycle=cycle,core=dst,rtl=expected,model=observed)
            if driver is not None:
                driver.tick()
                continue
            if phase.get('start'):
                pipeline.queues={s:deque([DmaCommand(0,b,0,b),DmaCommand(b,0,b,0)]*(args.jobs//2))
                                 for s,b in ((0,1),(4,2),(6,4))}
            if phase.get('done'):dispatch=False
            core_inputs=dict(init_start=bool(phase.get('init')),weight_init_valid=bool(phase.get('rowvalid')),
                iter_start=bool(phase.get('start')),partials_done=bool(phase.get('done')),
                commit=bool(phase.get('commit')),coeff_a=1,coeff_b=1)
            per_core={c:dict(init_state=0xa5a55a5a^(0x01010101*c),noise_seed=c+1,
                weight_init_data=pack_lanes([coupling(c*32+phase.get('row',0),c*32+col)
                                            for col in range(32)],8)) for c in range(8)}
            pipeline.tick(dict(dispatch_enable=dispatch,core_inputs=core_inputs,per_core=per_core,
                publish_valid=bool(phase.get('publish')),publish_index=phase.get('block',0)))
            if phase.get('start'):dispatch=True
    finally:
        backend.finalize()
    report=dict(scope=('independent_controller' if args.independent else 'identical_external_phase_stimulus')+
                      '; fullcore/memory/weightmesh/banks; '+('state_publication_and_completion_NoC' if args.full_publication else 'local_snapshot_not_publication_NoC'),
                full_system_certificate=False,cycles=len(debug)//8,first_debug=first_debug,
                checks=dict(core_debug=first_debug is None,events=sorted(events)==sorted(rtl_events),
                            memory=sorted(memory)==sorted(rtl_memory),state=sorted(state)==sorted(rtl_state)),
                counts=dict(rtl_events=len(rtl_events),model_events=len(events),
                            rtl_memory=len(rtl_memory),model_memory=len(memory)))
    report['source_sha256']=source_hashes
    report['checks']['sources_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
                                             for p,h in source_hashes.items())
    if driver is not None:
        report['checks']['independent_completion']=driver.finished
    report['evidence_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
        (args.root/f'stall{args.stall}.log',args.root/'dataset.txt',args.config)}
    if args.full_publication:
        report['checks'].update(publication_local=sorted(local)==sorted(rtl_local),
                                control_network=sorted(control)==sorted(rtl_control))
        report['phase_cycles']={str(k):v for k,v in driver.phase_cycles.items()}
    for label,a,b in [('events',rtl_events,events),('memory',rtl_memory,memory),('state',rtl_state,state),
                      ('local',rtl_local,local),('control',rtl_control,control)]:
        a=sorted(a);b=sorted(b)
        if a!=b:
            i=next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
            report['first_'+label]=dict(index=i,rtl=a[i:i+3],model=b[i:i+3])
    report['matched']=all(report['checks'].values())
    prefix='independent' if args.independent else 'differential'
    if args.compress_idle:prefix+='_compressed'
    if args.tag:prefix+='_'+args.tag
    (args.root/f'{prefix}_stall{args.stall}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['matched']:raise SystemExit(1)


if __name__=='__main__':main()
