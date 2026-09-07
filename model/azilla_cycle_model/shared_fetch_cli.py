"""Experimental timing entrypoint. Not cleared for paper performance results."""
def main():
    import sys
    if len(sys.argv)<2 or sys.argv[1] not in ('simulate-exact-events','simulate-mapped-exact-events'):
        raise SystemExit('shared_fetch_cli supports exact-event timing only')
    if '--execution-mode' not in sys.argv or sys.argv[sys.argv.index('--execution-mode')+1]!='cores-only':
        raise SystemExit('explicit --execution-mode cores-only required')
    from . import core_full_events
    from .shared_fetch_events import run_full_cores
    core_full_events.run_full_cores=run_full_cores
    from .cli import main as run
    run()

if __name__=='__main__':main()
