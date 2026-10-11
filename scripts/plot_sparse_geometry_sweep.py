#!/usr/bin/env python3
"""Plot sparse H0/core geometry timing, traffic, locality, and cost proxies."""
import argparse,csv
from pathlib import Path
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--summary',type=Path,default=Path('results/paper_benchmarks/sparse_geometry/summary.csv'));p.add_argument('--output-dir',type=Path,default=Path('plots/paper/sparse_geometry'));a=p.parse_args();rows=list(csv.DictReader(a.summary.open()));a.output_dir.mkdir(parents=True,exist_ok=True)
styles={('iso-total-h0-mvms','4x512'):('#2878B5','o'),('iso-total-h0-mvms','8x256'):('#E89C31','s'),('iso-total-h0-mvms','16x128'):('#2E8B57','^'),('fixed-4-mvms-per-h0','8x256'):('#B05AA5','D'),('fixed-4-mvms-per-h0','16x128'):('#D95319','v')}
for family in ('toroidal','random'):
 subset=[r for r in rows if r['workload'].startswith(family)];fig,ax=plt.subplots(figsize=(6.8,4.2))
 for (prov,geom),(color,marker) in styles.items():
  h0,cores=geom.split('x');q=sorted([r for r in subset if r['provisioning']==prov and r['h0_per_h1']==h0 and r['cores_per_h0']==cores],key=lambda r:int(r['spins']))
  if q:ax.plot([int(r['spins']) for r in q],[int(r['iteration_cycles']) for r in q],marker=marker,color=color,label=f'{geom}, {prov.replace("-"," ")}')
 ax.set_xscale('log',base=2);ax.set_yscale('log');ax.set_xlabel('Spins');ax.set_ylabel('Iteration cycles');ax.grid(alpha=.25);ax.legend(frameon=False,fontsize=8);fig.tight_layout()
 for ext in ('png','pdf'):fig.savefig(a.output_dir/f'{family}_latency.{ext}',dpi=220)
 plt.close(fig)
metrics=(('physical_link_flits','Physical-link flits'),('injected_flits','Injected flits'),('average_hops','Average hops'))
for key,ylabel in metrics:
 fig,axes=plt.subplots(1,len(sorted(set(r['workload'] for r in rows))),figsize=(15,3.4),sharey=False)
 for ax,workload in zip(axes,sorted(set(r['workload'] for r in rows))):
  q=[r for r in rows if r['workload']==workload and r['provisioning']=='iso-total-h0-mvms'];q.sort(key=lambda r:int(r['h0_per_h1']))
  ax.bar([f"{r['h0_per_h1']}×{r['cores_per_h0']}" for r in q],[float(r[key]) for r in q],color=['#2878B5','#E89C31','#2E8B57']);ax.set_title(workload.replace('_','\n'),fontsize=8);ax.tick_params(axis='x',rotation=35);ax.grid(axis='y',alpha=.2)
 axes[0].set_ylabel(ylabel);fig.tight_layout()
 for ext in ('png','pdf'):fig.savefig(a.output_dir/f'geometry_{key}.{ext}',dpi=220)
 plt.close(fig)
fig,axes=plt.subplots(1,3,figsize=(12,3.8))
for ax,level in zip(axes,('h0_jobs','h1_jobs','cross_jobs')):
 q=[r for r in rows if r['workload']=='random_d25_n131072' and r['provisioning']=='iso-total-h0-mvms'];q.sort(key=lambda r:int(r['h0_per_h1']));total=[sum(int(r[k]) for k in ('h0_jobs','h1_jobs','cross_jobs')) for r in q]
 ax.bar([f"{r['h0_per_h1']}×{r['cores_per_h0']}" for r in q],[int(r[level])/t if t else 0 for r,t in zip(q,total)],color='#4C78A8');ax.set_title(level.replace('_',' ').upper());ax.set_ylim(0,1);ax.tick_params(axis='x',rotation=25);ax.grid(axis='y',alpha=.2)
axes[0].set_ylabel('Fraction of occupied block jobs');fig.tight_layout()
for ext in ('png','pdf'):fig.savefig(a.output_dir/f'locality_distribution_random128k.{ext}',dpi=220)
plt.close(fig)
