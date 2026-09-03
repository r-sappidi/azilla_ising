import csv
import json
import tempfile
import unittest
from pathlib import Path

from azilla_cycle_model.exact_events import (
    DramPerformanceStats, ExactEventResult, NocResourceStats,
    NodePerformanceStats, RamulatorEventPerformanceModel,
)
from azilla_cycle_model.events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
)
from azilla_cycle_model.metrics import (
    write_event_performance_metrics, write_exact_event_metrics,
)
from azilla_cycle_model.performance import PerformanceCounters
from azilla_cycle_model.ramulator import RamulatorSystemStats
from azilla_cycle_model.workload import Geometry, IsingDataset


class MetricsTests(unittest.TestCase):
    def test_ramulator_derived_counters(self):
        stats = RamulatorSystemStats(10, 3, 8, 400, 90)
        self.assertEqual(stats.outstanding, 2)
        self.assertEqual(stats.average_latency_ticks, 50.0)

    def test_cores_only_dram_conservation(self):
        drained = RamulatorSystemStats(64, 7, 64, 640, 20)
        RamulatorEventPerformanceModel._validate_cores_only_dram(
            3, 64, drained
        )
        with self.assertRaisesRegex(RuntimeError, "conservation failed"):
            RamulatorEventPerformanceModel._validate_cores_only_dram(
                3, 64, RamulatorSystemStats(64, 0, 63, 630, 20)
            )

    def test_cores_only_prefetch_preserves_destination_order(self):
        head = {"received": 32, "compute": 2}
        prefetched_tail = {"received": 32, "compute": -1}
        slots = [head, prefetched_tail]
        self.assertFalse(
            RamulatorEventPerformanceModel._advance_cores_only_core(slots)
        )
        self.assertEqual(prefetched_tail["compute"], -1)
        self.assertTrue(
            RamulatorEventPerformanceModel._advance_cores_only_core(slots)
        )
        slots.pop(0)
        self.assertFalse(
            RamulatorEventPerformanceModel._advance_cores_only_core(slots)
        )
        self.assertEqual(prefetched_tail["compute"], 31)

    def test_exact_metrics_export(self):
        resource = NocResourceStats(
            "inject", 0, 0, 0, "local", 3, 2, 1, 1, 1, 1, 0, 0
        )
        node = NodePerformanceStats(
            0, 0, 0, 1, 2, 3, 6, 1, 1, 20, 21, 24
        )
        dram = DramPerformanceStats(
            0, "h0", 0, 0, 1, 1, 32, 4, 32, 0,
            80.0, 120, 2.0, 3.0,
        )
        result = ExactEventResult(
            accuracy="cycle-structured-unverified",
            initialization_cycles=10,
            iteration_cycles=20,
            total_cycles=30,
            scheduled_h0=1,
            scheduled_h1=2,
            scheduled_cross=3,
            counters=PerformanceCounters(
                cycles=20,
                injected_flits=2,
                ejected_flits=2,
                physical_link_flits=1,
                injection_stalls=1,
                type_flits=[1, 1, 0, 0],
            ),
            average_hops=0.5,
            hop_histogram=((0, 1), (1, 1)),
            noc_resources=(resource,),
            nodes=(node,),
            dram=(dram,),
            execution_mode="cir",
            unordered_interaction_blocks=6,
            weight_block_reads=6,
            logical_weight_blocks_stored=6,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_exact_event_metrics(
                Path(directory) / "run", result, Geometry(1, 1, 1, 1)
            )
            summary = json.loads(paths["summary"].read_text())
            self.assertEqual(summary["timing_cycles"]["iteration"], 20)
            self.assertEqual(
                summary["interconnect"]["forward_latency_cycles"], 0
            )
            self.assertEqual(
                summary["package"]["chiplet_link_latency_cycles"], 0
            )
            self.assertEqual(summary["noc"]["hop_histogram"], {"0": 1, "1": 1})
            self.assertEqual(
                summary["cores_only_ablation"]["unordered_interaction_blocks"],
                6,
            )
            self.assertEqual(
                summary["cores_only_ablation"]["weight_block_reads"], 6
            )
            self.assertEqual(
                summary["cores_only_ablation"]["logical_weight_blocks_stored"],
                6,
            )
            self.assertEqual(
                summary["effective_mvm_engines"],
                {"per_h0": 1, "per_h1_local": 0,
                 "per_h1_cross": 0, "system_total": 1},
            )
            with paths["noc"].open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["accepted_flits"], "2")
            self.assertEqual(rows[0]["backpressure"], str(1 / 3))

    def test_calibrated_event_metrics_export_is_distinct(self):
        geometry = Geometry(2, 1, 2, 2)
        root = Path(__file__).resolve().parents[2]
        config = EventPerformanceConfig()
        result = EventCompressedPerformanceModel(
            geometry,
            IsingDataset.load(root / "tb/datasets/g256_smoke.txt"),
            config,
        ).run()
        with tempfile.TemporaryDirectory() as directory:
            paths = write_event_performance_metrics(
                Path(directory) / "run", result, geometry, config
            )
            self.assertTrue(paths["summary"].name.endswith(
                "_event_summary.json"
            ))
            summary = json.loads(paths["summary"].read_text())
            self.assertEqual(summary["model"], "calibrated-event-compressed")
            self.assertEqual(
                summary["noc"]["injected_flits"], result.injected_flits
            )
            self.assertEqual(
                summary["estimated_dram_requests"],
                32 * (
                    result.scheduled_h0 + result.scheduled_h1
                    + result.scheduled_cross
                ),
            )


if __name__ == "__main__":
    unittest.main()
