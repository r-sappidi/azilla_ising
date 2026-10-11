#!/usr/bin/env python3
"""Figures retain analytical/observed distinctions; no bound-ratio speedups."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--report',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    d=json.loads(a.report.read_text())
    if d['status']!='pass':raise ValueError('failed analytical audit')
    a.output.mkdir(parents=True,exist_ok=False)
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,
                         'pdf.fonttype':42,'ps.fonttype':42})
    completed=sorted((r for r in d['validation'] if r['mode']=='cir'
                      and r['anchor']=='full-H1/exact-v3'),key=lambda r:r['spins'])
    fig,ax=plt.subplots(figsize=(6,3.4),layout='constrained')
    errors=[r['cir_estimate_error_percent'] for r in completed]
    ax.bar(range(len(completed)),errors,color=['#b35806' if abs(e)>5 else '#3274a1' for e in errors],width=.55)
    for i,e in enumerate(errors):
        ax.annotate(f'{e:+.2f}%',(i,e),xytext=(0,-13 if e<0 else 4),
                    textcoords='offset points',ha='center',fontsize=9)
    ax.axhline(0,color='black',linewidth=.8)
    ax.set_xticks(range(len(completed)),[f"{r['spins']//1024}K" for r in completed])
    ax.set_xlabel('Active spins; fixed H1 = 8 H0 × 256 cores')
    ax.set_ylabel('Signed iteration-latency error (%)')
    ax.set_title('CIR formulaic model versus completed dense exact-event runs')
    ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    ax.set_ylim(min(-2,min(errors)-2),max(2,max(errors)+2))
    for ext in ('png','pdf'):fig.savefig(a.output/f'cir_dense_error.{ext}',dpi=200)
    plt.close(fig)
    (a.output/'error_definition.txt').write_text(
        'Signed error = 100 * (unfitted formulaic iteration cycles / exact-event iteration cycles - 1).\n'
        'Negative values are underprediction. Completed fixed-H1 CIR points only.\n'
        'Cores-only has necessary resource bounds, not a latency predictor; no cores-only prediction error is defined.\n'
        'Count/lower-bound audit pass does not mean the latency estimate is accurate.\n'
        f'Source report: {a.report.resolve()}\n')
    fig,axes=plt.subplots(1,2,figsize=(8,3.2),layout='constrained')
    for ax,anchor,title in zip(axes,('full-H1/exact-v3','two-H1/VCS-differential'),
                              ('Fixed H1: 8 H0 × 256 cores','Two H1: 2 H0 each, 4–16 cores/H0')):
        rows=sorted((r for r in d['validation'] if r['mode']=='cir' and r['anchor']==anchor),key=lambda r:r['spins'])
        ns=[r['spins'] for r in rows]
        ax.plot(ns,[r['reference_cycles'] for r in rows],'o-',label='Exact-event / VCS reference')
        ax.plot(ns,[r['cir_estimate_cycles'] for r in rows],'s--',label='Unfitted CIR estimate')
        for index,r in enumerate(rows):
            align='left' if index==0 else 'center'
            shift=6 if index==0 else 0
            down=-28 if index==len(rows)-1 else -17
            ax.annotate(f"{r['cir_estimate_error_percent']:+.1f}%",(r['spins'],r['cir_estimate_cycles']),
                        xytext=(shift,down),textcoords='offset points',ha=align,fontsize=8)
        ax.set_xscale('log',base=2);ax.set_yscale('log')
        ax.set_xticks(ns,[f'{n//1024}K' if n>=1024 else str(n) for n in ns])
        ax.set_title(title,fontsize=10);ax.set_xlabel('Active spins');ax.set_ylabel('Iteration cycles')
        ax.grid(alpha=.2);ax.margins(x=.10,y=.22)
    axes[0].legend(loc='upper left',fontsize=8)
    for ext in ('png','pdf'):fig.savefig(a.output/f'cir_analytical_validation.{ext}',dpi=200)
    plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(11,3.3),layout='constrained')
    ps={mode:sorted((p for p in d['projections'] if p['mode']==mode and p['spins']>=16384),key=lambda p:p['spins'])
        for mode in ('cir','cores-only')}
    ns=[p['spins'] for p in ps['cir']]
    axes[0].plot(ns,[p['canonical_weight_bytes']/2**30 for p in ps['cir']],'o-',color='black')
    axes[0].set_ylabel('Canonical DRAM weights (GiB)');axes[0].set_title('Exact dense storage',fontsize=10)
    for mode,label in (('cir','CIR'),('cores-only','Cores-only')):
        axes[1].plot(ns,[p['hierarchy_result_or_weight_payload_bytes']/2**30 for p in ps[mode]],'o-',label=label)
    axes[1].set_title('Exact delivered payload',fontsize=10)
    axes[1].set_ylabel('All hierarchy levels (GiB/iteration)');axes[1].legend(fontsize=8)
    axes[2].plot(ns,[p['cir_steady_state_estimate_cycles'] for p in ps['cir']],'s--',label='CIR analytical projection')
    axes[2].plot(ns,[p['iteration_lower_bound_cycles'] for p in ps['cir']],':',label='CIR necessary lower bound')
    refs=[r for r in d['validation'] if r['mode']=='cir' and r['anchor']=='full-H1/exact-v3' and r['spins']>=16384]
    axes[2].scatter([r['spins'] for r in refs],[r['reference_cycles'] for r in refs],color='black',zorder=5,label='Completed reference')
    axes[2].set_title('CIR latency projection',fontsize=10)
    axes[2].set_ylabel('Iteration cycles');axes[2].legend(fontsize=7)
    for ax in axes:
        ax.set_xscale('log',base=2);ax.set_yscale('log');ax.grid(alpha=.2)
        ax.set_xticks(ns,[f'{n//1024}K' if n<1048576 else '1M' for n in ns],rotation=35)
        ax.set_xlabel('Active spins; fixed 8×256-core H1')
    for ext in ('png','pdf'):fig.savefig(a.output/f'dense_analytical_scaling.{ext}',dpi=200)
    plt.close(fig)


if __name__=='__main__':main()
