"""Integrated scheduling invariants; these are not RTL differential evidence."""

from collections import Counter, deque
from pathlib import Path
import unittest
from unittest.mock import patch

from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.memory import MemoryResponse
from azilla_cycle_model.noc import InterconnectConfig
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.ramulator import RamulatorSystemStats
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock


class ImmediateMemory:
    """Unlimited fast memory exposes transport/core buffering bottlenecks."""

    def __init__(self, *args, **kwargs):
        self.now = 0
        self.queues = {}
        self.accepted = Counter()
        self.completed = Counter()
        self.offers = []

    def send(self, system, address, tag):
        self.offers.append((self.now, system, address))
        self.queues.setdefault(system, deque()).append(MemoryResponse(0, tag))
        self.accepted[system] += 1
        return True

    def tick(self, count):
        self.now += count

    def pop(self, system):
        queue = self.queues.get(system)
        if not queue:
            return None
        self.completed[system] += 1
        return queue.popleft()

    def stats(self, system):
        return RamulatorSystemStats(self.accepted[system], 0,
                                    self.completed[system], 0, 0)


class CorePipelineTests(unittest.TestCase):
    def run_case(self, records, *, interval=1):
        geometry = Geometry(2, 1, 2, 4)
        with patch("azilla_cycle_model.exact_events.RamulatorBackend", ImmediateMemory):
            model = RamulatorEventPerformanceModel(
                geometry, IsingDataset(geometry.spin_count, 0, {}),
                dataset_path="unused", ramulator_library="unused",
                ramulator_config=Path(__file__), ticks_per_cycle=1,
                config=PerformanceConfig(
                    timing_only=True, execution_mode="cores-only",
                    max_cycles=100000,
                    interconnect=InterconnectConfig(flit_interval_cycles=interval),
                ),
            )
        result = model._run_cores_only(records, 0)
        return model, result

    def test_all_levels_have_bounded_sources_and_conserve_work(self):
        model, result = self.run_case([
            ScheduledBlock(0, 1), ScheduledBlock(0, 4),
            ScheduledBlock(0, 8, 0), ScheduledBlock(1, 9, 0),
            ScheduledBlock(2, 10, 0), ScheduledBlock(3, 11, 0),
        ])
        self.assertEqual(result.weight_block_reads, 12)
        self.assertEqual(sum(row.accepted_requests for row in result.dram), 384)
        self.assertEqual(result.counters.type_flits[3], 256)
        self.assertEqual(sum(model.core_pipeline_audit["local_delivery_beats"].values()), 128)
        for system, peak in model.core_pipeline_audit["peak_source_blocks"].items():
            self.assertLessEqual(peak, model.core_pipeline_audit["source_capacity_blocks"][system])
        self.assertTrue(all(value <= 64 for value in
                            model.core_pipeline_audit["peak_outstanding_requests"].values()))

    def test_network_backpressure_delays_later_memory_requests(self):
        records = [ScheduledBlock(a, a + 8, 0) for a in range(4)]
        fast, fast_result = self.run_case(records)
        slow, slow_result = self.run_case(records, interval=8)
        # The old separate DRAM/replay phases had identical request times
        # regardless of network serialization. This must no longer be true.
        self.assertGreater(max(t for t, _, _ in slow.backend.offers),
                           max(t for t, _, _ in fast.backend.offers))
        self.assertGreater(slow_result.iteration_cycles, fast_result.iteration_cycles)
        self.assertEqual(slow_result.weight_block_reads, fast_result.weight_block_reads)
        self.assertEqual(slow_result.counters.type_flits[3], fast_result.counters.type_flits[3])


if __name__ == "__main__":
    unittest.main()
