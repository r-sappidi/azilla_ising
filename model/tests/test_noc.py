import unittest

from azilla_cycle_model.noc import EAST, LOCAL, WEST, Flit, FlooRouter, Mesh


class RouterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
