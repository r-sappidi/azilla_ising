"""Full cores-only timing path using the independently checked controller.

Initialization may be bulk advanced only before any DRAM request or network
packet. All iteration phases use a single persistent mesh and live Ramulator.
Timing-only mode omits arithmetic payload evaluation, not resource handshakes.
"""
from collections import Counter
from .core_full_controller import CoreFullController
from .core_iteration_pipeline import CoreIterationPipeline
from .hierarchy import DmaCommand
from .performance import PerformanceCounters
from .scheduler import compile_schedule, compile_cores_only_schedule


def run_full_cores(model, records, initialization, *, iterations=1, arithmetic_fixture=None):
    from .exact_events import ExactEventResult, DramPerformanceStats, NodePerformanceStats
    g,cfg=model.geometry,model.config
    if cfg.chiplet_link_latency_cycles:
        raise ValueError('full cores-only chiplet-link latency awaits integrated validation')
    canonical=compile_schedule(g,records)
    directed=compile_cores_only_schedule(g,records)
    total_h0=g.node_count*g.h0_per_h1
    count=total_h0+2*g.node_count
    queues={s:[] for s in range(count)}
    for groups,base in ((canonical.h0,0),(canonical.h1,total_h0),(canonical.cross,total_h0+g.node_count)):
        for endpoint,jobs in enumerate(groups):
            for work in jobs:
                a,b=work.block_a,work.block_b
                queues[base+endpoint].extend((DmaCommand(a,b,a,b),DmaCommand(b,a,b,a)))
    engines={s:cfg.h0_mvm_count if s<total_h0 else
             cfg.h1_mvm_count if s<total_h0+g.node_count else cfg.cross_mvm_count for s in queues}
    counters=PerformanceCounters()
    def observe_noc(cycle,scope,node,direction,flit,accepted):
        model._observe_noc_resource(cycle,scope,node,direction,flit,accepted)
        if accepted:
            model.transfers.append(model._transfer(cycle,scope,node,direction,flit))
            if scope=='inject':
                counters.injected_flits+=1
                counters.type_flits[flit.packet_type]+=1
            elif scope=='eject':counters.ejected_flits+=1
            else:counters.physical_link_flits+=1
        elif scope=='inject':counters.injection_stalls+=1
        elif scope=='eject':counters.ejection_stalls+=1
        else:counters.link_stalls+=1
    fixture=arithmetic_fixture or {}
    p=CoreIterationPipeline(g,model.backend,{s:[] for s in queues},engines,
        ticks_per_cycle=model.ticks_per_cycle,fifo_depth=cfg.fifo_depth,
        interconnect=cfg.interconnect,mem_lanes=model.mem_lanes,
        timing_only=fixture.get('timing_only',arithmetic_fixture is None),compress_idle=True,
        observer=getattr(model,'core_event_observer',None),noc_observer=observe_noc,
        memory_observer=getattr(model,'core_memory_observer',None),
        stall_period=fixture.get('stall_period',0))
    p.state_observer=getattr(model,'core_state_observer',None)
    for block,core in p.cores.items():
        # Exact request-free initialization endpoint: all diagonal rows loaded,
        # init_done asserted, core idle; no iteration MVM has started yet.
        core.core_state=core.IDLE;core.init_done=True;core.weight_beat_count=32
        core.state_current=fixture.get('initial_states',{}).get(block,0)
        core.lfsr_state=fixture.get('noise_seeds',{}).get(block,0)
        core.sram.rows[0]=list(fixture.get('diagonal_rows',{}).get(block,[0]*32))
        core.sram.read_data=core.sram.rows[0][0]
    p.cycle=initialization
    model.backend.tick(initialization*model.ticks_per_cycle)
    controller=CoreFullController(p,queues,records,iterations=iterations,initialized=True,
        coeff_a=fixture.get('coeff_a',1),coeff_b=fixture.get('coeff_b',1),
        noise_amplitude=fixture.get('noise_amplitude',0))
    controller.local_observer=getattr(model,'core_local_state_observer',None)
    while not controller.finished:
        if p.cycle-initialization>=cfg.max_cycles:
            raise TimeoutError('full cores-only iteration exceeded maximum cycles')
        if hasattr(model,'core_debug_observer'):
            model.core_debug_observer(p)
        controller.tick()
    model.core_final_states=controller.driver.next_states
    model.core_pipeline_audit=p.audit
    iteration_cycles=p.cycle-initialization
    counters.cycles=iteration_cycles
    dram=[]
    for s in range(count):
        raw=model.backend.stats(s);jobs=len(queues[s])*iterations
        model._validate_cores_only_dram(s,jobs*32,raw)
        if s<total_h0:level,node,index='h0',s//g.h0_per_h1,s%g.h0_per_h1
        elif s<total_h0+g.node_count:level,node,index='h1',s-total_h0,0
        else:level,node,index='cross',s-total_h0-g.node_count,0
        dram.append(DramPerformanceStats(s,level,node,index,engines[s],jobs,
            raw.accepted,raw.rejected,raw.completed,raw.outstanding,
            raw.average_latency_ticks,raw.latency_max_ticks,
            raw.average_latency_ticks/model.ticks_per_cycle,raw.latency_max_ticks/model.ticks_per_cycle))
    sent=Counter(b//g.blocks_per_h1 for b,_ in directed.state_publications)
    received=Counter(n for _,n in directed.state_publications)
    nodes=tuple(NodePerformanceStats(n,n%g.mesh_x,n//g.mesh_x,
        sum(len(directed.h0[n*g.h0_per_h1+h]) for h in range(g.h0_per_h1))*iterations,
        0,0,sum(len(directed.h0[n*g.h0_per_h1+h]) for h in range(g.h0_per_h1))*iterations,
        sent[n]*iterations,received[n]*iterations,0,0,p.cycle) for n in range(g.node_count))
    resources=model._noc_stats();hops=tuple(sorted(model._hop_histogram.items()))
    model._validate_metric_totals(counters,resources,hops)
    return ExactEventResult(
        accuracy='cycle-structured-unverified' if cfg.interconnect.is_rtl_direct else 'parameterized-interconnect-projection',
        initialization_cycles=initialization,iteration_cycles=iteration_cycles,total_cycles=p.cycle,
        scheduled_h0=directed.directed_jobs*iterations,scheduled_h1=0,scheduled_cross=0,
        counters=counters,average_hops=counters.physical_link_flits/counters.injected_flits if counters.injected_flits else 0,
        hop_histogram=hops,noc_resources=resources,nodes=nodes,dram=tuple(dram),
        interconnect=cfg.interconnect,execution_mode='cores-only',
        unordered_interaction_blocks=len(records),directed_core_jobs=directed.directed_jobs*iterations,
        weight_block_reads=directed.directed_jobs*iterations,logical_weight_blocks_stored=len(records),
        remote_state_packets=len(directed.state_publications)*iterations,core_weight_buffers=1,
        core_pipeline_contract=dict(model='full_single_mvm_iteration_v1',
            one_mvm_per_core=True,canonical_transpose=True,persistent_mesh=True,
            state_gather_words=g.total_blocks*iterations,
            state_cache_write_words=sum(b.writes for b in p.state_banks.values()),
            state_operand_reads=sum(b.accepted_reads for b in p.state_banks.values()),
            state_bank_stalled_lane_cycles=sum(b.stalled_reads for b in p.state_banks.values()),
            phases={str(k):v for k,v in controller.phase_cycles.items()},
            initialization_scope='request-free bulk initialization; full iteration stepped',
            capacity_not_rtl_certificate=True,iterations=iterations,pipeline_audit=p.audit))
