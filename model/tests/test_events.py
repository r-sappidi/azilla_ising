import unittest

from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedMesh, EventCompressedPerformanceModel, EventLoop,
    PacketRelease,
)
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock


class EventCompressionTests(unittest.TestCase):
    def test_event_loop_counts_skipped_time(self):
        loop = EventLoop()
        observed = []
        loop.schedule(10, observed.append)
        loop.schedule(100, observed.append)
        loop.run()
        self.assertEqual(observed, [10, 100])
        self.assertEqual(loop.now, 100)
        self.assertEqual(loop.skipped_cycles, 100)

    def test_empty_network_gap_is_skipped_but_counted(self):
        result = EventCompressedMesh(2, 1).replay([
            PacketRelease(10, 0, 1, 0),
            PacketRelease(1000, 1, 0, 0),
        ])
        self.assertEqual(result.injected_flits, 2)
        self.assertEqual(result.ejected_flits, 2)
        self.assertEqual(result.physical_link_flits, 2)
        self.assertGreaterEqual(result.skipped_cycles, 980)
        self.assertEqual(result.end_cycle - result.start_cycle,
                         result.elapsed_cycles)

    def test_calibrated_smoke_timing_and_traffic(self):
        root = Path(__file__).resolve().parents[2]
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2),
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
        ).run()
        self.assertEqual(result.accuracy, "rtl-differential")
        self.assertEqual(result.initialization_cycles, 267)
        self.assertEqual(result.iteration_cycles, 179)
        self.assertEqual(result.total_cycles, 446)
        self.assertEqual(result.injected_flits, 22)
        self.assertEqual(result.ejected_flits, 22)
        self.assertEqual(result.physical_link_flits, 10)

    def test_changed_smoke_mapping_is_not_claimed_as_differential(self):
        root = Path(__file__).resolve().parents[2]
        dataset = IsingDataset.load(root / "tb/datasets/g256_smoke.txt")
        schedule = [
            ScheduledBlock(a, b, 1 if (a, b) == (3, 4) else -1)
            for a, b in sorted(dataset.active_block_pairs())
        ]
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2), dataset
        ).run(schedule=schedule)
        self.assertEqual(result.accuracy, "calibrated-extrapolation")


if __name__ == "__main__":
    unittest.main()
