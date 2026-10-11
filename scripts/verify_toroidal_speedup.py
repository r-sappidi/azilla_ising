#!/usr/bin/env python3
"""Isolated, low-priority evidence collection; never modifies live campaigns.

The existing CIR executable is diagnostic evidence identified by its hash,
not a fresh source-provenance certificate. Shared-fetch stress replays are
small RTL differentials, not a 64K endpoint-system RTL certificate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--double-buffer', action='store_true',
                    help='validate two-slot forwarding and gate the six-point rerun on all checks')
    args = ap.parse_args()
    out = args.output.resolve()
    out.relative_to(ROOT / 'results')
    out.mkdir(parents=True, exist_ok=False)
    snap = out / 'snapshot'
    manifest = {}
    # Project-authored sources only. External NoC and Ramulator are referenced
    # in place, not rebuilt. No PDK or SRAM-compiler files are accessed.
    for folder, suffixes in [('model', {'.py'}), ('scripts', {'.py', '.sh'}),
                              ('rtl', {'.sv', '.f'}), ('tb', {'.sv', '.cpp', '.h', '.yaml'})]:
        for src in (ROOT / folder).rglob('*'):
            if (not src.is_file() or src.suffix not in suffixes or
                    'FlooNoC' in src.parts or '__pycache__' in src.parts):
                continue
            rel = src.relative_to(ROOT)
            dst = snap / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            before = digest(src)
            shutil.copy2(src, dst)
            if digest(dst) != before or digest(src) != before:
                raise RuntimeError(f'source changed during snapshot: {rel}')
            manifest[str(rel)] = before
    (snap / 'rtl/FlooNoC').symlink_to(ROOT / 'rtl/FlooNoC', target_is_directory=True)
    (snap / 'third_party').symlink_to(ROOT / 'third_party', target_is_directory=True)
    (snap / 'build').mkdir()
    (snap / 'build/cycle_model_ramulator').symlink_to(
        ROOT / 'build/cycle_model_ramulator', target_is_directory=True)
    dataset = ROOT / 'results/paper_canonical/datasets/prepared/toroidal_d4_n65536.txt'
    data = snap / 'tb/datasets/toroidal.txt'
    data.parent.mkdir()
    shutil.copy2(dataset, data)
    # Independent occupancy extraction; explicitly schedule sparse pairs so
    # a dense-default executable cannot accidentally execute a dense workload.
    pairs = set()
    with data.open() as f:
        assert int(next(f).split()[0]) == 65536
        for line in f:
            if not line.strip():
                continue
            a, b, w = map(int, line.split())
            if w and (a-1)//32 != (b-1)//32:
                pairs.add(tuple(sorted(((a-1)//32, (b-1)//32))))
    schedule = out / 'schedule.txt'
    schedule.write_text(''.join(f'{a} {b} 0\n' for a,b in sorted(pairs)))
    assert len(pairs) == 4096, len(pairs)
    binary = ROOT / 'build/ising_mesh_vcs_1x1_h08_c256_e4-2-4_f4_s0_r1_i1_t1/simv'
    metadata = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                    sources=manifest, dataset_sha256=digest(data),
                    existing_cir_binary=str(binary), binary_sha256=digest(binary),
                    schedule_sha256=digest(schedule), unordered_blocks=len(pairs),
                    paper_ready=False, full_shared_fetch_64k_rtl_verified=False)
    (out / 'manifest.json').write_text(json.dumps(metadata, indent=2)+'\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               LD_LIBRARY_PATH=str(ROOT/'third_party/ramulator2')+':'+os.environ.get('LD_LIBRARY_PATH',''))
    results = {}

    def run(name, command, extra=None):
        (out / f'{name}.command.json').write_text(json.dumps(command, indent=2)+'\n')
        print('START', name, flush=True)
        with (out / f'{name}.log').open('x') as log:
            p = subprocess.run(['/usr/bin/time','-v','-o',str(out/f'{name}.time'),
                                *command], cwd=snap, env=env | (extra or {}),
                               stdout=log, stderr=subprocess.STDOUT)
        results[name] = dict(returncode=p.returncode)
        (out/'status.json').write_text(json.dumps(results,indent=2)+'\n')
        print('FINISH', name, p.returncode, flush=True)
        return p.returncode == 0

    if run('cir64_rtl', [str(binary), '+DATASET=toroidal.txt',
            f'+SCHEDULE={schedule}', f'+DUMP_SCHEDULE={out}/rtl_schedule.txt',
            f'+NOC_STATS_FILE={out}/cir64_noc.csv',
            f'+INTERNAL_STATS_FILE={out}/cir64_internal.csv',
            f'+NOC_EVENT_FILE={out}/cir64_events.csv',
            f'+INTERNAL_EVENT_FILE={out}/cir64_internal_events.csv']):
        run('cir64_differential', [sys.executable, 'scripts/check_cir_exact_vcs_trace.py',
            '--dataset',str(data),'--rtl-log',str(out/'cir64_rtl.log'),
            '--rtl-events',str(out/'cir64_events.csv'),'--rtl-noc',str(out/'cir64_noc.csv'),
            '--output',str(out/'cir64_differential.json'),
            '--mesh-x','1','--mesh-y','1','--h0-per-h1','8','--cores-per-h0','256'])
    # Serial execution, one compiler job: bounded contention with live work.
    for repeat in (1, 4, 16):
        run(f'shared_repeat{repeat}', ['bash','scripts/run_shared_fetch_publication_vcs.sh'],
            dict(OUTPUT_ROOT=str(out/f'shared_repeat{repeat}'),
                 BUILD_ROOT=str(snap/f'build/shared_repeat{repeat}'), VCS_JOBS='1',
                 ENGINES='4', H0_ENGINES='4', H1_ENGINES='2', CROSS_ENGINES='4',
                 JOBS_PER_SOURCE=str(repeat*2), DOUBLE_BUFFER=str(int(args.double_buffer))))
    if args.double_buffer and all(r['returncode']==0 for r in results.values()):
        # Only this new campaign is launched. Old workers/outputs stay intact.
        # Serial timing collection avoids adding more than one CPU-heavy child.
        for size in (65536, 131072):
            for family in ('toroidal_d4','community_d16','uniform_d16'):
                name=f'{family}_n{size}'+('' if family=='toroidal_d4' else '_s1')
                src=ROOT/'results/paper_canonical/datasets/prepared'/f'{name}.txt'
                dst=out/'datasets'/src.name
                dst.parent.mkdir(exist_ok=True)
                shutil.copy2(src,dst)
                prefix=out/'timing'/name/'metrics'
                prefix.parent.mkdir(parents=True)
                (prefix.parent/'input.json').write_text(json.dumps(dict(
                    dataset_sha256=digest(dst),fetch_policy='single_fetch_two_slot_cir_routes_v3',
                    paper_ready=False,geometry_rtl_verified=False),indent=2)+'\n')
                ok=run(f'timing_{name}', [sys.executable,'-m','azilla_cycle_model.shared_fetch_double_cli',
                    'simulate-exact-events','--execution-mode','cores-only',
                    '--dataset',str(dst),'--mesh-x',str(size//65536),'--mesh-y','1',
                    '--h0-per-h1','8','--cores-per-h0','256',
                    '--h0-mvms','4','--h1-mvms','2','--cross-mvms','4',
                    '--ramulator-library',str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
                    '--ramulator-config',str(snap/'tb/ramulator_128x32.yaml'),
                    '--metrics-prefix',str(prefix)],dict(PYTHONPATH=str(snap/'model')))
                if not ok:break
            if not ok:break
    print('COLLECTION COMPLETE; inspect all differentials before making claims', flush=True)


if __name__ == '__main__':
    main()
