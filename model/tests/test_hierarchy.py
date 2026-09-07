import unittest

from azilla_cycle_model.fixed import pack_lanes, unpack_signed_lanes
from azilla_cycle_model.hierarchy import DmaCommand, HierarchyNode


class HierarchyNodeTests(unittest.TestCase):
    def test_fetch_selects_preedge_slot_when_other_load_completes(self):
        node = HierarchyNode(state_entry_count=32, mvm_count=1,
                             timing_only=True)
        node.node_state = node.RUN
        engine = node.engines[0]
        engine.slot_valid = [False, True]
        engine.slot_commands = [DmaCommand(0, 8, 0, 8),
                                DmaCommand(1, 2, 1, 2)]
        engine.load_active = True
        engine.load_slot = 0
        engine.weight_beat = 31
        node.tick(weight_valid=[True])
        self.assertEqual(engine.active_slot, 1)
        self.assertEqual(engine.result_blocks[engine.result_write_slot], (1, 2))

    def test_output_selects_preedge_result_when_other_compute_completes(self):
        node = HierarchyNode(state_entry_count=32, mvm_count=1,
                             timing_only=True)
        node.node_state = node.RUN
        engine = node.engines[0]
        engine.state = engine.COMPUTE
        engine.result_valid = [False, True]
        engine.result_write_slot = 0
        engine.mvm.done = True
        node.tick()
        self.assertEqual(engine.result_read_slot, 1)

    def test_state_bank_conflicts_serialize_in_lane_order(self):
        node = HierarchyNode(state_entry_count=32, mvm_count=2,
                             state_bank_count=8)
        node.node_state = node.RUN
        for engine_index, engine in enumerate(node.engines):
            engine.state = engine.FETCH
            engine.slot_commands[0] = DmaCommand(
                8 * (2 * engine_index), 8 * (2 * engine_index + 1), 0, 1
            )

        # All four operands map to bank zero. The fixed-priority SRAM arbiter
        # therefore accepts one lane per cycle rather than inventing read
        # ports or duplicating the state table.
        accepted_lanes = []
        for _ in range(4):
            node.tick()
            self.assertEqual(len(node._state_responses), 1)
            accepted_lanes.append(node._state_responses[0][0])
        self.assertEqual(accepted_lanes, [0, 1, 2, 3])

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
