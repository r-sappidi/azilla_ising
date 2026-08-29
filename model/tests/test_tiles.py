import unittest

from azilla_cycle_model.fixed import pack_lanes
from azilla_cycle_model.tiles import H0Tile, H1Tile


class TileTests(unittest.TestCase):
    def test_two_core_end_to_end_iteration(self):
        tile = H0Tile(core_count=2, mvm_count=1)
        tile.tick(rst=True)
        tile.tick(init_start=True, init_states=[0xFFFFFFFF, 0],
                  coeff_b=1)
        zero = pack_lanes([0] * 32, 8)
        for _ in range(32):
            tile.tick(core_weight_valid=[True, True],
                      core_weight_data=[zero, zero], coeff_b=1)
        tile.tick(coeff_b=1)
        self.assertTrue(tile.outputs().init_done)

        tile.tick(iter_start=True, coeff_b=1)
        # No off-diagonal schedule: node completion plus parent completion
        # should still drain the core lifecycle exactly.
        sent_done = False
        for _ in range(100):
            schedule_done = tile.node.node_state == tile.node.RUN and not sent_done
            tile.tick(schedule_done=schedule_done,
                      external_partials_done=True, coeff_b=1)
            sent_done |= schedule_done
            if tile.outputs().iter_done:
                break
        self.assertTrue(tile.outputs().iter_done)

    def test_h1_lifecycle_without_off_diagonal_work(self):
        tile = H1Tile(h0_count=2, cores_per_h0=2,
                      h0_mvm_count=1, h1_mvm_count=1)
        tile.tick(rst=True)
        states = [[0xFFFFFFFF, 0], [0xAAAAAAAA, 0x55555555]]
        tile.tick(init_start=True, init_states=states, coeff_b=1)
        zero = pack_lanes([0] * 32, 8)
        for _ in range(32):
            tile.tick(core_weight_valid=[[True, True], [True, True]],
                      core_weight_data=[[zero, zero], [zero, zero]],
                      coeff_b=1)
        tile.tick(coeff_b=1)
        self.assertTrue(tile.outputs().init_done)
        tile.tick(iter_start=True, coeff_b=1)

        h0_done_sent = [False, False]
        h1_done_sent = False
        for _ in range(160):
            h0_done = [
                child.node.node_state == child.node.RUN and not h0_done_sent[index]
                for index, child in enumerate(tile.h0_tiles)
            ]
            h1_done = tile.node.node_state == tile.node.RUN and not h1_done_sent
            tile.tick(h0_schedule_done=h0_done,
                      h1_schedule_done=h1_done,
                      parent_partials_done=True, coeff_b=1)
            h0_done_sent = [old or new for old, new in zip(h0_done_sent, h0_done)]
            h1_done_sent |= h1_done
            if tile.outputs().iter_done:
                break
        self.assertTrue(tile.outputs().iter_done)


if __name__ == "__main__":
    unittest.main()
