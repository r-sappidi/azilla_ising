"""Non-executing guard tests; no experiments or process signals are issued."""
import json
from pathlib import Path
import tempfile
import unittest
from run_fast_exact_continuation import digest, save, signature_key, verify_gate


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gate = {'status':'pass','evidence':[], 'covered_signatures':[{}]}
        for label in ('baseline','optimized'):
            snapshot = self.root / label
            module = snapshot / 'model/azilla_cycle_model/example.py'
            module.parent.mkdir(parents=True)
            module.write_text('# dummy test module only\n')
            self.gate[label+'_snapshot'] = str(snapshot)
            self.gate[label+'_files'] = {'model/azilla_cycle_model/example.py':digest(module)}
        evidence = self.root / 'dummy_evidence.json'
        evidence.write_text('{"test_fixture_only":true}')
        self.gate['evidence'] = [{'path':str(evidence),'sha256':digest(evidence)}]
        self.path = self.root / 'gate.json'

    def check_gate(self):
        save(self.path,self.gate)
        return verify_gate(self.path)

    def test_fixture_gate_passes(self):
        self.assertEqual(self.check_gate()['status'],'pass')

    def test_failed_gate_rejected(self):
        self.gate['status']='fail'
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_missing_evidence_rejected(self):
        self.gate['evidence']=[]
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_empty_envelope_rejected(self):
        self.gate['covered_signatures']=[]
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_source_drift_rejected(self):
        (Path(self.gate['optimized_snapshot'])/'model/azilla_cycle_model/example.py').write_text('# changed\n')
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_added_module_rejected(self):
        (Path(self.gate['optimized_snapshot'])/'model/azilla_cycle_model/unlisted.py').write_text('# added\n')
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_evidence_drift_rejected(self):
        Path(self.gate['evidence'][0]['path']).write_text('{}')
        with self.assertRaises(RuntimeError):self.check_gate()

    def test_signature_memory_identity(self):
        a=dict(mode='cir',geometry={},engines=[4,2,4],ramulator_config_sha256='abc',ramulator_config='/old')
        b=dict(a,ramulator_config='/new')
        self.assertEqual(signature_key(a),signature_key(b))
        self.assertNotEqual(signature_key(a),signature_key(dict(b,ramulator_config_sha256='def')))


if __name__=='__main__':unittest.main()
