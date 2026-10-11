#!/usr/bin/env python3
"""Isolated, bounded reproduction of the interrupted provisioning VCS build."""
import json
import argparse
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home-storage',action='store_true')
    options=parser.parse_args()
    original=ROOT/'results/dense_streaming_provisioning_20261004_v1'
    work=Path(tempfile.mkdtemp(prefix='azilla_provisioning_diagnosis_',
                            dir=ROOT/'results' if options.home_storage else '/tmp'))
    for name in ('rtl','tb','third_party'):
        (work/name).symlink_to(original/'snapshot'/name,target_is_directory=True)
    args=json.loads((original/'gates/e4-2-4/cir/build.command.json').read_text())
    args=[f'-Mdir={work}/csrc' if x.startswith('-Mdir=') else x for x in args]
    args[args.index('-o')+1]=str(work/'simv')
    env=dict(os.environ,VCS_HOME='/home/hwpeng/vcs',VCS_ARCH_OVERRIDE='linux',VCS_TARGET_ARCH='linux64')
    report=dict(status='running',directory=str(work),command=args,started=time.time())
    target=ROOT/('results/provisioning_build_diagnosis_home_20261004.json' if options.home_storage
                 else 'results/provisioning_build_diagnosis_20261004.json')
    if target.exists():raise RuntimeError('refusing to overwrite diagnostic report')
    target.write_text(json.dumps(report,indent=2)+'\n')
    print(work,flush=True)
    with (work/'build.log').open('x') as f:
        p=subprocess.Popen(['/usr/bin/strace','-f','-e','trace=process,signal',
            '-o',str(work/'signals.log'),*args],cwd=work,env=env,stdout=f,stderr=subprocess.STDOUT,
            start_new_session=True)
        report['pid']=p.pid
        target.write_text(json.dumps(report,indent=2)+'\n')
        try:rc=p.wait(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGTERM)
            try:rc=p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);rc=p.wait()
            report['timeout']=True
    report.update(status='finished',returncode=rc,finished=time.time(),binary_exists=(work/'simv').exists())
    target.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
