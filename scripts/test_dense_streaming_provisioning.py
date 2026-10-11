#!/usr/bin/env python3
"""Queue admission checks; never launch a simulator or VCS."""
import json
from pathlib import Path
import tempfile
import unittest
import sys
import subprocess
from unittest.mock import patch
import queue_dense_streaming_provisioning as queue


class QueueTests(unittest.TestCase):
    def check_blocked(self, initially_running):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'results').mkdir()
            dep=root/'dependency.json'
            dep.write_text(json.dumps(dict(status='running' if initially_running else 'failed')))
            out=root/'results/output'
            def fail_dependency(_):
                dep.write_text(json.dumps(dict(status='failed')))
            argv=['queue','--output',str(out),'--dependency',str(dep)]
            with patch.object(queue,'ROOT',root), patch.object(queue.os,'nice'), \
                 patch.object(queue.time,'sleep',side_effect=fail_dependency) as sleeper, \
                 patch.object(queue,'run') as launch, patch.object(queue.sys,'argv',argv):
                with self.assertRaisesRegex(RuntimeError,'nominal validation did not pass'):
                    queue.main()
                launch.assert_not_called()
                self.assertEqual(sleeper.call_count,int(initially_running))
            status=json.loads((out/'status.json').read_text())
            self.assertEqual(status['status'],'blocked_failure')
            self.assertEqual(status['completed'],[])
            plan=json.loads((out/'plan.json').read_text())
            self.assertEqual(len(plan['cells']),32)
            keys={(c['size'],tuple(c['engines']),c['mode']) for c in plan['cells']}
            self.assertEqual(len(keys),32)
            self.assertFalse((out/'snapshot').exists())

    def test_failed_gate_blocks_all_work(self):
        self.check_blocked(False)

    def test_wait_then_failed_gate_blocks_all_work(self):
        self.check_blocked(True)

    def test_child_failure_records_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'failure.log'
            with patch.object(queue.shutil,'disk_usage') as disk:
                disk.return_value.free=10*1024**3
                with self.assertRaises(subprocess.CalledProcessError):
                    queue.run([sys.executable,'-c','raise SystemExit(7)'],log)
            d=json.loads(log.with_suffix('.process.json').read_text())
            self.assertEqual(d['returncode'],7)
            self.assertEqual(d['status'],'failed')

    def test_low_disk_blocks_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(queue.shutil,'disk_usage') as disk, patch.object(queue.subprocess,'Popen') as launch:
                disk.return_value.free=1024
                with self.assertRaisesRegex(RuntimeError,'disk safety'):
                    queue.run(['must-not-run'],Path(tmp)/'test.log')
                launch.assert_not_called()


if __name__=='__main__':unittest.main()
