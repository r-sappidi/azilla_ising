#!/usr/bin/env python3
import copy
import unittest
import yaml
from run_memory_width_sweep import ROOT, configurations


class WidthConfigsTest(unittest.TestCase):
    def setUp(self):
        self.base = yaml.safe_load((ROOT / "tb/ramulator_128x32.yaml").read_text())

    def test_only_controller_count_changes(self):
        before = copy.deepcopy(self.base)
        for width, config in configurations(self.base).items():
            controllers = config["memory_system"].pop("controllers")
            reference = copy.deepcopy(self.base)
            expected = reference["memory_system"].pop("controllers")
            self.assertEqual(config, reference)
            self.assertEqual(len(controllers), width // 16)
            self.assertTrue(all(c == expected[0] for c in controllers))
        self.assertEqual(self.base, before)

    def test_reject_heterogeneous_reference(self):
        self.base["memory_system"]["controllers"][1]["read_buffer_size"] = 1
        with self.assertRaises(ValueError):
            configurations(self.base)


if __name__ == "__main__":
    unittest.main()
