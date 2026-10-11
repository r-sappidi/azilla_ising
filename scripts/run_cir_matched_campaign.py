#!/usr/bin/env python3
"""Version-pinned replacement queue; historical results cannot satisfy v3 gates."""
import argparse,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(os.environ.get('AZILLA_REPOSITORY_ROOT',Path(__file__).resolve().parents[1])).resolve()
os.environ.setdefault('AZILLA_REPOSITORY_ROOT',str(ROOT))
V3=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
INVENTORY=ROOT/'results/paper_validation_20260907/fast_handoff_226703_quiescent/inventory.json'
POLICY='single_fetch_two_slot_cir_routes_v3'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix('.tmp');t.write_text(json.dumps(v,indent=2)+'\n');t.replace(p)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--prepare',action='store_true')
    ap.add_argument('--mapping-only',action='store_true',
                    help='Prepare an independent current-v3 mapping queue, without historical timing reuse.')
    args=ap.parse_args();out=args.output.resolve();out.relative_to(ROOT/'results')
    if args.prepare:
        out.mkdir(parents=True,exist_ok=False)
        points=[]
        for c in json.loads(INVENTORY.read_text())['campaigns']:
            for p in c['points']:
                points.append(dict(p,campaign=c['campaign']))
        # Carry over the already planned constrained-link comparison.
        for p in list(points):
            if (p['campaign']=='provisional_exact_matrix_v1' and
                p['relative'].split('/')[0] in ('community_d16_n131072_s1','uniform_d16_n131072_s1')):
                points.append(dict(p,campaign='link_sensitivity_v3',links=[8,2,4,8]))
        stretch=ROOT/'results/paper_deadline_20260907/scale_extension_v1/manifest.json'
        for d in json.loads(stretch.read_text())['datasets']:
            for mode in ('cores-only','cir'):
                points.append(dict(campaign='scale_extension_v3',relative=d['name']+'/'+mode,
                    dataset=d['dataset'],dataset_sha256=d['sha256'],geometry=d['geometry'],
                    engines=[4,2,4],mode=mode,
                    ramulator_config=str(V3/'snapshot/tb/ramulator_128x32.yaml'),
                    ramulator_config_sha256=sha(V3/'snapshot/tb/ramulator_128x32.yaml')))
        for p in points:
            p['required_fetch_policy']=POLICY if p['mode']=='cores-only' else 'cir'
            p['geometry_rtl_verified']=False
        # Minimum points first, then sensitivity/mapping, then larger stretch.
        primary={f'{f}_n{n}{s}' for n in (65536,131072)
                 for f,s in [('toroidal_d4',''),('community_d16','_s1'),('uniform_d16','_s1')]}
        points.sort(key=lambda p:(not(p['campaign']=='provisional_exact_matrix_v1' and p['relative'].split('/')[0] in primary),
            p['campaign']!='provisional_exact_matrix_v1',
            p['campaign']=='scale_extension_v3',
            p['geometry']['mesh_x']*p['geometry']['mesh_y']*p['geometry']['h0_per_h1']*p['geometry']['cores_per_h0'],
            p['campaign'],p['relative']))
        if args.mapping_only:
            points=[p for p in points if p['campaign']=='mapping_ablation_exact_v1']
            assert len(points)==54
            points.sort(key=lambda p:(
                p['geometry']['mesh_x']*p['geometry']['mesh_y'],
                0 if p['relative'].startswith('toroidal') else 1 if p['relative'].startswith('community') else 2,
                {'identity':0,'compression_mapping_lossless':1,'random':2}[p['relative'].split('/')[-2]],
                p['mode']!='cores-only'))
        tools=out/'tools';tools.mkdir()
        for name in ('audit_shared_fetch_v3.py','run_fast_exact_continuation.py','run_cir_matched_campaign.py'):
            shutil.copy2(ROOT/'scripts'/name,tools/name)
        save(out/'plan.json',dict(policy=POLICY,snapshot=str(V3/'snapshot'),
            validation_root=str(V3),inventory_sha256=sha(INVENTORY),points=points,
            tools={str(p):sha(p) for p in tools.iterdir()},
            note='All cores-only points must use v3; original CIR points may be reused only with audited provenance. Large results remain projections.'))
        print(json.dumps(dict(points=len(points),cores_only=sum(p['mode']=='cores-only' for p in points))))
        return
    plan=json.loads((out/'plan.json').read_text());snap=Path(plan['snapshot'])
    frozen=json.loads((V3/'manifest.json').read_text())
    def guard():
        assert all(sha(snap/k)==v for k,v in frozen['sources'].items())
        assert all(sha(p)==v for p,v in plan['tools'].items())
        for repeat in (1,4,16):
            for stall in (0,7,13):
                d=json.loads((V3/f'shared_repeat{repeat}/wrapper_stall{stall}.json').read_text())
                assert d['matched'] and d['double_buffer'] and all(d['checks'].values())
    guard()
    env=dict(os.environ,PYTHONPATH=str(snap/'model'),PYTHONDONTWRITEBYTECODE='1',
             OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    def run(cmd,folder):
        folder.mkdir(parents=True,exist_ok=True);save(folder/'command.json',cmd)
        with (folder/'run.log').open('x') as log:
            subprocess.run(['/usr/bin/time','-v','-o',str(folder/'wall.time'),*cmd],
                cwd=snap,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=0
    for p in plan['points']:
        dest=out/'points'/p['campaign']/p['relative']
        if (dest/'audit.json').exists():done+=1;continue
        guard();dataset=ROOT/p['dataset'];config=Path(p['ramulator_config'])
        assert sha(dataset)==p['dataset_sha256'] and sha(config)==p['ramulator_config_sha256']
        name=p['relative'].split('/')[0]
        minimum=(p['campaign']=='provisional_exact_matrix_v1' and
            name in [f'{f}_n{n}{s}' for n in (65536,131072)
                     for f,s in [('toroidal_d4',''),('community_d16','_s1'),('uniform_d16','_s1')]])
        save(out/'status.json',dict(status='active',done=done,total=len(plan['points']),point=p))
        try:
            if p['mode']=='cir' and p['campaign'].startswith('provisional_'):
                old=ROOT/'results/paper_validation_20260907'/p['campaign']/p['relative']
                if (old/'audit.json').exists() and (old/'metrics_summary.json').exists():
                    d=json.loads((old/'metrics_summary.json').read_text())
                    cmd=json.loads((old/'command.json').read_text())
                    assert d['execution_mode']=='cir'
                    assert all(d['geometry'][k]==v for k,v in p['geometry'].items())
                    assert d['geometry']['spin_count']==32*p['geometry']['cores_per_h0']*p['geometry']['h0_per_h1']*p['geometry']['mesh_x']*p['geometry']['mesh_y']
                    assert sha(cmd[cmd.index('--ramulator-config')+1])==p['ramulator_config_sha256']
                    assert sha(cmd[cmd.index('--dataset')+1])==p['dataset_sha256']
                    for level,count in zip(('h0','h1','cross'),p['engines']):assert int(cmd[cmd.index('--'+level+'-mvms')+1])==count
                    assert all(x['accepted_requests']==x['completed_requests'] and not x['outstanding_requests'] for x in d['dram_systems'])
                    files={str(f):sha(f) for f in old.iterdir() if f.is_file() and f.suffix in ('.json','.csv')}
                    save(dest/'audit.json',dict(status='adopted-unchanged-cir',files=files,
                        adopted_from=str(old),paper_ready=False,geometry_rtl_verified=False));done+=1;continue
            if minimum and p['mode']=='cores-only':
                prefix=V3/'timing'/name/'metrics'
                while not Path(str(prefix)+'_summary.json').exists():
                    save(out/'status.json',dict(status='waiting-existing-v3-point',done=done,point=p));time.sleep(30)
                if not (prefix.parent/'audit.json').exists():
                    subprocess.run([sys.executable,str(out/'tools/audit_shared_fetch_v3.py'),
                                    '--prefix',str(prefix)],check=True)
                audit=json.loads((prefix.parent/'audit.json').read_text())
                assert audit['fetch_policy']==POLICY
                assert all(sha(x)==h for x,h in audit['files'].items())
                save(dest/'audit.json',dict(audit,adopted_from=str(prefix.parent)));done+=1;continue
            if p['mode']=='cores-only':
                # New provisioning/memory configurations get representative
                # VCS checks before their timing projection is launched.
                key=hashlib.sha256(json.dumps([p['engines'],p['ramulator_config_sha256']]).encode()).hexdigest()[:16]
                gate=out/'gates'/key
                if not (gate/'pass.json').exists():
                    gate.mkdir(parents=True,exist_ok=True)
                    gate_env=env|dict(OUTPUT_ROOT=str(gate/'reports'),BUILD_ROOT=str(gate/'build'),
                        ENGINES=str(max(p['engines'])),H0_ENGINES=str(p['engines'][0]),
                        H1_ENGINES=str(p['engines'][1]),CROSS_ENGINES=str(p['engines'][2]),
                        DOUBLE_BUFFER='1',JOBS_PER_SOURCE='32',VCS_JOBS='1',RAMULATOR_CONFIG=str(config))
                    with (gate/'gate.log').open('x') as log:
                        subprocess.run(['bash','scripts/run_shared_fetch_publication_vcs.sh'],cwd=snap,
                            env=gate_env,stdout=log,stderr=subprocess.STDOUT,check=True)
                    for stall in (0,7,13):
                        d=json.loads((gate/f'reports/wrapper_stall{stall}.json').read_text());assert d['matched'] and d['double_buffer']
                    save(gate/'pass.json',dict(status='pass',geometry='small representative only',point=p))
            cmd=[sys.executable,'-m','azilla_cycle_model.shared_fetch_double_cli' if p['mode']=='cores-only' else 'azilla_cycle_model.cli',
                'simulate-exact-events','--execution-mode',p['mode'],'--dataset',str(dataset),
                '--ramulator-library',str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                '--ramulator-config',str(config),'--metrics-prefix',str(dest/'metrics')]
            if p['campaign']=='mapping_ablation_exact_v1':
                sys.path.insert(0,str(out/'tools'))
                from run_fast_exact_continuation import ensure_mapping
                artifact=ensure_mapping(ROOT/'results/paper_validation_20260907'/p['campaign'],out/'mapping',p,snap)
                cmd[3]='simulate-mapped-exact-events';cmd[cmd.index('--dataset')+1]=str(artifact/'dataset.txt')
                cmd+=['--artifact',str(artifact)]
            else:
                for k,v in p['geometry'].items():cmd+=['--'+k.replace('_','-'),str(v)]
            for level,count in zip(('h0','h1','cross'),p['engines']):cmd+=['--'+level+'-mvms',str(count)]
            for opt,val in zip(('latency','flit-interval','max-inflight-flits','credit-return'),p.get('links',[])):
                cmd+=['--link-'+opt+('' if opt=='max-inflight-flits' else '-cycles'),str(val)]
            run(cmd,dest);guard()
            if p['mode']=='cores-only':
                subprocess.run([sys.executable,str(out/'tools/audit_shared_fetch_v3.py'),'--prefix',str(dest/'metrics')],check=True)
            else:
                d=json.loads((dest/'metrics_summary.json').read_text())
                assert d['execution_mode']=='cir' and d['noc']['injected_flits']==d['noc']['ejected_flits']
                assert all(x['accepted_requests']==x['completed_requests'] and not x['outstanding_requests'] for x in d['dram_systems'])
                save(dest/'audit.json',dict(status='conservation-pass',paper_ready=False,geometry_rtl_verified=False))
            done+=1
        except Exception as e:
            save(out/'failure.json',dict(point=p,error=repr(e)));raise
    save(out/'status.json',dict(status='complete',done=done,paper_ready=False))

if __name__=='__main__':main()
