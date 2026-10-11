#!/usr/bin/env python3
"""Isolated optimized replay of a current dense-v3 CIR point."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path('/home/rsappidi/azilla_ising')
BASE=ROOT/'results/matched_dense_exact_v3_20260907'


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--size',type=int,choices=(4096,8192,16384,65536),default=65536)
    ap.add_argument('--prepare',action='store_true');a=ap.parse_args();out=a.output.resolve()
    out.relative_to(ROOT/'results')
    if a.prepare:
        gate=ROOT/'results/fast_h0_adapter_20260908/report.json'
        validation=json.loads(gate.read_text())
        assert validation['status']=='pass' and validation['sources_unchanged']
        candidate=ROOT/'model/azilla_cycle_model/fast_adapters.py'
        assert validation['source_sha256'][str(candidate)]==sha(candidate)
        out.mkdir(parents=True,exist_ok=False)
        shutil.copytree(BASE/'snapshot',out/'snapshot',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(candidate,out/'snapshot/model/azilla_cycle_model/fast_adapters.py')
        shutil.copy2(BASE/'runner.py',out/'worker.py')
        shutil.copy2(__file__,out/'run.py')
        shutil.copy2(gate,out/'adapter_equivalence.json')
        sources={str(p):sha(p) for p in (out/'snapshot').rglob('*') if p.is_file()}
        for p in (out/'worker.py',out/'run.py',out/'adapter_equivalence.json',
                  ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so',ROOT/'third_party/ramulator2/libramulator.so'):
            sources[str(p)]=sha(p)
        original_case=BASE/f'points/{a.size}/cir/implicit'
        if a.size!=65536:
            audit=json.loads((original_case/'audit.json').read_text())
            assert audit['status']=='conservation-pass'
            assert all(sha(p)==v for p,v in audit['files'].items())
            sources.update(audit['files'])
            sources[str(original_case/'audit.json')]=sha(original_case/'audit.json')
        save(out/'manifest.json',dict(sources=sources,baseline=str(BASE),spins=a.size,
            mode='cir',geometry=dict(mesh_x=1,mesh_y=1,h0_per_h1=8,cores_per_h0=256),
            engines=[4,2,4],optimization='FastH0Adapter only',command=sys.argv,
            scope='exact timing projection; size-specific equivalence pending comparison'))
        save(out/'status.json',dict(status='prepared',time=time.time()))
        return
    manifest=json.loads((out/'manifest.json').read_text())
    size=manifest['spins']
    assert all(sha(p)==v for p,v in manifest['sources'].items())
    sys.path.insert(0,str(out/'snapshot/model'))
    from azilla_cycle_model.fast_adapters import activate
    activate()
    spec=importlib.util.spec_from_file_location('frozen_dense_worker',out/'worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker);worker.ROOT=ROOT
    save(out/'status.json',dict(status='running',pid=os.getpid(),time=time.time()))
    print(f'START optimized CIR {size}; original runs untouched',flush=True)
    try:
        worker.worker(out,size,'cir','implicit',False)
        relative=Path(f'points/{size}/cir/implicit/metrics_summary.json')
        if (BASE/relative).exists():
            checks={'summary':json.loads((out/relative).read_text())==json.loads((BASE/relative).read_text())}
            for name in ('metrics_noc.csv','metrics_nodes.csv','metrics_dram.csv'):
                r=relative.with_name(name)
                checks[name]=sha(out/r)==sha(BASE/r)
            same=all(checks.values())
            save(out/'comparison.json',dict(status='pass' if same else 'fail',checks=checks,
                baseline=str(BASE/relative),scope='Full saved metrics equivalence; no timestamp trace was saved by original campaign'))
            if not same:raise RuntimeError(f'{size} original/candidate metrics differ')
        else:save(out/'comparison.json',dict(status='pending-original-completion',baseline=str(BASE/relative)))
        save(out/'status.json',dict(status='complete',time=time.time()))
    except BaseException as e:
        save(out/'status.json',dict(status='failed',error=repr(e),time=time.time()))
        raise


if __name__=='__main__':main()
