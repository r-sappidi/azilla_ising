import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from assemble_fast_exact_gate import Pending,read,full_report
from supervise_fast_exact_handoff import cover,same,preserved_child


class SupervisorTests(unittest.TestCase):
    def test_missing_report_pending(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Pending):read(Path(d)/'missing.json')

    def test_partial_json_pending(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text('{')
            with self.assertRaises(Pending):read(p)

    def test_failed_report_not_pending(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text(json.dumps({'status':'failed','cases':[]}))
            with self.assertRaises(RuntimeError):full_report(Path(d),[],{},{},set())

    def test_full_case_count_required(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text(json.dumps({'status':'passed','cases':[]}))
            with self.assertRaises(RuntimeError):full_report(Path(d),['missing'],{},{},set())

    def test_uncovered_unstarted_rejected(self):
        point=dict(status='unstarted',mode='cir',engines=[4,2,4],geometry={},ramulator_config_sha256='x')
        with self.assertRaises(RuntimeError):cover({'campaigns':[{'points':[point]}]},{'covered_signatures':[]})

    def test_completed_outside_envelope_permitted(self):
        cover({'campaigns':[{'points':[{'status':'audited'}]}]},{'covered_signatures':[]})

    def test_partial_not_auto_retried(self):
        with self.assertRaises(RuntimeError):cover({'campaigns':[{'points':[{'status':'ambiguous-started'}]}]},{'covered_signatures':[]})

    def test_pid_reuse_rejected(self):
        with patch('supervise_fast_exact_handoff.proc',return_value=dict(start_ticks='new',command=['python'])):
            with self.assertRaises(RuntimeError):same(dict(pid=1,start_ticks='old',command=['python']))

    def test_stopped_child_rejected(self):
        entry=dict(pid=1,start_ticks='old',command=['python'])
        with patch('supervise_fast_exact_handoff.proc',return_value=dict(entry,state='T')):
            with self.assertRaises(RuntimeError):preserved_child(entry)

    def test_running_child_preserved(self):
        entry=dict(pid=1,start_ticks='old',command=['python'])
        with patch('supervise_fast_exact_handoff.proc',return_value=dict(entry,state='R')):
            self.assertEqual(preserved_child(entry)['state'],'R')


if __name__=='__main__':unittest.main()
