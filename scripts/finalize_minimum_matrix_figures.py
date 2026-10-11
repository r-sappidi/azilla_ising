#!/usr/bin/env python3
"""Audit-gated minimum-matrix figures; no incomplete-cell interpolation."""
import csv,hashlib,json,time
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/paper_deadline_20260907/minimum_matrix_v1'
CORE=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
POLICY='single_fetch_two_slot_cir_routes_v3'
def audit_path(name,mode):
    return (CORE/'timing'/name/'audit.json' if mode=='cores-only' else
            BASE/'timing/provisional_exact_matrix_v1'/name/mode/'audit.json')
def main():
    expected=[f'{family}_n{size}{suffix}' for size in (65536,131072)
              for family,suffix in (('toroidal_d4',''),('community_d16','_s1'),('uniform_d16','_s1'))]
    audits=[audit_path(n,m)
            for n in expected for m in ('cir','cores-only')]
    while not all(p.exists() for p in audits):time.sleep(30)
    rows=[];hashes={}
    for n in expected:
        for mode in ('cir','cores-only'):
            p=audit_path(n,mode)
            audit=json.loads(p.read_text())
            for name,digest in audit['files'].items():
                assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
                hashes[name]=digest
            summaries=[x for x in audit['files'] if x.endswith('metrics_summary.json')]
            assert len(summaries)==1
            d=json.loads(Path(summaries[0]).read_text())
            assert d['execution_mode']==mode
            if mode=='cores-only':
                assert d['cores_only_ablation']['pipeline_contract']['fetch_policy']==POLICY
            rows.append(dict(dataset=n,mode=mode,iteration_cycles=d['timing_cycles']['iteration'],
                total_cycles=d['timing_cycles']['total'],injected_flits=d['noc']['injected_flits'],
                physical_link_flits=d['noc']['physical_link_flits'],
                dram_requests=sum(x['accepted_requests'] for x in d['dram_systems']),accuracy=d['accuracy']))
    out=CORE/'figures';out.mkdir(exist_ok=False)
    with (out/'minimum_matrix.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    for name,fields in [('latency',('iteration_cycles','total_cycles')),
                        ('communication',('injected_flits','physical_link_flits','dram_requests'))]:
        fig,axes=plt.subplots(1,len(fields),figsize=(5*len(fields),4.5),squeeze=False)
        for ax,key in zip(axes[0],fields):
            for j,mode in enumerate(('cir','cores-only')):
                values=[next(r[key] for r in rows if r['dataset']==n and r['mode']==mode) for n in expected]
                ax.bar([i+(j-.5)*.36 for i in range(6)],values,.36,label=mode)
            ax.set_xticks(range(6),['Torus64K','Comm64K','Uniform64K','Torus128K','Comm128K','Uniform128K'],rotation=35,ha='right')
            ax.set_ylabel(key.replace('_',' '));ax.legend();ax.grid(axis='y',alpha=.2)
            if key!='physical_link_flits':ax.set_yscale('log')
        fig.tight_layout()
        for suffix in ('pdf','png'):fig.savefig(out/f'{name}.{suffix}',dpi=180)
        plt.close(fig)
    (out/'audit.json').write_text(json.dumps(dict(status='inputs-audited',files=hashes,
        paper_ready=False,visual_review_required=True,scope='single-instance case studies; geometry-specific fidelity labels retained'),indent=2)+'\n')
    print('PASS: 12-cell figures generated; manual visual/claim review still required',flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        (CORE/'figure_failure.json').write_text(json.dumps(dict(error=repr(e)))+'\n');raise
