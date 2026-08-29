import unittest

from azilla_cycle_model.adapters import H0Adapter, H1ChildAdapter, RoutedPartial
from azilla_cycle_model.hierarchy import PartialOutput


class AdapterTests(unittest.TestCase):
    def test_h0_packet_lock(self):
        adapter = H0Adapter(core_count=1, mvm_count=2, base_block_id=10)
        p0 = PartialOutput(True, 1, 10, False)
        p1 = PartialOutput(True, 2, 10, True)
        first = adapter.tick([p0, p1], [True])
        self.assertEqual(first.core_partials[0].data, 1)
        second = adapter.tick([
            PartialOutput(True, 3, 10, True), p1
        ], [True])
        self.assertEqual(second.core_partials[0].data, 3)
        third = adapter.outputs([PartialOutput(), p1], [True])
        self.assertEqual(third.core_partials[0].data, 2)

    def test_h1_local_priority_over_parent(self):
        adapter = H1ChildAdapter(2, 32, 1)
        local = PartialOutput(True, 11, 40, True)
        parent = RoutedPartial(True, 22, 41)
        output = adapter.outputs([local], parent, [True, True])
        self.assertEqual(output.children[1].data, 11)
        self.assertFalse(output.parent_ready)


if __name__ == "__main__":
    unittest.main()
