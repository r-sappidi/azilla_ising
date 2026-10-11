#!/usr/bin/env python3
"""Capture short actual CIR hierarchy stimuli for isolated RTL replay."""
import sys
import json
import hashlib
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'model'))
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry


def main():
    output = Path(sys.argv[1])
    model = RamulatorEventPerformanceModel(
        Geometry(4, 4, 2, 16),
        BlockOccupancyDataset.load(ROOT / 'tb/datasets/g16384_kings.txt'),
        dataset_path=str(ROOT / 'tb/datasets/g16384_kings.txt'),
        ramulator_library=str(ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(ROOT / 'tb/ramulator_128x32.yaml'),
        config=PerformanceConfig(h0_mvm_count=4, h1_mvm_count=2,
                                 cross_mvm_count=4, timing_only=True),
    )
    records = []
    memory_records = []
    original_network = model._record_network
    cycle = 0
    def network(*args):
        nonlocal cycle
        cycle = args[-1]
        return original_network(*args)
    model._record_network = network
    node = next(node for target, node in model.local_nodes.items()
                if target.level == 'h0' and target.node == 0 and target.index == 0)
    original_tick = node.tick
    def tick(**kwargs):
        record = dict(kwargs)
        record['commands'] = [asdict(command) if command else None
                              for command in kwargs.get('commands', [])]
        records.append(dict(cycle=cycle, inputs=record,
                            outputs=asdict(node.outputs())))
        return original_tick(**kwargs)
    node.tick = tick
    for target, frontend in model.frontends.items():
        if target.node != 0 or target.index != 0 or target.level not in ('h0','cross'):
            continue
        def wrap(frontend=frontend, target=target):
            original=frontend.streamer.tick
            def memory_tick(**kwargs):
                before=frontend.streamer.outputs()
                memory_records.append(dict(cycle=cycle,level=target.level,
                    requests=[asdict(req) if req else None for req in before.memory_requests],
                    ready=kwargs['memory_request_ready'],
                    responses=[asdict(rsp) if rsp else None for rsp in kwargs['memory_responses']],
                    scheduler=[asdict(cmd) if cmd else None for cmd in kwargs['scheduler_commands']],
                    outstanding=before.outstanding))
                return original(**kwargs)
            return memory_tick
        frontend.streamer.tick=wrap()
    try:
        result = model.run()
        print('total_cycles', result.total_cycles, flush=True)
        from check_exact_event_model import rtl_transfers
        trace_path = ROOT / 'results/paper_validation_20260906/cir_reset_rtl_events.csv'
        expected = rtl_transfers(trace_path)
        observed = sorted(model.transfers)
        print('transfer_counts',len(expected),len(observed), flush=True)
        print('first_mismatch',next(((a,b) for a,b in zip(expected,observed) if a!=b),None),flush=True)
        from check_16k_cycle_model import rtl_noc_totals
        expected_noc = rtl_noc_totals(ROOT / 'results/paper_validation_20260906/cir_reset_noc.csv')
        counters = result.counters
        actual_noc = (counters.injected_flits,counters.ejected_flits,
                      counters.physical_link_flits,counters.injection_stalls,
                      counters.ejection_stalls,counters.link_stalls)
        assert expected == observed, 'accepted transfer mismatch'
        assert expected_noc == actual_noc, (expected_noc,actual_noc)
        assert (result.initialization_cycles,result.total_cycles)==(16899,17668)
        output.with_suffix('.validation.json').write_text(json.dumps(dict(
            status='PASS', geometry=[4,4,2,16], engines=[4,2,4],
            initialization_cycles=result.initialization_cycles,
            iteration_cycles=result.iteration_cycles,
            total_cycles=result.total_cycles, noc=actual_noc,
            accepted_transfers=len(observed),
            rtl_trace_sha256=hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            source_sha256={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT/'model/azilla_cycle_model/exact_events.py',
                             ROOT/'model/azilla_cycle_model/hierarchy.py')},
        ),indent=2))
        print('PASS exact CIR timing, all accepted transfers, and stall totals',flush=True)
    finally:
        model.close()
        output.write_text(json.dumps(records))
        output.with_suffix('.memory.json').write_text(json.dumps(memory_records))
        def mask(bits):
            return sum(int(bit)<<i for i,bit in enumerate(bits))
        lines=[]
        for record in records:
            inp=record['inputs']
            commands=inp['commands']
            values=[record['cycle'],mask([c is not None for c in commands]),
                    mask(inp['weight_valid']),mask(inp['partial_ready']),
                    int(inp['schedule_done'])]
            for c in commands:
                values.extend([c[k] if c else 0 for k in
                               ('state_a_index','state_b_index','block_a','block_b')])
            lines.append(' '.join(map(str,values)))
        output.with_suffix('.txt').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
