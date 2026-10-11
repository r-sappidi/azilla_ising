"""Small safety tests for maintenance; no simulator or vendor tool needed."""
import os
from pathlib import Path
import tempfile
import time
import unittest

from cleanup_build_cache import candidates


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="azilla-cleanup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.build.mkdir()
        (self.build / "Vtest.mk").touch()
        self.cutoff = time.time() - 7 * 86400

    def old(self, name):
        p = self.build / name
        p.touch()
        os.utime(p, (self.cutoff - 10, self.cutoff - 10))
        return p

    def test_only_compiler_caches(self):
        wanted = {self.old("Vtest" + ext) for ext in (".gch", ".o", ".a", ".d")}
        for name in ("Vtest", "Vtest.cpp", "Vtest.h", "run.log", "events.csv", "report.json"):
            self.old(name)
        self.assertEqual(set(candidates(self.build, self.cutoff, set())), wanted)

    def test_recent_and_tracked_files_survive(self):
        tracked = self.old("tracked.o")
        (self.build / "recent.o").touch()
        self.assertEqual(candidates(self.build, self.cutoff, {tracked}), [])

    def test_symlinks_and_subdirectories_survive(self):
        outside = self.root / "outside.o"
        outside.touch()
        (self.build / "linked.o").symlink_to(outside)
        nested = self.build / "nested"
        nested.mkdir()
        (nested / "nested.o").touch()
        self.assertEqual(candidates(self.build, self.cutoff, set()), [])
        link = self.root / "build-link"
        link.symlink_to(self.build)
        with self.assertRaises(ValueError):
            candidates(link, self.cutoff, set())

    def test_requires_verilator_marker(self):
        other = self.root / "unmarked"
        other.mkdir()
        with self.assertRaises(ValueError):
            candidates(other, self.cutoff, set())


if __name__ == "__main__":
    unittest.main()
