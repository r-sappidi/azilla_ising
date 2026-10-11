#!/usr/bin/env python3
"""Streaming endpoint-only reruns paired with completed, audited CIR points.

Each command is derived from the CIR partner's recorded command: only the
module (streaming_cli), execution mode and metrics prefix change, so dataset,
mapping artifact, geometry, engines/lanes and Ramulator config match by
construction. Exact-event timing projections; not full-size RTL validation.
Workers write to a tmpfs work directory and results are copied to the output
with retries, because a full /home previously killed a queue mid-write.
"""
import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1'
SOURCES = [ROOT/'results/cir_matched_all_campaigns_v3_active',
           ROOT/'results/mapping_shared_fetch_v3_20260908']
NOMINAL = ROOT/'results/streaming_nominal_20261004_v1'
LIBRARIES = [ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',
             ROOT/'third_party/ramulator2/libramulator.so']
SUFFIXES = ('_summary.json','_dram.csv','_noc.csv','_nodes.csv')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, d):
    """Atomic JSON write that waits out a full filesystem instead of dying."""
    p = Path(p); tmp = p.with_suffix(p.suffix+'.tmp')
    while True:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(d,indent=2)+'\n'); tmp.replace(p); return
        except OSError as e:
            print(f'write retry {p}: {e}',flush=True); time.sleep(30)


def copy(src, dst):
    while True:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src,dst)
            assert sha(src)==sha(dst)
            return
        except (OSError, AssertionError) as e:
            print(f'copy retry {dst}: {e!r}',flush=True); time.sleep(30)


def available_memory():
    return 1024*int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()
                         if x.startswith('MemAvailable:')))


