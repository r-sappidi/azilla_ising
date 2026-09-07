#!/usr/bin/env python3
"""Paired large-geometry exact-model windows, never a new RTL certificate.

Runs frozen reference and opt-in optimized models in separate processes because
Ramulator owns process-global state. Worker-local instrumentation observes real
requests/responses and all offered NoC events, then stops at a matching
pre-backend-tick boundary after the requested active window. Full completion
before that boundary is accepted only when the complete results match.
"""
from __future__ import annotations
import argparse
from collections import Counter, deque
import dataclasses
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OMIT = {'backend', 'library', 'pipeline', '_cached_output', '_cached_outputs',
        '_cached_node_state', '_total_identity', '_state_next', '_last_row',
        '_start_row', '_empty_systems'}


def canonical(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))
                if k not in OMIT and not callable(v)}
    if isinstance(value, (list, tuple, deque)):
        return [canonical(v) for v in value]
    if isinstance(value, set):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    # Match a counter-derived MVM to the reference's explicitly stepped rows.
    if hasattr(value, 'active') and hasattr(value, 'request_row') and hasattr(value, 'result'):
        return {k: canonical(getattr(value, k)) for k in
                ('config', 'timing_only', 'active', 'process_row', 'request_row',
                 'read_valid', 'done', 'transpose_mode', 'result') if hasattr(value, k)}
    if dataclasses.is_dataclass(value):
        return {f.name: canonical(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, '__dict__'):
        return canonical(vars(value))
    raise TypeError(f'No canonical representation for {type(value)}')


def encoded(value):
    return json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()


def hashes(model_path):
    return {str(p.relative_to(model_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(model_path.rglob('*.py'))}


class WindowComplete(Exception):
    pass


def worker(args):
    job = json.loads(args.job.read_text())
    model_path = Path(args.model_path).resolve()
    sys.path.insert(0, str(model_path))
    before = hashes(model_path)
    if args.fast:
        from azilla_cycle_model.fast_cli import activate
        activate()
    from azilla_cycle_model import exact_events
    from azilla_cycle_model.performance import PerformanceConfig
    from azilla_cycle_model.workload import Geometry, BlockOccupancyDataset
    geometry = Geometry(**job['signature']['geometry'])
    config_path=Path(job['signature']['ramulator_config'])
    if hashlib.sha256(config_path.read_bytes()).hexdigest()!=job['signature']['ramulator_config_sha256']:
        raise RuntimeError('Ramulator configuration differs from validation envelope')
    args.output.mkdir(parents=True, exist_ok=False)
    streams = {}
    counts = Counter()
    digests = {}
    def record(kind, *values):
        if kind not in streams:
            streams[kind] = gzip.open(args.output / (kind+'.jsonl.gz'), 'wb')
            digests[kind] = hashlib.sha256()
        line = encoded(values)+b'\n'
        streams[kind].write(line)
        digests[kind].update(line)
        counts[kind] += 1
    state = dict(ticks=0, active_ticks=0, first_send=None, window_start=None)
    base_backend = exact_events.RamulatorBackend
    class ObservedBackend(base_backend):
        def tick(self, count):
            if count == 40:
                if (state['first_send'] is not None and state['window_start'] is None
                        and state['active_ticks'] >= geometry.blocks_per_h1+128):
                    state['window_start'] = state['active_ticks']
                    print(f'WINDOW_START active_tick={state["active_ticks"]} memory_ticks={state["ticks"]}',flush=True)
                if (state['window_start'] is not None and
                        state['active_ticks']-state['window_start'] >= args.cycles):
                    raise WindowComplete()
                state['active_ticks'] += 1
            super().tick(count)
            state['ticks'] += count
        def send(self, system, address, tag):
            result = super().send(system, address, tag)
            if state['first_send'] is None:
                state['first_send'] = state['active_ticks']
            record('dram_send', state['ticks'], system, address, tag, result)
            return result
        def pop(self, system):
            result = super().pop(system)
            if result is not None:
                record('dram_response', state['ticks'], system, result)
            return result
    exact_events.RamulatorBackend = ObservedBackend
    engines = job['signature']['engines']
    model = exact_events.RamulatorEventPerformanceModel(
        geometry, BlockOccupancyDataset.load(job['dataset']),
        dataset_path=job['dataset'], ramulator_library=str(args.library.resolve()),
        ramulator_config=job['signature']['ramulator_config'],
        config=PerformanceConfig(h0_mvm_count=engines[0], h1_mvm_count=engines[1],
            cross_mvm_count=engines[2], timing_only=True,
            execution_mode=job['signature']['mode']))
    original_observer = model._observe_noc_resource
    def noc(*values):
        record('noc_offered', *values)
        original_observer(*values)
    model._observe_noc_resource = noc
    # Capture hierarchy dispatch, completed compute, every accepted partial,
    # and bank grant/stall decisions, without modifying any model transition.
    for target, node in list(model.local_nodes.items()) + [
            (f'cross:{i}', cross.node) for i, cross in enumerate(model.cross_nodes)]:
        original_tick = node.tick
        def hierarchy_tick(*, _node=node, _target=str(target), _tick=original_tick, **kwargs):
            outputs = _node.outputs()
            cycle = state['ticks']
            commands = kwargs.get('commands') or [None]*len(_node.engines)
            ready = kwargs.get('partial_ready') or [False]*len(_node.engines)
            requests = []
            for lane, engine in enumerate(_node.engines):
                if commands[lane] is not None and outputs.command_ready[lane]:
                    record('hierarchy_dispatch', cycle, _target, lane, commands[lane])
                if engine.state == engine.COMPUTE and engine.mvm.done:
                    record('hierarchy_completion', cycle, _target, lane,
                           engine.result_blocks[engine.result_write_slot])
                if outputs.partials[lane].valid:
                    record('hierarchy_partial_offered', cycle, _target, lane,
                           outputs.partials[lane], bool(ready[lane]))
                if engine.state == engine.FETCH:
                    command = engine.slot_commands[engine.active_slot]
                    if command is not None:
                        if not engine.state_a_valid and not engine.state_a_pending:
                            requests.append((2*lane,command.state_a_index))
                        if not engine.state_b_valid and not engine.state_b_pending:
                            requests.append((2*lane+1,command.state_b_index))
            responses = _node._state_responses[:]
            result = _tick(**kwargs)
            if requests or responses:
                accepted = {lane for lane,_ in _node._state_responses}
                record('hierarchy_banks',cycle,_target,
                       [(lane,index,lane in accepted) for lane,index in requests],responses)
            return result
        node.tick = hierarchy_tick
    for attribute, kind in (('core_event_observer', 'core_jobs'),
                            ('core_state_observer', 'core_banks'),
                            ('core_local_state_observer', 'core_publication'),
                            ('core_memory_observer', 'core_memory')):
        setattr(model, attribute, lambda *values, kind=kind: record(kind, *values))
    pipeline = None
    def capture_core(p):
        nonlocal pipeline
        if pipeline is None:
            for index, bank in p.state_banks.items():
                original_tick = bank.tick
                def bank_tick(requests, *, _bank=bank, _index=index,
                              _tick=original_tick, **kwargs):
                    if requests or _bank.responses or kwargs.get('write') is not None:
                        record('core_bank_grants', p.cycle, _index, requests,
                               _bank.ready(requests), _bank.responses, kwargs)
                    return _tick(requests, **kwargs)
                bank.tick = bank_tick
        pipeline = p
    model.core_debug_observer = capture_core
    start = time.monotonic()
    completed = False
    result = None
    frame_state = {}
    try:
        result = model.run()
        completed = True
    except WindowComplete as exc:
        tb = exc.__traceback__
        while tb:
            local = tb.tb_frame.f_locals
            if tb.tb_frame.f_code.co_filename.endswith('/exact_events.py'):
                for name in ('cycle', 'phase', 'quiet', 'dispatcher', 'counters',
                             'done_pending', 'done_received', 'local_done', 'cross_done',
                             'node_completion', 'compute_start', 'initialization'):
                    if name in local:
                        frame_state[name] = canonical(local[name])
            if tb.tb_frame.f_code.co_filename.endswith('/core_full_events.py'):
                if 'controller' in local:
                    frame_state['controller'] = canonical(local['controller'])
            tb = tb.tb_next
    finally:
        # Capture before finalizing the live C++ backend.
        stats = [model.backend.stats(s) for s in range(model.backend.system_count)]
        final = dict(clock=state, complete=completed, result=result,
                     dram_stats=stats, frame=frame_state,
                     transfers=model.transfers, noc_resources=model._noc_resources,
                     hop_histogram=model._hop_histogram,
                     local_nodes=model.local_nodes, cross_nodes=model.cross_nodes,
                     frontends=model.frontends, mesh=model.mesh,
                     h0_adapters=model.h0_adapters, h1_adapters=model.h1_adapters,
                     injection_arbiters=model.injection_arbiters,
                     core_pipeline=pipeline)
        raw = encoded(final)
        with gzip.open(args.output/'final_state.json.gz', 'wb') as f:
            f.write(raw)
        model.close()
        for f in streams.values():
            f.close()
    summary = dict(status='complete' if completed else 'window', clock=state,
        active_window_cycles=(state['active_ticks']-state['window_start'])
            if state['window_start'] is not None else 0,
        wall_seconds=time.monotonic()-start,
        final_sha256=hashlib.sha256(raw).hexdigest(),
        event_counts=dict(counts), event_sha256={k: v.hexdigest() for k,v in digests.items()},
        source_sha256=before, sources_unchanged=before == hashes(model_path),
        dram_accepted=sum(s.accepted for s in stats), dram_completed=sum(s.completed for s in stats),
        level_accepted={'h0':sum(s.accepted for s in stats[:geometry.node_count*geometry.h0_per_h1]),
                        'h1':sum(s.accepted for s in stats[geometry.node_count*geometry.h0_per_h1:geometry.node_count*(geometry.h0_per_h1+1)]),
                        'cross':sum(s.accepted for s in stats[geometry.node_count*(geometry.h0_per_h1+1):])},
        completed_compute_jobs=pipeline.completed if pipeline is not None else counts['hierarchy_completion'],
        scope='Optimization equivalence window, not expanded RTL validation')
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2), flush=True)


def fixture(signature, path):
    g = signature['geometry']
    nodes = g['mesh_x']*g['mesh_y']
    cores, h0s = g['cores_per_h0'], g['h0_per_h1']
    per_h1 = cores*h0s
    pairs = set()
    def add(a,b):
        if a != b:
            pairs.add(tuple(sorted((a,b))))
    # Eight operand blocks alias SRAM bank zero to force real arbitration.
    offsets = [8*i for i in range(8)]
    for node in range(nodes):
        for h in range(h0s):
            blocks = [node*per_h1+h*cores+i for i in offsets]
            for a,b in itertools.combinations(blocks,2):
                add(a,b)
            if h+1 < h0s:
                for a,b in itertools.product(offsets,repeat=2):
                    add(node*per_h1+h*cores+a,node*per_h1+(h+1)*cores+b)
        if node+1 < nodes:
            for a,b in itertools.product(offsets,repeat=2):
                add(node*per_h1+a,(node+1)*per_h1+b)
    with path.open('x') as f:
        f.write(f'{nodes*per_h1*32} 0\n')
        for a,b in sorted(pairs):
            f.write(f'{a*32+1} {b*32+2} -1\n')
    return len(pairs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker', action='store_true')
    p.add_argument('--fast', action='store_true')
    p.add_argument('--job', type=Path)
    p.add_argument('--model-path', type=Path)
    p.add_argument('--cycles', type=int, default=2000)
    p.add_argument('--library', type=Path, default=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so')
    p.add_argument('--envelope', type=Path, default=ROOT/'results/paper_validation_20260907/optimized_queue_inventory_v3/validation_envelope.json')
    p.add_argument('--optimized-snapshot', type=Path, default=ROOT/'results/paper_validation_20260907/fast_exact_candidate_v2/model')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--indices', help='Comma-separated envelope indices; default all')
    args=p.parse_args()
    if args.worker:
        return worker(args)
    envelope=json.loads(args.envelope.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    oracle=Path(envelope['oracle_snapshot'])/'model'
    signatures=envelope['required_signatures']
    indices=list(map(int,args.indices.split(','))) if args.indices else list(range(len(signatures)))
    run_manifest=dict(envelope=str(args.envelope), oracle=str(oracle),
        optimized=str(args.optimized_snapshot), cycles=args.cycles, indices=indices,
        oracle_hashes=hashes(oracle), optimized_hashes=hashes(args.optimized_snapshot),
        checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        library_sha256=hashlib.sha256(args.library.read_bytes()).hexdigest(),
        scope='Sampled large-geometry optimization equivalence; no additional RTL claims')
    (args.output/'manifest.json').write_text(json.dumps(run_manifest,indent=2)+'\n')
    comparisons=[]
    for index in indices:
        signature=signatures[index]
        case=args.output/f'case_{index:03d}'
        case.mkdir()
        dataset=case/'dataset.txt'
        blocks=fixture(signature,dataset)
        job=dict(signature=signature,dataset=str(dataset.resolve()),occupied_blocks=blocks)
        job_path=case/'job.json'
        job_path.write_text(json.dumps(job,indent=2)+'\n')
        print(f'START case={index} mode={signature["mode"]} geometry={signature["geometry"]} engines={signature["engines"]}',flush=True)
        running=[]
        for tag,model_path,fast in [('reference',oracle,False),('optimized',args.optimized_snapshot,True)]:
            command=[sys.executable,str(Path(__file__).resolve()),'--worker','--job',str(job_path.resolve()),
                     '--model-path',str(model_path.resolve()),'--cycles',str(args.cycles),
                     '--library',str(args.library.resolve()),'--output',str((case/tag).resolve())]
            if fast:command.append('--fast')
            log=(case/(tag+'.log')).open('x')
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                                     env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            running.append((tag,process,log))
        codes={}
        while running:
            for entry in running[:]:
                tag,process,log=entry
                if process.poll() is not None:
                    codes[tag]=process.returncode;log.close();running.remove(entry)
            if running:time.sleep(5)
        if any(codes.values()):
            raise SystemExit(f'Worker failed case={index}: {codes}; inspect case logs')
        ref=json.loads((case/'reference/summary.json').read_text())
        opt=json.loads((case/'optimized/summary.json').read_text())
        checks={key:ref[key]==opt[key] for key in
                ('status','clock','active_window_cycles','final_sha256','event_counts','event_sha256')}
        checks['sources_unchanged']=ref['sources_unchanged'] and opt['sources_unchanged']
        checks['real_memory_work']=min(ref['dram_accepted'],ref['dram_completed'])>0
        checks['completed_compute_work']=ref['completed_compute_jobs']>0
        levels=('h0','h1','cross') if signature['geometry']['mesh_x']*signature['geometry']['mesh_y']>1 else ('h0','h1')
        checks['all_resident_levels_exercised']=all(ref['level_accepted'][level]>0 for level in levels)
        checks['duration']=ref['status']=='complete' or ref['active_window_cycles']>=args.cycles
        comparison=dict(index=index,signature=signature,checks=checks,
            status='pass' if all(checks.values()) else 'fail',
            reference_wall=ref['wall_seconds'],optimized_wall=opt['wall_seconds'],
            speedup=ref['wall_seconds']/opt['wall_seconds'],event_counts=ref['event_counts'],
            completed_compute_jobs=ref['completed_compute_jobs'],level_accepted=ref['level_accepted'],
            active_window_cycles=ref['active_window_cycles'],complete=ref['status']=='complete')
        (case/'comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')
        comparisons.append(comparison)
        (args.output/'summary.json').write_text(json.dumps(comparisons,indent=2)+'\n')
        print(f'{comparison["status"].upper()} case={index} speedup={comparison["speedup"]:.3f}',flush=True)
        if not all(checks.values()):
            raise SystemExit(f'Differential failure case={index}: {checks}')


if __name__=='__main__':
    main()
