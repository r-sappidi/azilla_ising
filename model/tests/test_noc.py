import unittest

from azilla_cycle_model.noc import (
    EAST, LOCAL, WEST, Flit, FlooRouter, InterconnectConfig, Mesh,
)


class RouterTests(unittest.TestCase):
    def test_sparse_request_priority_matches_floo_binary_tree(self):
        # Independently checked against cc_rr_arb_tree in VCS. A rotating
        # linear encoder disagrees when the priority requester disappears.
        self.assertEqual(FlooRouter._select_rr([0, 2], 1), 0)
        self.assertEqual(FlooRouter._select_rr([2, 3], 1), 3)
        self.assertEqual(FlooRouter._select_rr([1, 4], 2), 1)

    def test_input_fifo_adds_one_cycle(self):
        router = FlooRouter(0, 0, fifo_depth=4)
        flit = Flit(dest_x=0, dest_y=0)
        before = router.tick([flit, None, None, None, None], [True] * 5)
        self.assertIsNone(before.output_flits[LOCAL])
        self.assertEqual(router.outputs().output_flits[LOCAL], flit)

    def test_packet_lock(self):
        router = FlooRouter(0, 0, fifo_depth=4)
        first = Flit(data=1, dest_x=1, dest_y=0, last=False)
        tail = Flit(data=2, dest_x=1, dest_y=0, last=True)
        competitor = Flit(data=3, dest_x=1, dest_y=0, last=True)
        router.tick([competitor, None, None, None, first], [True] * 5)
        observed = router.tick([None, None, None, None, tail], [True] * 5)
        self.assertEqual(observed.output_flits[EAST].data, 1)
        observed = router.tick([None] * 5, [True] * 5)
        self.assertEqual(observed.output_flits[EAST].data, 2)

    def test_late_request_is_excluded_from_packet_fairness_snapshot(self):
        """Match floo_wormhole_arbiter's registered request vector."""

        router = FlooRouter(2, 3, fifo_depth=4)

        def packet(block_id):
            return [
                Flit(
                    dest_x=1, dest_y=2, source_id=15,
                    block_id=block_id, last=beat == 3,
                )
                for beat in range(4)
            ]

        router.fifos[EAST].queue.extend(
            packet(316) + packet(317) + packet(318)
        )
        ready = [True] * 5
        empty = [None] * 5

        for _ in range(4):
            router.tick(empty, ready)

        # Packet 317 starts with only EAST requesting WEST. The LOCAL packet
        # enters during those four beats and must not affect packet 317's
        # final-beat priority update.
        for beat in range(4):
            inputs = [None] * 5
            inputs[LOCAL] = Flit(
                dest_x=0, dest_y=3, source_id=14,
                block_id=384, last=beat == 3,
            )
            router.tick(inputs, ready)

        selected = router.outputs().output_flits[WEST]
        self.assertIsNotNone(selected)
        self.assertEqual(selected.source_id, 15)
        self.assertEqual(selected.block_id, 318)

    def test_two_hop_mesh(self):
        mesh = Mesh(3, 1, fifo_depth=4)
        flit = Flit(data=99, dest_x=2, dest_y=0)
        delivered_cycle = None
        held = flit
        for cycle in range(10):
            ready, ejected = mesh.tick({0: held} if held else {})
            if held and ready[0]:
                held = None
            if 2 in ejected:
                delivered_cycle = cycle
                self.assertEqual(ejected[2].data, 99)
                break
        self.assertEqual(delivered_cycle, 3)

    def test_forward_latency_is_paid_per_hop(self):
        mesh = Mesh(
            3, 1, fifo_depth=4,
            interconnect=InterconnectConfig(
                forward_latency_cycles=3,
                max_inflight_flits=8,
            ),
        )
        flit = Flit(data=99, dest_x=2, dest_y=0)
        held = flit
        delivered_cycle = None
        for cycle in range(20):
            ready, ejected = mesh.tick({0: held} if held else {})
            if held and ready[0]:
                held = None
            if 2 in ejected:
                delivered_cycle = cycle
                break
        # The direct RTL mesh delivers at cycle 3. Three additional cycles
        # on each of two physical links move delivery to cycle 9.
        self.assertEqual(delivered_cycle, 9)

    def test_flit_interval_limits_sustained_bandwidth(self):
        mesh = Mesh(
            2, 1, fifo_depth=4,
            interconnect=InterconnectConfig(
                flit_interval_cycles=3,
                max_inflight_flits=8,
            ),
        )
        pending = [Flit(data=index, dest_x=1, dest_y=0) for index in range(4)]
        delivered = []
        for cycle in range(20):
            ready, ejected = mesh.tick({0: pending[0]} if pending else {})
            if pending and ready[0]:
                pending.pop(0)
            if 1 in ejected:
                delivered.append((cycle, ejected[1].data))
            if len(delivered) == 4:
                break
        self.assertEqual(delivered, [(2, 0), (5, 1), (8, 2), (11, 3)])

    def test_credit_window_and_return_delay_throttle_link(self):
        mesh = Mesh(
            2, 1, fifo_depth=4,
            interconnect=InterconnectConfig(
                forward_latency_cycles=2,
                max_inflight_flits=1,
                credit_return_latency_cycles=2,
            ),
        )
        pending = [Flit(data=index, dest_x=1, dest_y=0) for index in range(4)]
        delivered = []
        for cycle in range(30):
            ready, ejected = mesh.tick({0: pending[0]} if pending else {})
            if pending and ready[0]:
                pending.pop(0)
            if 1 in ejected:
                delivered.append((cycle, ejected[1].data))
            if len(delivered) == 4:
                break
        self.assertEqual(delivered, [(4, 0), (9, 1), (14, 2), (19, 3)])

    def test_receive_backpressure_preserves_order_and_data(self):
        mesh = Mesh(
            2, 1, fifo_depth=2,
            interconnect=InterconnectConfig(
                forward_latency_cycles=2,
                max_inflight_flits=2,
            ),
        )
        pending = [Flit(data=index, dest_x=1, dest_y=0) for index in range(4)]
        delivered = []
        for cycle in range(30):
            ready, ejected = mesh.tick(
                {0: pending[0]} if pending else {},
                {1: cycle >= 10},
            )
            if pending and ready[0]:
                pending.pop(0)
            if 1 in ejected and cycle >= 10:
                delivered.append(ejected[1].data)
            if len(delivered) == 4:
                break
        self.assertEqual(delivered, [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
