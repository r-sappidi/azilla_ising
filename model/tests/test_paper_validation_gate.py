"""Publication gates reject absent, stale, or merely compositional evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_paper_validation_gate.py"
spec = importlib.util.spec_from_file_location("paper_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def test_missing_certificate_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(gate.validate_certificate(root / "absent", "cir", root / "config"))

    def test_source_and_evidence_mutations_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = ("model/azilla_cycle_model/a.py", "rtl/a.sv",
                     "tb/ramulator_dpi.cpp",
                     "build/cycle_model_ramulator/libazilla_ramulator.so")
            for name in (*files, "config.yaml", "evidence.log"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            cert = {
                "status": "pass", "simulator": "vcs", "execution_mode": "cir",
                "checks": dict.fromkeys(gate.REQUIRED_CHECKS, True),
                "source_sha256": {n: gate.digest(root / n) for n in files},
                "evidence_sha256": {"evidence.log": gate.digest(root / "evidence.log")},
                "ramulator_config_sha256": gate.digest(root / "config.yaml"),
            }
            path = root / "cert.json"
            path.write_text(json.dumps(cert))
            with patch.object(gate, "ROOT", root):
                self.assertEqual(gate.validate_certificate(path, "cir", root / "config.yaml"), [])
                for name in (files[0], "evidence.log", "config.yaml"):
                    (root / name).write_text("changed")
                    self.assertTrue(gate.validate_certificate(path, "cir", root / "config.yaml"))
                    (root / name).write_text("original")
                cert["checks"]["backpressure"] = False
                path.write_text(json.dumps(cert))
                self.assertTrue(gate.validate_certificate(path, "cir", root / "config.yaml"))


if __name__ == "__main__":
    unittest.main()
