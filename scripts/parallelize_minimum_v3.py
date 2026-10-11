#!/usr/bin/env python3
"""Run every missing corrected minimum point, preserving the in-flight child."""
import concurrent.futures,hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
V3=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
OUT=ROOT/'results/parallel_minimum_v3_20260907'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_name(p.name+'.tmp');temp.write_text(json.dumps(v,indent=2)+'\n');temp.replace(p)
def proc(pid):
    p=Path('/proc')/str(pid)
    s=p.joinpath('stat').read_text().split()
    return dict(pid=pid,start=s[21],state=s[2],argv=p.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0'))
def main():
    OUT.mkdir(exist_ok=False)
    expected={24894:['scripts/verify_toroidal_speedup.py','--double-buffer','--output','results/shared_fetch_cir_matched_v3_20260907'],
              39461:['results/cir_matched_all_campaigns_v3_active/tools/run_cir_matched_campaign.py','--output','results/cir_matched_all_campaigns_v3_active']}
    parents=[]
    for pid,tail in expected.items():
        r=proc(pid);assert r['argv'][1:]==tail,(pid,r)
        assert (Path('/proc')/str(pid)).stat().st_uid==os.getuid()
        parents.append(r)
    child=proc(30966)
    assert 'azilla_cycle_model.shared_fetch_double_cli' in child['argv']
    assert str(V3/'timing/community_d16_n65536_s1/metrics') in child['argv']
    assert child['state'] not in ('T','t','Z')
    # Pause producers, not simulation descendants. The broad queue resumes
    # only after all six audited core points exist, preventing adoption races.
    for r in parents:
        assert proc(r['pid'])['start']==r['start'];os.kill(r['pid'],signal.SIGSTOP)
    save(OUT/'handoff.json',dict(paused_parents=parents,preserved_child=child))
    snap=V3/'snapshot';manifest=json.loads((V3/'manifest.json').read_text())
    def guard():
        assert all(sha(snap/k)==h for k,h in manifest['sources'].items())
        for n in (1,4,16):
            for s in (0,7,13):
                d=json.loads((V3/f'shared_repeat{n}/wrapper_stall{s}.json').read_text())
                assert d['matched'] and d['double_buffer'] and all(d['checks'].values())
    guard()
    names=[f'{f}_n{n}{s}' for n in (65536,131072)
           for f,s in [('toroidal_d4',''),('community_d16','_s1'),('uniform_d16','_s1')]]
    env=dict(os.environ,PYTHONPATH=str(snap/'model'),PYTHONDONTWRITEBYTECODE='1',
             OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    def point(name):
        prefix=V3/'timing'/name/'metrics';audit=prefix.parent/'audit.json'
        if audit.exists():return name,'already-audited'
        if name=='community_d16_n65536_s1':
            save(OUT/f'{name}.json',dict(status='adopting-existing',pid=child['pid']))
            while True:
                try:r=proc(child['pid'])
                except FileNotFoundError:break
                if r['start']!=child['start'] or r['state']=='Z':break
                time.sleep(30)
            # The /usr/bin/time parent is independent of the stopped producer.
            wall=V3/f'timing_{name}.time'
            while not wall.exists() or 'Exit status:' not in wall.read_text():time.sleep(1)
            assert 'Exit status: 0' in wall.read_text(),'existing v3 child failed'
        else:
            assert not prefix.parent.exists(),'refusing to duplicate a started point'
            prefix.parent.mkdir(parents=True)
            dataset=V3/'datasets'/f'{name}.txt'
            source=ROOT/'results/paper_canonical/datasets/prepared'/dataset.name
            import shutil
            shutil.copy2(source,dataset)
            save(prefix.parent/'input.json',dict(dataset_sha256=sha(dataset),
                fetch_policy='single_fetch_two_slot_cir_routes_v3',paper_ready=False,geometry_rtl_verified=False))
            cmd=[sys.executable,'-m','azilla_cycle_model.shared_fetch_double_cli','simulate-exact-events',
                '--execution-mode','cores-only','--dataset',str(dataset),
                '--mesh-x','2' if '_n131072' in name else '1','--mesh-y','1',
                '--h0-per-h1','8','--cores-per-h0','256','--h0-mvms','4','--h1-mvms','2','--cross-mvms','4',
                '--ramulator-library',str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                '--ramulator-config',str(snap/'tb/ramulator_128x32.yaml'),'--metrics-prefix',str(prefix)]
            save(prefix.parent/'command.json',cmd)
            with (V3/f'timing_{name}.log').open('x') as log:
                p=subprocess.Popen(['/usr/bin/time','-v','-o',str(V3/f'timing_{name}.time'),*cmd],
                    cwd=snap,env=env,stdout=log,stderr=subprocess.STDOUT)
                save(OUT/f'{name}.json',dict(status='running',pid=p.pid,command=cmd))
                assert p.wait()==0,'new v3 child failed'
        guard()
        subprocess.run([sys.executable,str(ROOT/'scripts/audit_shared_fetch_v3.py'),'--prefix',str(prefix)],check=True)
        save(OUT/f'{name}.json',dict(status='audited',audit=str(audit)))
        return name,'audited'
    save(OUT/'status.json',dict(status='running',new_workers=4,preserved_workers=1,already_complete=1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(point,n):n for n in names}
        for future in concurrent.futures.as_completed(futures):
            try:print('COMPLETE',future.result(),flush=True)
            except Exception as e:
                save(OUT/'failure.json',dict(point=futures[future],error=repr(e)));raise
    r=next(p for p in parents if p['pid']==39461)
    assert proc(r['pid'])['start']==r['start'];os.kill(r['pid'],signal.SIGCONT)
    save(OUT/'status.json',dict(status='complete',audited=6,broad_queue_resumed=True,
                               old_serial_producer='left paused to prevent duplicate launches'))
if __name__=='__main__':main()
