import tempfile
import unittest
from pathlib import Path

import torch

from azilla_integration import CompileConfig, compile_mapping, write_schedule


class PipelineSmokeTest(unittest.TestCase):
    def test_compile_and_write(self):
        config = CompileConfig(
            mesh_x=2, mesh_y=2, tile_size=4, blocks_per_node=2,
            macro_iterations=1, micro_iterations=1, candidate_swaps=2,
        )
        src = torch.tensor([0, 1, 2, 4, 8, 16], dtype=torch.long)
        dst = torch.tensor([1, 5, 9, 12, 20, 31], dtype=torch.long)
        weight = torch.ones(len(src))
        result = compile_mapping(src, dst, weight, 32, config)
        self.assertEqual(sorted(result.permutation.tolist()), list(range(32)))
        self.assertEqual(len(result.block_pairs), len(result.owners))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mapping.schedule"
            write_schedule(target, result)
            self.assertTrue(target.read_text().startswith("# block_a"))


if __name__ == "__main__":
    unittest.main()
