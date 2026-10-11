#!/usr/bin/env python3
"""Compare CIR, cores-only, and static hybrid on the frozen paper suite."""
from __future__ import annotations
import argparse,csv,json,time
from pathlib import Path
from azilla_cycle_model.events import EventCompressedPerformanceModel,EventPerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset,Geometry

def names(text): return {x.strip() for x in text.split(',') if x.strip()}
def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,default=Path('results/paper_benchmarks/datasets/manifest.json'))
 p.add_argument('--output-dir',type=Path,default=Path('results/paper_benchmarks/execution_modes'))
 p.add_argument('--names',type=names);p.add_argument('--families',type=names)
 p.add_argument('--h0-mvms',type=int,default=4);p.add_argument('--h1-mvms',type=int,default=2);p.add_argument('--cross-mvms',type=int,default=4);p.add_argument('--force',action='store_true');a=p.parse_args()
 entries=json.loads(a.manifest.read_text())['datasets']
 if a.names: entries=[e for e in entries if e['name'] in a.names]
 if a.families: entries=[e for e in entries if e['family'] in a.families]
 rows=[];a.output_dir.mkdir(parents=True,exist_ok=True)
 for e in entries:
  g=Geometry(**e['geometry']);d=BlockOccupancyDataset.load(e['dataset']);pairs=len(d.active_block_pairs())
  for mode in ('cir','cores-only','hybrid'):
   out=a.output_dir/e['name']/mode/'point.json'
   if out.exists() and not a.force: row=json.loads(out.read_text())
   else:
    c=EventPerformanceConfig(h0_mvm_count=a.h0_mvms,h1_mvm_count=a.h1_mvms,cross_mvm_count=a.cross_mvms,execution_mode=mode)
    start=time.perf_counter();r=EventCompressedPerformanceModel(g,d,c).run();wall=time.perf_counter()-start
    row={'dataset':e['name'],'family':e['family'],'problem_vertices':e['problem_vertices'],'capacity_spins':e['capacity_spins'],'average_degree':e['average_degree'],'occupied_blocks':pairs,'mode':mode,'accuracy':r.accuracy,'iteration_cycles':r.iteration_cycles,'directed_core_jobs':r.directed_core_jobs,'weight_block_reads':r.weight_block_reads,'remote_state_packets':r.remote_state_packets,'injected_flits':r.injected_flits,'physical_link_flits':r.physical_link_flits,'injection_stalls':r.injection_stalls,'link_stalls':r.link_stalls,'wall_seconds':wall}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(row,indent=2)+'\n')
   rows.append(row);print(e['name'],mode,row['iteration_cycles'],flush=True)
 with (a.output_dir/'summary.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
if __name__=='__main__':main()
