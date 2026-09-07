#!/usr/bin/env python3
"""Complete-run exact-equivalence checks against an immutable model snapshot.

No timing tolerance, dropped metric fields, or transfer sorting is permitted.
Two subprocesses maximum; each owns an independent Ramulator DPI process.
This is optimization equivalence, not an extension of RTL validation coverage.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_ORACLE=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot/model'


def hashes(directory):
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*.py'))}


def cases(large):
    result=[]
    # Deliberately bounded complete runs, not truncated execution windows.
    settings=[('single_sparse',(1,1,2,8),'sparse',(1,1,1),128),
      ('single_dense',(1,1,2,4),'dense',(2,2,2),64),
      ('two_uneven',(2,1,2,8),'uneven',(4,2,4),32),
      ('four_conflict',(2,2,2,8),'bank-conflict',(8,8,8),128),
      ('sixteen_sparse',(4,4,2,2),'sparse',(16,16,16),64),
      ('two_dense',(2,1,2,4),'dense',(4,2,4),128)]
    for name,geometry,family,engines,width in settings:
        for mode in ('cir','cores-only'):
            result.append(dict(name=name+'_'+mode,geometry=geometry,family=family,
                               engines=engines,width=width,mode=mode))
    if large:
        for name,path,geometry in (
          ('kings16k','tb/datasets/g16384_kings.txt',(4,4,2,16)),
          ('torus32k','results/paper_benchmarks/datasets/prepared/toroidal_d4_n32768.txt',(1,1,8,128)),
          ('torus64k','results/paper_canonical/datasets/prepared/toroidal_d4_n65536.txt',(1,1,8,256))):
            for mode in ('cir','cores-only'):
                result.append(dict(name=name+'_'+mode,geometry=geometry,family=name,
                    engines=(4,2,4),width=128,mode=mode,dataset=str(ROOT/path)))
    return result


def dataset(case,path):
    x,y,h,c=case['geometry']; blocks=x*y*h*c
    family=case['family']
    pairs=[]
    for a in range(blocks):
        for b in range(a+1,blocks):
            if (family=='dense' or
                (family=='sparse' and (b==a+1 or b==(a+blocks//2)%blocks)) or
                (family=='uneven' and (a==0 or (a%7==0 and b%3==0))) or
                (family=='bank-conflict' and a%8==0 and b%8==0)):
                pairs.append((a,b))
    with path.open('w') as handle:
        handle.write(f'{blocks*32} 0\n')
        # Dense means every interaction block is occupied; full dense weights
        # are unnecessary for timing but emitted for the actual dense fixture.
        for a,b in pairs:
            for r in range(32 if family=='dense' else 1):
                for col in range(32 if family=='dense' else 1):
                    u,v=a*32+r+1,b*32+col+1
                    handle.write(f'{u} {v} -1\n{v} {u} -1\n')


def first_difference(a,b,path='$'):
    if type(a)!=type(b): return dict(path=path,oracle=a,optimized=b)
    if isinstance(a,dict):
        if a.keys()!=b.keys(): return dict(path=path,oracle_keys=list(a),optimized_keys=list(b))
        for key in a:
            difference=first_difference(a[key],b[key],path+'.'+key)
            if difference: return difference
    elif isinstance(a,list):
        if len(a)!=len(b): return dict(path=path,oracle_length=len(a),optimized_length=len(b))
        for index,(left,right) in enumerate(zip(a,b)):
            difference=first_difference(left,right,f'{path}[{index}]')
            if difference: return difference
    elif a!=b: return dict(path=path,oracle=a,optimized=b)
    return None


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--oracle',type=Path,default=DEFAULT_ORACLE)
    p.add_argument('--large',action='store_true')
    p.add_argument('--filter',default='')
    p.add_argument('--optimized-snapshot',type=Path)
    p.add_argument('--mapped-only',action='store_true')
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    oracle=args.oracle.resolve()
    fast=out/'optimized_snapshot'/'model'
    source=args.optimized_snapshot.resolve() if args.optimized_snapshot else ROOT/'model'
    before=hashes(source)
    shutil.copytree(source,fast,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if hashes(source)!=before or hashes(fast)!=before:
        raise RuntimeError('optimized sources changed during snapshot')
    oracle_hashes=hashes(oracle)
    library=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'
    selected=[c for c in cases(args.large) if args.filter in c['name']]
    if args.mapped_only:
        artifact=ROOT/'results/paper_validation_20260907/mapping_ablation_exact_v1/toroidal_d4_n65536/compression_mapping_lossless'
        selected=[dict(name='mapped_torus64k_'+mode,geometry=(1,1,8,256),
            family='mapped-torus64k',engines=(4,2,4),width=128,mode=mode,
            artifact=str(artifact),dataset=str(artifact/'dataset.txt')) for mode in ('cir','cores-only')]
    report=dict(status='running',oracle=str(oracle),optimized=str(fast),
        oracle_sha256=oracle_hashes,optimized_sha256=before,
        scope='complete-run optimization equivalence; no new RTL certification',
        created_utc=datetime.now(timezone.utc).isoformat(),cases=[])
    def save(): (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    for case in selected:
        case_out=out/case['name'];case_out.mkdir()
        data=Path(case['dataset']) if 'dataset' in case else case_out/'dataset.txt'
        if 'dataset' not in case: dataset(case,data)
        if not data.exists():
            report['cases'].append(dict(case=case,status='missing-dataset',dataset=str(data)))
            save();continue
        memory=ROOT/f"results/memory_width_prepare_20260907_v1/ramulator_x{case['width']}_32gbps.yaml"
        immutable={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in (data,memory,library)}
        if 'artifact' in case:
            for q in Path(case['artifact']).iterdir():
                if q.is_file(): immutable[str(q)]=hashlib.sha256(q.read_bytes()).hexdigest()
        x,y,h,c=case['geometry'];h0,h1,cross=case['engines']
        def run(label,model,module):
            target=case_out/label;target.mkdir()
            command=[sys.executable,'-m',module,'simulate-exact-events','--dataset',str(data),
              '--mesh-x',str(x),'--mesh-y',str(y),'--h0-per-h1',str(h),'--cores-per-h0',str(c),
              '--h0-mvms',str(h0),'--h1-mvms',str(h1),'--cross-mvms',str(cross),
              '--execution-mode',case['mode'],'--ramulator-library',str(library),
              '--ramulator-config',str(memory),'--idle-refresh-period-ticks','7600',
              '--metrics-prefix',str(target/'metrics'),'--transfer-trace',str(target/'transfers.csv')]
            if 'artifact' in case:
                command[3]='simulate-mapped-exact-events'
                for flag in ('--mesh-x','--mesh-y','--h0-per-h1','--cores-per-h0'):
                    index=command.index(flag);del command[index:index+2]
                command.extend(['--artifact',case['artifact']])
            environment=dict(os.environ,PYTHONPATH=str(model),PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1')
            start=time.perf_counter()
            with (target/'run.log').open('w') as log:
                process=subprocess.run(command,cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT)
            return dict(label=label,command=command,returncode=process.returncode,wall_seconds=time.perf_counter()-start)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run,'oracle',oracle,'azilla_cycle_model.cli'),
                     pool.submit(run,'optimized',fast,'azilla_cycle_model.fast_cli')]
            execution=[f.result() for f in futures]
        item=dict(case=case,execution=execution,input_sha256=immutable,status='checking')
        report['cases'].append(item)
        for q,digest in immutable.items():
            if hashlib.sha256(Path(q).read_bytes()).hexdigest()!=digest: raise RuntimeError('input changed: '+q)
        if hashes(oracle)!=oracle_hashes or hashes(fast)!=before: raise RuntimeError('snapshot changed')
        if any(r['returncode'] for r in execution):
            item['status']='execution-failure';report['status']='failed';save()
            raise RuntimeError('execution failed: '+case['name'])
        artifacts=sorted((case_out/'oracle').glob('metrics*'))+[case_out/'oracle/transfers.csv']
        item['compared_artifacts']=[]
        for left in artifacts:
            right=case_out/'optimized'/left.name
            a,b=left.read_bytes(),right.read_bytes()
            if a!=b:
                if left.suffix=='.json': difference=first_difference(json.loads(a),json.loads(b))
                else:
                    aa,bb=a.decode().splitlines(),b.decode().splitlines()
                    difference=first_difference(aa,bb)
                item.update(status='mismatch',first_divergence=dict(file=left.name,detail=difference))
                report['status']='failed';save()
                raise RuntimeError(f"first divergence {case['name']}: {item['first_divergence']}")
            item['compared_artifacts'].append(dict(name=left.name,bytes=len(a),sha256=hashlib.sha256(a).hexdigest()))
        item['status']='pass'
        item['speedup']=execution[0]['wall_seconds']/execution[1]['wall_seconds']
        save();print('PASS',case['name'],'speedup',round(item['speedup'],3),flush=True)
    report['status']='passed' if all(c['status']=='pass' for c in report['cases']) else 'incomplete'
    save()


if __name__=='__main__': main()
