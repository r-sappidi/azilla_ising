#!/usr/bin/env python3
"""Independent block/byte accounting, not a replacement for RTL timing checks."""
import argparse
import csv
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset',type=Path,required=True)
    ap.add_argument('--cir',type=Path,required=True)
    ap.add_argument('--shared',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--rtl-internal',type=Path)
    a=ap.parse_args()
    if a.output.exists():ap.error('refusing to overwrite evidence')
    pairs=set()
    with a.dataset.open() as f:
        assert int(next(f).split()[0])==65536
        for row in f:
            if not row.strip():continue
            i,j,w=map(int,row.split())
            x,y=sorted(((i-1)//32,(j-1)//32))
            if w and x!=y:pairs.add((x,y))
    # Fixed 8 H0 x 256 cores; each core owns 32 spins. Each endpoint
    # receives 32 x 256-bit beats per off-diagonal weight block.
    delivery=Counter();source=Counter();classes=Counter()
    for x,y in pairs:
        hx,hy=x//256,y//256
        level='h0' if hx==hy else 'h1'
        classes[level]+=1
        source[hx if level=='h0' else 8]+=64
        delivery[hx]+=32;delivery[hy]+=32
    cir=json.loads(a.cir.read_text());shared=json.loads(a.shared.read_text())
    measured=shared['cores_only_ablation']['pipeline_contract']['pipeline_audit']['local_delivery_beats']
    assert {str(k):v for k,v in source.items()}==measured
    assert cir['scheduled_jobs']['total']==len(pairs)
    rtl_checks={}
    if a.rtl_internal:
        with a.rtl_internal.open() as f:
            rows={(r['endpoint_class'],int(r['endpoint_id']),r['transfer']):
                  int(r['accepted_transfers']) for r in csv.DictReader(f)}
        for endpoint in range(9):
            level,index=('h0',endpoint) if endpoint<8 else ('h1',0)
            jobs=source[endpoint]//64
            for transfer,count in [('command',jobs),('weight',32*jobs),('partial',8*jobs)]:
                key=(level,index,transfer)
                assert rows[key]==count,(key,rows[key],count)
                rtl_checks[str(key)]=count
    # A necessary bound conditional on the implemented one-beat/cycle per
    # source AND destination-H0 arbitration. Not a universal cores-only bound.
    bound=max(max(delivery.values()),max(source.values()))
    result=dict(status='pass',scope='independent occupancy and conditional bandwidth lower bound',
        canonical_blocks=len(pairs),classes=dict(classes),
        delivered_beats_by_h0=dict(delivery),delivered_beats_by_source=dict(source),
        minimum_core_delivery_cycles=bound,
        rtl_internal_accepted_counts=rtl_checks,
        assumptions=['one accepted 256-bit weight beat per source per cycle',
                     'one accepted weight beat per destination H0 per cycle',
                     'two full unicast weight deliveries per canonical block'],
        limitation='does not validate other core timing or prove this is the best possible baseline',
        inputs_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (a.dataset,a.cir,a.shared)})
    if a.rtl_internal:
        result['inputs_sha256'][str(a.rtl_internal)]=hashlib.sha256(a.rtl_internal.read_bytes()).hexdigest()
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
