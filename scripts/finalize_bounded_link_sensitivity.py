#!/usr/bin/env python3
"""Durable read-only experiment watcher; plot only a fully passing six-point set."""
import argparse,csv,hashlib,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/paper_deadline_20260907/link_sensitivity_v1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(value):
    temp=OUT/'watcher_status.tmp.json';temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(OUT/'watcher_status.json')
def main():
    p=argparse.ArgumentParser();p.add_argument('--launcher-pid',type=int,required=True);args=p.parse_args()
    proc=Path('/proc')/str(args.launcher_pid)
    identity=dict(pid=args.launcher_pid,start_ticks=proc.joinpath('stat').read_text().split()[21],
                  command=proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')) if proc.exists() else dict(pid=args.launcher_pid,already_exited=True)
    identity_path=OUT/'launcher_identity.json'
    if identity_path.exists():
        identity=json.loads(identity_path.read_text())
    else:
        identity_path.write_text(json.dumps(identity,indent=2)+'\n')
    try:
        while True:
            try:r=json.loads((OUT/'report.json').read_text())
            except json.JSONDecodeError:time.sleep(1);continue
            if r['status']=='failed':raise RuntimeError(r.get('error','sweep failed'))
            if r['status']=='passed':break
            if not proc.exists() or proc.joinpath('stat').read_text().split()[21]!=identity.get('start_ticks'):
                raise RuntimeError('launcher exited or PID changed before complete report')
            save(dict(status='waiting',timestamp=time.time(),completed=len(r['cases']),launcher=identity))
            time.sleep(30)
        expected={(m,v) for m in ('cir','cores-only') for v in ('baseline','latency8','latency8_serial2_credit8')}
        assert len(r['cases'])==6 and {(c['mode'],c['variant']) for c in r['cases']}==expected
        assert all(c['status']=='pass' and all(c['checks'].values()) for c in r['cases'])
        assert all(sha(p)==h for p,h in {**r['source_sha256'],**r['input_sha256']}.items())
        rows=[]
        for c in r['cases']:
            assert all(sha(p)==h for p,h in c['files'].items())
            rows.append(dict(mode=c['mode'],variant=c['variant'],iteration_cycles=c['iteration_cycles'],
                iteration_us=c['iteration_us'],accelerator_mhz=100,physical_link_flits=c['noc']['physical_link_flits'],
                injected_flits=c['noc']['injected_flits'],injection_stalls=c['noc']['injection_stalls'],
                ejection_stalls=c['noc']['ejection_stalls'],link_stalls=c['noc']['link_stalls'],
                model='frozen-original exact model',interpretation='interconnect sensitivity projection'))
        with (OUT/'summary.csv').open('w') as handle:
            w=csv.DictWriter(handle,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        variants=['baseline','latency8','latency8_serial2_credit8']
        fig,axes=plt.subplots(1,3,figsize=(11,3.5))
        for mode,offset,color in [('cir',-.19,'#0072B2'),('cores-only',.19,'#D55E00')]:
            series=[next(x for x in rows if x['mode']==mode and x['variant']==v) for v in variants]
            for ax,key,title in zip(axes,['iteration_us','physical_link_flits','link_stalls'],
                ['Iteration latency (µs)','Physical-link flits','Aggregate link-stall cycles']):
                ax.bar([i+offset for i in range(3)],[x[key] for x in series],width=.36,label='CIR' if mode=='cir' else mode,color=color)
                ax.set_xticks(range(3),['Direct-wire','+8 cycles','+8 / II=2 / CR=8'],fontsize=8)
                ax.set_title(title,fontsize=10);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        axes[0].legend(frameon=False,fontsize=8)
        fig.suptitle('16K Kings • original exact model • 100 MHz\nInterconnect sensitivity; nondefault links are physical projections',fontsize=10)
        fig.tight_layout()
        fig.savefig(OUT/'latency_traffic.pdf',bbox_inches='tight');fig.savefig(OUT/'latency_traffic.png',dpi=200,bbox_inches='tight')
        metadata=dict(status='pass',timestamp=time.time(),points=6,source_sha256=r['source_sha256'],input_sha256=r['input_sha256'],
            report_sha256=sha(OUT/'report.json'),figure_sha256={p.name:sha(p) for p in [OUT/'summary.csv',OUT/'latency_traffic.pdf',OUT/'latency_traffic.png']})
        (OUT/'figure_audit.json').write_text(json.dumps(metadata,indent=2)+'\n');save(metadata)
        print('PASS six-point CSV/figures after conservation and immutable-input checks',flush=True)
    except Exception as error:
        save(dict(status='failed',timestamp=time.time(),error=repr(error),launcher=identity));raise
if __name__=='__main__':main()
