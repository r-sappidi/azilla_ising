#!/usr/bin/env python3
"""Sweep deterministic global batch placement parameters on mapped graphs."""

from __future__ import annotations

import argparse,csv,json,time
from pathlib import Path

from azilla_cycle_model.dynamic_scheduler import select_batch_balanced_core_pairs
from azilla_cycle_model.events import EventCompressedPerformanceModel,EventPerformanceConfig
from azilla_cycle_model.mapping_adapter import MappingArtifact


def values(text, convert): return [convert(item) for item in text.split(',')]
def names(text): return {item.strip() for item in text.split(',') if item.strip()}


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--artifact-root',type=Path,default=Path('results/paper_benchmarks/mapping_comparison'))
 p.add_argument('--oracle-root',type=Path,default=Path('results/paper_benchmarks/hybrid_oracle'))
 p.add_argument('--output-dir',type=Path,required=True)
 p.add_argument('--datasets',type=names);p.add_argument('--exclude-datasets',type=names)
 p.add_argument('--mappings',type=names)
 p.add_argument('--cir-cost-scales',default='0.5,0.75,1,1.25,1.5,2')
 p.add_argument('--traffic-weights',default='0,1,2')
 p.add_argument('--config-json',type=Path,
                help='use one frozen cir_cost_scale/traffic_weight pair')
 p.add_argument('--h0-mvms',type=int,default=4);p.add_argument('--h1-mvms',type=int,default=2);p.add_argument('--cross-mvms',type=int,default=4)
 p.add_argument('--force',action='store_true');a=p.parse_args()
 if a.config_json:
  frozen=json.loads(a.config_json.read_text())
  specs=[(float(frozen['cir_cost_scale']),int(frozen['traffic_weight']))]
 else:
  specs=[(scale,weight) for scale in values(a.cir_cost_scales,float) for weight in values(a.traffic_weights,int)]
 manifests=sorted(a.artifact_root.glob('*/*/manifest.json'))
 manifests=[m for m in manifests if (not a.datasets or m.parent.parent.name in a.datasets) and (not a.exclude_datasets or m.parent.parent.name not in a.exclude_datasets) and (not a.mappings or m.parent.name in a.mappings)]
 if not manifests:p.error('no matching mapping artifacts')
 rows=[]
 for manifest in manifests:
  art=MappingArtifact.load(manifest.parent);dataset=art.occupancy_dataset();schedule=art.schedule();name=manifest.parent.parent.name;mapping=manifest.parent.name
  oracle=json.loads((a.oracle_root/name/mapping/'summary.json').read_text());run_dir=a.output_dir/name/mapping;out=run_dir/'summary.json'
  if out.exists() and not a.force:
   saved=json.loads(out.read_text())['points'];rows.extend(saved);print('SKIP',name,mapping,flush=True);continue
  points=[]
  for scale,weight in specs:
   start=time.perf_counter();placement=select_batch_balanced_core_pairs(art.geometry,schedule,h0_mvm_count=a.h0_mvms,h1_mvm_count=a.h1_mvms,cross_mvm_count=a.cross_mvms,cir_cost_scale=scale,traffic_cycle_weight=weight);selection=time.perf_counter()-start
   start=time.perf_counter();result=EventCompressedPerformanceModel(art.geometry,dataset,EventPerformanceConfig(h0_mvm_count=a.h0_mvms,h1_mvm_count=a.h1_mvms,cross_mvm_count=a.cross_mvms,execution_mode='hybrid')).run(schedule=schedule,hybrid_core_pairs=placement.core_pairs);simulation=time.perf_counter()-start
   fixed=oracle['fixed_policy_oracle_cycles'];searched=oracle['best_searched']['iteration_cycles']
   row={'dataset':name,'mapping':mapping,'cir_cost_scale':scale,'traffic_weight':weight,'iteration_cycles':result.iteration_cycles,'core_pairs':placement.core_decisions,'cir_pairs':placement.cir_decisions,'predicted_completion_cycles':placement.predicted_completion_cycles,'weight_block_reads':result.weight_block_reads,'remote_state_packets':result.remote_state_packets,'injected_flits':result.injected_flits,'physical_link_flits':result.physical_link_flits,'link_stalls':result.link_stalls,'selection_wall_seconds':selection,'simulation_wall_seconds':simulation,'fixed_policy_oracle_cycles':fixed,'searched_placement_cycles':searched,'speedup_over_fixed_oracle':fixed/result.iteration_cycles,'regret_to_searched':result.iteration_cycles/searched-1,'accuracy':result.accuracy}
   points.append(row);rows.append(row);print(name,mapping,scale,weight,result.iteration_cycles,placement.core_decisions,flush=True)
  run_dir.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps({'dataset':name,'mapping':mapping,'points':points},indent=2)+'\n')
 a.output_dir.mkdir(parents=True,exist_ok=True)
 with (a.output_dir/'summary.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


if __name__=='__main__':main()
