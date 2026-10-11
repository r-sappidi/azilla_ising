#!/usr/bin/env python3
"""Observe queue liveness independently of its isolated subprocess groups.

No automatic retries after failure: resume must first check orphaned workers.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from queue_dense_streaming_provisioning import ROOT, save


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--dependency',type=Path,required=True)
    ap.add_argument('--resume',action='store_true')
    a=ap.parse_args();out=a.output.resolve()
    record=out.with_name(out.name+'.supervisor.json')
    if record.exists():
        old=json.loads(record.read_text())
        for row in old.get('identities',[]):
            p=Path('/proc')/str(row['pid'])/'stat'
            if p.exists() and p.read_text().rsplit(')',1)[1].split()[19]==row['start_ticks']:
                raise RuntimeError('recorded supervisor or queue still alive')
        if not a.resume:raise RuntimeError('existing supervisor record; use explicit resume')
    if a.resume and out.exists():
        for p in out.rglob('*.process.json'):
            d=json.loads(p.read_text())
            # Conservative refusal on live PID; manual identity review if reused.
            if d.get('status')=='running' and Path('/proc',str(d['pid'])).exists():
                raise RuntimeError(f'possible orphan worker; inspect {p}')
    cmd=[sys.executable,'-u',str(ROOT/'scripts/queue_dense_streaming_provisioning.py'),
         '--output',str(out),'--dependency',str(a.dependency.resolve())]+(['--resume'] if a.resume else [])
    log=ROOT/'logs'/f'{out.name}_supervised_{time.time_ns()}.log'
    with log.open('x') as f:
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,start_new_session=True,
                               env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1'))
        identities=[dict(pid=pid,start_ticks=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19])
                    for pid in (os.getpid(),child.pid)]
        while True:
            save(record,dict(status='running',heartbeat=time.time(),identities=identities,log=str(log),command=cmd))
            try:rc=child.wait(timeout=15);break
            except subprocess.TimeoutExpired:pass
        save(record,dict(status='complete' if rc==0 else 'queue_exited_abnormally',returncode=rc,
            identities=identities,finished=time.time(),log=str(log),
            note='inspect child process records before any resume; no automatic failure bypass'))
    return rc


if __name__=='__main__':sys.exit(main())
