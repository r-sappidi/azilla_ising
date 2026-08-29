import unittest

from azilla_cycle_model.fixed import pack_lanes, unpack_signed_lanes
from azilla_cycle_model.hierarchy import DmaCommand, HierarchyNode


class HierarchyNodeTests(unittest.TestCase):
    def test_one_block_pipeline_and_serialization(self):
        node = HierarchyNode(state_entry_count=2, mvm_count=1)
        node.tick(rst=True)
        node.tick()  # RESET -> IDLE
        node.tick(state_valid=True, state_index=0, state_data=0xFFFFFFFF)
        node.tick(state_valid=True, state_index=1, state_data=0)
        node.tick(iter_start=True)

        command = DmaCommand(0, 1, 10, 20)
        while not node.outputs().command_ready[0]:
            node.tick()
        node.tick(commands=[command])
        identity = [[1 if row == column else 0 for column in range(32)]
                    for row in range(32)]
        for row in identity:
            node.tick(weight_valid=[True], weight_data=[pack_lanes(row, 8)])
        node.tick(schedule_done=True)

        packets: dict[int, list[int]] = {10: [], 20: []}
        for _ in range(200):
            output = node.outputs().partials[0]
            if output.valid:
                packets[output.block_id].extend(
                    unpack_signed_lanes(output.data, 32, 8)
                )
            node.tick(partial_ready=[True])
            if node.outputs().iter_done:
                break
        self.assertTrue(node.outputs().iter_done)
        # J*state_b with all -1 gives -1 on each diagonal row.  J^T*state_a
        # with all +1 gives +1.
        self.assertEqual(packets[10], [-1] * 32)
        self.assertEqual(packets[20], [1] * 32)


if __name__ == "__main__":
    unittest.main()
