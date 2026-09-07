import unittest
from azilla_cycle_model.core_iteration import CoreIteration, DirectedMVM
from azilla_cycle_model.compute import SynchronousBlockSram
from azilla_cycle_model.fixed import pack_lanes, lfsr_advance_32


class CoreIterationTests(unittest.TestCase):
    def test_transpose_has_same_timing_and_correct_values(self):
        matrix = [[(r*7+c*3)%15-7 for c in range(32)] for r in range(32)]
        state = 0x12345678
        for transpose in (False, True):
            mvm = DirectedMVM()
            mem = SynchronousBlockSram()
            mem.rows[0] = [pack_lanes(row, 8) for row in matrix]
            for cycle in range(33):
                old_row, data = mvm.weight_row, mem.read_data
                mvm.tick(start=cycle == 0, state=state, weight_data=data, transpose=transpose)
                mem.tick(read_slot=0, read_row=old_row)
            self.assertTrue(mvm.done)
            expected = [sum((matrix[c][r] if transpose else matrix[r][c]) *
                            (1 if state >> c & 1 else -1) for c in range(32))
                        for r in range(32)]
            self.assertEqual(mvm.result, expected)

    def test_complete_two_iterations_shared_engine(self):
        core = CoreIteration()
        diagonal = [[0 if r==c else (r+c)%3-1 for c in range(32)] for r in range(32)]
        block = [[(r*5+c*2)%11-5 for c in range(32)] for r in range(32)]
        state = 0x35fa8671
        seed = 0x83164521
        core.tick(rst=True)
        core.tick(init_start=True, init_state=state, noise_seed=seed)
        for row in diagonal:
            core.tick(weight_init_valid=True, weight_init_data=pack_lanes(row,8))
        core.tick()
        self.assertEqual(core.core_state, core.IDLE)
        for iteration in range(2):
            core.tick(iter_start=True, coeff_a=2, coeff_b=3)
            seed = lfsr_advance_32(seed)
            self.assertFalse(core.outputs().job_ready)
            for _ in range(40):
                if core.outputs().job_ready:
                    break
                core.tick()
            self.assertTrue(core.outputs().job_ready)
            expected = [sum(diagonal[r][c]*(1 if state>>c&1 else -1)
                            for c in range(32)) for r in range(32)]
            for transpose, source_state in ((False,0x12568ac3),(True,0xac623581)):
                core.tick(job_valid=True, source=3, transpose=transpose)
                for _ in range(3):
                    self.assertTrue(core.outputs().state_req_valid)
                    core.tick()
                core.tick(state_req_ready=True)
                core.tick(state_rsp_valid=True, state_rsp_data=source_state)
                for row in block:
                    core.tick(weight_valid=True, weight_data=pack_lanes(row,8))
                for _ in range(40):
                    if core.outputs().job_done:
                        break
                    core.tick()
                self.assertTrue(core.outputs().job_done)
                core.tick()
                self.assertTrue(core.outputs().job_ready)
                expected = [expected[r]+sum((block[c][r] if transpose else block[r][c])*
                            (1 if source_state>>c&1 else -1) for c in range(32)) for r in range(32)]
            core.tick(partials_done=True)
            core.tick()
            core.tick(noise_amplitude=3)
            self.assertTrue(core.outputs().iter_done)
            expected = [(2 if state>>r&1 else -2)+3*expected[r]+(3 if seed>>r&1 else -3)
                        for r in range(32)]
            self.assertEqual(core.accumulator_total, expected)
            state = sum(int(value>=0)<<r for r,value in enumerate(expected))
            self.assertEqual(core.outputs().state_next, state)
            core.tick(commit=True)
            core.tick()
            self.assertEqual(core.outputs().state_current, state)


if __name__ == '__main__':
    unittest.main()
