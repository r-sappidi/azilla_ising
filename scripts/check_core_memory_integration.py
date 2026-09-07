#!/usr/bin/env python3
"""Compare memory, delivery, core, and optional real-mesh RTL handshakes."""
from pathlib import Path
import argparse
import json
import hashlib
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from azilla_cycle_model.hierarchy import DmaCommand
from azilla_cycle_model.ramulator import RamulatorBackend


def simulate(stall, config, use_mesh=False, engines=1, jobs=2):
    from azilla_cycle_model.core_pipeline import CoreMemoryPipeline
    from azilla_cycle_model.workload import Geometry
    backend = RamulatorBackend(ROOT / "build/cycle_model_ramulator/libazilla_ramulator.so",
                               config, ROOT / "tb/datasets/g256_smoke.txt", 8, 8,
                               timing_only=True)
    queues = {s: [DmaCommand(0,b,0,b), DmaCommand(b,0,b,0)]*(jobs//2)
              for s,b in ((0,1),(4,2),(6,4))}
    events = []
    memory_events = []
    source_indices = {0:0,4:1,6:2}
    pipeline = CoreMemoryPipeline(
        Geometry(2,1,2,2), backend, queues, {s:engines for s in queues},
        stall_period=stall, use_mesh=use_mesh,
        observer=lambda c,e,s,a,b: events.append((c,e,source_indices[s],a,b)),
        memory_observer=lambda c,e,s,t: memory_events.append((c,e,s,t)),
    )
    try:
        pipeline.run(100000)
        return events, memory_events, pipeline.audit
    finally:
        backend.finalize()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--config", type=Path, default=ROOT/"tb/ramulator_128x32.yaml")
    p.add_argument("--stall", type=int, default=0)
    p.add_argument("--mesh", action="store_true")
    p.add_argument("--engines", type=int, default=1)
    p.add_argument("--jobs", type=int, default=2)
    args = p.parse_args()
    pattern = re.compile(r"CORE_INT cycle=(\d+) event=(\w+) source=(\d+) dst=(\d+) src=(\d+)")
    rtl = [(int(c),e,int(s),int(a),int(b)) for c,e,s,a,b in
           pattern.findall((args.root/f"stall{args.stall}.log").read_text())]
    observed, memory_events, audit = simulate(args.stall,args.config,args.mesh,args.engines,args.jobs)
    memory_pattern = re.compile(r"CORE_MEM cycle=(\d+) event=(\w+) system=(\d+) tag=(\d+)")
    rtl_memory = sorted((int(c),e,int(s),int(t)) for c,e,s,t in
                        memory_pattern.findall((args.root/f"stall{args.stall}.log").read_text()))
    memory_events.sort()
    rtl.sort();observed.sort()
    report = {"scope":"memory_shared_delivery_core"+("_mesh" if args.mesh else ""), "cross_noc_covered":args.mesh,
              "full_core_mode_certificate":False, "rtl_events":len(rtl),"model_events":len(observed),
              "matched":rtl==observed and rtl_memory==memory_events,"engines_per_source":args.engines,
              "rtl_memory_events":len(rtl_memory),"model_memory_events":len(memory_events),
              "directed_jobs_per_source":args.jobs,"pipeline_audit":audit}
    inputs = [args.config, args.root/f"stall{args.stall}.log", ROOT/"tb/core_memory_integration_vcs_tb.sv",
              ROOT/"rtl/core_local_mvm_engine.sv", ROOT/"rtl/dram_weight_streamer.sv",
              ROOT/"rtl/mvm.sv", ROOT/"rtl/azilla_floo_router.sv",
              ROOT/"tb/ramulator_node_frontend.sv", ROOT/"tb/ramulator_dpi_bridge.sv",
              ROOT/"tb/ramulator_dpi.cpp", ROOT/"model/azilla_cycle_model/core_pipeline.py",
              ROOT/"model/azilla_cycle_model/core_local.py", ROOT/"model/azilla_cycle_model/memory.py",
              ROOT/"model/azilla_cycle_model/compute.py", ROOT/"model/azilla_cycle_model/noc.py"]
    report["source_and_evidence_sha256"] = {
        str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    if rtl != observed:
        i = next((i for i,(a,b) in enumerate(zip(rtl,observed)) if a!=b),min(len(rtl),len(observed)))
        report.update(first_mismatch=i,rtl=rtl[i:i+3],model=observed[i:i+3])
    if rtl_memory != memory_events:
        i = next((i for i,(a,b) in enumerate(zip(rtl_memory,memory_events)) if a!=b),min(len(rtl_memory),len(memory_events)))
        report.update(first_memory_mismatch=i,rtl_memory=rtl_memory[i:i+3],model_memory=memory_events[i:i+3])
    (args.root/f"comparison_stall{args.stall}.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if not report["matched"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
