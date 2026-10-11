"""Opt-in endpoint bookkeeping acceleration for canonical shared-fetch v3.

No memory, buffering, placement, routing, arithmetic latency or controller
transitions change. Timing-only: not an arithmetic-validation entrypoint.
"""


def activate():
    from . import core_iteration_pipeline, core_full_events
    from .fast_core import FastCoreIteration
    from .shared_fetch_double_cli import run_full_cores
    core_iteration_pipeline.CoreIteration = FastCoreIteration
    core_full_events.run_full_cores = run_full_cores


def main():
    import sys
    if len(sys.argv)<2 or sys.argv[1] not in ('simulate-exact-events','simulate-mapped-exact-events'):
        raise SystemExit('Only exact-event timing commands supported')
    if '--execution-mode' not in sys.argv or sys.argv[sys.argv.index('--execution-mode')+1]!='cores-only':
        raise SystemExit('Explicit --execution-mode cores-only required')
    activate()
    from .cli import main as cli_main
    cli_main()


if __name__=='__main__':main()
