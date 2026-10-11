import random
import unittest
from azilla_cycle_model.adapters import H0Adapter
from azilla_cycle_model.fast_adapters import FastH0Adapter
from azilla_cycle_model.hierarchy import PartialOutput


class FastAdapterTests(unittest.TestCase):
    def test_random_cycle_differential(self):
        rng=random.Random(908)
        for cores,engines,base in ((1,4,10),(8,8,0),(256,4,512)):
            original=H0Adapter(cores,engines,base)
            fast=FastH0Adapter(cores,engines,base)
            for cycle in range(5000):
                partials=[PartialOutput(rng.random()<.6,rng.getrandbits(256),
                                        base+rng.randrange(-2,cores+2),rng.random()<.3)
                          for _ in range(engines)]
                ready=[rng.random()<.6 for _ in range(cores)]
                # Also exercise diagnostic edits and otherwise unusual locks.
                if cycle%101==0:
                    core=rng.randrange(cores);engine=rng.randrange(engines)
                    original.locked[core]=fast.locked[core]=engine
                self.assertEqual(original.outputs(partials,ready),fast.outputs(partials,ready))
                ready[rng.randrange(cores)] ^= True
                rst=cycle%137==0
                self.assertEqual(original.tick(partials,ready,rst=rst),fast.tick(partials,ready,rst=rst))
                self.assertEqual(original.locked,fast.locked)
                self.assertEqual(original.outputs(partials,ready),fast.outputs(partials,ready))

    def test_idle_cache_and_reset(self):
        fast=FastH0Adapter(256,4)
        partials=[PartialOutput()]*4;ready=[True]*256
        before=fast.outputs(partials,ready)
        self.assertIs(before,fast.outputs(list(partials),list(ready)))
        self.assertIs(before,fast.tick(partials,ready))
        fast.reset()
        self.assertEqual(before,fast.outputs(partials,ready))


if __name__=='__main__':unittest.main()
