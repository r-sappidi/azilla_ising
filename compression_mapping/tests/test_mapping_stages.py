import unittest

import torch

from graph_mapping.graph_compression.tile_compressor import (
    extract_block_coords_from_edges,
    partition_vertices,
    permutation_from_partitions,
    prune_low_weight_blocks,
)


class MappingStageTests(unittest.TestCase):
    def test_stages_preserve_balance_bijection_and_edges(self):
        vertices = 128
        src = torch.tensor([0, 1, 2, 64, 65, 66, 5, 70])
        dst = torch.tensor([1, 2, 3, 65, 66, 67, 70, 5])
        weight = torch.ones(len(src))
        macro = partition_vertices(
            src, dst, weight, vertices, 2,
            iterations=2, candidate_swaps=8, seed=4,
        )
        self.assertEqual(torch.bincount(macro).tolist(), [64, 64])
        for refine in (False, True):
            perm = permutation_from_partitions(
                src, dst, weight, macro, vertices, 2, 32,
                refine_tiles=refine, micro_iterations=2,
                candidate_swaps=8, seed=4,
            )
            self.assertTrue(torch.equal(torch.sort(perm).values,
                                        torch.arange(vertices)))
            coords, inverse = extract_block_coords_from_edges(
                src, dst, perm, 32, vertices
            )
            self.assertEqual(inverse.shape, (vertices,))
            self.assertGreater(len(coords), 0)

    def test_lossy_pruning_respects_weight_budget(self):
        src = torch.tensor([0, 0, 32, 64])
        dst = torch.tensor([32, 64, 64, 96])
        weight = torch.tensor([1.0, 2.0, 20.0, 40.0])
        permutation = torch.arange(128)
        coords, inverse = extract_block_coords_from_edges(
            src, dst, permutation, 32, 128
        )
        pruned, fraction, count = prune_low_weight_blocks(
            src, dst, weight, inverse, coords, 32, 128, 0.05
        )
        self.assertEqual(count, 2)
        self.assertLessEqual(fraction, 0.05)
        self.assertEqual(len(pruned), len(coords) - count)


if __name__ == "__main__":
    unittest.main()
