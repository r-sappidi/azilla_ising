import unittest
from azilla_cycle_model.core_state_bank import CoreStateBank


class CoreStateBankTests(unittest.TestCase):
    def test_fixed_lane_priority_and_registered_response(self):
        bank = CoreStateBank(32, lanes=4)
        for i in (0, 8, 1):
            bank.tick({}, write=(i, i+100))
        requests = {2: 8, 0: 0, 1: 1}
        self.assertEqual(bank.ready(requests), {0: True, 1: True, 2: False})
        self.assertEqual(bank.tick(requests), {})
        old = bank.tick({2: 8})
        self.assertEqual({k:v.data for k,v in old.items()}, {0:100, 1:101})
        self.assertEqual(bank.responses[2].data, 108)
        self.assertEqual((bank.accepted_reads, bank.stalled_reads), (3, 1))

    def test_read_before_write_and_missing_publication(self):
        bank = CoreStateBank(8, lanes=1)
        with self.assertRaises(RuntimeError):
            bank.tick({0: 2})
        bank.tick({}, write=(2, 7))
        bank.tick({0: 2}, write=(2, 9))
        self.assertEqual(bank.responses[0].data, 7)
        bank.tick({0: 2})
        self.assertEqual(bank.responses[0].data, 9)


if __name__ == '__main__':
    unittest.main()
