#!/usr/bin/env python3
"""Run unchanged strict reference checkers with the opt-in streaming model.

No RTL timestamp drives the full-system model. Only the component check replays
external input stimuli. Existing buffered checker semantics are preserved.
"""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.streaming_cli import StreamingPipeline, run_full_cores, POLICY
from azilla_cycle_model import shared_fetch_double


def main():
    kind=sys.argv.pop(1)
    if kind=='single':
        import check_shared_fetch_single_h1 as checker
        checker.run_full_cores=run_full_cores
        target=Path(sys.argv[sys.argv.index('--output')+1])
    elif kind=='mesh':
        import check_shared_fetch_two_h1 as checker
        checker.run_full_cores=run_full_cores
        target=Path(sys.argv[sys.argv.index('--output')+1])
    elif kind=='shared':
        import check_shared_fetch_full_events as checker
        checker.run_full_cores=run_full_cores
        target=Path(sys.argv[sys.argv.index('--root')+1])/'streaming_validation_sources.json'
    else:raise ValueError(kind)
    files=list((ROOT/'model/azilla_cycle_model').glob('*.py'))
    files+=list((ROOT/'rtl/experimental/streaming').glob('*.sv'))
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with patch.object(shared_fetch_double,'DoubleFetchPipeline',StreamingPipeline):
        checker.main()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    target.with_suffix('.streaming_sources.json').write_text(json.dumps(dict(
        status='pass',fetch_policy=POLICY,source_sha256=before,
        scope='exact cycles and recorded events; see adjacent differential report'),indent=2)+'\n')


if __name__=='__main__':main()
