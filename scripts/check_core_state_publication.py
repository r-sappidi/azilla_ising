#!/usr/bin/env python3
"""Exact publication timing and payload/cache audit of the independent VCS TB."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"model"))
from azilla_cycle_model.core_state_publication import core_state_publication_plan
from azilla_cycle_model.core_publication import run_core_state_publication
from azilla_cycle_model.workload import Geometry,ScheduledBlock

def state_word(epoch,block):
    return ((0x9e3779b9*(block+1)) ^ (0x1020304*(epoch+1)))&0xffffffff

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--log",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();text=args.log.read_text()
    if "PASS CORE_STATE_PUBLICATION" not in text: raise RuntimeError("RTL incomplete")
    g=Geometry(2,1,2,2)
    plan=core_state_publication_plan(g,[ScheduledBlock(0,1),ScheduledBlock(0,2),ScheduledBlock(0,4)])
    phases={(int(e),phase):int(c) for e,phase,c in re.findall(r"PUB_PHASE epoch=(\d+) phase=(\w+) cycle=(\d+)",text)}
    observed_net=[(int(e),int(c),scope,int(n),int(b),int(data,16)) for e,c,scope,n,b,data in
      re.findall(r"PUB_NET epoch=(\d+) cycle=(\d+) scope=(\w+) node=(\d+) block=(\d+) data=([0-9a-fA-F]+)",text)]
    observed_local=[(int(e),int(c),event,int(n),int(h),int(b),int(data,16)) for e,c,event,n,h,b,data in
      re.findall(r"PUB_LOCAL epoch=(\d+) cycle=(\d+) event=(\w+) h1=(\d+) h0=(-?\d+) block=(\d+) data=([0-9a-fA-F]+)",text)]
    observed_reads=[(int(e),int(c),int(h),int(b),int(data,16)) for e,c,h,b,data in
      re.findall(r"PUB_READ epoch=(\d+) cycle=(\d+) h0=(\d+) block=(\d+) data=([0-9a-fA-F]+)",text)]
    expected_net=[];expected_local=[];expected_reads=[];epochs=[];mesh=None
    for epoch in range(2):
        gather=phases[(epoch,"gather")];network=phases[(epoch,"network")]
        fill=phases[(epoch,"fill")];ready=phases[(epoch,"compute_ready")]
        phase=run_core_state_publication(g,[ScheduledBlock(0,1),ScheduledBlock(0,2),ScheduledBlock(0,4)],
          states={b:state_word(epoch,b) for b in range(8)},epoch=epoch,start_cycle=gather,mesh=mesh,
          observer=lambda c,s,n,d,f:expected_net.append((epoch,c,s,n,f.block_id,f.data)),
          local_observer=lambda c,event,n,h,b,data:expected_local.append((epoch,c,event,n,h,b,data)))
        mesh=phase.mesh
        replay=phase.network
        for node,writes in enumerate(plan.local_writes_by_h1):
            for i,(local_h0,block) in enumerate(writes):
                h0=node*g.h0_per_h1+local_h0
                expected_reads.append((epoch,ready+block+1,h0,block,state_word(epoch,block)))
        if (network-gather,fill-network,ready-fill)!=(plan.gather_cycles,replay.elapsed_cycles,plan.local_fill_cycles):
            raise AssertionError(f"phase duration mismatch epoch{epoch}")
        epochs.append(dict(epoch=epoch,gather_cycles=plan.gather_cycles,remote_cycles=replay.elapsed_cycles,
                           local_fill_cycles=plan.local_fill_cycles,compute_ready_after_cycles=ready-gather))
    for name,observed,expected in (("network",observed_net,expected_net),("local",observed_local,expected_local),("cache",observed_reads,expected_reads)):
        if sorted(observed)!=sorted(expected):
            raise AssertionError(f"{name} mismatch: RTL={sorted(observed)} Python={sorted(expected)}")
    sources=[args.log,ROOT/"tb/core_state_publication_vcs_tb.sv",ROOT/"rtl/banked_state_sram.sv",
             ROOT/"rtl/azilla_floo_router.sv",ROOT/"model/azilla_cycle_model/core_state_publication.py",
             ROOT/"model/azilla_cycle_model/core_publication.py",
             ROOT/"model/azilla_cycle_model/events.py",ROOT/"model/azilla_cycle_model/noc.py"]
    report={"matched":True,"scope":"state_gather_real_noc_filtered_local_cache_fill",
            "epochs":epochs,"remote_publications":plan.remote_publications,
            "local_writes_by_h1":plan.local_writes_by_h1,"network_events_compared":len(expected_net),
            "local_events_compared":len(expected_local),"cache_responses_compared":len(expected_reads),
            "full_iteration_certificate":False,
            "source_and_evidence_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(f"PASS state publication epochs=2 network_events={len(expected_net)} cache_reads={len(expected_reads)} cycles_per_epoch={epochs[0]['compute_ready_after_cycles']}")
if __name__=="__main__":main()
