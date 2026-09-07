#!/usr/bin/env python3
"""Check every bank ready/response edge including conflicts and read/write."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.core_state_bank import CoreStateBank

def main():
    path=Path(sys.argv[1])
    model=CoreStateBank(64,banks=8,lanes=4)
    rows=0
    for line in path.read_text().splitlines():
        if not line.startswith('BANK '):continue
        f=line.split()[1:]
        cycle,wv,wi,wd,qv,*_=map(int,f[:5])
        idx=list(map(int,f[5:9]));qr,rv=map(int,f[9:11]);rst=int(f[15])
        requests={i:idx[i] for i in range(4) if qv>>i&1}
        if not rst:
            ready=model.ready(requests)
            assert qr==sum(int(v)<<i for i,v in ready.items()),(cycle,'ready')
            assert rv==sum(1<<i for i in model.responses),(cycle,'response_valid')
            for lane,response in model.responses.items():
                assert int(f[11+lane])==response.data,(cycle,lane,'response_data')
        model.tick(requests,write=(wi,wd) if wv and not rst else None,rst=bool(rst))
        rows+=1
    assert rows>70 and model.stalled_reads>=18 and model.accepted_reads>=17
    result=dict(status='PASS',cycles=rows,reads=model.accepted_reads,
                stalled_lane_cycles=model.stalled_reads,writes=model.writes,
                banks=8,lanes=4,read_before_write=True)
    path.with_suffix('.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

if __name__=='__main__':main()
