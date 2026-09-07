#!/usr/bin/env python3
"""Compare independent VCS replay-buffer edges against the Python component."""
import sys,re,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.shared_fetch import PairReplay
from azilla_cycle_model.hierarchy import DmaCommand
p=Path(sys.argv[1]);text=p.read_text();r=PairReplay();count=0
assert 'PASS SHARED_FETCH_REPLAY' in text
for line in text.splitlines():
    if not line.startswith('REPLAY '):continue
    f=dict(re.findall(r'(\w+)=(\S+)',line));o=r.outputs()
    if int(f['c'])>0:
        assert int(f['icr'])==(r.state==r.IDLE),f
        assert int(f['iwr'])==(r.state==r.FILL),f
        assert int(f['ocv'])==o.command_valid and int(f['owv'])==o.weight_valid,f
        if o.command_valid or o.weight_valid:
            assert (int(f['oa']),int(f['ob']))==(o.command.block_a,o.command.block_b),f
        if o.weight_valid:assert int(f['odata'],16)==o.weight_data,f
    r.tick(command=DmaCommand(int(f['ia']),int(f['ib']),int(f['ia']),int(f['ib'])) if int(f['icv']) else None,
           weight_valid=bool(int(f['iwv'])),weight_data=int(f['idata'],16),
           command_ready=bool(int(f['ocr'])),weight_ready=bool(int(f['owr'])),rst=bool(int(f['rst'])))
    count+=1
report=dict(status='pass',edges=count,scope='replay buffer component only, not full hierarchy RTL',
            files={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in
                   [p,ROOT/'rtl/shared_fetch_replay.sv',ROOT/'tb/shared_fetch_replay_tb.sv',ROOT/'model/azilla_cycle_model/shared_fetch.py']})
p.with_suffix('.audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
