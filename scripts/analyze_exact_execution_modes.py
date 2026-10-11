#!/usr/bin/env python3
"""Consolidate exact-event execution-mode finalists for paper plots."""
import csv,json,re
from pathlib import Path
root=Path('results/paper_benchmarks/exact_execution_modes');rows=[]
for p in sorted(root.glob('*/*/metrics_summary.json')):
 d=json.loads(p.read_text());t=d['timing_cycles'];a=d.get('cores_only_ablation',{});n=d.get('noc',{})
 wall=None;wp=p.parent/'wall.time'
 if wp.exists():
  m=re.search(r'Elapsed \(wall clock\) time.*: ([0-9:.]+)',wp.read_text())
  if m: wall=m.group(1)
 rows.append({'dataset':p.parent.parent.name,'mode':p.parent.name,'accuracy':d['accuracy'],'iteration_cycles':t['iteration'],'total_cycles':t['total'],'directed_core_jobs':a.get('directed_core_jobs',0),'weight_block_reads':a.get('weight_block_reads',0),'remote_state_packets':a.get('remote_state_packets',0),'injected_flits':n.get('injected_flits',0),'physical_link_flits':n.get('physical_link_flits',0),'wall_time':wall or ''})
if not rows: raise SystemExit('no exact execution-mode results')
with (root/'summary.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(root/'summary.csv')
