#!/usr/bin/env python3
"""Complete 16K Kings equivalence with explicit, original refresh default zero.

Uses frozen source trees directly; does not patch another checker or change any
active validation. Five complete artifacts must match byte-for-byte.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from check_fast_exact_model import hashes, first_difference

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'results/paper_validation_20260907/fast_exact_refresh0_v2')
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    oracle=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot/model'
    fast=ROOT/'results/paper_validation_20260907/fast_exact_candidate_v2/model'
    dataset=ROOT/'tb/datasets/g16384_kings.txt'
    memory=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot/ramulator_128x32.yaml'
    library=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'
    oh,fh=hashes(oracle),hashes(fast)
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    immutable={str(p):digest(p) for p in (dataset,memory,library)}
    report=dict(status='running',oracle=str(oracle),optimized=str(fast),
        oracle_sha256=oh,optimized_sha256=fh,cases=[],
        created_utc=datetime.now(timezone.utc).isoformat(),
        scope='Complete-run optimization equivalence with explicit idle-refresh-period-ticks=0; no new RTL coverage',
        checker_sha256=digest(__file__))
    def save():
        temp=out/'report.tmp.json';temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(out/'report.json')
    save()
    for mode in ('cir','cores-only'):
        name='kings16k_'+mode;directory=out/name;directory.mkdir()
        case=dict(name=name,geometry=[4,4,2,16],engines=[4,2,4],width=128,
                  mode=mode,dataset=str(dataset),idle_refresh_period_ticks=0)
        def run(label,model,module):
            target=directory/label;target.mkdir()
            command=[sys.executable,'-m',module,'simulate-exact-events','--dataset',str(dataset),
                '--mesh-x','4','--mesh-y','4','--h0-per-h1','2','--cores-per-h0','16',
                '--h0-mvms','4','--h1-mvms','2','--cross-mvms','4','--execution-mode',mode,
                '--ramulator-library',str(library),'--ramulator-config',str(memory),
                '--idle-refresh-period-ticks','0','--metrics-prefix',str(target/'metrics'),
                '--transfer-trace',str(target/'transfers.csv')]
            env=dict(os.environ,PYTHONPATH=str(model),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
            start=time.perf_counter()
            with (target/'run.log').open('w') as log:
                result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            return dict(label=label,command=command,returncode=result.returncode,wall_seconds=time.perf_counter()-start)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(run,'oracle',oracle,'azilla_cycle_model.cli')
            b=pool.submit(run,'optimized',fast,'azilla_cycle_model.fast_cli')
            executions=[a.result(),b.result()]
        item=dict(case=case,execution=executions,input_sha256=immutable,status='checking',compared_artifacts=[])
        report['cases'].append(item)
        try:
            if hashes(oracle)!=oh or hashes(fast)!=fh:raise RuntimeError('source drift')
            if any(digest(p)!=d for p,d in immutable.items()):raise RuntimeError('input drift')
            if any(r['returncode'] for r in executions):raise RuntimeError('child execution failed')
            for artifact in ('metrics_summary.json','metrics_noc.csv','metrics_nodes.csv','metrics_dram.csv','transfers.csv'):
                left=directory/'oracle'/artifact;right=directory/'optimized'/artifact
                aa,bb=left.read_bytes(),right.read_bytes()
                if aa!=bb:
                    diff=first_difference(json.loads(aa),json.loads(bb)) if artifact.endswith('.json') else first_difference(aa.decode().splitlines(),bb.decode().splitlines())
                    item['first_divergence']=dict(file=artifact,detail=diff)
                    raise RuntimeError('first divergence: '+str(item['first_divergence']))
                item['compared_artifacts'].append(dict(name=artifact,bytes=len(aa),sha256=digest(left)))
            item['status']='pass';item['speedup']=executions[0]['wall_seconds']/executions[1]['wall_seconds']
            save();print('PASS',name,'refresh=0 speedup=',round(item['speedup'],3),flush=True)
        except Exception as error:
            item['status']='mismatch' if 'first_divergence' in item else 'execution-failure'
            item['error']=repr(error);report['status']='failed';save();raise
    report['status']='passed';save()


if __name__=='__main__':main()
