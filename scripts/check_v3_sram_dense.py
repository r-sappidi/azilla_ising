#!/usr/bin/env python3
"""Isolated single-RW candidate against frozen v3 two-H1 dense timing."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/sram_v3_dense_20260908'
BASE=ROOT/'results/dense_multi_h1_vcs_v2_20260908'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def main():
    OUT.mkdir(exist_ok=False);save(OUT/'status.json',dict(status='preparing'))
    assert json.loads((BASE/'status.json').read_text())['status']=='complete'
    snap=OUT/'snapshot'
    for folder in ('model','rtl','tb','scripts'):
        shutil.copytree(BASE/'snapshot'/folder,snap/folder,symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
    for old,new in [('j_block_sram.sv','j_block_sram_single_rw_candidate.sv'),
                    ('banked_state_sram.sv','banked_state_sram_phase_guard_candidate.sv')]:
        shutil.copy2(ROOT/'rtl'/new,snap/'rtl'/old)
    (snap/'third_party').symlink_to(ROOT/'third_party',target_is_directory=True)
    (snap/'build').mkdir();(snap/'build/cycle_model_ramulator').symlink_to(ROOT/'build/cycle_model_ramulator',target_is_directory=True)
    hashes={str(p):sha(p) for folder in ('model','rtl','tb','scripts') for p in (snap/folder).rglob('*') if p.is_file() and not p.is_symlink()}
    save(OUT/'manifest.json',dict(sources=hashes,geometry=[2,1,2,16],spins=2048,
        scope='single-RW logical candidate timing, not vendor macro binding or arithmetic',changed_rtl=['j_block_sram.sv','banked_state_sram.sv']))
    for stall in (0,7,13):
        case=OUT/f'stall{stall}';case.mkdir()
        save(OUT/'status.json',dict(status='running',stall=stall))
        dataset=snap/'tb/datasets/dense_occupancy_2048.txt'
        env=dict(os.environ,OUTPUT_ROOT=str(case),H0_PER_H1='2',CORES_PER_H0='16',DATASET=str(dataset),STALL_PERIOD=str(stall),PYTHONDONTWRITEBYTECODE='1')
        if stall==0:commands=[('launcher',['bash','scripts/run_shared_fetch_two_h1.sh'])]
        else:commands=[('rtl',[str(OUT/'stall0/build/simv'),f'+DATASET={dataset}',f'+STALL_PERIOD={stall}']),
            ('differential',['/home/rsappidi/miniforge3/envs/azilla/bin/python3','scripts/check_shared_fetch_two_h1.py',
             '--dataset',str(dataset),'--log',str(case/'rtl.log'),'--output',str(case/'differential.json'),'--h0','2','--cores','16','--stall',str(stall)])]
        for name,cmd in commands:
            save(case/f'{name}.command.json',cmd)
            with (case/f'{name}.log').open('x') as f:
                subprocess.run(['/usr/bin/time','-v','-o',str(case/f'{name}.time'),*cmd],cwd=snap,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        d=json.loads((case/'differential.json').read_text());assert d['status']=='pass' and all(d['checks'].values())
        assert all(sha(p)==h for p,h in hashes.items())
    save(OUT/'status.json',dict(status='complete',vendor_macro_binding=False,arithmetic=False))
if __name__=='__main__':
    try:main()
    except Exception as e:
        if OUT.exists():save(OUT/'status.json',dict(status='failed',error=repr(e)))
        raise
