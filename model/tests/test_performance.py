import unittest
from unittest.mock import patch

from azilla_cycle_model.performance import DirectPerformanceModel, PerformanceConfig, RamulatorPerformanceModel
from azilla_cycle_model.workload import Geometry, IsingDataset


class PerformanceModelTests(unittest.TestCase):
    def test_ramulator_clock_begins_after_cycle_zero(self):
        # RTL releases reset after a falling edge: the first nonreset rising
        # edge has no preceding active DRAM tick. This matters at refresh.
        with patch('azilla_cycle_model.performance.RamulatorBackend') as backend_type:
            backend = backend_type.return_value
            backend.pop.return_value = None
            model = RamulatorPerformanceModel(
                Geometry(1, 1, 1, 1), IsingDataset(32, 0, {}),
                ramulator_library='unused', ramulator_config='unused',
                dataset_path='unused', ticks_per_cycle=40,
                config=PerformanceConfig(timing_only=True),
            )
            for _ in range(5):
                model._tick(rst=True)
            model._tick()
            backend.tick.assert_not_called()
            self.assertEqual(model.counters.cycles, 1)
            model._tick()
            backend.tick.assert_called_once_with(40)
            model._tick()
            self.assertEqual(backend.tick.call_count, 2)

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
