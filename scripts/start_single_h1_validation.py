#!/usr/bin/env python3
"""Freeze a dedicated full-endpoint RTL campaign, small smoke before 64K."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    out=a.output.resolve();out.relative_to(ROOT/'results');out.mkdir(parents=True,exist_ok=False)
    snap=out/'snapshot';base=ROOT/'results/shared_fetch_cir_matched_v3_20260907/snapshot'
    for folder in ('model','rtl','tb'):
        shutil.copytree(base/folder,snap/folder,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','datasets'))
    (snap/'scripts').mkdir()
    for name in ('check_shared_fetch_single_h1.py','run_shared_fetch_single_h1.sh'):
        shutil.copy2(ROOT/'scripts'/name,snap/'scripts'/name)
    shutil.copy2(ROOT/'tb/shared_fetch_single_h1_tb.sv',snap/'tb/shared_fetch_single_h1_tb.sv')
    (snap/'third_party').symlink_to(ROOT/'third_party',target_is_directory=True)
    (snap/'build').mkdir();(snap/'build/cycle_model_ramulator').symlink_to(ROOT/'build/cycle_model_ramulator',target_is_directory=True)
    datasets=out/'datasets';datasets.mkdir();small=datasets/'toroidal256.txt'
    rows=['256 0']
    for v in range(256):
        x,y=divmod(v,16)
        for u in (x*16+(y+1)%16,((x+1)%16)*16+y):
            rows.extend((f'{v+1} {u+1} 1',f'{u+1} {v+1} 1'))
    small.write_text('\n'.join(rows)+'\n')
    large=datasets/'toroidal65536.txt'
    shutil.copy2(ROOT/'results/paper_canonical/datasets/prepared/toroidal_d4_n65536.txt',large)
    files=[p for p in snap.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.sv','.sh','.yaml')]
    files+=list(datasets.iterdir())
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (out/'manifest.json').write_text(json.dumps(dict(files=hashes,scope='single-H1 full endpoints; timing-only'),indent=2)+'\n')
    status={}
    for name,h0,cores,dataset in [('smoke256',2,4,small),('full65536',8,256,large)]:
        case=out/name;case.mkdir()
        env=dict(os.environ,OUTPUT_ROOT=str(case),H0_COUNT=str(h0),CORES_PER_H0=str(cores),DATASET=str(dataset),
                 PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        print('START',name,flush=True)
        status[name]='running';(out/'status.json').write_text(json.dumps(status,indent=2)+'\n')
        with (case/'launcher.log').open('x') as log:
            r=subprocess.run(['bash','scripts/run_shared_fetch_single_h1.sh'],cwd=snap,env=env,
                             stdout=log,stderr=subprocess.STDOUT)
        status[name]='pass' if r.returncode==0 else 'fail'
        (out/'status.json').write_text(json.dumps(status,indent=2)+'\n')
        print('FINISH',name,status[name],flush=True)
        if r.returncode:raise SystemExit(r.returncode)
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
if __name__=='__main__':main()
