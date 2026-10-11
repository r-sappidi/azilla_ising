#!/usr/bin/env python3
"""Gate unchanged CIR checks on a diagnostic, then run immutable source replay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--diagnostic', required=True, type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    manifest = json.loads((snapshot / 'snapshot_manifest.json').read_text())
    if manifest['status'] != 'ready':
        raise SystemExit('invalid snapshot')
    while not args.diagnostic.exists():
        time.sleep(30)
    diagnostic = json.loads(args.diagnostic.read_text())
    # The live-tree run is diagnostic only: source changes invalidate its
    # certificate, even when behavior matches. The final checker below retains
    # every original check, including its complete source immutability guard.
    if not all(diagnostic['checks'].get(key) is True
               for key in ('timing', 'transfers', 'noc')):
        raise SystemExit('diagnostic behavior failed; immutable replay not launched')
    for name, value in manifest['files'].items():
        if hashlib.sha256((snapshot / name).read_bytes()).hexdigest() != value:
            raise SystemExit(f'snapshot modified: {name}')
    output = snapshot / 'evidence'
    output.mkdir(exist_ok=False)
    cases = [
        ('random', 'holdout_random_d25_n16384.txt', 'cir_reset_random_rtl.log',
         'cir_reset_random_events.csv', 'cir_reset_random_noc.csv'),
        ('kings', 'g16384_kings.txt', 'cir_reset_rtl.log',
         'cir_reset_rtl_events.csv', 'cir_reset_noc.csv'),
        ('toroidal', 'holdout_toroidal_d4_n16384.txt', 'cir_reset_toroidal_rtl.log',
         'cir_reset_toroidal_events.csv', 'cir_reset_toroidal_noc.csv')]
    evidence_root = ROOT / 'results/paper_validation_20260906'
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for name, dataset, rtl_log, events, noc in cases:
        command = [sys.executable, str(snapshot / 'scripts/check_cir_exact_vcs_trace.py'),
                   '--dataset', str(ROOT / 'tb/datasets' / dataset),
                   '--rtl-log', str(evidence_root / rtl_log),
                   '--rtl-events', str(evidence_root / events),
                   '--rtl-noc', str(evidence_root / noc),
                   '--library', str(ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'),
                   '--config', str(ROOT / 'tb/ramulator_128x32.yaml'),
                   '--output', str(output / f'{name}.json')]
        print(f'START {name}: {command}', flush=True)
        with (output / f'{name}.log').open('x') as log:
            subprocess.run(['/usr/bin/time', '-v', *command], cwd=snapshot,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        print(f'PASS {name}', flush=True)
    print('ALL IMMUTABLE CIR CHECKS PASS', flush=True)


if __name__ == '__main__':
    main()
