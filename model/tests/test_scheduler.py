import unittest

from azilla_cycle_model.scheduler import (
    CROSS, H0, H1, ConcurrentDispatcher, DispatchPort, WorkTarget,
    allocate_h1_pairs, compile_cores_only_schedule,
    compile_hybrid_schedule, compile_static_hybrid_schedule, compile_schedule,
)
from azilla_cycle_model.workload import Geometry, ScheduledBlock


class SchedulerTests(unittest.TestCase):
    def test_pair_allocator_is_balanced_and_deterministic(self):
        geometry = Geometry(2, 2, 1, 1)
        owners = allocate_h1_pairs(geometry)
        loads = [0] * geometry.node_count
        for a in range(geometry.node_count):
            for b in range(a + 1, geometry.node_count):
                loads[owners[(a, b)]] += 1
                self.assertEqual(owners[(a, b)], owners[(b, a)])
        self.assertEqual(loads, [2, 2, 1, 1])

    def test_hierarchy_classification_and_publications(self):
        geometry = Geometry(2, 1, 2, 2)
        schedule = compile_schedule(geometry, [
            ScheduledBlock(0, 1),
            ScheduledBlock(0, 2),
            ScheduledBlock(0, 4, 1),
        ])
        self.assertEqual(schedule.counts, (1, 1, 1))
        self.assertEqual(schedule.publications(), ((0, 1), (4, 1)))

    def test_cores_only_duplicates_work_at_destination_and_deduplicates_state(self):
        geometry = Geometry(2, 1, 2, 2)
        schedule = compile_cores_only_schedule(geometry, [
            ScheduledBlock(0, 1),
            ScheduledBlock(0, 2),
            ScheduledBlock(0, 4),
            ScheduledBlock(1, 4),
        ])
        self.assertEqual(schedule.directed_jobs, 8)
        self.assertEqual(
            [(work.block_a, work.block_b) for work in schedule.h0[0]],
            [(0, 1), (1, 0), (0, 2), (0, 4), (1, 4)],
        )
        # Block 4 is cached once at destination H1 0 despite two consumers.
        self.assertEqual(schedule.state_publications, ((0, 1), (1, 1), (4, 0)))

    def test_static_hybrid_exclusive_partition_and_core_preference(self):
        geometry = Geometry(2, 1, 2, 2)
        pairs = [(0, b) for b in range(1, 8)]
        schedule = compile_static_hybrid_schedule(
            geometry, pairs, h0_mvm_count=1, h1_mvm_count=1,
            cross_mvm_count=1,
        )
        self.assertEqual(
            set(schedule.core_pairs) | set(schedule.cir_pairs), set(pairs)
        )
        self.assertFalse(set(schedule.core_pairs) & set(schedule.cir_pairs))
        self.assertGreater(len(schedule.core_pairs), 0)
        self.assertGreater(len(schedule.cir_pairs), 0)
        self.assertEqual(schedule.directed_core_jobs, 2 * len(schedule.core_pairs))

    def test_explicit_hybrid_partition_is_exclusive_and_preserves_owner(self):
        geometry = Geometry(2, 1, 1, 2)
        records = [ScheduledBlock(0, 1), ScheduledBlock(0, 2, 1)]
        schedule = compile_hybrid_schedule(
            geometry, records, core_pairs=[(0, 1)],
        )
        self.assertEqual(schedule.core_pairs, ((0, 1),))
        self.assertEqual(schedule.cir_pairs, ((0, 2),))
        self.assertEqual(schedule.directed_core_jobs, 2)
        self.assertEqual(schedule.cir.counts, (0, 0, 1))
        self.assertEqual(schedule.cir.cross[1][0].block_b, 2)

    def test_explicit_hybrid_partition_rejects_unknown_pair(self):
        with self.assertRaises(ValueError):
            compile_hybrid_schedule(
                Geometry(1, 1, 1, 2), [(0, 1)], core_pairs=[(0, 2)],
            )

    def test_concurrent_dispatch_holds_valid_and_refills(self):
        geometry = Geometry(1, 1, 1, 4)
        schedule = compile_schedule(geometry, [(0, 1), (0, 2), (0, 3)])
        dispatcher = ConcurrentDispatcher(
            schedule, h0_mvm_count=2, h1_mvm_count=1, cross_mvm_count=1
        )
        p0 = DispatchPort(WorkTarget(H0, 0, 0), 0)
        p1 = DispatchPort(WorkTarget(H0, 0, 0), 1)
        driven = dispatcher.drive({p0: True, p1: False})
        self.assertEqual(set(driven), {p0})
        first = driven[p0]
        driven = dispatcher.drive({p0: False, p1: False})
        self.assertNotIn(p0, driven)  # accepted at the preceding edge
        self.assertNotIn(p1, driven)
        driven = dispatcher.drive({p0: False, p1: True})
        self.assertEqual(driven[p1].block_a, 0)
        self.assertNotEqual(first.block_b, driven[p1].block_b)

    def test_concurrent_dispatch_does_not_same_step_refill_accepted_port(self):
        """Mirror the SV cmd_accepted exclusion on the retirement negedge."""

        geometry = Geometry(1, 1, 1, 4)
        schedule = compile_schedule(geometry, [(0, 1), (0, 2)])
        dispatcher = ConcurrentDispatcher(
            schedule, h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=1
        )
        port = DispatchPort(WorkTarget(H0, 0, 0), 0)

        first = dispatcher.drive({port: True})
        self.assertEqual(first[port].block_b, 1)

        # The first command was accepted on the preceding edge.  Even though
        # ready remains high, RTL leaves valid low for this dispatch step.
        self.assertEqual(dispatcher.drive({port: True}), {})

        second = dispatcher.drive({port: True})
        self.assertEqual(second[port].block_b, 2)


if __name__ == "__main__":
    unittest.main()
