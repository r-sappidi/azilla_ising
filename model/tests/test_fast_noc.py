import random
import unittest
from azilla_cycle_model.noc import Mesh, Flit
from azilla_cycle_model.fast_noc import FastMesh
from azilla_cycle_model.noc import InterconnectConfig


class FastNocTests(unittest.TestCase):
    def test_edges_and_empty_intervals(self):
        for w,h in [(1,1),(2,1),(2,2),(4,4)]:
            for latency in (0,3):
                cfg=InterconnectConfig(forward_latency_cycles=latency,
                    flit_interval_cycles=2,max_inflight_flits=2,
                    credit_return_latency_cycles=5)
                ref,fast=Mesh(w,h,4,cfg),FastMesh(w,h,4,cfg)
                rng=random.Random(w*13+h+latency)
                for cycle in range(1200):
                    inject={}
                    if cycle%300<180:
                        for node in range(w*h):
                            if rng.random()<.25:
                                inject[node]=Flit(dest_x=rng.randrange(w),dest_y=rng.randrange(h),
                                    packet_type=rng.randrange(4),source_id=node,block_id=cycle,
                                    last=True,data=cycle)
                    ready={node:rng.random()<.6 for node in range(w*h)}
                    self.assertEqual([r.outputs() for r in ref.routers],
                                     [r.outputs() for r in fast.routers])
                    self.assertEqual(ref.tick(inject,ready),fast.tick(inject,ready))
                    self.assertEqual(ref.cycle,fast.cycle)
                    for a,b in zip(ref.routers,fast.routers):
                        for attr in ('fifos','rr_priority','request_snapshot','last_handshake',
                                     'arbiter_locked','locked_requests'):
                            self.assertEqual(getattr(a,attr),getattr(b,attr),(cycle,attr))
                    for key,a in ref.links.items():
                        self.assertEqual(a,fast.links[key])
                ref.reset();fast.reset()
                self.assertEqual(ref.tick(),fast.tick())


if __name__=='__main__':
    unittest.main()
