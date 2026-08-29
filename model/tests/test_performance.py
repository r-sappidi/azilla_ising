import unittest

from azilla_cycle_model.performance import DirectPerformanceModel, PerformanceConfig
from azilla_cycle_model.workload import Geometry, IsingDataset


class PerformanceModelTests(unittest.TestCase):
    def test_complete_zero_off_diagonal_iteration(self):
        model = DirectPerformanceModel(
            Geometry(1, 1, 1, 1), IsingDataset(32, 0, {}),
            PerformanceConfig(max_cycles=1_000),
        )
        result = model.run(iterations=1)
        self.assertEqual(result.initialization_cycles, 36)
        self.assertEqual(result.total_cycles, 73)
        self.assertEqual(result.iterations[0].states, (0xFFFF_FFFF,))


if __name__ == "__main__":
    unittest.main()
