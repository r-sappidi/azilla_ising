#!/usr/bin/env python3
"""Unfitted CIR latency projection with completed dense reference markers."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'results/dense_analytical_v3_20260908/through32k/report.json'
OUT=ROOT/'plots/paper/dense_projection_20260908'


def main():
    d=json.loads(REPORT.read_text());assert d['status']=='pass'
    ps=sorted((p for p in d['projections'] if p['mode']=='cir'),key=lambda p:p['spins'])
    refs=sorted((r for r in d['validation'] if r['mode']=='cir' and r['anchor']=='full-H1/exact-v3'),key=lambda r:r['spins'])
    OUT.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(5.2,3.1),layout='constrained')
    ax.axvspan(32768,1048576,color='0.93',zorder=0)
    ax.plot([p['spins'] for p in ps],[p['cir_steady_state_estimate_cycles'] for p in ps],
            's--',color='#3274a1',markersize=3.5,label='Unfitted analytical projection')
    ax.scatter([r['spins'] for r in refs],[r['reference_cycles'] for r in refs],
               color='black',s=23,zorder=3,label='Completed exact-event results')
    ax.set_xscale('log',base=2);ax.set_yscale('log')
    ax.set_xticks([1024,4096,16384,65536,262144,1048576],['1K','4K','16K','64K','256K','1M'])
    ax.set_ylabel('CIR iteration cycles');ax.set_xlabel('Active spins')
    ax.legend(fontsize=7,loc='upper left');ax.grid(alpha=.2)
    ax.text(.98,.06,'Gray: no completed nominal-size anchor',transform=ax.transAxes,
             ha='right',fontsize=7,color='0.35')
    for ext in ('pdf','png'):fig.savefig(OUT/f'dense_scaling.{ext}',dpi=220)
    plt.close(fig)
    (OUT/'provenance.json').write_text(json.dumps(dict(report=str(REPORT),
        projections=ps,references=refs,scope='No fitted constants. Large-mesh latency unverified.'),indent=2)+'\n')


if __name__=='__main__':main()
