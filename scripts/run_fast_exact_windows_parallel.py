#!/usr/bin/env python3
"""Resume the owned window campaign with bounded parallel worker pairs.

Preserves the original immutable manifest, case paths and comparison schema.
An explicitly named stopped supervisor may be retired only after its verified
worker children finish naturally. No other process or experiment is touched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from check_fast_exact_windows import ROOT, fixture, hashes


def process(pid):
    base=Path('/proc')/str(pid)
    try:
        status=base.joinpath('stat').read_text().rsplit(')',1)[1].split()
        command=[part.decode() for part in base.joinpath('cmdline').read_bytes().split(b'\0') if part]
        return dict(state=status[0],ppid=int(status[1]),start_ticks=int(status[19]),command=command)
    except FileNotFoundError:
        return None


def alive(pid):
    p=process(pid)
    return p is not None and p['state']!='Z'


def comparison(case,index,signature,manifest):
    ref=json.loads((case/'reference/summary.json').read_text())
    opt=json.loads((case/'optimized/summary.json').read_text())
    checks={key:ref[key]==opt[key] for key in
            ('status','clock','active_window_cycles','final_sha256','event_counts','event_sha256')}
    checks['sources_unchanged']=ref['sources_unchanged'] and opt['sources_unchanged']
    checks['real_memory_work']=min(ref['dram_accepted'],ref['dram_completed'])>0
    checks['completed_compute_work']=ref['completed_compute_jobs']>0
    g=signature['geometry']
    levels=('h0','h1','cross') if g['mesh_x']*g['mesh_y']>1 else ('h0','h1')
    checks['all_resident_levels_exercised']=all(ref['level_accepted'][level]>0 for level in levels)
    checks['duration']=ref['status']=='complete' or ref['active_window_cycles']>=manifest['cycles']
    if ref['source_sha256']!=manifest['oracle_hashes'] or opt['source_sha256']!=manifest['optimized_hashes']:
        raise RuntimeError('Worker source manifest differs from frozen campaign')
    return dict(index=index,signature=signature,checks=checks,
        status='pass' if all(checks.values()) else 'fail',
        reference_wall=ref['wall_seconds'],optimized_wall=opt['wall_seconds'],
        speedup=ref['wall_seconds']/opt['wall_seconds'],event_counts=ref['event_counts'],
        completed_compute_jobs=ref['completed_compute_jobs'],level_accepted=ref['level_accepted'],
        active_window_cycles=ref['active_window_cycles'],complete=ref['status']=='complete')


def atomic_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2)+'\n')
    temp.replace(path)


def self_test(output):
    # Verify identical comparator schema on the small/first completed fixtures.
    manifest=json.loads((output/'manifest.json').read_text())
    for index in (0,1):
        case=output/f'case_{index:03d}'
        expected=json.loads((case/'comparison.json').read_text())
        assert comparison(case,index,expected['signature'],manifest)==expected
    with tempfile.TemporaryDirectory(prefix='azilla-window-driver-') as temp:
        for nodes in (1,4):
            signature=dict(geometry=dict(mesh_x=nodes,mesh_y=1,h0_per_h1=2,cores_per_h0=64))
            path=Path(temp)/f'fixture{nodes}.txt'
            count=fixture(signature,path)
            lines=path.read_text().splitlines()
            capacity=int(lines[0].split()[0])
            edges=[tuple(map(int,line.split()[:2])) for line in lines[1:]]
            assert count==len(edges)==len(set(edges)) and count>0
            assert all(1<=a<=capacity and 1<=b<=capacity and a!=b for a,b in edges)
    print('SELF_TEST PASS: comparator schema and 1/4-node small fixture bounds',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pairs',type=int,default=4)
    parser.add_argument('--adopt-stopped-supervisor',type=int)
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve()
    if args.self_test:return self_test(output)
    if not 1<=args.pairs<=4:parser.error('pair count must be between1and4')
    manifest=json.loads((output/'manifest.json').read_text())
    checker=ROOT/'scripts/check_fast_exact_windows.py'
    library=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'
    assert hashlib.sha256(checker.read_bytes()).hexdigest()==manifest['checker_sha256']
    assert hashlib.sha256(library.read_bytes()).hexdigest()==manifest['library_sha256']
    assert hashes(Path(manifest['oracle']))==manifest['oracle_hashes']
    assert hashes(Path(manifest['optimized']))==manifest['optimized_hashes']
    signatures=json.loads(Path(manifest['envelope']).read_text())['required_signatures']
    self_test(output)
    # Exclusivity protects the shared summary and case allocation.
    lock=os.open(output/'parallel_driver.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.write(lock,str(os.getpid()).encode());os.close(lock)
    running={}
    adopted=[]
    supervisor=args.adopt_stopped_supervisor
    supervisor_identity=None
    if supervisor:
        parent=process(supervisor)
        assert parent and parent['state']=='T','Supervisor must already be stopped'
        assert any(x.endswith('/check_fast_exact_windows.py') or x=='scripts/check_fast_exact_windows.py'
                   for x in parent['command']) and '--worker' not in parent['command']
        assert '--output' in parent['command']
        assert Path(parent['command'][parent['command'].index('--output')+1]).resolve()==output
        supervisor_identity=(parent['start_ticks'],parent['command'])
        children=Path(f'/proc/{supervisor}/task/{supervisor}/children').read_text().split()
        for child in children:
            pid=int(child);info=process(pid)
            if info is None:continue
            if info['state']=='Z':
                # Completed child may have lost cmdline; its existing summaries
                # will be audited below before the stopped supervisor is retired.
                adopted.append(pid);continue
            assert info['ppid']==supervisor and '--worker' in info['command']
            job=Path(info['command'][info['command'].index('--job')+1]).resolve()
            assert job.parent.parent==output and job.name=='job.json'
            index=int(job.parent.name.split('_')[1])
            running.setdefault(index,[]).append((pid,None,None))
            adopted.append(pid)
    comparisons=[]
    for index in manifest['indices']:
        case=output/f'case_{index:03d}'
        previous=case/'comparison.json'
        if previous.exists():
            row=json.loads(previous.read_text())
            assert row==comparison(case,index,signatures[index],manifest) and row['status']=='pass'
            comparisons.append(row)
        elif case.exists() and index not in running:
            # An adopted child may already be a zombie; retain the case and
            # verify finished evidence without relaunching it.
            if all((case/tag/'summary.json').exists() for tag in ('reference','optimized')):
                row=comparison(case,index,signatures[index],manifest)
                atomic_json(previous,row);comparisons.append(row)
                assert row['status']=='pass',row
            else:
                raise RuntimeError(f'Unowned incomplete case {case}; refusing overwrite')
    completed={row['index'] for row in comparisons}
    pending=[i for i in manifest['indices'] if i not in completed and i not in running]
    # Longer bulk-memory warmups first reduce the final straggler tail.
    pending.sort(key=lambda i: -(signatures[i]['geometry']['mesh_x']*
        signatures[i]['geometry']['mesh_y'])**2*signatures[i]['geometry']['h0_per_h1'])
    migration=dict(supervisor=supervisor,adopted_worker_pids=adopted,
        preserved_completed_indices=sorted(completed),pairs=args.pairs,
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        started_unix=time.time(),status='running',scope='Only owned validation orchestration replaced')
    atomic_json(output/'parallel_manifest.json',migration)
    atomic_json(output/'summary.json',sorted(comparisons,key=lambda r:r['index']))
    atomic_json(output/'parallel_status.json',dict(status='running',completed=len(comparisons),pairs=args.pairs))
    while pending or running:
        if supervisor and not any(alive(pid) for pid in adopted):
            # No worker is interrupted. SIGKILL retires only the stopped
            # dispatcher so it cannot issue a duplicate next case on resume.
            parent=process(supervisor)
            if parent:
                assert parent['state']=='T'
                assert (parent['start_ticks'],parent['command'])==supervisor_identity
                os.kill(supervisor,signal.SIGKILL)
            migration['retired_supervisor_unix']=time.time()
            atomic_json(output/'parallel_manifest.json',migration)
            print(f'RETIRED owned stopped supervisor {supervisor}; children finished naturally',flush=True)
            supervisor=None
        while pending and len(running)<args.pairs:
            index=pending.pop(0);signature=signatures[index]
            case=output/f'case_{index:03d}';case.mkdir()
            dataset=case/'dataset.txt';blocks=fixture(signature,dataset)
            job_path=case/'job.json'
            atomic_json(job_path,dict(signature=signature,dataset=str(dataset),occupied_blocks=blocks))
            pair=[]
            for tag,model_path,fast in [('reference',manifest['oracle'],False),('optimized',manifest['optimized'],True)]:
                command=[sys.executable,str(checker),'--worker','--job',str(job_path),
                    '--model-path',model_path,'--cycles',str(manifest['cycles']),
                    '--library',str(library),'--output',str(case/tag)]
                if fast:command.append('--fast')
                log=(case/(tag+'.log')).open('x')
                proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                                      env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
                pair.append((proc.pid,proc,log))
            running[index]=pair
            print(f'START case={index} mode={signature["mode"]} worker_pids={[x[0] for x in pair]}',flush=True)
        for index,pair in list(running.items()):
            done=True
            for pid,proc,log in pair:
                if proc is not None:
                    if proc.poll() is None:done=False
                elif alive(pid):done=False
            if not done:continue
            for pid,proc,log in pair:
                if log:log.close()
                if proc and proc.returncode:
                    raise RuntimeError(f'Worker{pid} failed{proc.returncode}; no new cases launched')
            case=output/f'case_{index:03d}'
            row=comparison(case,index,signatures[index],manifest)
            atomic_json(case/'comparison.json',row)
            comparisons.append(row)
            atomic_json(output/'summary.json',sorted(comparisons,key=lambda r:r['index']))
            atomic_json(output/'parallel_status.json',dict(status='running',completed=len(comparisons),pairs=args.pairs))
            print(f'{row["status"].upper()} case={index} completed={len(comparisons)}/78 speedup={row["speedup"]:.3f}',flush=True)
            if row['status']!='pass':raise RuntimeError(f'Differential failure case{index}')
            del running[index]
        if pending or running:time.sleep(3)
    assert {r['index'] for r in comparisons}==set(manifest['indices'])
    migration.update(status='complete',finished_unix=time.time())
    atomic_json(output/'parallel_manifest.json',migration)
    atomic_json(output/'parallel_status.json',dict(status='complete',completed=len(comparisons),pairs=args.pairs))
    print('ALL78WINDOWS PASS',flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        if '--output' in sys.argv:
            directory=Path(sys.argv[sys.argv.index('--output')+1])
            if directory.is_dir():
                atomic_json(directory/'parallel_status.json',dict(status='failed',error=repr(exc),unix=time.time()))
        raise
