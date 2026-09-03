import unittest

from azilla_cycle_model.dynamic_scheduler import (
    QUEUE_AWARE, READY_ONLY, TRAFFIC_AWARE,
    select_batch_balanced_core_pairs, select_dynamic_hybrid_core_pairs,
)
from azilla_cycle_model.workload import Geometry, ScheduledBlock


class DynamicSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.geometry = Geometry(2, 1, 2, 2)
        self.pairs = [(0, block) for block in range(1, 8)]

    def select(self, policy, records=None):
        return select_dynamic_hybrid_core_pairs(
            self.geometry, records or self.pairs, policy=policy,
            h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=1,
        )

    def test_policies_produce_exclusive_complete_partitions(self):
        for policy in (READY_ONLY, QUEUE_AWARE, TRAFFIC_AWARE):
            result = self.select(policy)
            self.assertEqual(
                set(result.core_pairs) | set(result.cir_pairs), set(self.pairs)
            )
            self.assertFalse(set(result.core_pairs) & set(result.cir_pairs))
            self.assertEqual(
                result.core_decisions + result.cir_decisions, len(self.pairs)
            )

    def test_ready_policy_atomically_spills_busy_core_pair(self):
        result = self.select(READY_ONLY)
        self.assertIn((0, 1), result.core_pairs)
        self.assertTrue(result.cir_pairs)
        self.assertTrue(result.core_pairs)

    def test_queue_policy_is_deterministic(self):
        first = self.select(QUEUE_AWARE)
        second = self.select(QUEUE_AWARE)
        self.assertEqual(first, second)

    def test_cross_owner_affects_traffic_estimate(self):
        geometry = Geometry(2, 2, 1, 2)
        arguments = dict(
            policy=TRAFFIC_AWARE, h0_mvm_count=1,
            h1_mvm_count=1, cross_mvm_count=1,
            core_block_cycles=100, cross_block_cycles=1,
        )
        near = select_dynamic_hybrid_core_pairs(
            geometry, [ScheduledBlock(0, 2, 0)], **arguments,
        )
        far = select_dynamic_hybrid_core_pairs(
            geometry, [ScheduledBlock(0, 2, 2)], **arguments,
        )
        self.assertNotEqual(
            near.estimated_state_flit_hops + near.estimated_partial_flit_hops,
            far.estimated_state_flit_hops + far.estimated_partial_flit_hops,
        )

    def test_invalid_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            self.select("not-a-policy")

    def test_batch_balancer_is_complete_deterministic_and_mixed(self):
        arguments = dict(
            h0_mvm_count=1, h1_mvm_count=1, cross_mvm_count=1,
        )
        first = select_batch_balanced_core_pairs(
            self.geometry, self.pairs, **arguments,
        )
        second = select_batch_balanced_core_pairs(
            self.geometry, self.pairs, **arguments,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            set(first.core_pairs) | set(first.cir_pairs), set(self.pairs)
        )
        self.assertFalse(set(first.core_pairs) & set(first.cir_pairs))
        self.assertTrue(first.core_pairs)
        self.assertTrue(first.cir_pairs)

    def test_batch_balancer_rejects_invalid_cost(self):
        with self.assertRaises(ValueError):
            select_batch_balanced_core_pairs(
                self.geometry, self.pairs, h0_mvm_count=1,
                h1_mvm_count=1, cross_mvm_count=1, cir_cost_scale=0,
            )


if __name__ == "__main__":
    unittest.main()
