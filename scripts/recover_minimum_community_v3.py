#!/usr/bin/env python3
"""Recover the community worker lost when its tmux producer exited."""
import json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'results/shared_fetch_cir_matched_v3_20260907'
name='timing_community_d16_n65536_s1'
cmd=json.loads((out/(name+'.command.json')).read_text())
env=dict(os.environ,PYTHONPATH=str(out/'snapshot/model'),PYTHONDONTWRITEBYTECODE='1',
         OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
with (out/(name+'.recovery.log')).open('x') as log:
    result=subprocess.run(['/usr/bin/time','-v','-o',str(out/(name+'.time')),*cmd],
        cwd=out/'snapshot',env=env,stdout=log,stderr=subprocess.STDOUT)
sys.exit(result.returncode)
