import random
import unittest

from azilla_cycle_model.compute import SymmetricMVM
from azilla_cycle_model.config import ArchitectureConfig
from azilla_cycle_model.fast_compute import FastHierarchyNode, FastSymmetricMVM
from azilla_cycle_model.hierarchy import DmaCommand, HierarchyNode


def snapshot(node):
    return (node.node_state, node.schedule_done_pending, node.state_mem[:],
            node._state_responses[:], [
                {k: v for k, v in vars(e).items()
                 if k not in ('mvm', 'sram')} | {'mvm': mvm_snapshot(e.mvm)}
                for e in node.engines])


def mvm_snapshot(mvm):
    return (mvm.active, mvm.process_row, mvm.request_row, mvm.read_valid,
            mvm.result_a[:], mvm.result_b[:], mvm.done, mvm.weight_row)


class FastComputeTests(unittest.TestCase):
    def test_mvm_every_cycle(self):
        for spins in (4, 32, 64):
            config = ArchitectureConfig(spin_count=spins, data_width=spins*8)
            ref = SymmetricMVM(config, timing_only=True)
            fast = FastSymmetricMVM(config)
            rng = random.Random(spins)
            for cycle in range(5000):
                args = dict(rst=cycle % 137 == 0, start=rng.random() < .2,
                            state_a=rng.getrandbits(spins),
                            state_b=rng.getrandbits(spins),
                            weight_data=rng.getrandbits(spins*8))
                ref.tick(**args)
                fast.tick(**args)
                self.assertEqual(mvm_snapshot(ref), mvm_snapshot(fast))

    def test_hierarchy_random_backpressure_and_banks(self):
        for engines, banks in ((1, 1), (2, 8), (4, 8), (8, 8), (16, 4)):
            ref = HierarchyNode(128, engines, timing_only=True, state_bank_count=banks)
            fast = FastHierarchyNode(128, engines, state_bank_count=banks)
            rng = random.Random(engines)
            for cycle in range(2500):
                commands = [DmaCommand(rng.randrange(16)*8, rng.randrange(16)*8,
                                       rng.randrange(128), rng.randrange(128))
                            if rng.random() < .2 else None for _ in range(engines)]
                args = dict(rst=cycle % 811 == 0,
                            iter_start=cycle % 811 in (2, 3),
                            state_valid=rng.random() < .1, state_index=rng.randrange(128),
                            state_data=rng.getrandbits(32), schedule_done=cycle % 811 == 600,
                            commands=commands,
                            weight_valid=[rng.random() < .7 for _ in range(engines)],
                            weight_data=[rng.getrandbits(256) for _ in range(engines)],
                            partial_ready=[rng.random() < .45 for _ in range(engines)])
                self.assertEqual(ref.outputs(), fast.outputs())
                self.assertIs(fast.outputs(), fast.outputs())
                self.assertEqual(ref.tick(**args), fast.tick(**args))
                self.assertEqual(ref.outputs(), fast.outputs())
                self.assertEqual(snapshot(ref), snapshot(fast))

    def test_preedge_completion_and_direct_state_edit(self):
        for cls in (HierarchyNode, FastHierarchyNode):
            node = cls(32, 1, timing_only=True)
            node.outputs()
            node.node_state = node.RUN
            self.assertTrue(node.outputs().command_ready[0])
            engine = node.engines[0]
            engine.state = engine.COMPUTE
            engine.result_valid = [False, True]
            engine.result_write_slot = 0
            engine.mvm.done = True
            if isinstance(node, FastHierarchyNode):
                node.invalidate_outputs()
            node.tick()
            self.assertEqual(engine.result_read_slot, 1)
            self.assertEqual(engine.result_valid, [True, True])

    def test_complete_job_with_output_stalls(self):
        reference = HierarchyNode(32, 2, timing_only=True)
        fast = FastHierarchyNode(32, 2)
        accepted = [[], []]
        for cycle in range(180):
            args = dict(rst=cycle == 0, iter_start=cycle == 2,
                        commands=[DmaCommand(0, 8, 10, 20), DmaCommand(16, 24, 30, 40)]
                        if cycle == 3 else None,
                        weight_valid=[4 <= cycle < 36] * 2,
                        schedule_done=cycle == 36,
                        partial_ready=[cycle % 5 != 0, cycle % 7 != 0])
            out = reference.outputs()
            for lane in range(2):
                if out.partials[lane].valid and args['partial_ready'][lane]:
                    accepted[lane].append((cycle, out.partials[lane]))
            self.assertEqual(reference.tick(**args), fast.tick(**args))
            self.assertEqual(snapshot(reference), snapshot(fast))
            self.assertEqual(reference.outputs(), fast.outputs())
        self.assertTrue(reference.outputs().iter_done)
        self.assertEqual([len(packets) for packets in accepted], [8, 8])
        self.assertEqual([packet.block_id for _, packet in accepted[0]], [10]*4+[20]*4)

    def test_rejects_arithmetic_mode(self):
        for cls in (FastHierarchyNode, FastSymmetricMVM):
            with self.assertRaises(ValueError):
                cls(timing_only=False)


def benchmark():
    """Reproducible throughput microbenchmark, not a CI speed assertion."""
    import json
    import platform
    from statistics import median
    from time import perf_counter
    rng = random.Random(20260907)
    trace = [dict(rst=cycle == 0, iter_start=cycle == 2,
                  commands=[DmaCommand(0, 8, i, i+1) if cycle % 47 == 0 else None
                            for i in range(4)],
                  weight_valid=[rng.random() < .8 for _ in range(4)],
                  partial_ready=[rng.random() < .65 for _ in range(4)])
             for cycle in range(8000)]
    for polls in (1, 3, 6):
        times = {}
        for cls in (HierarchyNode, FastHierarchyNode):
            samples = []
            for repeat in range(3):
                node = cls(128, 4, timing_only=True)
                start = perf_counter()
                for args in trace:
                    for _ in range(polls):
                        node.outputs()
                    node.tick(**args)
                samples.append(perf_counter()-start)
            times[cls.__name__] = median(samples)
        print(json.dumps(dict(cycles=len(trace), engines=4, seed=20260907,
                              python=platform.python_version(),
                              output_polls_per_cycle=polls, seconds=times,
                              speedup=times['HierarchyNode']/times['FastHierarchyNode'])))


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--benchmark']:
        benchmark()
    else:
        unittest.main()
