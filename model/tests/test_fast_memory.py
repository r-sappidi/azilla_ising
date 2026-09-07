import random
import unittest
from unittest.mock import patch
from azilla_cycle_model.fast_memory import FastDramWeightStreamer, FastRamulatorBackend
from azilla_cycle_model.memory import DramWeightStreamer, MemoryResponse
from azilla_cycle_model.hierarchy import DmaCommand
from azilla_cycle_model.ramulator import RamulatorBackend


class FastMemoryTests(unittest.TestCase):
    def test_randomized_streamer_edges(self):
        for engines, lanes in [(1, 1), (2, 4), (4, 16), (8, 16)]:
            rng = random.Random(81 + engines)
            kwargs = dict(mvm_count=engines, total_block_count=128,
                          request_lanes=lanes, response_lanes=lanes)
            ref, fast = DramWeightStreamer(**kwargs), FastDramWeightStreamer(**kwargs)
            pending = []
            for cycle in range(5000):
                a, b = ref.outputs(), fast.outputs()
                self.assertEqual(a, b, (engines, cycle))
                self.assertIs(b, fast.outputs())
                commands = [DmaCommand(rng.randrange(128), rng.randrange(128), 0, 0)
                            if ready and rng.random() < .2 else None
                            for ready in a.scheduler_ready]
                request_ready = [rng.random() < .65 for _ in range(lanes)]
                for request, ready in zip(a.memory_requests, request_ready):
                    if request is not None and ready:
                        pending.append((cycle + rng.randrange(1, 41),
                                        MemoryResponse(rng.getrandbits(256), request.tag)))
                rng.shuffle(pending)
                due = [v for v in pending if v[0] <= cycle][:lanes]
                for v in due:
                    pending.remove(v)
                responses = [v[1] for v in due] + [None] * (lanes - len(due))
                inputs = dict(scheduler_commands=commands,
                              node_command_ready=[rng.random() < .7 for _ in range(engines)],
                              node_weight_ready=[rng.random() < .7 for _ in range(engines)],
                              memory_request_ready=request_ready, memory_responses=responses)
                self.assertEqual(ref.tick(**inputs), fast.tick(**inputs))
                self.assertEqual(ref.slots, fast.slots)
                self.assertEqual(ref.head, fast.head)
                self.assertEqual(ref.tail, fast.tail)
            self.assertEqual(ref.tick(rst=True), fast.tick(rst=True))
            for _ in range(50):
                self.assertEqual(ref.tick(), fast.tick())

    def test_invalid_inputs_still_rejected(self):
        for factory in (DramWeightStreamer, FastDramWeightStreamer):
            obj = factory(mvm_count=2, total_block_count=16)
            with self.assertRaises(ValueError):
                obj.tick(node_weight_ready=[True])
            with self.assertRaises(TypeError):
                obj.tick(nonexistent=True)

    def test_empty_backend_fifo_cache(self):
        with patch.object(RamulatorBackend, '__init__', return_value=None), \
             patch.object(RamulatorBackend, 'pop', side_effect=[None, MemoryResponse(1, 2), None, None]) as pop, \
             patch.object(RamulatorBackend, 'tick') as tick, \
             patch.object(RamulatorBackend, 'send', return_value=True):
            b = FastRamulatorBackend()
            for _ in range(16):
                self.assertIsNone(b.pop(0))
            self.assertEqual(pop.call_count, 1)
            b.tick(40)
            self.assertEqual(b.pop(0), MemoryResponse(1, 2))
            self.assertIsNone(b.pop(0))
            self.assertIsNone(b.pop(0))
            self.assertEqual(pop.call_count, 3)
            b.send(0, 0, 0)
            self.assertIsNone(b.pop(0))
            self.assertEqual(pop.call_count, 4)
            tick.assert_called_once_with(40)


if __name__ == '__main__':
    unittest.main()
