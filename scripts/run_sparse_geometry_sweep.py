#!/usr/bin/env python3
"""Compare physical H0/core decompositions on capacity-matched sparse graphs."""

from __future__ import annotations
import argparse,csv,json,platform,sys,time
from pathlib import Path
from azilla_cycle_model.events import EventCompressedPerformanceModel,EventPerformanceConfig
from azilla_cycle_model.metrics import write_event_performance_metrics
from azilla_cycle_model.workload import BlockOccupancyDataset,Geometry

WORKLOADS=(
 ('toroidal_d4_n65536',65536,1,1),('random_d25_n65536',65536,1,1),
 ('toroidal_d4_n131072',131072,2,1),('random_d25_n131072',131072,2,1),
 ('toroidal_d4_n1048576',1048576,4,4),
)
GEOMETRIES=((4,512),(8,256),(16,128))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,default=Path('results/paper_benchmarks/sparse_geometry'));p.add_argument('--workloads',help='comma-separated workload names');p.add_argument('--skip-summary',action='store_true');p.add_argument('--force',action='store_true');a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
 selected={x.strip() for x in a.workloads.split(',')} if a.workloads else None
 points=[]
 for name,spins,mx,my in WORKLOADS:
  if selected is not None and name not in selected: continue
  dataset_path=Path('results/paper_benchmarks/datasets/prepared')/(name+'.txt');dataset=BlockOccupancyDataset.load(dataset_path)
  for h0,cores in GEOMETRIES:
   for provisioning,h0_mvms in (('iso-total-h0-mvms',16//h0),('fixed-4-mvms-per-h0',4)):
    if provisioning=='fixed-4-mvms-per-h0' and h0==4: continue
    label=f'{name}_{mx}x{my}_h0{h0}_c{cores}_{provisioning}'
    run=a.output_dir/label;point_path=run/'point.json'
    if point_path.exists() and not a.force: point=json.loads(point_path.read_text());points.append(point);print('SKIP',label,flush=True);continue
    geometry=Geometry(mx,my,h0,cores)
    if geometry.spin_count!=spins: raise RuntimeError(f'capacity mismatch for {label}')
    config=EventPerformanceConfig(h0_mvm_count=h0_mvms,h1_mvm_count=2,cross_mvm_count=4,execution_mode='cir')
    start=time.perf_counter();result=EventCompressedPerformanceModel(geometry,dataset,config).run();wall=time.perf_counter()-start
    run.mkdir(parents=True,exist_ok=True);paths=write_event_performance_metrics(run/'metrics',result,geometry,config)
    level_jobs={}
    for resource in result.work_resources:level_jobs[resource.level]=level_jobs.get(resource.level,0)+resource.scheduled_jobs
    point={'workload':name,'spins':spins,'mesh_x':mx,'mesh_y':my,'h1_nodes':mx*my,'h0_per_h1':h0,'cores_per_h0':cores,'cores_per_h1':h0*cores,'provisioning':provisioning,'h0_mvms_per_h0':h0_mvms,'aggregate_h0_mvms_per_h1':h0*h0_mvms,'h1_mvms_per_h1':2,'cross_mvms_per_h1':4,'memory_interfaces_per_h1':h0+2,'total_mvm_engines':mx*my*(h0*h0_mvms+6),'accuracy':result.accuracy,'initialization_cycles':result.initialization_cycles,'iteration_cycles':result.iteration_cycles,'total_cycles':result.total_cycles,'occupied_blocks':result.unordered_interaction_blocks,'h0_jobs':level_jobs.get('h0',0),'h1_jobs':level_jobs.get('h1',0),'cross_jobs':level_jobs.get('cross',0),'injected_flits':result.injected_flits,'physical_link_flits':result.physical_link_flits,'average_hops':result.average_hops,'injection_stalls':result.injection_stalls,'link_stalls':result.link_stalls,'wall_seconds':wall,'metrics':{k:str(v) for k,v in paths.items()}}
    point_path.write_text(json.dumps(point,indent=2)+'\n');points.append(point);print(label,result.iteration_cycles,flush=True)
 if not a.skip_summary:
  with (a.output_dir/'summary.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=[k for k in points[0] if k!='metrics']);w.writeheader();w.writerows({k:v for k,v in row.items() if k!='metrics'} for row in points)
 if not a.skip_summary:
  (a.output_dir/'provenance.json').write_text(json.dumps({'command':sys.argv,'python':sys.version,'platform':platform.platform(),'workloads':[x[0] for x in WORKLOADS],'geometries':[{'h0_per_h1':h,'cores_per_h0':c} for h,c in GEOMETRIES],'accuracy':'calibrated-extrapolation','note':'All points hold 2048 cores/H1. Iso-total points hold 16 H0 MVMs/H1; memory endpoint count still varies with H0 count.'},indent=2)+'\n')
if __name__=='__main__':main()
