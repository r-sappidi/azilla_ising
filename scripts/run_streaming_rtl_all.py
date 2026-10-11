#!/usr/bin/env python3
"""Drive check_cpp_streaming_vcs_trace.py over every stored streaming RTL trace
whose inputs can be recovered: the nominal campaign
(results/streaming_nominal_20261004_v1) and the 18 timing workloads of
results/streaming_differentials_20261004_v1 (RTL logs in
results/streaming_workloads_20261004_v1).  Writes <out>/<label>/comparison.json
and <out>/summary.json.  Runs sequentially (one C++ process at a time)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/home/rsappidi/azilla_ising')
HERE = Path(__file__).resolve().parent
PYTHON = '/home/rsappidi/miniforge3/envs/azilla/bin/python3'
NOMINAL = ROOT / 'results/streaming_nominal_20261004_v1'
DIFF = ROOT / 'results/streaming_differentials_20261004_v1'
WORKLOADS = ROOT / 'results/streaming_workloads_20261004_v1'


def traces():
    out = []
    out.append(dict(label='nominal_smoke256', kind='single', dataset=NOMINAL / 'datasets/toroidal_256.txt',
                    log=NOMINAL / 'smoke256/rtl.log', h0=2, cores=4, mesh=(1, 1), stall=0,
                    differential=NOMINAL / 'smoke256/differential.json'))
    out.append(dict(label='nominal_toroidal_65536', kind='single', dataset=NOMINAL / 'datasets/toroidal_65536.txt',
                    log=NOMINAL / 'toroidal_65536/streaming/stall0.log', h0=8, cores=256, mesh=(1, 1), stall=0,
                    differential=NOMINAL / 'toroidal_65536/differential_stall0.json'))
    for stall in (0, 7):
        out.append(dict(label=f'nominal_hierarchy_stress_131072_stall{stall}', kind='mesh',
                        dataset=NOMINAL / 'datasets/hierarchy_stress_131072.txt',
                        schedule=NOMINAL / 'datasets/hierarchy_stress_131072.schedule',
                        log=NOMINAL / f'hierarchy_stress_131072/streaming/stall{stall}.log', h0=8, cores=256,
                        mesh=(2, 1), stall=stall,
                        differential=NOMINAL / f'hierarchy_stress_131072/differential_stall{stall}.json'))
    manifest = json.loads((DIFF / 'manifest.json').read_text())
    for name, args in manifest['tasks']:
        if not name.startswith('mesh'):
            continue  # jobs2/8/32 component cases use arithmetic payload fixtures (not timing-only)

        def arg(flag):
            return args[args.index(flag) + 1]
        out.append(dict(label=f'workload_{name}', kind='mesh', dataset=ROOT / arg('--dataset'),
                        schedule=ROOT / arg('--schedule'), log=ROOT / arg('--log'), h0=int(arg('--h0')),
                        cores=int(arg('--cores')), mesh=(int(arg('--mesh-x')), int(arg('--mesh-y'))),
                        stall=int(arg('--stall')), differential=Path(arg('--output')),
                        manifest_task=args))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, default=Path('/var/tmp/azilla_cpp_streaming_20261007/results/rtl'))
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--filter', default='')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    selected = [t for t in traces() if (a.only is None or t['label'] in a.only) and a.filter in t['label']]
    if a.list:
        for t in selected:
            print(t['label'])
        return
    a.output.mkdir(parents=True, exist_ok=True)
    summary_path = a.output / 'summary.json'
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else dict(traces={})
    for t in selected:
        label = t['label']
        workdir = a.output / label / 'cpp'
        output = a.output / label / 'comparison.json'
        missing = [str(p) for p in (t['dataset'], t['log'], t.get('schedule'), t['differential']) if p and not p.exists()]
        if missing:
            summary['traces'][label] = dict(status='missing-inputs', missing=missing)
            summary_path.write_text(json.dumps(summary, indent=2) + '\n')
            print('MISSING', label, missing, flush=True)
            continue
        command = [PYTHON, str(HERE / 'check_cpp_streaming_vcs_trace.py'), '--kind', t['kind'], '--dataset', str(t['dataset']),
                   '--log', str(t['log']), '--h0', str(t['h0']), '--cores', str(t['cores']), '--mesh-x', str(t['mesh'][0]),
                   '--mesh-y', str(t['mesh'][1]), '--stall', str(t['stall']), '--workdir', str(workdir), '--output',
                   str(output), '--python-differential', str(t['differential']), '--label', label]
        if t.get('schedule'):
            command += ['--schedule', str(t['schedule'])]
        (a.output / label).mkdir(parents=True, exist_ok=True)
        (a.output / label / 'driver_command.json').write_text(json.dumps(command, indent=2) + '\n')
        start = time.perf_counter()
        with (a.output / label / 'checker.log').open('w') as log:
            proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        entry = dict(status='unknown', returncode=proc.returncode, checker_wall_seconds=time.perf_counter() - start,
                     trace=dict({k: (str(v) if isinstance(v, Path) else v) for k, v in t.items()}))
        if output.exists():
            report = json.loads(output.read_text())
            entry.update(status=report['status'], checks=report['checks'], rtl_total=report.get('rtl_total'),
                         model_total=report.get('model_total'), iteration_cycles=report.get('iteration_cycles'),
                         three_way_agreement=report.get('three_way_agreement'), execution=report.get('execution'),
                         event_counts=report.get('event_counts'), python_differential=report.get('python_differential'))
        summary['traces'][label] = entry
        summary_path.write_text(json.dumps(summary, indent=2) + '\n')
        print(entry['status'].upper(), label, 'three_way', entry.get('three_way_agreement'),
              'wall', entry.get('execution', {}).get('wall_seconds'), flush=True)


if __name__ == '__main__':
    main()
