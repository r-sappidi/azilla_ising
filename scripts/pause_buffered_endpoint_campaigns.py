#!/usr/bin/env python3
"""Pause explicitly identified buffered workers/dispatchers; preserve CIR child.

One-time operational action for the October 4 streaming transition. Records
process start times so a later resume cannot accidentally target reused PIDs.
"""
import json
import os
from pathlib import Path
import signal
import time

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    (39461, 'results/cir_matched_all_campaigns_v3_active/tools/run_cir_matched_campaign.py'),
    (58772, 'results/mapping_shared_fetch_v3_20260908/tools/run_cir_matched_campaign.py'),
    (109109, 'scripts/run_matched_dense_exact.py'),
    (110606, 'azilla_cycle_model.shared_fetch_double_cli'),
    (110607, 'azilla_cycle_model.shared_fetch_double_cli'),
    (214438, 'azilla_cycle_model.shared_fetch_double_cli'),
    (214439, 'azilla_cycle_model.shared_fetch_double_cli'),
]


def identity(pid, token):
    p = Path('/proc') / str(pid)
    args = (p / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
    assert token in args, (pid, 'unexpected command')
    assert p.stat().st_uid == os.getuid(), (pid, 'different owner')
    stat = (p / 'stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, command=args, start_ticks=stat[19], state=stat[0])


def main():
    out = ROOT / 'results/buffered_endpoint_pause_20261004'
    out.mkdir(exist_ok=True)
    path = out / 'pause.json'
    if path.exists():
        record = json.loads(path.read_text())
        rows = record['processes']
    else:
        rows = [identity(pid, token) for pid, token in TARGETS]
        record = dict(status='prepared', time=time.time(), processes=rows,
                      scope='SIGSTOP only; dense CIR child remains running; dispatcher pauses also hold future CIR cells')
    path.write_text(json.dumps(record, indent=2) + '\n')
    for row, (pid, token) in zip(rows, TARGETS):
        assert identity(pid, token)['start_ticks'] == row['start_ticks']
        os.kill(pid, signal.SIGSTOP)
    time.sleep(0.2)
    record['after'] = [identity(pid, token) for pid, token in TARGETS]
    path.write_text(json.dumps(record, indent=2) + '\n')
    # tmux may continue its direct pane process. That sequential dispatcher
    # remains blocked in waitpid on the stopped worker; it cannot issue work.
    for row in record['after']:
        if row['pid'] in (39461, 110606) and row['state'] != 'T':
            assert (Path('/proc') / str(row['pid']) / 'wchan').read_text().strip() == 'do_wait'
            row['held_by_stopped_child'] = True
        else:
            assert row['state'] == 'T', row['pid']
    record['status'] = 'paused'
    path.write_text(json.dumps(record, indent=2) + '\n')
    print(path)


if __name__ == '__main__':
    main()
