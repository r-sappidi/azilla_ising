import itertools
import unittest
from collections import Counter

from azilla_cycle_model.dense_analytical import dense_model, xy_path
from azilla_cycle_model.scheduler import compile_schedule, compile_cores_only_schedule
from azilla_cycle_model.workload import Geometry
from azilla_cycle_model.hierarchy import HierarchyNode, DmaCommand
from azilla_cycle_model.noc import FlooRouter, Flit, EAST, WEST, NORTH, SOUTH


class DenseAnalyticalTests(unittest.TestCase):
    def test_paths_against_router(self):
        g=Geometry(4,4,8,256)
        for source in range(16):
            for destination in range(16):
                flit=Flit(dest_x=destination%4,dest_y=destination//4)
                position=source;edges=[]
                while position!=destination:
                    direction=FlooRouter(position%4,position//4).route(flit)
                    nxt=position+{EAST:1,WEST:-1,NORTH:-4,SOUTH:4}[direction]
                    edges.append((position,nxt));position=nxt
                self.assertEqual(edges,list(xy_path(g,source,destination)))

    def test_enumerated_jobs_publications_and_paths(self):
        for g in (Geometry(1,1,8,8), Geometry(2,1,2,4),
                  Geometry(2,2,2,4), Geometry(3,2,2,4), Geometry(2,2,3,3)):
            for b in (1, g.total_blocks//2+1, g.total_blocks):
                pairs = list(itertools.combinations(range(b), 2))
                schedule = compile_schedule(g, pairs)
                for mode in ('cir', 'cores-only'):
                    with self.subTest(g=g, b=b, mode=mode):
                        p = dense_model(b*32, mode, g)
                        self.assertEqual(tuple(p['jobs'].values()), schedule.counts)
                        self.assertEqual(p['canonical_jobs'], len(pairs))
                        pub = (schedule.publications() if mode == 'cir' else
                               compile_cores_only_schedule(g, pairs).state_publications)
                        flows = Counter((block//g.blocks_per_h1, owner) for block,owner in pub)
                        self.assertEqual(len(pub), p['traffic_by_type']['state'])
                        same = dict(h0=[], h1=[], cross=[])
                        for level in same:
                            same[level] = [sum(w.block_a%8 == w.block_b%8 for w in q)
                                           for q in getattr(schedule, level)]
                        self.assertEqual(same, p['same_bank_jobs_per_resource'])
                        for owner, queue in enumerate(schedule.cross):
                            for job in queue:
                                for block in (job.block_a, job.block_b):
                                    flows[owner,block//g.blocks_per_h1] += 4 if mode=='cir' else 32
                        for n in range(g.node_count):
                            flows[n,n] += 1
                        links = Counter()
                        for (s,d),v in flows.items():
                            for a,z in xy_path(g,s,d):
                                links[f'{a}->{z}'] += v
                        self.assertEqual(dict(links), p['directed_links'])
                        self.assertEqual(sum(flows.values()), p['injected_flits'])

    def test_component_initiation_period(self):
        for pair, expected in (((0,1),38), ((0,8),39)):
            node = HierarchyNode(state_entry_count=16,mvm_count=1,timing_only=True)
            node.tick(rst=True); node.tick(); node.tick(iter_start=True)
            starts=[]; sent=0
            for cycle in range(3000):
                o=node.outputs()
                if node.engines[0].state == 'START': starts.append(cycle)
                cmd=DmaCommand(*pair,*pair) if o.command_ready[0] and sent<64 else None
                if cmd: sent+=1
                node.tick(commands=[cmd],weight_valid=[o.weight_ready[0]],
                          partial_ready=[True],schedule_done=sent==64)
                if node.outputs().iter_done: break
            self.assertEqual(len(starts),64)
            self.assertEqual({y-x for x,y in zip(starts,starts[1:])},{expected})

    def test_million_and_invalid(self):
        p=dense_model(1048576)
        self.assertEqual(p['canonical_jobs'],536854528)
        self.assertEqual(p['geometry'],dict(mesh_x=4,mesh_y=4,h0_per_h1=8,cores_per_h0=256))
        for n in (0,31,-32):
            with self.assertRaises(ValueError): dense_model(n)
        with self.assertRaises(ValueError): dense_model(128,geometry=Geometry(1,1,1,1))


if __name__ == '__main__':
    unittest.main()
