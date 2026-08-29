import unittest

from azilla_cycle_model.workload import (
    BlockOccupancyDataset,
    Geometry,
    IsingDataset,
    ScheduledBlock,
    validate_schedule,
)


class WorkloadTests(unittest.TestCase):
    def test_schedule_coverage(self):
        dataset = IsingDataset(256, 0, {(0, 40): 1, (40, 0): 1})
        geometry = Geometry(1, 1, 1, 8)
        validate_schedule(dataset, geometry, [ScheduledBlock(0, 1, -1)])
        with self.assertRaisesRegex(ValueError, "missing=1"):
            validate_schedule(dataset, geometry, [])

    def test_occupancy_dataset_omits_edge_payloads(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        dataset = BlockOccupancyDataset.load(
            root / "tb/datasets/g256_smoke.txt"
        )
        self.assertEqual(dataset.spin_count, 256)
        self.assertEqual(len(dataset.active_block_pairs()), 8)


if __name__ == "__main__":
    unittest.main()
