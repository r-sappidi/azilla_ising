#!/usr/bin/env python3
"""Audited port-level traffic from matched exact-event runs, without trace inference."""
import csv
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT/'results/matched_pair_review_20260908_v2'
OUT = ROOT/'plots/paper/noc_ports_20260908'


def main():
    audit = json.loads((SRC/'audit.json').read_text())
    assert audit['status'] == 'pass'
    for p,h in audit['files'].items():
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == h, p
    # Read audited original files, not an unauthenticated merged CSV.
    rows=[]
    for p in audit['files']:
        if not p.endswith('metrics_noc.csv'): continue
        p=Path(p)
        d=json.loads(p.with_name('metrics_summary.json').read_text())
        name=p.parent.name if d['execution_mode']=='cores-only' else p.parent.parent.name
        for r in csv.DictReader(p.open()):
            rows.append(dict(dataset=name,mode=d['execution_mode'],iteration_cycles=d['timing_cycles']['iteration'],**r))
    ports=[('inject','0','local'),('inject','1','local'),('link','0','east'),
           ('link','1','west'),('eject','0','local'),('eject','1','local')]
    labels=['Inject\nH1 0','Inject\nH1 1','Link\n0 → 1','Link\n1 → 0','Eject\nH1 0','Eject\nH1 1']
    exported=[]
    OUT.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':8,'axes.spines.top':False,'axes.spines.right':False})
    for family,name in [('community','community_d16_n131072_s1'),('toroidal','toroidal_d4_n131072')]:
        assert next(p for p in audit['pairs'] if p['dataset']==name)['status']=='pass'
        selected=[r for r in rows if r['dataset']==name]
        lookup={(r['mode'],r['scope'],r['node'],r['direction']):r for r in selected}
        fig,axes=plt.subplots(1,2,figsize=(7.2,2.8),layout='constrained')
        for j,(mode,label,color) in enumerate([('cir','CIR','#3274a1'),('cores-only','Cores-only','#e1812c')]):
            for i,key in enumerate(ports):
                r=lookup[mode,*key]
                accepted,stalled,offered=(int(r[k]) for k in ('accepted_flits','stall_cycles','offered_cycles'))
                assert accepted+stalled==offered
                util=accepted/r['iteration_cycles']
                bp=stalled/offered if offered else 0
                assert abs(util-float(r['window_utilization']))<1e-12
                assert abs(bp-float(r['backpressure']))<1e-12
                exported.append(dict(dataset=name,mode=mode,port=' '.join(key),accepted_flits=accepted,
                    stalled_cycles=stalled,offered_cycles=offered,iteration_cycles=r['iteration_cycles'],
                    busy_percent=100*util,blocked_offer_percent=100*bp))
                x=i+(j-.5)*.36
                for ax,v in zip(axes,[100*util,100*bp]):
                    ax.bar(x,v,.34,color=color,label=label if i==0 else None)
                    ax.text(x,v+.7+(2 if j==1 and 0<v<.1 else 0),f'{v:.1f}' if v>=.1 else ('0' if v==0 else '<0.1'),
                            ha='center',va='bottom',fontsize=6)
        for ax in axes:
            ax.set_xticks(range(6),labels); ax.set_axisbelow(True);ax.grid(axis='y',alpha=.2)
        axes[0].set_ylim(0,70);axes[1].set_ylim(0,50)
        axes[0].set_ylabel('Accepted flits / iteration cycles (%)')
        axes[1].set_ylabel('Stalled cycles / offered cycles (%)')
        axes[0].set_title('(a) Port activity');axes[1].set_title('(b) Backpressure when offered')
        axes[0].legend(fontsize=7,ncol=2,loc='upper left')
        for ext in ('pdf','png'):fig.savefig(OUT/f'{family}_128k_ports.{ext}',dpi=220)
        plt.close(fig)
    with (OUT/'port_metrics.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(exported[0]));w.writeheader();w.writerows(exported)
    (OUT/'audit.json').write_text(json.dumps(dict(status='pass',sources=audit['files'],
        checks=['paired coverage','source hashes','accepted+stalled=offered','exported ratio identities'],
        scope='Aggregate top-level ports; no temporal burst inference or new RTL certification.'),indent=2)+'\n')


if __name__=='__main__':main()
