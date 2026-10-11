#!/usr/bin/env python3
"""RTL-only workload/solution-equivalence gate after streaming component tests."""
import argparse
import json
import os
from pathlib import Path
import random
import re
from evaluate_streaming_endpoints import build, command, save


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.environ.update(VCS_HOME='/home/hwpeng/vcs',VCS_ARCH_OVERRIDE='linux',VCS_TARGET_ARCH='linux64')
    os.nice(15)
    report=dict(status='running',quality=[],timing=[]);save(out/'status.json',report)
    try:
        bins={m:build(out/'quality'/m,m,'streaming_endpoint_quality_tb') for m in ('buffered','streaming')}
        for family in (0,1,2):
            for seed in (1,7):
                for noise in (0,5):
                    traces={}
                    for mode,binary in bins.items():
                        log=out/'quality'/mode/f'f{family}_s{seed}_n{noise}.log'
                        command([binary,f'+FAMILY={family}',f'+SEED={seed}',f'+NOISE={noise}'],log)
                        text=log.read_text();assert 'PASS STREAMING_QUALITY' in text
                        traces[mode]=re.findall(r'QUALITY epoch=(\d+) states=(\w+) cut=(-?\d+)',text)
                        assert len(traces[mode])==32
                    assert traces['buffered']==traces['streaming']
                    report['quality'].append(dict(family=family,seed=seed,noise=noise,iterations=32,
                        status='identical states and cut trajectory; independent scalar field checks pass'))
                    save(out/'status.json',report)
        for mx,my,cores in ((2,1,8),(2,2,4),(4,2,4)):
            tag=f'mesh{mx}x{my}_c{cores}';n=mx*my*2*cores*32
            bins={m:build(out/tag/m,m,'shared_fetch_two_h1_tb',
                [('MESH_X',mx),('MESH_Y',my),('H0_PER_H1',2),('CORES_PER_H0',cores)])
                  for m in ('buffered','streaming')}
            for family in ('dense','sparse','hotspot'):
                data=out/tag/f'{family}.txt';schedule=out/tag/f'{family}.schedule'
                rng=random.Random(7);pairs=[]
                for b in range(n//32):
                    for c in range(b+1,n//32):
                        if family=='sparse' and c!=b+1 and rng.random()>.15:continue
                        if family=='hotspot' and b!=0:continue
                        pairs.append((b,c,(b+c)%(mx*my)))
                data.write_text(f'{n} 0\n'+''.join(f'{b*32+1} {c*32+1} -1\n{c*32+1} {b*32+1} -1\n' for b,c,_ in pairs))
                schedule.write_text(''.join(f'{b} {c} {owner}\n' for b,c,owner in pairs))
                for stall in (0,7):
                    cycles={};jobs={}
                    for mode,binary in bins.items():
                        log=out/tag/mode/f'{family}_stall{stall}.log'
                        command([binary,f'+DATASET={data}',f'+SCHEDULE={schedule}',f'+STALL_PERIOD={stall}'],log)
                        text=log.read_text();end=re.search(r'PASS SHARED_FETCH_TWO_H1 total_cycles=(\d+) jobs=(\d+)',text)
                        init=re.search(r'FULL_INIT cycle=(\d+)',text);assert end and init
                        cycles[mode]=int(end[1])-int(init[1]);jobs[mode]=int(end[2])
                    assert jobs['buffered']==jobs['streaming']==2*len(pairs)
                    report['timing'].append(dict(geometry=tag,spins=n,family=family,stall=stall,
                        iteration_cycles=cycles,speedup=cycles['buffered']/cycles['streaming'],
                        scope='occupied-block witness; timing only, not dense arithmetic quality'))
                    save(out/'status.json',report)
        report['status']='pass';save(out/'status.json',report)
    except BaseException as e:
        report.update(status='failed',error=repr(e));save(out/'status.json',report);raise


if __name__=='__main__':main()
