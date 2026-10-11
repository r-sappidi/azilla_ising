#!/usr/bin/env python3
"""Pause only three identified legacy producers; leave their children running."""
import json,os,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
targets={253610:['python3','scripts/run_shared_fetch_followups.py','shared'],
         253611:['python3','scripts/run_shared_fetch_followups.py','links'],
         254065:['python3','scripts/finalize_minimum_matrix_figures.py']}
out=ROOT/'results/cir_matched_all_campaigns_v3_ready/legacy_queue_pause.json'
if out.exists():raise SystemExit('pause already recorded')
records=[]
for pid,expected in targets.items():
    proc=Path('/proc')/str(pid)
    if not proc.exists():continue
    assert proc.stat().st_uid==os.getuid()
    argv=proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
    assert argv==expected,(pid,argv)
    assert proc.joinpath('cwd').resolve()==ROOT
    stat=proc.joinpath('stat').read_text().split()
    records.append(dict(pid=pid,argv=argv,start_ticks=stat[21],previous_state=stat[2]))
for record in records:
    proc=Path('/proc')/str(record['pid'])
    assert proc.joinpath('stat').read_text().split()[21]==record['start_ticks']
    os.kill(record['pid'],signal.SIGSTOP)
out.write_text(json.dumps(dict(action='SIGSTOP launcher parents only; no child signals',records=records),indent=2)+'\n')
print(out)
