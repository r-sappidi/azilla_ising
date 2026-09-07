import unittest
from unittest.mock import patch
from azilla_cycle_model.shared_fetch_double import DoublePairReplay, DoubleFetchPipeline
from azilla_cycle_model.hierarchy import DmaCommand
from test_shared_fetch import SharedFetchTests


class DoubleFetchIntegrationTests(SharedFetchTests):
    def case(self, engines, interval, stall):
        with patch('azilla_cycle_model.shared_fetch_events.CoreIterationPipeline',DoubleFetchPipeline):
            super().case(engines,interval,stall)

    def test_distinct_cores_in_one_h0_deliver_concurrently(self):
        from collections import Counter
        from pathlib import Path
        from test_core_pipeline import ImmediateMemory
        from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
        from azilla_cycle_model.performance import PerformanceConfig
        from azilla_cycle_model.workload import Geometry,IsingDataset,ScheduledBlock
        from azilla_cycle_model.shared_fetch_double_cli import run_full_cores
        g=Geometry(1,1,1,8)
        with patch('azilla_cycle_model.exact_events.RamulatorBackend',ImmediateMemory):
            m=RamulatorEventPerformanceModel(g,IsingDataset(g.spin_count,0,{}),
                dataset_path='unused',ramulator_library='unused',ramulator_config=Path(__file__),
                ticks_per_cycle=1,config=PerformanceConfig(execution_mode='cores-only',timing_only=True,
                    h0_mvm_count=4,h1_mvm_count=2,cross_mvm_count=4,max_cycles=10000))
        accepted=Counter()
        def observe(cycle,event,source,dst,src):
            if source==0 and event=='weight':accepted[cycle]+=1
        m.core_event_observer=observe
        result=run_full_cores(m,[ScheduledBlock(a,a+1) for a in (0,2,4,6)],0)
        self.assertEqual(max(accepted.values()),4)
        self.assertEqual(sum(accepted.values()),4*64)
        self.assertEqual(sum(d.accepted_requests for d in result.dram),4*32)
        self.assertEqual(result.core_pipeline_contract['fetch_policy'],'single_fetch_two_slot_cir_routes_v3')


class DoubleReplayTests(unittest.TestCase):
    def test_order_backpressure_overlap_and_drain(self):
        p=DoublePairReplay()
        issued=0;filling=None;row=0;commands=[];beats=[];overlap=0
        for cycle in range(4000):
            out=p.outputs()
            ready_command=cycle%7!=0
            ready_weight=cycle%11>2
            command=None;wv=False;data=0
            if filling is None and issued<12 and p.state==p.IDLE:
                command=DmaCommand(issued,issued+20,issued,issued+20)
                filling=issued;issued+=1;row=0
            elif filling is not None and p.state==p.FILL and cycle%5!=0:
                wv=True;data=filling*100+row
                row+=1
                if row==32:filling=None
            if out.command_valid and ready_command:
                commands.append((out.command.block_a,out.command.block_b))
            if out.weight_valid and ready_weight:
                beats.append(out.weight_data)
                overlap+=int(wv)
            p.tick(command=command,weight_valid=wv,weight_data=data,
                   command_ready=ready_command,weight_ready=ready_weight)
            if issued==12 and filling is None and p.idle:break
        self.assertTrue(p.idle)
        self.assertGreater(overlap,0)
        self.assertEqual(commands,[(a,b) for n in range(12) for a,b in ((n,n+20),(n+20,n))])
        self.assertEqual(beats,[n*100+r for n in range(12) for _ in range(2) for r in range(32)])
        p.tick(command=DmaCommand(1,2,1,2))
        p.tick(rst=True)
        self.assertTrue(p.idle)
        self.assertEqual((p.fill_slot,p.drain_slot),(0,0))


if __name__=='__main__':unittest.main()
