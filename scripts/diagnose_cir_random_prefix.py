#!/usr/bin/env python3
"""Stop an exact CIR diagnostic immediately after a requested RTL prefix."""
import csv
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'model'))
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import Geometry,BlockOccupancyDataset

class PrefixDone(Exception):pass

def main():
    limit=int(sys.argv[1]);output=Path(sys.argv[2])
    expected=[]
    with (ROOT/'results/paper_validation_20260906/cir_reset_random_events.csv').open() as f:
        for row in csv.DictReader(f):
            if int(row['cycle'])>limit:break
            if row['event']=='accept':
                expected.append((int(row['cycle']),row['scope'],int(row['node']),row['direction'],
                  *(int(row[k]) for k in ('packet_type','source_id','epoch','block_id','dest_x','dest_y','last'))))
    expected.sort()
    path=ROOT/'tb/datasets/holdout_random_d25_n16384.txt'
    model=RamulatorEventPerformanceModel(Geometry(4,4,2,16),BlockOccupancyDataset.load(path),
        dataset_path=str(path),ramulator_library=str(ROOT/'build/cycle_model_ramulator/libazilla_ramulator.so'),
        ramulator_config=str(ROOT/'tb/ramulator_128x32.yaml'),
        config=PerformanceConfig(h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4,timing_only=True))
    original=model._record_network
    def observe(*args):
        original(*args)
        if args[-1]>=limit:raise PrefixDone()
    model._record_network=observe
    try:
        model.run()
    except PrefixDone:pass
    finally:model.close()
    observed=sorted(model.transfers)
    difference=next(((a,b) for a,b in zip(expected,observed) if a!=b),None)
    result=dict(status='PASS' if expected==observed else 'FAIL',limit=limit,
                expected_count=len(expected),observed_count=len(observed),first_difference=difference)
    output.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    assert expected==observed

if __name__=='__main__':main()
