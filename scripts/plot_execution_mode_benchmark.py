#!/usr/bin/env python3
"""Paper-oriented CIR/core/hybrid latency and speedup figures."""
import argparse,csv
from pathlib import Path
import matplotlib.pyplot as plt

p=argparse.ArgumentParser();p.add_argument('--summary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
rows=list(csv.DictReader(a.summary.open())); grouped={}
for r in rows: grouped.setdefault(r['dataset'],{})[r['mode']]=r
names=[n for n,v in grouped.items() if {'cir','cores-only','hybrid'}<=set(v)]
a.output_dir.mkdir(parents=True,exist_ok=True); colors={'cir':'#2878B5','cores-only':'#D95319','hybrid':'#2E8B57'}
fig,ax=plt.subplots(figsize=(max(7,len(names)*.65),4));x=range(len(names));w=.25
for j,m in enumerate(('cir','cores-only','hybrid')):
 ax.bar([i+(j-1)*w for i in x],[int(grouped[n][m]['iteration_cycles']) for n in names],w,label=m,color=colors[m])
ax.set_yscale('log');ax.set_ylabel('Iteration cycles (log scale)');ax.set_xticks(list(x),names,rotation=40,ha='right');ax.legend(frameon=False);ax.grid(axis='y',alpha=.25);fig.tight_layout()
for ext in ('png','pdf'): fig.savefig(a.output_dir/f'execution_mode_latency.{ext}',dpi=220)
plt.close(fig)
fig,ax=plt.subplots(figsize=(max(7,len(names)*.65),3.8)); gains=[]
for n in names:
 v=grouped[n];best=min(int(v['cir']['iteration_cycles']),int(v['cores-only']['iteration_cycles']));h=int(v['hybrid']['iteration_cycles']);gains.append(best/h)
ax.bar(names,gains,color=colors['hybrid']);ax.axhline(1,color='black',lw=1);ax.set_ylabel('Hybrid speedup over best static baseline');ax.tick_params(axis='x',rotation=40);ax.grid(axis='y',alpha=.25);fig.tight_layout()
for ext in ('png','pdf'): fig.savefig(a.output_dir/f'hybrid_speedup.{ext}',dpi=220)
