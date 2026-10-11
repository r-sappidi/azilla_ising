#!/usr/bin/env python3
"""Paper panels from completed, audited pairs only (no missing-cell imputation)."""
import json
import hashlib
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'results/matched_pair_review_20260908_v2'
OUT = ROOT/'plots/paper/matched_pairs_20260908'


def main():
    audit = json.loads((SOURCE/'audit.json').read_text())
    assert audit['status'] == 'pass'
    for p, h in audit['files'].items():
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == h, p
    rows = json.loads((SOURCE/'collection.json').read_text())['rows']
    by = {(r['dataset'], r['mode']): r for r in rows}
    names = [p['dataset'] for p in audit['pairs'] if p['status'] == 'pass']
    assert len(names) == 5
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, ax = plt.subplots(figsize=(5.1, 2.9))
    for j, (metric, title, color) in enumerate([
            ('iteration_cycles','Iteration','#3274a1'),
            ('total_cycles','Initialization + iteration','#e1812c')]):
        values = [by[n,'cores-only'][metric]/by[n,'cir'][metric] for n in names]
        ax.bar([i+(j-.5)*.36 for i in range(5)], values, .34, label=title, color=color)
        for i, v in enumerate(values): ax.text(i+(j-.5)*.36,v+.06,f'{v:.2f}',ha='center',fontsize=7)
    ax.axhline(1, color='0.5', linewidth=.8, linestyle='--')
    ax.set_xticks(range(5), ['Torus\n64K','Community\n64K','Uniform\n64K','Torus\n128K','Community\n128K'])
    ax.set_ylim(0,4.7); ax.set_ylabel('Speedup over cores-only')
    ax.legend(fontsize=8, loc='upper left', ncol=2)
    ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    fig.tight_layout()
    for ext in ('pdf','png'): fig.savefig(OUT/f'latency_speedup.{ext}', dpi=200)
    plt.close(fig)
    import csv
    data = list(csv.DictReader((SOURCE/'matched_measurements.csv').open()))
    lookup = {(r['dataset'],r['mode']):r for r in data}
    names = [n for n in names if '_n131072' in n]
    fig, ax = plt.subplots(figsize=(5.1,2.8))
    for j,(mode,label,color) in enumerate([('cir','CIR','#3274a1'),('cores-only','Cores-only','#e1812c')]):
        values=[int(lookup[n,mode]['physical_link_flits']) for n in names]
        ax.bar([i+(j-.5)*.34 for i in range(2)],values,.32,label=label,color=color)
        for i,v in enumerate(values): ax.text(i+(j-.5)*.34,v*1.15,f'{v:,}',ha='center',fontsize=8)
    ax.set_yscale('log');ax.set_ylim(70,2e7)
    ax.set_xticks(range(2),['Torus, 128K','Community, 128K'])
    ax.set_ylabel('Inter-H1 flit-hops');ax.legend(fontsize=8,loc='upper left')
    ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);fig.tight_layout()
    for ext in ('pdf','png'):fig.savefig(OUT/f'inter_h1_traffic.{ext}',dpi=200)
    plt.close(fig)
    (OUT/'provenance.json').write_text(json.dumps(audit,indent=2)+'\n')


if __name__ == '__main__': main()
