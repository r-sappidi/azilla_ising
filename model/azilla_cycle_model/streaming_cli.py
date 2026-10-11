"""Opt-in streaming endpoints; baseline v3 model and launchers stay unchanged."""
from unittest.mock import patch
from dataclasses import replace
from . import core_full_events, shared_fetch_events, core_iteration_pipeline
from .shared_fetch_double import DoubleFetchPipeline
from .streaming_core import StreamingCoreIteration

POLICY = 'single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1'


class StreamingPipeline(DoubleFetchPipeline):
    def __init__(self, *args, **kwargs):
        with patch.object(core_iteration_pipeline, 'CoreIteration', StreamingCoreIteration):
            super().__init__(*args, **kwargs)
        self.audit['fetch_policy'] = POLICY
        self.audit['endpoint_compute'] = 'accepted-row streaming; one shared MVM'


def run_full_cores(*args, **kwargs):
    with patch.object(shared_fetch_events, 'CoreIterationPipeline', StreamingPipeline):
        result = shared_fetch_events.run_full_cores(*args, **kwargs)
    result.core_pipeline_contract['fetch_policy'] = POLICY
    result.core_pipeline_contract['model'] = 'streaming_endpoint_v1'
    result.core_pipeline_contract['endpoint_compute'] = 'accepted-row streaming'
    return replace(result, core_weight_buffers=0)


if __name__ == '__main__':
    import sys
    if len(sys.argv)<2 or sys.argv[1] not in ('simulate-exact-events','simulate-mapped-exact-events'):
        raise SystemExit('streaming_cli supports exact-event timing only')
    if '--execution-mode' not in sys.argv or sys.argv[sys.argv.index('--execution-mode')+1]!='cores-only':
        raise SystemExit('explicit --execution-mode cores-only required')
    core_full_events.run_full_cores = run_full_cores
    from .cli import main
    main()
