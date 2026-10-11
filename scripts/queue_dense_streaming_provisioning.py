#!/usr/bin/env python3
"""Fail-closed dense provisioning queue behind nominal streaming validation.

Exact-event timing only. Geometry/DRAM/links stay fixed; endpoint counts are
forwarding lanes, CIR counts are compute engines. No equal-area claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import fcntl

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1'
BASE = (4,2,4)
CONFIGS = [BASE,(2,2,4),(8,2,4),(4,4,4),(4,8,4),(4,16,4),
           (4,2,2),(4,2,8),(4,2,16),(4,8,16)]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, d):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(d,indent=2)+'\n'); tmp.replace(p)


def tag(e):
    return 'e'+'-'.join(map(str,e))


def run(cmd, log):
    log.parent.mkdir(parents=True,exist_ok=True)
    free=shutil.disk_usage(log.parent).free
    if free < 2*1024**3:
        raise RuntimeError(f'disk safety: only {free} bytes free; need 2 GiB reserve')
    save(log.with_suffix('.command.json'),list(map(str,cmd)))
    with log.open('x') as f:
        p=subprocess.Popen(['/usr/bin/time','-v','-o',str(log.with_suffix('.time')),*map(str,cmd)],
                       cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        while True:
            save(log.with_suffix('.process.json'),dict(pid=p.pid,status='running',
                heartbeat=time.time(),free_bytes=shutil.disk_usage(log.parent).free))
            try:rc=p.wait(timeout=15);break
            except subprocess.TimeoutExpired:pass
        save(log.with_suffix('.process.json'),dict(pid=p.pid,status='pass' if rc==0 else 'failed',
            returncode=rc,finished=time.time()))
        if rc:raise subprocess.CalledProcessError(rc,cmd)


def point(out, n, mode, engines, encoding='implicit', smoke=False):
    sys.path.insert(0,str(ROOT/'model'))
    from azilla_cycle_model.workload import Geometry, BlockOccupancyDataset
    from azilla_cycle_model.performance import PerformanceConfig
    from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
    from azilla_cycle_model.metrics import write_exact_event_metrics
    if mode=='cores-only':
        from azilla_cycle_model import core_full_events
        from azilla_cycle_model.streaming_cli import run_full_cores
        core_full_events.run_full_cores=run_full_cores
    g=Geometry(2,1,1,2) if smoke else Geometry(1 if n==65536 else 2,1,8,256)
    assert g.spin_count==n
    case=out/('encoding' if smoke else 'points')/f'dense_{n}'/tag(engines)/mode/encoding
    case.mkdir(parents=True,exist_ok=False)
    data=case/'timing_input.txt'
    with data.open('x') as f:
        f.write(f'{n} 0\n')
        if encoding=='explicit':
            assert smoke
            for i in range(n):
                for j in range(n):
                    if i!=j:f.write(f'{i+1} {j+1} -1\n')
    pairs={(a,b) for a in range(n//32) for b in range(a+1,n//32)}
    ds=BlockOccupancyDataset.load(data) if encoding=='explicit' else BlockOccupancyDataset(n,0,pairs)
    assert ds.active_block_pairs()==pairs
    count=len(pairs)
    cfg=PerformanceConfig(execution_mode=mode,timing_only=True,
        h0_mvm_count=engines[0],h1_mvm_count=engines[1],cross_mvm_count=engines[2],max_cycles=1_000_000_000)
    m=RamulatorEventPerformanceModel(g,ds,dataset_path=str(data),
        ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(ROOT/'tb/ramulator_128x32.yaml'),config=cfg,
        mem_lanes=16,ticks_per_cycle=40,idle_tick_modulus=None)
    started=time.monotonic()
    try:r=m.run()
    finally:m.close()
    assert sum(d.scheduled_jobs for d in r.dram)==count
    assert sum(d.accepted_requests for d in r.dram)==32*count
    assert all(d.accepted_requests==d.completed_requests and not d.outstanding_requests for d in r.dram)
    assert r.counters.injected_flits==r.counters.ejected_flits
    if mode=='cores-only':
        assert r.core_pipeline_contract['fetch_policy']==POLICY
        assert r.weight_block_reads==count and r.directed_core_jobs==2*count
    paths=write_exact_event_metrics(case/'metrics',r,g)
    save(case/'audit.json',dict(status='pass',spins=n,mode=mode,engines_or_lanes=engines,
        initialization_cycles=r.initialization_cycles,iteration_cycles=r.iteration_cycles,
        total_cycles=r.total_cycles,canonical_blocks=count,wall_seconds=time.monotonic()-started,
        transfer_hash=hashlib.sha256(repr(m.transfers).encode()).hexdigest() if smoke else None,
        files={str(p):sha(p) for p in paths.values()},accuracy=r.accuracy,
        fetch_policy=POLICY if mode=='cores-only' else 'cir',paper_ready=False,
        scope='exact-event dense timing projection; not full-size dense RTL or solution-quality validation',
        encoding='complete J=-1 graph; implicit mode elides values, not occupancy or 32 beats/block'))


def gate(out, engines):
    from evaluate_streaming_endpoints import build
    case=out/'gates'/tag(engines);case.mkdir(parents=True,exist_ok=False)
    # Streaming: signed arithmetic, multiple epochs, all hierarchy sources,
    # repeated jobs to saturate even the 16-lane configurations, delivery gaps.
    core=case/'streaming';core.mkdir()
    run([sys.executable,'scripts/generate_core_iteration_dataset.py',core/'dataset.txt'],core/'dataset.log')
    binary=build(core/'build','streaming','shared_fetch_publication_vcs_tb',
        [('ENGINES',max(engines)),('H0_ENGINES',engines[0]),('H1_ENGINES',engines[1]),
         ('CROSS_ENGINES',engines[2]),('DOUBLE_BUFFER',1),('JOBS_PER_SOURCE',32)])
    for stall in (0,7,13):
        run([binary,f'+DATASET={core}/dataset.txt',f'+STALL_PERIOD={stall}'],core/f'stall{stall}.log')
        run([sys.executable,'scripts/check_streaming_integrated.py','shared','--root',core,
             '--stall',stall,'--engines',max(engines),'--h0-engines',engines[0],
             '--h1-engines',engines[1],'--cross-engines',engines[2],'--repeat',16,'--double-buffer'],
            core/f'check{stall}.log')
    # CIR: fresh 2-H1 dense-occupancy timing oracle at this exact signature.
    import run_dense_rtl_winners as builder
    cir=case/'cir';cir.mkdir()
    data=ROOT/'tb/datasets'/f'provision_gate_{tag(engines)}.txt'
    data.parent.mkdir(exist_ok=True)
    pairs=[(a,b) for a in range(16) for b in range(a+1,16)]
    data.write_text('512 0\n'+''.join(f'{a*32+1} {b*32+1} -1\n{b*32+1} {a*32+1} -1\n' for a,b in pairs))
    sched=cir/'schedule.txt';sched.write_text(''.join(f'{a} {b} {(a+b)%2}\n' for a,b in pairs))
    row=dict(mesh_x='2',mesh_y='1',h0_per_h1='2',cores_per_h0='4',
             h0_mvms=str(engines[0]),h1_mvms=str(engines[1]),cross_mvms=str(engines[2]))
    cmd,binary=builder.vcs_build(row,1,dict(os.environ),True,skip_zero_blocks=True)
    run(cmd,cir/'build.log')
    run([binary,f'+DATASET={data.name}',f'+SCHEDULE={sched}',f'+NOC_STATS_FILE={cir}/noc.csv',
         f'+NOC_EVENT_FILE={cir}/events.csv',f'+INTERNAL_STATS_FILE={cir}/internal.csv'],cir/'rtl.log')
    run([sys.executable,'scripts/check_cir_exact_vcs_trace.py','--dataset',data,'--schedule',sched,
         '--rtl-log',cir/'rtl.log','--rtl-events',cir/'events.csv','--rtl-noc',cir/'noc.csv',
         '--output',cir/'differential.json','--mesh-x',2,'--mesh-y',1,'--h0-per-h1',2,'--cores-per-h0',4,
         '--h0-mvms',engines[0],'--h1-mvms',engines[1],'--cross-mvms',engines[2]],cir/'check.log')
    assert json.loads((cir/'differential.json').read_text())['status']=='pass'
    save(case/'pass.json',dict(status='pass',engines=engines,
        scope='representative small RTL signature checks; not full-size dense certification'))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--dependency',type=Path)
    ap.add_argument('--action',choices=('queue','gate','point'),default='queue')
    ap.add_argument('--engines',type=int,nargs=3,default=BASE)
    ap.add_argument('--size',type=int)
    ap.add_argument('--mode',choices=('cir','cores-only'))
    ap.add_argument('--encoding',default='implicit',choices=('implicit','explicit'))
    ap.add_argument('--smoke',action='store_true')
    ap.add_argument('--resume',action='store_true')
    a=ap.parse_args();out=a.output.resolve();e=tuple(a.engines)
    os.environ.update(VCS_HOME='/home/hwpeng/vcs',VCS_ARCH_OVERRIDE='linux',VCS_TARGET_ARCH='linux64',
                      OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    if a.action=='gate':return gate(out,e)
    if a.action=='point':return point(out,a.size,a.mode,e,a.encoding,a.smoke)
    assert a.dependency
    out.relative_to(ROOT/'results');out.mkdir(parents=True,exist_ok=a.resume)
    lockfile=(out/'queue.lock').open('a')
    fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
    os.nice(15)
    cells=[dict(size=n,engines=list(e),mode=m) for n in (65536,131072)
           for e in (CONFIGS[:6] if n==65536 else CONFIGS) for m in ('cores-only','cir')]
    save(out/'plan.json',dict(cells=cells,total=len(cells),baseline=BASE,
        dependency=str(a.dependency.resolve()),geometry='256 cores/H0, 8 H0/H1; 1H1 at64K,2H1 at128K',
        memory='fixed existing128x32 configuration,16 lanes,40ticks/cycle; no extra bandwidth per engine',
        stages=['nominal streaming RTL/model gate','signature/encoding gate',
                'streaming dense baseline performance64K then128K','paired provisioning'],
        scope='one-factor sensitivity plus4/8/16 combined; not exhaustive or equal-area; no hybrid policy',
        scheduling='one large point at a time; no resuming legacy queues'))
    state=dict(status='waiting_for_nominal_validation',completed=[],total=len(cells))
    save(out/'status.json',state)
    def update(**kw):state.update(kw);save(out/'status.json',state)
    try:
        while True:
            d=json.loads(a.dependency.read_text())
            if d['status']=='pass':break
            if d['status']!='running':raise RuntimeError('nominal validation did not pass')
            time.sleep(30)
        assert d['sources_unchanged']
        nominal_hashes=json.loads((a.dependency.parent/'sources.json').read_text())
        assert all(sha(p)==h for p,h in nominal_hashes.items()), 'nominal gate source drift'
        snap=out/'snapshot'
        if not (out/'manifest.json').exists():
          snap.mkdir()
          for directory in ('model','rtl'):
            shutil.copytree(ROOT/directory,snap/directory,symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__','.git'))
          (snap/'tb').mkdir();(snap/'scripts').mkdir();(snap/'build').mkdir()
          for p in (ROOT/'tb').iterdir():
            if p.is_file() and p.suffix in ('.sv','.cpp','.yaml'):shutil.copy2(p,snap/'tb'/p.name)
          for p in (ROOT/'scripts').glob('*.py'):shutil.copy2(p,snap/'scripts'/p.name)
          (snap/'third_party').symlink_to(ROOT/'third_party',target_is_directory=True)
          (snap/'build/cycle_model_ramulator').symlink_to(ROOT/'build/cycle_model_ramulator',target_is_directory=True)
          files=[p for folder in ('model','rtl','tb','scripts') for p in (snap/folder).rglob('*') if p.is_file()]
          files += [ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',ROOT/'third_party/ramulator2/libramulator.so']
          hashes={str(p):sha(p) for p in files};save(out/'manifest.json',dict(sources=hashes,policy=POLICY))
        hashes=json.loads((out/'manifest.json').read_text())['sources']
        runner=snap/'scripts/queue_dense_streaming_provisioning.py'
        def stable():assert all(sha(p)==h for p,h in hashes.items()),'snapshot changed'
        def child(action,e=BASE,extra=(),name=None):
            stable()
            label=name or action+'_'+tag(e)
            receipt=out/'receipts'/f'{label}.json'
            if receipt.exists():
                recorded=json.loads(receipt.read_text())
                assert recorded['status']=='pass'
                assert all(sha(p)==h for p,h in recorded['files'].items()),'completed artifact drift'
                return
            if action=='gate':artifact=out/'gates'/tag(e)
            else:
                opts=list(extra);n=int(opts[opts.index('--size')+1]);mode=opts[opts.index('--mode')+1]
                enc=opts[opts.index('--encoding')+1] if '--encoding' in opts else 'implicit'
                artifact=out/('encoding' if '--smoke' in opts else 'points')/f'dense_{n}'/tag(e)/mode/enc
            if artifact.exists() or (out/'logs'/f'{label}.log').exists():
                archive=out/'interrupted'/f'{label}_{time.time_ns()}';archive.mkdir(parents=True)
                if artifact.exists():shutil.move(str(artifact),str(archive/'artifacts'))
                for p in (out/'logs').glob(label+'.*'):shutil.move(str(p),str(archive/p.name))
            run([sys.executable,runner,'--output',out,'--action',action,'--engines',*e,*extra],
                out/'logs'/f'{label}.log')
            stable()
            files={str(p):sha(p) for p in artifact.rglob('*') if p.is_file() and p.suffix in ('.json','.csv','.log','.txt') and 'build' not in p.relative_to(artifact).parts}
            assert files,'missing child evidence'
            save(receipt,dict(status='pass',files=files))
        update(status='baseline_signature_gate')
        child('gate')
        update(status='dense_encoding_gate')
        for mode in ('cores-only','cir'):
            for enc in ('explicit','implicit'):
                child('point',extra=('--size',128,'--mode',mode,'--encoding',enc,'--smoke'),name=f'encoding_{mode}_{enc}')
            p=out/'encoding/dense_128'/tag(BASE)/mode
            l=json.loads((p/'explicit/audit.json').read_text());r=json.loads((p/'implicit/audit.json').read_text())
            for k in ('initialization_cycles','iteration_cycles','total_cycles','canonical_blocks','transfer_hash'):
                assert l[k]==r[k],(mode,k)
        save(out/'encoding_gate.json',dict(status='pass',scope='explicit complete graph vs implicit timing occupancy;128spins'))
        # Endpoint-only performance comes before the provisioning experiment.
        ordered=[c for c in cells if c['engines']==list(BASE) and c['mode']=='cores-only']
        ordered += [c for c in cells if c not in ordered]
        gated={BASE}
        for c in ordered:
            e=tuple(c['engines']);n=c['size'];mode=c['mode'];name=f'dense_{n}_{mode}_{tag(e)}'
            if e not in gated:
                update(status='signature_gate',current=c)
                child('gate',e);gated.add(e)
            estimate=2*1024**3+(n//32)*(n//32-1)//2*2000
            while True:
                free=shutil.disk_usage(out).free
                if free<5*1024**3:
                    update(status='waiting_disk',current=c,free_bytes=free,required_bytes=5*1024**3)
                    time.sleep(30);continue
                available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
                if available>estimate+24*1024**3:break
                update(status='waiting_memory',current=c,estimated_bytes=estimate);time.sleep(30)
            update(status='endpoint_baseline_performance' if len(state['completed'])<2 else 'provisioning',current=c)
            child('point',e,('--size',n,'--mode',mode),name)
            audit=out/'points'/f'dense_{n}'/tag(e)/mode/'implicit/audit.json'
            assert json.loads(audit.read_text())['status']=='pass'
            state['completed'].append(name);update(last_audit=str(audit))
        update(status='complete',sources_unchanged=True,paper_ready=False)
    except BaseException as ex:
        update(status='blocked_failure',error=repr(ex));raise
    finally:
        lockfile.close()


if __name__=='__main__':main()
