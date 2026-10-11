#!/usr/bin/env python3
"""Nominal streaming endpoint validation, isolated evidence and fail-closed gates.

64K real toroidal dataset, then 128K full endpoints with all hierarchy paths.
Only timing is checked at scale; arithmetic preservation has a separate gate.
Existing campaigns are neither resumed nor migrated by this program.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from evaluate_streaming_endpoints import ROOT, build, command, save


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(VCS_HOME='/home/hwpeng/vcs', VCS_ARCH_OVERRIDE='linux', VCS_TARGET_ARCH='linux64')
    os.nice(10)
    assert json.loads((ROOT/'results/streaming_differentials_20261004_v1/status.json').read_text())['status'] == 'pass'
    sources = list((ROOT/'model/azilla_cycle_model').glob('*.py'))
    sources += list((ROOT/'rtl').glob('*.sv'))
    sources += list((ROOT/'rtl/experimental/streaming').glob('*.sv'))
    sources += [ROOT/p for p in ('tb/shared_fetch_single_h1_tb.sv', 'tb/shared_fetch_two_h1_tb.sv',
        'tb/ramulator_128x32.yaml', 'tb/ramulator_dpi.cpp', 'scripts/check_streaming_integrated.py',
        'scripts/check_shared_fetch_single_h1.py', 'scripts/check_shared_fetch_two_h1.py',
        'scripts/evaluate_streaming_endpoints.py', 'scripts/run_streaming_nominal_validation.py')]
    hashes = {str(p): digest(p) for p in sources}
    save(out/'sources.json', hashes)
    datasets = out/'datasets'
    datasets.mkdir()
    torus = datasets/'toroidal_65536.txt'
    shutil.copy2(ROOT/'results/full64k_shared_fetch_rtl_20260907_v1/datasets/toroidal65536.txt', torus)
    cross = datasets/'hierarchy_stress_131072.txt'
    schedule = datasets/'hierarchy_stress_131072.schedule'
    blocks = 4096
    # Every endpoint participates; adjacent, cross-H0 and cross-H1 blocks.
    pairs = sorted({tuple(sorted((b, (b+d) % blocks))) for b in range(blocks) for d in (1,256,2048)})
    cross.write_text('131072 0\n' + ''.join(f'{32*b+1} {32*c+1} -1\n{32*c+1} {32*b+1} -1\n' for b,c in pairs))
    schedule.write_text(''.join(f'{b} {c} {(b+c)%2}\n' for b,c in pairs))
    save(out/'plan.json', dict(
        geometry=dict(cores_per_h0=256,h0_per_h1=8,h0_lanes=4,h1_lanes=2,cross_lanes=4),
        cases=[dict(name='toroidal_65536',mesh=[1,1],stalls=[0],dataset=str(torus)),
               dict(name='hierarchy_stress_131072',mesh=[2,1],stalls=[0,7],dataset=str(cross),schedule=str(schedule))],
        scope='timing-only nominal endpoint integration; 128K occupancy stress is not a solution-quality benchmark',
        dataset_hashes={str(p):digest(p) for p in (torus,cross,schedule)}))
    status = dict(status='running', started=time.time(), smoke='pending',
                  cases={n:dict(stage='queued',results=[]) for n in ('toroidal_65536','hierarchy_stress_131072')})
    lock = threading.Lock()
    def update(name=None, **fields):
        with lock:
            (status if name is None else status['cases'][name]).update(fields)
            save(out/'status.json', status)
    def stable():
        assert all(digest(Path(p)) == h for p,h in hashes.items()), 'frozen source changed'
    update()
    try:
        # Verify the newly enabled single-H1 runner before large elaboration.
        smoke = out/'smoke256'
        smoke.mkdir()
        smoke_data = datasets/'toroidal_256.txt'
        shutil.copy2(ROOT/'results/full64k_shared_fetch_rtl_20260907_v1/datasets/toroidal256.txt',smoke_data)
        binary = build(smoke/'streaming','streaming','shared_fetch_single_h1_tb', [('H0_COUNT',2),('CORES_PER_H0',4)])
        command([binary,f'+DATASET={smoke_data}'],smoke/'rtl.log')
        command([sys.executable,'scripts/check_streaming_integrated.py','single',
                 '--dataset',smoke_data,'--log',smoke/'rtl.log','--output',smoke/'differential.json',
                 '--h0','2','--cores','4'],smoke/'differential.log')
        update(smoke='pass')
        def run_case(name, multi):
            case = out/name
            case.mkdir()
            top = 'shared_fetch_two_h1_tb' if multi else 'shared_fetch_single_h1_tb'
            params = [('H0_PER_H1',8),('CORES_PER_H0',256),('MESH_X',2),('MESH_Y',1)] if multi else [('H0_COUNT',8),('CORES_PER_H0',256)]
            data = cross if multi else torus
            try:
                stable()
                bins = {}
                for mode in ('streaming','buffered'):
                    update(name,stage=f'build_{mode}')
                    bins[mode] = build(case/mode,mode,top,params)
                for stall in ((0,7) if multi else (0,)):
                    cycles = {}
                    for mode in ('streaming','buffered'):
                        update(name,stage=f'rtl_{mode}_stall{stall}')
                        log = case/mode/f'stall{stall}.log'
                        args = [bins[mode],f'+DATASET={data}']
                        if multi: args += [f'+SCHEDULE={schedule}',f'+STALL_PERIOD={stall}']
                        command(args,log)
                        text = log.read_text()
                        match = re.search(r'PASS SHARED_FETCH_(?:SINGLE|TWO)_H1 total_cycles=(\d+) jobs=(\d+)',text)
                        init = re.search(r'FULL_INIT cycle=(\d+)',text)
                        assert match and init, str(log)
                        cycles[mode] = dict(initialization=int(init[1]),total=int(match[1]),
                                            iteration=int(match[1])-int(init[1]),jobs=int(match[2]))
                    assert cycles['buffered']['jobs']==cycles['streaming']['jobs']
                    update(name,stage=f'differential_stall{stall}',rtl_cycles=cycles)
                    target = case/f'differential_stall{stall}.json'
                    args = [sys.executable,'scripts/check_streaming_integrated.py','mesh' if multi else 'single',
                            '--dataset',data,'--log',case/'streaming'/f'stall{stall}.log','--output',target,
                            '--h0','8','--cores','256']
                    if multi: args += ['--mesh-x','2','--mesh-y','1','--schedule',schedule,'--stall',str(stall)]
                    command(args,case/f'differential_stall{stall}.log')
                    assert json.loads(target.read_text())['status']=='pass'
                    stable()
                    with lock:
                        status['cases'][name]['results'].append(dict(stall=stall,cycles=cycles,
                            speedup=cycles['buffered']['iteration']/cycles['streaming']['iteration'],differential='pass'))
                    update(name,stage=f'stall{stall}_passed')
                update(name,stage='pass')
            except BaseException as e:
                update(name,stage='failed',error=repr(e))
                raise
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(run_case,'toroidal_65536',False),pool.submit(run_case,'hierarchy_stress_131072',True)]
            for task in tasks:task.result()
        stable()
        update(status='pass',sources_unchanged=True,finished=time.time())
    except BaseException as e:
        update(status='failed',error=repr(e),finished=time.time())
        raise


if __name__ == '__main__':
    main()
