#!/usr/bin/env python3
"""Replay independent VCS stimulus into the shared single-MVM Python mirror."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"model"))
from azilla_cycle_model.core_iteration import CoreIteration


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--log",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    core=CoreIteration()
    lifecycle={"RESET":0,"INIT":1,"IDLE":2,"ACCUMULATE":3,"FINALIZE":4,"WAIT_COMMIT":5,"COMMIT":6,"DONE":7}
    job={"IDLE":0,"STATE_REQ":1,"STATE_WAIT":2,"LOAD":3,"RUN":4,"RETIRE":5}
    checked=0;fields=0;masks=[]
    field_by_cycle={}
    text=args.log.read_text()
    if "PASS CORE_SHARED_MVM" not in text:
        raise RuntimeError("RTL arithmetic/lifecycle test did not pass")
    for c,lane,value in re.findall(r"CORE_SHARED_FIELD cycle=(\d+) lane=(\d+) value=(-?\d+)",text):
        field_by_cycle.setdefault(int(c),[]).append((int(lane),int(value)))
    for line in text.splitlines():
        words=line.split()
        if words[:1]!=["CORE_SHARED_CYCLE"]:
            continue
        raw=words[1:]
        c=int(raw[0]);o=core.outputs()
        expected=[int(o.init_done),int(o.weight_init_ready),int(o.iter_done),o.state_current,o.state_next,
                  int(o.job_ready),int(o.state_req_valid),o.state_req_block_id,int(o.weight_ready),int(o.job_done),
                  lifecycle[core.core_state],job[core.off_state],int(core.diagonal_captured),
                  int(core.local_mvm.done),core.off_beat,core.lfsr_state]
        observed=[]
        for i,value in enumerate(raw[15:]):
            if i==4 and value.lower()=="xxxxxxxx" and not o.iter_done:
                observed.append(expected[i]);masks.append(c)
            else:
                observed.append(int(value,16 if i in (3,4,15) else 10))
        if observed!=expected:
            raise AssertionError(f"cycle {c}: expected {expected}, RTL {observed}")
        # The TB prints fields at the preceding negedge with the upcoming cycle
        # counter value. They are the same pre-edge state as this trace row.
        for lane,value in field_by_cycle.get(c,[]):
            if core.accumulator_total[lane]!=value:
                raise AssertionError(f"cycle{c} lane{lane}: Python={core.accumulator_total[lane]} RTL={value}")
            fields+=1
        core.tick(init_start=bool(int(raw[1])),weight_init_valid=bool(int(raw[2])),weight_init_data=int(raw[3],16),
                  iter_start=bool(int(raw[4])),partials_done=bool(int(raw[5])),commit=bool(int(raw[6])),
                  job_valid=bool(int(raw[7])),source=int(raw[8]),transpose=bool(int(raw[9])),
                  state_req_ready=bool(int(raw[10])),state_rsp_valid=bool(int(raw[11])),state_rsp_data=int(raw[12],16),
                  weight_valid=bool(int(raw[13])),weight_data=int(raw[14],16),
                  init_state=0xa55aa55a,noise_seed=0x12345678,coeff_a=3,coeff_b=-2,noise_amplitude=5)
        checked+=1
    if fields!=96: raise AssertionError(f"expected96 arithmeticfields, compared{fields}")
    report={"matched":True,"scope":"single_MVM_diagonal_directed_transpose_noise_commit",
            "cycles_compared":checked,"fields_compared":fields,"undefined_state_next_cycles":masks,
            "full_mesh_certificate":False}
    sources=[ROOT/"rtl/core.sv",ROOT/"rtl/mvm.sv",ROOT/"rtl/j_block_sram.sv",
             ROOT/"tb/core_shared_mvm_vcs_tb.sv",ROOT/"model/azilla_cycle_model/core_iteration.py",
             ROOT/"model/azilla_cycle_model/compute.py",args.log]
    report["source_and_evidence_sha256"]={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(f"PASS shared single-MVM Python/VCS cycles={checked} fields={fields}")


if __name__=="__main__":main()
