#!/usr/bin/env python3
"""Frozen-original exact-event 16K link sensitivity, at most two processes.

All nondefault links are physical-interconnect projections, not RTL evidence.
No accelerator reclocking: each accelerator cycle remains 10 ns (100 MHz).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot'
OUT=ROOT/'results/paper_deadline_20260907/link_sensitivity_v1'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=False)
    dataset=ROOT/'tb/datasets/g16384_kings.txt'
    memory=BASE/'ramulator_128x32.yaml';library=ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'
    sources={str(p):sha(p) for p in (BASE/'model/azilla_cycle_model').glob('*.py')}
    inputs={str(p):sha(p) for p in (dataset,memory,library)}
    variants=[('baseline',0,1,4,0),('latency8',8,1,4,0),('latency8_serial2_credit8',8,2,4,8)]
    report=dict(status='running',dataset=str(dataset),geometry=dict(mesh_x=4,mesh_y=4,h0_per_h1=2,cores_per_h0=16),
        engines=[4,2,4],source_sha256=sources,input_sha256=inputs,accelerator_cycle_ns=10,
        scope='Exact-original-model physical-link sensitivity; nondefault settings are projections, not RTL verification',
        random_case='Not included in bounded first wave: existing16Krandom exceeds2.7million accepted events; Kings closes one representative multi-H1 matrix first.',cases=[])
    def save():
        p=OUT/'report.tmp.json';p.write_text(json.dumps(report,indent=2)+'\n');p.replace(OUT/'report.json')
    save()
    def run(mode,variant):
        name,latency,interval,inflight,credit=variant
        directory=OUT/(name+'_'+mode);directory.mkdir()
        command=[sys.executable,'-m','azilla_cycle_model.cli','simulate-exact-events',
            '--dataset',str(dataset),'--mesh-x','4','--mesh-y','4','--h0-per-h1','2','--cores-per-h0','16',
            '--h0-mvms','4','--h1-mvms','2','--cross-mvms','4','--execution-mode',mode,
            '--ramulator-library',str(library),'--ramulator-config',str(memory),
            '--idle-refresh-period-ticks','0','--link-latency-cycles',str(latency),
            '--link-flit-interval-cycles',str(interval),'--link-max-inflight-flits',str(inflight),
            '--link-credit-return-cycles',str(credit),'--metrics-prefix',str(directory/'metrics'),
            '--transfer-trace',str(directory/'transfers.csv')]
        (directory/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        start=time.perf_counter()
        env=dict(os.environ,PYTHONPATH=str(BASE/'model'),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
        with (directory/'run.log').open('w') as log:
            result=subprocess.run(['/usr/bin/time','-v','-o',str(directory/'wall.time'),*command],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError(str(directory)+' exited '+str(result.returncode))
        d=json.loads((directory/'metrics_summary.json').read_text())
        checks=dict(sources_unchanged=all(sha(p)==s for p,s in sources.items()),
            inputs_unchanged=all(sha(p)==s for p,s in inputs.items()),
            flit_conservation=d['noc']['injected_flits']==d['noc']['ejected_flits'],
            dram_conservation=all(r['accepted_requests']==r['completed_requests']==32*r['scheduled_jobs'] and not r['outstanding_requests'] for r in d['dram_systems']))
        if not all(checks.values()):raise RuntimeError(str(directory)+' failed '+str(checks))
        return dict(mode=mode,variant=name,status='pass',checks=checks,wall_seconds=time.perf_counter()-start,
            iteration_cycles=d['timing_cycles']['iteration'],iteration_us=d['timing_cycles']['iteration']*.01,
            total_cycles=d['timing_cycles']['total'],interconnect=d['interconnect'],noc=d['noc'],
            accuracy=d['accuracy'],physical_projection=latency!=0 or interval!=1 or credit!=0,
            files={str(p):sha(p) for p in directory.iterdir() if p.is_file()})
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run,mode,variant) for variant in variants for mode in ('cir','cores-only')]
            for future in as_completed(futures):
                result=future.result();report['cases'].append(result);save()
                print('PASS',result['variant'],result['mode'],result['iteration_cycles'],flush=True)
        report['status']='passed';save()
    except Exception as error:
        report['status']='failed';report['error']=repr(error);save();raise

if __name__=='__main__':main()
