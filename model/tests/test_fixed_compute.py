import unittest

from azilla_cycle_model.compute import run_symmetric_block
from azilla_cycle_model.fixed import (
    dot_spin_row,
    pack_lanes,
    signed,
    unsigned,
)


class FixedArithmeticTests(unittest.TestCase):
    def test_signed_truncation(self):
        self.assertEqual(signed(0x7F, 8), 127)
        self.assertEqual(signed(0x80, 8), -128)
        self.assertEqual(unsigned(-1, 8), 255)

    def test_spin_dot_product(self):
        row = pack_lanes(range(-16, 16), 8)
        all_positive = (1 << 32) - 1
        self.assertEqual(dot_spin_row(row, all_positive), sum(range(-16, 16)))
        self.assertEqual(dot_spin_row(row, 0), -sum(range(-16, 16)))

    def test_symmetric_results_and_latency(self):
        matrix = [[((row * 7 + column * 3) % 17) - 8
                   for column in range(32)] for row in range(32)]
        packed = [pack_lanes(row, 8) for row in matrix]
        state_a = 0xA5A55A5A
        state_b = 0x13579BDF
        result_a, result_b, cycles = run_symmetric_block(packed, state_a, state_b)

        expected_a = []
        expected_b = [0] * 32
        for row in range(32):
            expected_a.append(sum(
                matrix[row][column] if (state_b >> column) & 1
                else -matrix[row][column]
                for column in range(32)
            ))
            for column in range(32):
                expected_b[column] += (
                    matrix[row][column] if (state_a >> row) & 1
                    else -matrix[row][column]
                )
        self.assertEqual(result_a, expected_a)
        self.assertEqual(result_b, expected_b)
        self.assertEqual(cycles, 33)  # start edge plus 32 consumed rows


if __name__ == "__main__":
    unittest.main()
