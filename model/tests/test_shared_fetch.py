import unittest
from pathlib import Path
from unittest.mock import patch
from test_core_pipeline import ImmediateMemory
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock
from azilla_cycle_model.noc import InterconnectConfig
from azilla_cycle_model.shared_fetch_events import run_full_cores


class SharedFetchTests(unittest.TestCase):
    def case(self, engines, interval, stall):
        g=Geometry(2,1,2,4)
        records=[ScheduledBlock(0,1),ScheduledBlock(0,4),ScheduledBlock(0,8,0),
                 ScheduledBlock(1,9,0),ScheduledBlock(2,10,0)]
        with patch('azilla_cycle_model.exact_events.RamulatorBackend',ImmediateMemory):
            m=RamulatorEventPerformanceModel(g,IsingDataset(g.spin_count,0,{}),
                dataset_path='unused',ramulator_library='unused',ramulator_config=Path(__file__),
                ticks_per_cycle=1,config=PerformanceConfig(execution_mode='cores-only',timing_only=True,
                h0_mvm_count=engines,h1_mvm_count=engines,cross_mvm_count=engines,max_cycles=100000,
                interconnect=InterconnectConfig(flit_interval_cycles=interval)))
        r=run_full_cores(m,records,0,iterations=2,
                         arithmetic_fixture={'timing_only':True,'stall_period':stall})
        self.assertEqual(r.weight_block_reads,10)
        self.assertEqual(sum(x.accepted_requests for x in r.dram),320)
        self.assertEqual(r.directed_core_jobs,20)
        self.assertEqual(r.counters.injected_flits,r.counters.ejected_flits)
        self.assertEqual(r.counters.type_flits[3],3*64*2)
        self.assertEqual(sum(m.core_pipeline_audit['local_delivery_beats'].values()),2*64*2)
        self.assertEqual(len(m.core_final_states),2)

    def test_multilane_backpressure(self):
        for engines in (1,2,4):
            for interval,stall in ((1,0),(2,7),(1,13)):
                with self.subTest(engines=engines,interval=interval,stall=stall):
                    self.case(engines,interval,stall)

if __name__=='__main__':unittest.main()
