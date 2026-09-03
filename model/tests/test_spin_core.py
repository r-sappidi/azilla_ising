import unittest

from azilla_cycle_model.compute import SpinCore
from azilla_cycle_model.fixed import pack_lanes


class SpinCoreTests(unittest.TestCase):
    def initialize(self, core: SpinCore, rows: list[int], state: int) -> None:
        core.tick(init_start=True, init_state=state, coeff_a=0, coeff_b=1,
                  noise_seed=1)
        for row in rows:
            core.tick(weight_init_valid=True, weight_init_data=row)
        core.tick()
        self.assertTrue(core.outputs().init_done)

    def test_local_iteration(self):
        matrix = [[1 if row == column else 0 for column in range(32)]
                  for row in range(32)]
        rows = [pack_lanes(row, 8) for row in matrix]
        initial = 0xA55AA55A
        core = SpinCore()
        self.initialize(core, rows, initial)

        core.tick(iter_start=True, coeff_a=0, coeff_b=1)
        # Completion may arrive early and must be retained until the MVM drains.
        core.tick(partials_done=True)
        for _ in range(40):
            core.tick()
            if core.outputs().iter_done:
                break
        self.assertTrue(core.outputs().iter_done)
        # J=I, a=0, b=1 means the sign remains the original spin.
        self.assertEqual(core.outputs().state_next, initial)
        core.tick(commit=True)
        core.tick()
        self.assertEqual(core.outputs().state_current, initial)

    def test_partial_accumulation_packet_wrapping(self):
        rows = [0] * 32
        core = SpinCore()
        self.initialize(core, rows, 0)
        core.tick(iter_start=True)
        one_lane_word = pack_lanes([1] * 8, 32)
        for _ in range(4):
            core.tick(h0_partial_valid=True, h0_partial_data=one_lane_word)
        self.assertEqual(core.h0_partial_beat_count, 0)
        self.assertEqual(core.accumulator_h0, [1] * 32)

    def test_coefficients_are_sampled_at_iteration_boundary(self):
        core = SpinCore()
        initial = 0xA55AA55A
        self.initialize(core, [0] * 32, initial)

        core.tick(iter_start=True, coeff_a=3, coeff_b=0)
        core.tick(partials_done=True, coeff_a=-9, coeff_b=-9)
        for _ in range(40):
            core.tick(coeff_a=-9, coeff_b=-9)
            if core.outputs().iter_done:
                break

        self.assertTrue(core.outputs().iter_done)
        self.assertEqual(core.outputs().state_next, initial)


if __name__ == "__main__":
    unittest.main()
