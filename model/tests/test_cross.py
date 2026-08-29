import unittest

from azilla_cycle_model.cross import LocalInjectionArbiter
from azilla_cycle_model.noc import Flit


class CrossTests(unittest.TestCase):
    def test_local_cross_priority_and_lock(self):
        arbiter = LocalInjectionArbiter()
        cross_head = Flit(data=1, last=False)
        cross_tail = Flit(data=2, last=True)
        h1 = Flit(data=3, last=True)
        first = arbiter.tick(cross_head, h1, True)
        self.assertEqual(first.selected.data, 1)
        second = arbiter.tick(cross_tail, h1, True)
        self.assertEqual(second.selected.data, 2)
        third = arbiter.outputs(None, h1, True)
        self.assertEqual(third.selected.data, 3)


if __name__ == "__main__":
    unittest.main()