def start_ticks(pid):
    try:return (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:return None


def plan_cells():
    cells=[]
    for source in SOURCES:
        for p in json.loads((source/'plan.json').read_text())['points']:
            if p['mode']!='cir':continue
            cir=source/'points'/p['campaign']/p['relative']
            if not (cir/'audit.json').exists():continue
            origin=Path(json.loads((cir/'audit.json').read_text()).get('adopted_from',cir))
            cmd=json.loads((origin/'command.json').read_text())
            prefix=Path(cmd[cmd.index('--metrics-prefix')+1])
            summary=origin/(prefix.name+'_summary.json')
            d=json.loads(summary.read_text())
            assert d['execution_mode']=='cir' and cmd[1:3]==['-m','azilla_cycle_model.cli']
            assert cmd[cmd.index('--execution-mode')+1]=='cir'
            g=d['geometry']
            cells.append(dict(name=source.name+'/'+p['campaign']+'/'+str(Path(p['relative']).parent),
                spins=g['spin_count'],cir_command=cmd,cir_origin=str(origin),
                cir_summary=str(summary),cir_summary_sha256=sha(summary),
                cir_timing_cycles=d.get('timing_cycles'),
                inputs={k:sha(cmd[cmd.index(k)+1]) for k in ('--dataset','--ramulator-config')}))
    # Cheapest partners first so early cells act as a smoke check.
    cells.sort(key=lambda c:(c['spins'],(c['cir_timing_cycles'] or {}).get('iteration',0),c['name']))
    return cells


def command(cell, prefix):
    cmd=list(cell['cir_command'])
    cmd[2]='azilla_cycle_model.streaming_cli'
    cmd[cmd.index('--execution-mode')+1]='cores-only'
    cmd[cmd.index('--metrics-prefix')+1]=str(prefix)
    return cmd


def audit(cell, dest):
    files=[dest/('metrics'+s) for s in SUFFIXES]
    summary=json.loads(files[0].read_text())
    core=summary['cores_only_ablation'];contract=core['pipeline_contract']
    assert summary['execution_mode']=='cores-only' and contract['fetch_policy']==POLICY
    cir=json.loads(Path(cell['cir_summary']).read_text())
    assert sha(cell['cir_summary'])==cell['cir_summary_sha256'],'CIR partner changed'
    assert summary['geometry']==cir['geometry']
    with files[1].open() as f:dram=list(csv.DictReader(f))
    jobs=core['unordered_interaction_blocks']*contract['iterations']
    assert core['weight_block_reads']==jobs and core['directed_core_jobs']==2*jobs
    assert sum(int(d['scheduled_jobs']) for d in dram)==jobs
    assert sum(int(d['accepted_requests']) for d in dram)==32*jobs
    assert all(int(d['accepted_requests'])==int(d['completed_requests']) and
               int(d['outstanding_requests'])==0 for d in dram)
    assert summary['noc']['injected_flits']==summary['noc']['ejected_flits']
    save(dest/'audit.json',dict(status='conservation-pass',fetch_policy=POLICY,
        paper_ready=False,geometry_rtl_verified=False,timing_cycles=summary['timing_cycles'],
        cir_timing_cycles=cell['cir_timing_cycles'],cir_origin=cell['cir_origin'],
        cir_summary_sha256=cell['cir_summary_sha256'],canonical_jobs=jobs,directed_jobs=2*jobs,
        scope='exact-event streaming endpoint timing projection paired with existing CIR point; '
              'not full-size RTL or solution-quality validation',
        files={str(f):sha(f) for f in files}))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--memory-reserve-gib',type=float,default=40)
    ap.add_argument('--resume',action='store_true')
    a=ap.parse_args();out=a.output.resolve();out.relative_to(ROOT/'results')
    out.mkdir(parents=True,exist_ok=a.resume)
    lockfile=(out/'queue.lock').open('a')
    fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
    os.nice(15)
    work=Path('/tmp')/f'azilla_{out.name}'
    snap=out/'snapshot'
    if not (out/'manifest.json').exists():
        nominal=json.loads((NOMINAL/'status.json').read_text())
        assert nominal['status']=='pass' and nominal['sources_unchanged']
        validated=json.loads((NOMINAL/'sources.json').read_text())
        assert all(sha(p)==h for p,h in validated.items()),'source drift since nominal streaming validation'
        shutil.copytree(ROOT/'model',snap/'model',ignore=shutil.ignore_patterns('__pycache__','tests'))
        for p,h in validated.items():
            p=Path(p)
            if (ROOT/'model') in p.parents:assert sha(snap/p.relative_to(ROOT))==h
        files=[p for p in snap.rglob('*') if p.is_file()]+LIBRARIES
        save(out/'manifest.json',dict(policy=POLICY,sources={str(p):sha(p) for p in files},
            validation=str(NOMINAL/'status.json')))
        save(out/'plan.json',dict(policy=POLICY,cells=plan_cells(),
            note='Endpoint cells only; CIR partners reused unchanged with recorded hashes.'))
    hashes=json.loads((out/'manifest.json').read_text())['sources']
    cells=json.loads((out/'plan.json').read_text())['cells']
    def stable():assert all(sha(p)==h for p,h in hashes.items()),'snapshot changed'
    for record in out.glob('points/**/process.json'):
        d=json.loads(record.read_text())
        if d['status']=='running' and start_ticks(d['pid'])==d['start_ticks']:
            raise RuntimeError(f'worker still alive; inspect {record}')
    env=dict(os.environ,PYTHONPATH=str(snap/'model'),PYTHONDONTWRITEBYTECODE='1',
             OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    pending=[];done=[];failed=[];active={}
    for c in cells:
        dest=out/'points'/c['name']/'cores-only'
        if (dest/'audit.json').exists():done.append(c['name']);continue
        if dest.exists() or (work/c['name']).exists():
            archive=out/'interrupted'/f"{c['name'].replace('/','__')}_{time.time_ns()}"
            if dest.exists():shutil.move(str(dest),str(archive/'results'))
            if (work/c['name']).exists():shutil.move(str(work/c['name']),str(archive/'work'))
        pending.append(c)
    def status(state):
        save(out/'status.json',dict(status=state,total=len(cells),completed=done,failed=failed,
            active=sorted(active),pending=len(pending),heartbeat=time.time(),paper_ready=False))
    def finish(name):
        proc,log,cell,scratch,dest,started=active.pop(name);log.close()
        try:
            if proc.returncode:raise subprocess.CalledProcessError(proc.returncode,proc.args)
            stable()
            for f in scratch.iterdir():copy(f,dest/f.name)
            audit(cell,dest)
            save(dest/'process.json',dict(pid=proc.pid,status='pass',returncode=0,
                wall_seconds=time.time()-started))
            shutil.rmtree(scratch);done.append(name)
        except Exception as e:
            # Independent cells continue; a failure is kept as evidence, never retried silently.
            for f in scratch.glob('run.log'):copy(f,dest/f.name)
            save(dest/'failure.json',dict(cell=name,error=repr(e),returncode=proc.returncode,work=str(scratch)))
            save(dest/'process.json',dict(pid=proc.pid,status='failed',returncode=proc.returncode))
            failed.append(name)
    while pending or active:
        for name in [n for n,v in active.items() if v[0].poll() is not None]:finish(name)
        if pending and len(active)<a.workers and available_memory()>a.memory_reserve_gib*1024**3:
            c=pending.pop(0);name=c['name']
            dest=out/'points'/name/'cores-only';scratch=work/name
            scratch.mkdir(parents=True);stable()
            for k,h in c['inputs'].items():
                assert sha(c['cir_command'][c['cir_command'].index(k)+1])==h,'input changed since CIR run'
            cmd=command(c,scratch/'metrics')
            save(dest/'command.json',cmd)
            log=(scratch/'run.log').open('x')
            proc=subprocess.Popen(['/usr/bin/time','-v','-o',str(scratch/'wall.time'),*cmd],
                cwd=snap,env=env,stdout=log,stderr=subprocess.STDOUT)
            save(dest/'process.json',dict(pid=proc.pid,status='running',start_ticks=start_ticks(proc.pid),
                work=str(scratch)))
            active[name]=(proc,log,c,scratch,dest,time.time())
            print(f'started {name} pid={proc.pid}',flush=True)
        status('running');time.sleep(15)
    status('complete' if not failed else 'complete_with_failures')


if __name__=='__main__':main()
