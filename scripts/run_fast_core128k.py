#!/usr/bin/env python3
"""Run isolated uniform128K v3 cores-only after counter-core equivalence gate."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path('/home/rsappidi/azilla_ising')
BASE=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
OUT=ROOT/'results/uniform128k_core_fast_20260908'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(p)


def main():
    gate=ROOT/'results/fast_shared_fetch_validation_20260908/retry/report.json'
    validation=json.loads(gate.read_text())
    assert validation['status']=='pass' and validation['sources_unchanged']
    candidates=[ROOT/'model/azilla_cycle_model'/n for n in ('fast_core.py','fast_shared_fetch_cli.py')]
    assert all(sha(p)==validation['source_sha256'][str(p)] for p in candidates)
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE/'snapshot/model',OUT/'snapshot/model',ignore=shutil.ignore_patterns('__pycache__'))
    for p in candidates:shutil.copy2(p,OUT/'snapshot/model/azilla_cycle_model'/p.name)
    shutil.copy2(BASE/'snapshot/tb/ramulator_128x32.yaml',OUT/'ramulator.yaml')
    shutil.copy2(BASE/'datasets/uniform_d16_n131072_s1.txt',OUT/'dataset.txt')
    shutil.copy2(gate,OUT/'equivalence.json');shutil.copy2(__file__,OUT/'launcher.py')
    library=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'
    files=[p for p in OUT.rglob('*') if p.is_file()]+[library,ROOT/'third_party/ramulator2/libramulator.so']
    hashes={str(p):sha(p) for p in files}
    cmd=[sys.executable,'-m','azilla_cycle_model.fast_shared_fetch_cli','simulate-exact-events',
        '--execution-mode','cores-only','--dataset',str(OUT/'dataset.txt'),
        '--mesh-x','2','--mesh-y','1','--h0-per-h1','8','--cores-per-h0','256',
        '--h0-mvms','4','--h1-mvms','2','--cross-mvms','4',
        '--ramulator-library',str(library),'--ramulator-config',str(OUT/'ramulator.yaml'),
        '--metrics-prefix',str(OUT/'metrics')]
    save(OUT/'manifest.json',dict(command=cmd,sources=hashes,baseline=str(BASE/'timing/uniform_d16_n131072_s1'),
        optimization='FastCoreIteration countdown and sign-cache only',architecture_changed=False,
        fetch_policy='single_fetch_two_slot_cir_routes_v3',scope='Timing-only large-geometry projection; not a new RTL certificate'))
    env=dict(os.environ,PYTHONPATH=str(OUT/'snapshot/model'),PYTHONDONTWRITEBYTECODE='1',
             OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    save(OUT/'status.json',dict(status='running',time=time.time(),parent_pid=os.getpid()))
    try:
        with (OUT/'run.log').open('x') as log:
            proc=subprocess.Popen(['/usr/bin/time','-v','-o',str(OUT/'wall.time'),*cmd],env=env,cwd=ROOT,
                                  stdout=log,stderr=subprocess.STDOUT)
            save(OUT/'status.json',dict(status='running',time=time.time(),time_wrapper_pid=proc.pid))
            if proc.wait()!=0:raise RuntimeError('optimized cores-only worker failed; inspect run.log')
        assert all(sha(Path(p))==h for p,h in hashes.items())
        summary=json.loads((OUT/'metrics_summary.json').read_text());a=summary['cores_only_ablation']
        assert a['pipeline_contract']['fetch_policy']=='single_fetch_two_slot_cir_routes_v3'
        jobs=a['unordered_interaction_blocks']
        assert a['weight_block_reads']==jobs and a['directed_core_jobs']==2*jobs
        dram=summary['dram_systems']
        assert sum(r['scheduled_jobs'] for r in dram)==jobs
        assert sum(r['accepted_requests'] for r in dram)==32*jobs
        assert all(r['accepted_requests']==r['completed_requests'] and r['outstanding_requests']==0 for r in dram)
        assert summary['noc']['injected_flits']==summary['noc']['ejected_flits']
        save(OUT/'audit.json',dict(status='conservation-pass',sources_unchanged=True,canonical_jobs=jobs,
             full_geometry_rtl_verified=False))
        baseline=BASE/'timing/uniform_d16_n131072_s1'
        if (baseline/'metrics_summary.json').exists():
            checks={f:sha(OUT/f)==sha(baseline/f) for f in
                    ('metrics_summary.json','metrics_noc.csv','metrics_nodes.csv','metrics_dram.csv')}
            save(OUT/'comparison.json',dict(status='pass' if all(checks.values()) else 'fail',checks=checks))
            if not all(checks.values()):raise RuntimeError('Original/candidate saved metrics differ')
        else:save(OUT/'comparison.json',dict(status='pending-original-completion'))
        save(OUT/'status.json',dict(status='complete',time=time.time()))
    except BaseException as e:
        save(OUT/'status.json',dict(status='failed',error=repr(e),time=time.time()))
        raise


if __name__=='__main__':main()
