#!/usr/bin/env python3
"""Prepare inert frozen automation; --activate is required for actual handoff.

Run activated supervisor inside tmux. Only the six explicitly recorded Python
launcher parents can receive SIGSTOP; simulation children are never signaled.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from run_fast_exact_continuation import digest,save,verify_gate,signature_key


def proc(pid):
    directory=Path('/proc')/str(pid)
    stat=(directory/'stat').read_text().split()
    return dict(pid=pid,start_ticks=stat[21],state=stat[2],parent=int(stat[3]),
        command=(directory/'cmdline').read_bytes().decode().rstrip('\0').split('\0'))


def same(record):
    current=proc(record['pid'])
    if current['start_ticks']!=record['start_ticks'] or current['command']!=record['command']:
        raise RuntimeError('PID identity changed')
    return current


def cover(inventory,gate):
    accepted={signature_key(s) for s in gate['covered_signatures']}
    missing=[p for c in inventory['campaigns'] for p in c['points']
             if p['status']=='unstarted' and signature_key(p) not in accepted]
    if missing:raise RuntimeError(f'{len(missing)} unstarted configurations lack coverage')
    if any(p['status'] in ('failed','ambiguous-started','audit-needs-review') for c in inventory['campaigns'] for p in c['points']):
        raise RuntimeError('inventory has failed or ambiguous points')


def preserved_child(entry):
    current=proc(entry['pid'])
    if current['start_ticks']!=entry['start_ticks'] or current['command']!=entry['command']:
        raise RuntimeError('child PID identity changed')
    if current['state'] in ('T','t','Z'):
        raise RuntimeError('child unexpectedly stopped or exited')
    return current


def prepare(args):
    config=json.loads(args.config.read_text());out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(config['repository']).resolve();bundle=out/'tools';bundle.mkdir()
    names=['run_fast_exact_continuation.py','prepare_fast_exact_handoff.py',
           'assemble_fast_exact_gate.py','supervise_fast_exact_handoff.py']
    for name in names:shutil.copy2(repo/'scripts'/name,bundle/name)
    shutil.copy2(config['inventory_script'],bundle/'inventory_pending_exact.py')
    hashes={str(p):digest(p) for p in bundle.iterdir()}
    for p in bundle.iterdir():p.chmod(0o444)
    env=dict(os.environ,AZILLA_REPOSITORY_ROOT=str(repo),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([config['python'],str(bundle/'prepare_fast_exact_handoff.py'),
        '--inventory',config['inventory'],'--output',str(out/'initial_handoff_plan.json')],env=env,check=True)
    launchers=json.loads((out/'initial_handoff_plan.json').read_text())['launchers']
    if len(launchers)!=6:raise RuntimeError('expected six launchers')
    save(out/'plan.json',dict(status='prepared-not-active',configuration=config,tools=hashes,
        bundle=str(bundle),output=str(out),launchers=launchers))
    print('Prepared inert plan',out/'plan.json')


def activate(plan_path):
    plan=json.loads(plan_path.read_text());cfg=plan['configuration'];out=Path(plan['output']);bundle=Path(plan['bundle'])
    worker_count=cfg['worker_count']
    if not isinstance(worker_count,int) or not 1<=worker_count<=8:
        raise RuntimeError('explicit worker_count must be between 1 and 8')
    if plan['status']!='prepared-not-active':raise RuntimeError('unexpected plan status')
    lock=(out/'activation.lock').open('x');lock.write(str(os.getpid()));lock.close()
    env=dict(os.environ,AZILLA_REPOSITORY_ROOT=cfg['repository'],PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONOPTIMIZE',None)
    gate_path=Path(cfg['gate']);stopped=[];workers=[];launched=False
    def note(status,detail):
        save(out/'supervisor_status.json',dict(status=status,time=time.time(),detail=detail))
        with (Path(cfg['repository'])/'LOCAL_RESEARCH_STATUS.md').open('a') as f:
            f.write('\n### Fast exact supervisor '+time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime())+'\n\n'+status+': '+detail+'\n')
        print(status,detail,flush=True)
    def tools_unchanged():
        if any(digest(p)!=sha for p,sha in plan['tools'].items()):raise RuntimeError('frozen automation changed')
    def inventory(tag):
        name='fast_handoff_'+str(os.getpid())+'_'+tag
        subprocess.run([cfg['python'],str(bundle/'inventory_pending_exact.py'),name],env=env,check=True)
        return Path(cfg['repository'])/'results/paper_validation_20260907'/name/'inventory.json'
    try:
        note('waiting-validation','No original launcher or simulation has been signaled.')
        previous_progress=None
        while True:
            tools_unchanged()
            progress={}
            for p in cfg['failure_reports']:
                p=Path(p)
                if p.exists():
                    try:value=json.loads(p.read_text())
                    except json.JSONDecodeError:continue
                    records=value if isinstance(value,list) else [value]+value.get('cases',[])
                    progress[str(p)]=sum(r.get('status')=='pass' for r in records)
                    if any(r.get('status') in ('fail','failed','mismatch','execution-failure','missing-dataset','incomplete') for r in records):
                        raise RuntimeError('validation failure: '+str(p))
            for directory in cfg['validation_directories']:
                directory=Path(directory)
                for failure in directory.rglob('failure.json'):
                    raise RuntimeError('validation failure marker: '+str(failure))
                for comparison in directory.glob('case_*/comparison.json'):
                    try:record=json.loads(comparison.read_text())
                    except json.JSONDecodeError:continue
                    if record.get('status')=='fail' or any(v is False for v in record.get('checks',{}).values()):
                        raise RuntimeError('failed case: '+str(comparison))
                for log in directory.rglob('*.log'):
                    with log.open('rb') as f:
                        f.seek(max(0,log.stat().st_size-16384));tail=f.read().decode(errors='replace')
                    if any(marker in tail for marker in ('Traceback (most recent call last):','Differential failure','Worker failed case=')):
                        raise RuntimeError('validation worker failed: '+str(log))
            if progress!=previous_progress:
                note('validation-progress',json.dumps(progress,sort_keys=True));previous_progress=progress
            result=subprocess.run([cfg['python'],str(bundle/'assemble_fast_exact_gate.py'),*cfg['assembler_arguments']],env=env)
            if result.returncode==0:break
            if result.returncode!=2:raise RuntimeError('gate assembler failed')
            time.sleep(60)
        gate=verify_gate(gate_path)
        before=inventory('before');cover(json.loads(before.read_text()),gate)
        note('validation-passed','All 18 full, 2 mapped, 2 refresh0 full, 78 window, and 13 component checks passed; preparing parent-only stop.')
        for launcher in plan['launchers']:
            current=same(launcher)
            if current['state'] in ('Z','T','t'):raise RuntimeError('unexpected launcher state before stop')
            os.kill(current['pid'],signal.SIGSTOP);stopped.append(launcher)
        for _ in range(2):
            time.sleep(.3)
            if any(same(p)['state'] not in ('T','t') for p in stopped):raise RuntimeError('launcher stop did not persist')
        # Capture children now; no stopped parent can issue a new child.
        child_state=[]
        for entry in json.loads(before.read_text())['processes']:
            try:child_state.append(preserved_child(entry))
            except FileNotFoundError:raise RuntimeError('child finished during stop; review before proceeding')
        save(out/'preserved_children.json',child_state)
        fresh=inventory('quiescent');cover(json.loads(fresh.read_text()),gate)
        handoff=out/'handoff.json'
        subprocess.run([cfg['python'],str(bundle/'prepare_fast_exact_handoff.py'),
            '--inventory',str(fresh),'--gate',str(gate_path),'--output',str(handoff),'--require-quiescent'],env=env,check=True)
        common=[cfg['python'],str(bundle/'run_fast_exact_continuation.py'),'--inventory',str(fresh),
            '--gate',str(gate_path),'--handoff',str(handoff),'--output',cfg['continuation_output'],
            '--python',cfg['python'],'--worker-count',str(worker_count)]
        if cfg.get('minimum_matrix_only', False):
            common.append('--minimum-matrix-only')
        for index in range(worker_count):subprocess.run([*common,'--worker-index',str(index),'--dry-run'],env=env,check=True)
        note('handoff-verified',f'All six parents stopped; current simulation children preserved. Launching {worker_count} versioned workers.')
        launched=True
        for index in range(worker_count):
            log=(out/f'worker{index}.log').open('x')
            child=subprocess.Popen([*common,'--worker-index',str(index)],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            log.close();workers.append(child)
        save(out/'workers.json',[dict(pid=p.pid,start_ticks=proc(p.pid)['start_ticks']) for p in workers])
        note('workers-active',f'{worker_count} continuation workers active; old parents remain stopped and old children complete normally.')
        previous_count=-1
        while any(p.poll() is None for p in workers):
            if any(p.poll() not in (None,0) for p in workers):raise RuntimeError('continuation worker failed; preserve other runs, manual review required')
            count=0
            for merged in Path(cfg['continuation_output']).glob('merged_provenance_worker*.json'):
                try:count+=len(json.loads(merged.read_text()))
                except json.JSONDecodeError:pass
            if count!=previous_count:
                note('collection-progress',str(count)+' points adopted or completed with audits.');previous_count=count
            time.sleep(60)
        if any(p.returncode!=0 for p in workers):raise RuntimeError('continuation worker failed')
        note('complete',f'All {worker_count} workers completed and audited their assigned points. Original stopped parents retained for explicit cleanup; no process groups signaled.')
    except BaseException as error:
        # Before any new worker exists, restore original queues on abort. Never
        # resume old launchers after new workers start (could duplicate points).
        if not launched:
            for launcher in stopped:
                try:
                    if same(launcher)['state'] in ('T','t'):os.kill(launcher['pid'],signal.SIGCONT)
                except (OSError,RuntimeError):pass
        note('blocked',repr(error)+'; original children were not signaled.')
        raise


def main():
    if not __debug__:raise RuntimeError('assertions must be enabled')
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('activate');p.add_argument('--plan',type=Path,required=True)
    args=ap.parse_args()
    if args.action=='prepare':prepare(args)
    else:activate(args.plan)


if __name__=='__main__':main()
