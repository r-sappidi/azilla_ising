"""Explicit experimental CLI; never changes cli/fast_cli or running processes."""
from . import shared_fetch_events, core_full_events
from .shared_fetch_double import DoubleFetchPipeline


def run_full_cores(*args, **kwargs):
    from unittest.mock import patch
    with patch.object(shared_fetch_events, 'CoreIterationPipeline', DoubleFetchPipeline):
        result = shared_fetch_events.run_full_cores(*args, **kwargs)
    result.core_pipeline_contract['fetch_policy'] = 'single_fetch_two_slot_cir_routes_v3'
    result.core_pipeline_contract['model'] = 'experimental_cir_matched_shared_fetch_full_iteration_v3'
    return result


if __name__ == '__main__':
    import sys
    if len(sys.argv)<2 or sys.argv[1] not in ('simulate-exact-events','simulate-mapped-exact-events'):
        raise SystemExit('shared_fetch_double_cli supports exact-event timing only')
    if '--execution-mode' not in sys.argv or sys.argv[sys.argv.index('--execution-mode')+1]!='cores-only':
        raise SystemExit('explicit --execution-mode cores-only required')
    core_full_events.run_full_cores = run_full_cores
    from .cli import main
    main()
