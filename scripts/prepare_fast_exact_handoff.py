#!/usr/bin/env python3
"""Read-only launcher handoff plan/verification; never sends process signals.

Without --require-quiescent, output is only a plan and cannot unlock continuation.
After root has independently quiesced launcher Python processes and refreshed the
inventory, --require-quiescent verifies their stopped states and issues a record.
"""
import argparse
import json
from pathlib import Path
import time
from run_fast_exact_continuation import digest, save, verify_gate

RUNNERS = {'run_provisional_exact_matrix.py', 'run_provisional_mvm_sweep.py',
           'run_provisional_sensitivities.py', 'run_frozen_mapping_ablation.py'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inventory', type=Path, required=True)
    ap.add_argument('--gate', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--require-quiescent', action='store_true')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('refusing to overwrite a handoff record')
    inv = json.loads(args.inventory.read_text())
    processes = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
            stat = proc.joinpath('stat').read_text().split()
            processes[int(proc.name)] = dict(pid=int(proc.name), parent=int(stat[3]),
                state=stat[2], start_ticks=stat[21], command=argv)
        except (OSError, UnicodeError, ValueError):
            continue
    launchers = [p for p in processes.values()
                 if p['command'] and 'python' in Path(p['command'][0]).name
                 and any(Path(a).name in RUNNERS for a in p['command'][1:])]
    for launcher in launchers:
        descendants = {launcher['pid']}
        while True:
            expanded = descendants | {p['pid'] for p in processes.values() if p['parent'] in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        launcher['children_to_preserve'] = [processes[p] for p in sorted(descendants - {launcher['pid']})]
    if args.require_quiescent:
        if args.gate is None:
            ap.error('validated --gate required')
        verify_gate(args.gate)
        if len(launchers) != 6 or any(p['state'] not in ('T', 't') for p in launchers):
            raise RuntimeError('expected all six launcher Python processes to remain stopped')
        # Inventory must postdate quiescence review and be fresh, not an earlier
        # estimate of pending points while launchers were still issuing work.
        if time.time() - inv['timestamp'] > 120:
            raise RuntimeError('refresh read-only inventory immediately before handoff verification')
    save(args.output, dict(status='launchers-quiescent' if args.require_quiescent else 'plan-only',
         timestamp=time.time(), inventory_sha256=digest(args.inventory),
         gate_sha256=digest(args.gate) if args.gate else None, launchers=launchers,
         rules=['Never signal process groups, tmux sessions, /usr/bin/time, or simulation children.',
                'This script does not pause, terminate, resume, or restart anything.',
                'Do not retire stopped launchers until preserved children finish and outputs are audited.',
                'Root must refresh inventory after verified launcher stop before unlocking continuation.']))
    print(json.dumps(dict(status='launchers-quiescent' if args.require_quiescent else 'plan-only',
                         launchers=[dict(pid=p['pid'],state=p['state'],children=len(p['children_to_preserve'])) for p in launchers]), indent=2))


if __name__ == '__main__':
    main()
