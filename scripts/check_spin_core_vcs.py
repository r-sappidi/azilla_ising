#!/usr/bin/env python3
"""VCS check of existing diagonal/update core; no cores-only integration claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from check_cycle_model import python_spin_core_trace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    build=ROOT/"build/spin_core_vcs_diagonal_trace"
    build.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.setdefault("VCS_HOME","/home/hwpeng/vcs")
    env.update(VCS_ARCH_OVERRIDE="linux",VCS_TARGET_ARCH="linux64")
    sources=["rtl/ising_pkg.sv","rtl/j_block_sram.sv","rtl/mvm.sv",
             "rtl/core.sv","tb/cycle_model_spin_core_trace_tb.sv"]
    command=[str(Path(env["VCS_HOME"])/"bin/vcs"),"-full64","-sverilog",
             "-timescale=1ns/1ps","-LDFLAGS","-no-pie",
             "-top","cycle_model_spin_core_trace_tb",f"-Mdir={build/'csrc'}",
             "-o",str(build/"simv"),*sources]
    with (args.output/"build.log").open("w") as log:
        subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (args.output/"rtl.log").open("w") as log:
        subprocess.run([str(build/"simv")],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    observed=[];totals=[];invalid_state_next_cycles=[]
    for line in (args.output/"rtl.log").read_text().splitlines():
        fields=line.split()
        if fields[:1]==["CORE"]:
            values=[]
            for index,value in enumerate(fields[1:]):
                if value.lower()=="x" and index==8 and int(fields[2])<=4:
                    # accumulator_total is deliberately not reset in RTL.
                    # state_next has no validity until CORE_FINALIZE writes it;
                    # never mask any control field or a valid final state.
                    values.append(None)
                    invalid_state_next_cycles.append(int(fields[1]))
                else:
                    values.append(int(value))
            observed.append(tuple(values))
        if fields[:1]==["TOTAL"]: totals.append(tuple(map(int,fields[1:3])))
    expected,expected_totals=python_spin_core_trace()
    report={"scope":"existing_diagonal_mvm_partial_accumulation_noise_update_commit",
            "shared_offdiagonal_engine_covered":False,
            "matched":len(observed)==len(expected) and all(
                all(a is None or a==b for a,b in zip(row,reference))
                for row,reference in zip(observed,expected)) and totals==expected_totals,
            "cycles_compared":len(expected),"arithmetic_lanes_compared":len(expected_totals),
            "undefined_state_next_cycles":invalid_state_next_cycles,
            "mask_reason":"unreset accumulator_total; state_next invalid before finalization",
            "source_sha256":{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}}
    (args.output/"comparison.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if not report["matched"]:
        raise SystemExit(1)


if __name__=="__main__": main()
