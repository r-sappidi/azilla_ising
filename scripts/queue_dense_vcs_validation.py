#!/usr/bin/env python3
"""Isolated paired dense-block RTL differentials on fixed-H1 hardware.

Occupancy-witness stimulus is timing-only: all dense block pairs are scheduled,
with all 32 rows consumed per block. It is not an arithmetic dataset.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();out=a.output.resolve();out.relative_to(ROOT/'results')
    out.mkdir(parents=True,exist_ok=False)
    sweep=ROOT/'results/matched_dense_exact_v3_20260907'
    assert json.loads((sweep/'encoding_gate.json').read_text())['status']=='pass'
    base=ROOT/'results/shared_fetch_cir_matched_v3_20260907/snapshot'
    snap=out/'snapshot'
    for folder in ('model','rtl','tb','scripts'):
        shutil.copytree(base/folder,snap/folder,symlinks=True,
            ignore=shutil.ignore_patterns('__pycache__','datasets'))
    for name in ('check_shared_fetch_single_h1.py','run_shared_fetch_single_h1.sh',
                 'check_cir_exact_vcs_trace.py','run_dense_rtl_winners.py'):
        shutil.copy2(ROOT/'scripts'/name,snap/'scripts'/name)
    shutil.copy2(ROOT/'tb/shared_fetch_single_h1_tb.sv',snap/'tb/shared_fetch_single_h1_tb.sv')
    (snap/'third_party').symlink_to(ROOT/'third_party',target_is_directory=True)
    (snap/'build').mkdir();(snap/'build/cycle_model_ramulator').symlink_to(ROOT/'build/cycle_model_ramulator',target_is_directory=True)
    (snap/'tb/datasets').mkdir()
    # Verify that both RTL comparisons use exactly the dense campaign model/config.
    assert all(sha(p)==sha(snap/'model'/p.relative_to(sweep/'snapshot/model'))
        for p in (sweep/'snapshot/model').rglob('*.py'))
    assert sha(snap/'tb/ramulator_128x32.yaml')==sha(sweep/'snapshot/ramulator.yaml')
    for n in (1024,16384):
        data=snap/f'tb/datasets/dense_occupancy_{n}.txt'
        schedule=out/f'schedule_{n}.txt'
        with data.open('x') as f,schedule.open('x') as s:
            f.write('65536 0\n')
            for b in range(n//32):
                # Mark intra-core occupancy too; values are irrelevant in timing-only.
                f.write(f'{b*32+1} {b*32+2} -1\n{b*32+2} {b*32+1} -1\n')
                for c in range(b+1,n//32):
                    f.write(f'{b*32+1} {c*32+1} -1\n{c*32+1} {b*32+1} -1\n')
                    s.write(f'{b} {c} 0\n')
    files=[p for p in snap.rglob('*') if p.is_file() and not p.is_symlink() and
           p.suffix in ('.py','.sv','.sh','.yaml','.txt')]
    hashes={str(p):sha(p) for p in files}
    for p in (ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',ROOT/'third_party/ramulator2/libramulator.so'):
        hashes[str(p)]=sha(p)
    save(out/'manifest.json',dict(sources=hashes,problem_sizes=[1024,16384],
        allocated_spins=65536,geometry=[1,1,8,256],engines=[4,2,4],modes=['cir','cores-only'],
        coverage='dense timing: H0-only then cross-H0 within H1; no cross-H1 certificate',
        stimulus='all dense occupied block pairs; payload-elided witness edges, not solution quality',
        sweep=str(sweep),paper_ready=False))
    env=dict(os.environ,VCS_HOME=os.environ.get('VCS_HOME','/home/hwpeng/vcs'),
        VCS_ARCH_OVERRIDE='linux',VCS_TARGET_ARCH='linux64',PYTHONDONTWRITEBYTECODE='1',
        OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    def run(cmd,case,tag,extra=None):
        save(case/f'{tag}.command.json',cmd)
        with (case/f'{tag}.log').open('x') as f:
            subprocess.run(['/usr/bin/time','-v','-o',str(case/f'{tag}.time'),*cmd],
                cwd=snap,env=env|(extra or {}),stdout=f,stderr=subprocess.STDOUT,check=True)
    def mode_worker(mode):
        try:
            for n in (1024,16384):
                case=out/mode/str(n);case.mkdir(parents=True)
                save(out/f'{mode}_status.json',dict(status='running',problem_spins=n))
                assert all(sha(p)==h for p,h in hashes.items())
                data=snap/f'tb/datasets/dense_occupancy_{n}.txt'
                if mode=='cores-only':
                    run(['bash','scripts/run_shared_fetch_single_h1.sh'],case,'launcher',
                        dict(OUTPUT_ROOT=str(case),H0_COUNT='8',CORES_PER_H0='256',DATASET=str(data)))
                else:
                    spec=importlib.util.spec_from_file_location('dense_build',snap/'scripts/run_dense_rtl_winners.py')
                    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                    module.ROOT=snap
                    row=dict(mesh_x='1',mesh_y='1',h0_per_h1='8',cores_per_h0='256',h0_mvms='4',h1_mvms='2',cross_mvms='4')
                    cmd,binary=module.vcs_build(row,1,env,True,skip_zero_blocks=True)
                    if n==1024:run(cmd,case,'build')
                    run([str(binary),f'+DATASET={data.name}',f'+SCHEDULE={out}/schedule_{n}.txt',
                         f'+NOC_STATS_FILE={case}/noc_stats.csv',f'+NOC_EVENT_FILE={case}/noc_events.csv',
                         f'+INTERNAL_STATS_FILE={case}/internal_stats.csv'],case,'rtl')
                    run([sys.executable,'scripts/check_cir_exact_vcs_trace.py','--dataset',str(data),
                         '--rtl-log',str(case/'rtl.log'),'--rtl-events',str(case/'noc_events.csv'),
                         '--rtl-noc',str(case/'noc_stats.csv'),'--output',str(case/'differential.json'),
                         '--mesh-x','1','--mesh-y','1','--h0-per-h1','8','--cores-per-h0','256'],case,'differential')
                d=json.loads((case/'differential.json').read_text())
                assert d['status']=='pass' and all(d['checks'].values())
                assert all(sha(p)==h for p,h in hashes.items())
                save(case/'release_check.json',dict(status='pass',scope='this dense timing point only',
                    differential_sha256=sha(case/'differential.json')))
                save(out/f'{mode}_status.json',dict(status='point-passed',problem_spins=n))
            save(out/f'{mode}_status.json',dict(status='complete',passed=[1024,16384]))
        except Exception as e:
            save(out/f'{mode}_status.json',dict(status='failed',error=repr(e)));raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(mode_worker,m) for m in ('cir','cores-only')]
        for f in concurrent.futures.as_completed(futures):f.result()
    save(out/'status.json',dict(status='complete',paired_points=2,large_dense_rtl_verified=False))

if __name__=='__main__':main()
