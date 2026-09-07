#!/usr/bin/env python3
"""Fail-closed conservation audit for completed CIR-route-matched endpoints."""
import argparse,csv,hashlib,json
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prefix',required=True,type=Path)
    a=ap.parse_args();p=a.prefix
    files=[Path(str(p)+s) for s in ('_summary.json','_dram.csv','_noc.csv','_nodes.csv')]
    summary=json.loads(files[0].read_text())
    core=summary['cores_only_ablation'];contract=core['pipeline_contract']
    assert contract['fetch_policy']=='single_fetch_two_slot_cir_routes_v3'
    with files[1].open() as f:dram=list(csv.DictReader(f))
    jobs=core['unordered_interaction_blocks']*contract['iterations']
    assert core['weight_block_reads']==jobs
    assert core['directed_core_jobs']==2*jobs
    assert sum(int(d['scheduled_jobs']) for d in dram)==jobs
    assert sum(int(d['accepted_requests']) for d in dram)==32*jobs
    assert all(int(d['accepted_requests'])==int(d['completed_requests']) and
               int(d['outstanding_requests'])==0 for d in dram)
    report=dict(status='conservation-pass',fetch_policy=contract['fetch_policy'],
        paper_ready=False,geometry_rtl_verified=False,
        timing_cycles=summary['timing_cycles'],canonical_jobs=jobs,
        directed_jobs=2*jobs,accepted_memory_requests=32*jobs,
        files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files})
    target=p.parent/'audit.json'
    if target.exists():
        if json.loads(target.read_text()) != report:
            raise SystemExit('refusing to overwrite inconsistent audit')
        print('Existing audit reverified against completed metrics')
        return
    target.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
