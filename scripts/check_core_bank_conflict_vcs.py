#!/usr/bin/env python3
"""Frozen campaign CoreStateBank vs real synchronous bank RTL, every edge."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot'
sys.path.insert(0,str(BASE/'model'))
from azilla_cycle_model.core_state_bank import CoreStateBank
OUT=ROOT/'results/paper_deadline_20260907/validation_fairness_v1'
text=(OUT/'bank_conflict.log').read_text()
assert 'PASS BANK_CONFLICT_STIMULUS' in text
bank=CoreStateBank(32,lanes=4)
cycles=0
for line in text.splitlines():
    if not line.startswith('BANK '):continue
    f=dict(re.findall(r'(\w+)=(\S+)',line));cycles+=1
    rst=int(f['rst']);valid=int(f['qv'],16);indices=int(f['qi'],16)
    requests={lane:(indices>>(lane*5))&31 for lane in range(4) if valid>>lane&1}
    ready=bank.ready(requests)
    assert int(f['qr'],16)==sum(int(v)<<lane for lane,v in ready.items()),f
    if int(f['cycle'])>0:
        assert int(f['rv'],16)==sum(1<<lane for lane in bank.responses),f
        for lane,response in bank.responses.items():
            # Other lanes may be uninitialized X; compare only valid responses.
            word=f['rd'][-8*(lane+1):len(f['rd'])-8*lane or None]
            assert int(word,16)==response.data,(f,response)
    bank.tick(requests,write=(int(f['wi']),int(f['wd'])) if int(f['wv']) else None,rst=bool(rst))
assert bank.stalled_reads==3 and bank.accepted_reads==6
paths=[OUT/'bank_conflict.log',OUT/'bank_conflict_tb.sv',ROOT/'rtl/banked_state_sram.sv',BASE/'model/azilla_cycle_model/core_state_bank.py']
report=dict(status='pass',scope='banked operand component only; four lanes,32entries,eightbanks',
    cycles=cycles,accepted_reads=bank.accepted_reads,stalled_reads=bank.stalled_reads,
    synchronous_one_cycle=True,read_before_write=True,
    sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
(OUT/'bank_conflict.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
