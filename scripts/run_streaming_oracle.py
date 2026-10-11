#!/usr/bin/env python3
"""Python oracle driver for the C++ cores-only port.

Runs ``azilla_cycle_model.streaming_cli`` (or ``shared_fetch_double_cli`` with
``--endpoint-policy buffered-v3``) unchanged, adding two test-only knobs the
RTL checkers (scripts/check_shared_fetch_two_h1.py) pass through
``arithmetic_fixture``: ``--stall-period N`` and ``--core-events PATH`` (the
core/memory/state observer streams as ``stream,cycle,event,a,b,c`` rows, the
schema the C++ binary writes for the same flags).  Without those flags it is
exactly ``python3 -m azilla_cycle_model.streaming_cli``.

No model file is modified; the wrapper only installs observers and the
fixture, as the existing checkers do.
"""
import sys
from pathlib import Path

ROOT = Path('/home/rsappidi/azilla_ising')
sys.path.insert(0, str(ROOT / 'model'))


def take(argv, flag, default=None):
    if flag in argv:
        index = argv.index(flag)
        value = argv[index + 1]
        del argv[index:index + 2]
        return value
    return default


def main():
    argv = sys.argv
    stall = int(take(argv, '--stall-period', '0'))
    events = take(argv, '--core-events')
    policy = take(argv, '--endpoint-policy', 'streaming')
    if argv[1] not in ('simulate-exact-events', 'simulate-mapped-exact-events'):
        raise SystemExit('run_streaming_oracle supports exact-event timing only')
    if '--execution-mode' not in argv or argv[argv.index('--execution-mode') + 1] != 'cores-only':
        raise SystemExit('explicit --execution-mode cores-only required')
    from azilla_cycle_model import core_full_events
    if policy == 'streaming':
        from azilla_cycle_model.streaming_cli import run_full_cores as inner
    elif policy == 'buffered-v3':
        from azilla_cycle_model.shared_fetch_double_cli import run_full_cores as inner
    else:
        raise SystemExit(f'unknown endpoint policy {policy}')
    sink = open(events, 'w') if events else None
    if sink:
        sink.write('stream,cycle,event,a,b,c\n')

    def run(model, records, initialization, **kwargs):
        if sink:
            model.core_event_observer = lambda c, e, s, a, b: sink.write(f'core,{c},{e},{s},{a},{b}\n')
            model.core_memory_observer = lambda c, e, s, t: sink.write(f'memory,{c},{e},{s},{t},0\n')
            model.core_state_observer = lambda c, e, k, v: sink.write(
                f'state,{c},{e},{k},{v if e == "request" else 0},0\n')
        fixture = dict(timing_only=True, stall_period=stall)
        return inner(model, records, initialization, arithmetic_fixture=fixture, **kwargs)

    core_full_events.run_full_cores = run
    from azilla_cycle_model.cli import main as cli_main
    try:
        cli_main()
    finally:
        if sink:
            sink.close()
    print(f'oracle_wrapper endpoint_policy={policy} stall_period={stall} core_events={events}')


if __name__ == '__main__':
    main()
