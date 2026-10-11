#!/usr/bin/env python3
"""Screen execution modes across frozen mapping artifacts."""
import argparse,csv,json,time
from pathlib import Path
from azilla_cycle_model.events import EventCompressedPerformanceModel,EventPerformanceConfig
from azilla_cycle_model.mapping_adapter import MappingArtifact

p=argparse.ArgumentParser();p.add_argument('--artifact-root',type=Path,default=Path('results/paper_benchmarks/mapping_comparison'));p.add_argument('--output-dir',type=Path,default=Path('results/paper_benchmarks/mapped_execution_modes'));a=p.parse_args();rows=[]
for manifest in sorted(a.artifact_root.glob('*/*/manifest.json')):
 art=MappingArtifact.load(manifest.parent);dataset=art.occupancy_dataset();schedule=art.schedule()
 name=manifest.parent.parent.name;variant=manifest.parent.name
 for mode in ('cir','cores-only','hybrid'):
  start=time.perf_counter();r=EventCompressedPerformanceModel(art.geometry,dataset,EventPerformanceConfig(h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4,execution_mode=mode)).run(schedule=schedule);wall=time.perf_counter()-start
  row={'dataset':name,'mapping':variant,'mode':mode,'blocks':len(schedule),'iteration_cycles':r.iteration_cycles,'directed_core_jobs':r.directed_core_jobs,'weight_block_reads':r.weight_block_reads,'injected_flits':r.injected_flits,'physical_link_flits':r.physical_link_flits,'link_stalls':r.link_stalls,'wall_seconds':wall,'accuracy':r.accuracy};rows.append(row);print(name,variant,mode,r.iteration_cycles,flush=True)
a.output_dir.mkdir(parents=True,exist_ok=True)
with (a.output_dir/'summary.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
