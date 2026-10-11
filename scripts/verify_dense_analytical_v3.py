#!/usr/bin/env python3
"""Read-only validation against completed v3 results; write unique output dir.

Does not launch/modify simulations, fit parameters, or import old calibrated
results. Missing/failed/running points never become validation evidence.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.dense_analytical import dense_model
from azilla_cycle_model.workload import Geometry


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def rtl_traffic(folder, mode, g):
    counts=Counter();links=Counter();inputs=[]
    if mode=='cir':
        path=folder/'noc_events.csv';inputs.append(path)
        with path.open() as f:
            for row in csv.DictReader(f):
                if row['event']!='accept':continue
                scope=row['scope'];kind=int(row['packet_type']);n=int(row['node'])
                counts[scope,kind]+=1
                if scope=='link':
                    delta={'east':1,'west':-1,'north':-g.mesh_x,'south':g.mesh_x}[row['direction']]
                    links[f'{n}->{n+delta}']+=1
    else:
        path=folder/'rtl.log';inputs.append(path)
        with path.open() as f:
            for line in f:
                if not line.startswith('CORE_NOC'):continue
                row=dict(re.findall(r'(\w+)=\s*(\S+)',line))
                scope=row['scope'];kind=int(row['type']);n=int(row['node'])
                counts[scope,kind]+=1
                if scope=='link':
                    delta={'east':1,'west':-1,'north':-g.mesh_x,'south':g.mesh_x}[row['direction']]
                    links[f'{n}->{n+delta}']+=1
    return counts,dict(links),inputs


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    sources={};rows=[]
    def record(path):sources[str(path.relative_to(ROOT))]=sha(path)
    def compare(label,p,cycles,checks,source):
        estimate=p['cir_steady_state_estimate_cycles']
        checks['latency_lower_bound']=p['iteration_lower_bound_cycles']<=cycles
        rows.append(dict(anchor=label,spins=p['spins'],mode=p['mode'],geometry=p['geometry'],
                         reference_cycles=cycles,lower_bound_cycles=p['iteration_lower_bound_cycles'],
                         bound_slack_percent=100*(cycles-p['iteration_lower_bound_cycles'])/cycles,
                         cir_estimate_cycles=estimate,
                         cir_estimate_error_percent=None if estimate is None else 100*(estimate/cycles-1),
                         exact_count_checks=checks,passed=all(checks.values()),source=str(source)))
    base=ROOT/'results/matched_dense_exact_v3_20260907/points'
    for folder in sorted(base.glob('*/*/implicit')):
        audit=folder/'audit.json';summary=folder/'metrics_summary.json'
        if not audit.exists() or not summary.exists():continue
        a=json.loads(audit.read_text())
        if a.get('status')!='conservation-pass':continue
        d=json.loads(summary.read_text());record(audit);record(summary)
        gd=d['geometry'];g=Geometry(*(gd[k] for k in ('mesh_x','mesh_y','h0_per_h1','cores_per_h0')))
        n=int(folder.parent.parent.name);mode=folder.parent.name
        engine_sets=[{r['engines'] for r in d['dram_systems'] if r['level']==level}
                     for level in ('h0','h1','cross')]
        if any(len(s)!=1 for s in engine_sets):raise ValueError('nonuniform source engines')
        engines=tuple(next(iter(s)) for s in engine_sets)
        if mode=='cores-only':
            assert d['cores_only_ablation']['pipeline_contract']['fetch_policy']=='single_fetch_two_slot_cir_routes_v3'
        if a['files'].get(str(summary)) != sha(summary):raise ValueError('summary changed since audit')
        p=dense_model(n,mode,g,engines)
        jobs=Counter();requests=0;complete=0
        for resource in d['dram_systems']:
            jobs[resource['level']]+=resource['scheduled_jobs']
            requests+=resource['accepted_requests'];complete+=resource['completed_requests']
        noc=d['noc']
        checks=dict(jobs=p['jobs']==dict(jobs),requests=p['dram_row_requests']==requests==complete,
                    initialization=p['initialization_cycles']==d['timing_cycles']['initialization'],
                    injected=p['injected_flits']==noc['injected_flits'],
                    physical=p['physical_link_flits']==noc['physical_link_flits'],
                    state=p['traffic_by_type']['state']==noc['state_flits'],
                    partial=p['traffic_by_type']['partial']==noc['partial_flits'],
                    weight=p['traffic_by_type']['weight']==noc['other_flits'],
                    epoch=p['traffic_by_type']['epoch_done']==noc['epoch_done_flits'])
        checks['per_resource_jobs']=all(
            resource['scheduled_jobs']==p['jobs_per_resource'][resource['level']][
                resource['node']*g.h0_per_h1+resource['index'] if resource['level']=='h0'
                else resource['node']]
            for resource in d['dram_systems'])
        if mode=='cores-only':
            ablation=d['cores_only_ablation']
            checks['directed_core_jobs']=ablation['directed_core_jobs']==2*p['canonical_jobs']
            checks['canonical_fetches']=ablation['weight_block_reads']==p['canonical_jobs']
        compare('full-H1/exact-v3',p,d['timing_cycles']['iteration'],checks,summary)
    base=ROOT/'results/dense_multi_h1_vcs_v2_20260908'
    for folder in sorted(base.glob('*/*')):
        report=folder/'differential.json'
        if not report.exists():continue
        d=json.loads(report.read_text())
        if d.get('status')!='pass' or not all(d['checks'].values()):continue
        n=int(folder.name);mode=folder.parent.name;g=Geometry(2,1,2,n//128)
        p=dense_model(n,mode,g)
        counts,links,files=rtl_traffic(folder,mode,g)
        for f in [report,*files]:record(f)
        checks={kind:p['traffic_by_type'][kind]==counts['inject',t]
                for kind,t in [('state',0),('partial',1),('epoch_done',2),('weight',3)]}
        checks['links']=p['directed_links']==links
        checks['ejection']=p['injected_flits']==sum(v for (s,k),v in counts.items() if s=='eject')
        if mode=='cir':
            init,cycles,total=d['observed_timing']
        else:
            cycles=d['iteration_cycles'];total=d['rtl_total'];init=total-cycles
        checks['initialization']=init==p['initialization_cycles']
        compare('two-H1/VCS-differential',p,cycles,checks,report)
    projections=[]
    start=time.perf_counter()
    for n in (1024,4096,8192,16384,32768,65536,131072,262144,524288,1048576):
        for mode in ('cir','cores-only'):projections.append(dense_model(n,mode))
    runtime=time.perf_counter()-start
    for f in ('model/azilla_cycle_model/dense_analytical.py','model/azilla_cycle_model/scheduler.py',
              'model/azilla_cycle_model/hierarchy.py','model/azilla_cycle_model/compute.py',
              'model/tests/test_dense_analytical.py','scripts/verify_dense_analytical_v3.py',
              'tb/ramulator_128x32.yaml'):record(ROOT/f)
    result=dict(status='pass' if rows and all(r['passed'] for r in rows) else 'fail',
                validation_scope='conservation-and-lower-bound-consistency',
                large_mesh_latency_certified=False,
                command=sys.argv,validation=rows,source_sha256=sources,
                projection_runtime_seconds=runtime,projections=projections,
                policy='No fitted constants. Exact counts and necessary bounds validated; CIR latency is an unguaranteed steady-state approximation, not a cycle-exact prediction. No cores-only latency estimate or speedup claim.')
    # Independently completed full-H1 16K VCS checks anchor the large active
    # subset. Do not transfer this certificate to a different mesh or size.
    certificates={}
    for mode,relative in (
        ('cir','results/dense_vcs_validation_v3_20260908/cir/16384/differential.json'),
        ('cores-only','results/dense_core16k_timeout_retry_20260908/16384/differential.json')):
        path=ROOT/relative;d=json.loads(path.read_text());record(path)
        anchor=next(r for r in rows if r['anchor']=='full-H1/exact-v3' and r['spins']==16384 and r['mode']==mode)
        observed=d['observed_timing'][1] if mode=='cir' else d['iteration_cycles']
        ok=d['status']=='pass' and all(d['checks'].values()) and observed==anchor['reference_cycles']
        certificates[mode]=dict(path=relative,passed=ok,iteration_cycles=observed)
        if not ok:result['status']='fail'
    result['full_h1_16k_vcs_timing_certificates']=certificates
    (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    for name,items in [('validation',rows),('scaling',projections)]:
        flat=[{k:v for k,v in item.items() if not isinstance(v,(list,dict))} for item in items]
        with (args.output/(name+'.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    lines=['# Dense analytical v3 validation','',result['policy'],'',
           '| Anchor | Spins | Mode | Observed iteration | Bound | CIR estimate error | Checks |',
           '|---|---:|---|---:|---:|---:|---|']
    for r in rows:
        error=r['cir_estimate_error_percent'];err='n/a' if error is None else f'{error:+.3f}%'
        lines.append(f"| {r['anchor']} | {r['spins']} | {r['mode']} | {r['reference_cycles']} | {r['lower_bound_cycles']} | {err} | {r['passed']} |")
    lines+=['',f'20 analytical points through 1,048,576 spins: {runtime:.6f} seconds.',
            '', 'Large mesh estimates are projections, not validation anchors. Lower-bound ratios are not speedups.']
    (args.output/'README.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    if result['status']!='pass':raise SystemExit(1)


if __name__=='__main__':main()
