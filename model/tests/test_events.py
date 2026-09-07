import unittest

from pathlib import Path

from azilla_cycle_model.events import (
    EventCompressedMesh, EventCompressedPerformanceModel,
    EventPerformanceConfig, EventLoop, PacketRelease,
)
from azilla_cycle_model.noc import InterconnectConfig
from azilla_cycle_model.workload import Geometry, IsingDataset, ScheduledBlock


class EventCompressionTests(unittest.TestCase):
    def test_multi_engine_latency_and_issue_interval_are_distinct(self):
        queues = [[object() for _ in range(5)]]
        self.assertEqual(
            EventCompressedPerformanceModel._resource_completions(
                queues, engines=4, latency=67, issue_interval=40,
            ),
            [67, 67, 67, 67, 107],
        )

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

    def test_idle_skip_advances_credit_return_timers(self):
        result = EventCompressedMesh(
            2, 1,
            interconnect=InterconnectConfig(
                max_inflight_flits=1,
                credit_return_latency_cycles=50,
            ),
        ).replay([
            PacketRelease(0, 0, 1, 0),
            PacketRelease(100, 0, 1, 0),
        ])
        self.assertEqual(result.injected_flits, 2)
        self.assertEqual(result.ejected_flits, 2)
        self.assertGreaterEqual(result.skipped_cycles, 90)

    def test_resource_observer_sees_offers_and_acceptance(self):
        offers = []
        result = EventCompressedMesh(2, 1, fifo_depth=2).replay(
            [
                PacketRelease(0, 0, 1, 0),
                PacketRelease(0, 0, 1, 0),
            ],
            resource_observer=lambda *record: offers.append(record),
        )
        by_scope = {
            scope: [row for row in offers if row[1] == scope]
            for scope in ("inject", "eject", "link")
        }
        self.assertEqual(
            sum(row[-1] for row in by_scope["inject"]),
            result.injected_flits,
        )
        self.assertEqual(
            sum(not row[-1] for row in by_scope["inject"]),
            result.injection_stalls,
        )
        self.assertEqual(
            sum(row[-1] for row in by_scope["link"]),
            result.physical_link_flits,
        )
        self.assertEqual(len(by_scope["eject"]), result.ejected_flits)

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
        self.assertEqual(
            sum(row.accepted_flits for row in result.noc_resources
                if row.scope == "inject"),
            result.injected_flits,
        )
        self.assertEqual(
            sum(row.accepted_flits for row in result.noc_resources
                if row.scope == "link"),
            result.physical_link_flits,
        )
        self.assertEqual(
            sum(count for _, count in result.hop_histogram),
            result.injected_flits,
        )
        self.assertEqual(len(result.nodes), 2)

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

    def test_cores_only_uses_destination_compute_and_has_no_partial_packets(self):
        root = Path(__file__).resolve().parents[2]
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2),
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
            config=EventPerformanceConfig(execution_mode="cores-only"),
        ).run()
        self.assertEqual((result.scheduled_h0, result.scheduled_h1,
                          result.scheduled_cross), (16, 0, 0))
        self.assertEqual(
            sum(row.partial_flits for row in result.noc_resources), 0
        )
        self.assertTrue(all(row.engines == 2 for row in result.work_resources))
        self.assertEqual(result.accuracy, "calibrated-extrapolation")
        self.assertEqual(result.unordered_interaction_blocks, 8)
        self.assertEqual(result.directed_core_jobs, 16)
        self.assertEqual(result.weight_block_reads, 16)
        self.assertEqual(result.logical_weight_blocks_stored, 16)
        self.assertEqual(result.remote_state_packets, 4)

    def test_static_hybrid_preserves_exclusive_work_accounting(self):
        root = Path(__file__).resolve().parents[2]
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2),
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
            config=EventPerformanceConfig(execution_mode="hybrid"),
        ).run()
        cir_jobs = result.weight_block_reads - result.directed_core_jobs
        self.assertEqual(
            result.directed_core_jobs // 2 + cir_jobs,
            result.unordered_interaction_blocks,
        )
        self.assertEqual(result.execution_mode, "hybrid")
        self.assertEqual(result.accuracy, "calibrated-extrapolation")

    def test_explicit_hybrid_partition_controls_work_accounting(self):
        root = Path(__file__).resolve().parents[2]
        dataset = IsingDataset.load(root / "tb/datasets/g256_smoke.txt")
        pairs = sorted(dataset.active_block_pairs())
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2), dataset,
            config=EventPerformanceConfig(execution_mode="hybrid"),
        ).run(hybrid_core_pairs=pairs[:3])
        self.assertEqual(result.directed_core_jobs, 6)
        self.assertEqual(result.weight_block_reads, 6 + len(pairs) - 3)
        self.assertEqual(
            result.directed_core_jobs // 2
            + result.weight_block_reads - result.directed_core_jobs,
            len(pairs),
        )

    def test_explicit_hybrid_partition_requires_hybrid_mode(self):
        root = Path(__file__).resolve().parents[2]
        model = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2),
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
        )
        with self.assertRaises(ValueError):
            model.run(hybrid_core_pairs=[(0, 1)])

    def test_exact_event_explicit_hybrid_partition(self):
        from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
        # The exact path's explicit-partition API is exercised by its live
        # Ramulator integration checks; keep the public signature guarded here.
        self.assertIn(
            "hybrid_core_pairs",
            __import__("inspect").signature(
                RamulatorEventPerformanceModel.run
            ).parameters,
        )

    def test_physical_link_parameters_are_labeled_as_projection(self):
        root = Path(__file__).resolve().parents[2]
        result = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2),
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
            config=EventPerformanceConfig(
                interconnect=InterconnectConfig(forward_latency_cycles=2)
            ),
        ).run()
        self.assertEqual(
            result.accuracy, "parameterized-interconnect-projection"
        )

    def test_chiplet_latency_is_charged_and_labeled_as_projection(self):
        root = Path(__file__).resolve().parents[2]
        dataset = IsingDataset.load(root / "tb/datasets/g256_smoke.txt")
        baseline = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2), dataset
        ).run()
        projected = EventCompressedPerformanceModel(
            Geometry(2, 1, 2, 2), dataset,
            config=EventPerformanceConfig(
                chiplet_link_latency_cycles=3
            ),
        ).run()
        self.assertEqual(
            projected.accuracy, "parameterized-interconnect-projection"
        )
        self.assertEqual(projected.chiplet_link_latency_cycles, 3)
        self.assertEqual(
            projected.iteration_cycles,
            baseline.iteration_cycles + 2 * 3,
        )

    def test_chiplet_latency_must_be_non_negative(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            EventPerformanceConfig(chiplet_link_latency_cycles=-1)


if __name__ == "__main__":
    unittest.main()
