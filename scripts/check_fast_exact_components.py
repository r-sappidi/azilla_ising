#!/usr/bin/env python3
"""Run the 13 optimization component tests against an explicit frozen model."""
import argparse
from pathlib import Path
import sys
import unittest
from assemble_fast_exact_gate import hashes
from run_fast_exact_continuation import save


def main():
    if not __debug__:raise RuntimeError('assertions must be enabled')
    ap=argparse.ArgumentParser();ap.add_argument('--model',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists():ap.error('refusing overwrite')
    model=args.model.resolve();before=hashes(model)
    sys.path[:0]=[str(model/'tests'),str(model)]
    suite=unittest.TestLoader().loadTestsFromNames(['test_fast_compute','test_fast_core','test_fast_memory','test_fast_noc'])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    after=hashes(model)
    good=result.wasSuccessful() and result.testsRun==13 and not result.skipped and before==after
    save(args.output,dict(status='pass' if good else 'fail',tests_run=result.testsRun,
         failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
         source_sha256=before,sources_unchanged=before==after,model=str(model)))
    return 0 if good else 1


if __name__=='__main__':sys.exit(main())
