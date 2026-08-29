import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from azilla_cycle_model.events import EventCompressedPerformanceModel
from azilla_cycle_model.mapping_adapter import (
    ARTIFACT_SCHEMA,
    MappingArtifact,
    artifact_from_mapping,
    assign_cross_owners,
    load_sparse_dataset_arrays,
    write_permuted_dataset,
)
from azilla_cycle_model.scheduler import compile_schedule
from azilla_cycle_model.workload import Geometry, IsingDataset, validate_schedule


class MappingAdapterTests(unittest.TestCase):
    def test_hardware_local_placement_and_balanced_cross_owners(self):
        geometry = Geometry(3, 1, 1, 1)
        pairs = np.asarray([(0, 1), (0, 2), (1, 2)], dtype=np.int64)
        owners = assign_cross_owners(geometry, pairs)
        self.assertEqual(sorted(owners.tolist()), [0, 1, 2])

        local_geometry = Geometry(2, 1, 2, 2)
        local_and_cross = np.asarray(
            [(0, 1), (0, 3), (0, 4), (4, 7)], dtype=np.int64
        )
        owners = assign_cross_owners(local_geometry, local_and_cross)
        self.assertEqual(owners[0], -1)
        self.assertEqual(owners[1], -1)
        self.assertGreaterEqual(owners[2], 0)
        self.assertEqual(owners[3], -1)

    def test_artifact_round_trip_and_event_model_consumption(self):
        geometry = Geometry(2, 1, 2, 2)
        permutation = np.arange(geometry.spin_count, dtype=np.int64)
        coords = np.asarray([
            (0, 0), (0, 1), (0, 4), (1, 2),
            (2, 3), (3, 4), (4, 5), (6, 7), (7, 6),
        ])
        artifact = artifact_from_mapping(
            geometry, permutation, permutation.copy(), coords,
            known_cut=123, metadata={"test": True},
        )
        self.assertNotIn((0, 0), artifact.occupancy_dataset().active_block_pairs())
        validate_schedule(
            artifact.occupancy_dataset(), geometry, artifact.schedule()
        )
        self.assertEqual(compile_schedule(
            geometry, artifact.schedule()
        ).counts, (4, 1, 2))

        with tempfile.TemporaryDirectory() as temporary:
            artifact.save(temporary)
            loaded = MappingArtifact.load(temporary)
            self.assertTrue(np.array_equal(
                loaded.permutation, artifact.permutation
            ))
            self.assertTrue(np.array_equal(
                loaded.block_pairs, artifact.block_pairs
            ))
            self.assertEqual(loaded.known_cut, 123)
            manifest = json.loads(
                (Path(temporary) / "manifest.json").read_text()
            )
            self.assertEqual(manifest["schema"], ARTIFACT_SCHEMA)
            self.assertTrue((Path(temporary) / "schedule.txt").is_file())

            result = EventCompressedPerformanceModel(
                loaded.geometry, loaded.occupancy_dataset()
            ).run(schedule=loaded.schedule())
            self.assertGreater(result.total_cycles, 0)
            self.assertEqual(
                result.scheduled_h0 + result.scheduled_h1 +
                result.scheduled_cross,
                len(loaded.block_pairs),
            )

    def test_permuted_dataset_preserves_weights_and_matches_schedule(self):
        geometry = Geometry(1, 1, 1, 2)
        permutation = np.concatenate((
            np.arange(32, 64, dtype=np.int64),
            np.arange(0, 32, dtype=np.int64),
        ))
        inverse = np.empty_like(permutation)
        inverse[permutation] = np.arange(64)
        artifact = artifact_from_mapping(
            geometry, permutation, inverse, np.asarray([(0, 1)])
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.txt"
            destination = Path(temporary) / "mapped.txt"
            source.write_text("64 7\n1 33 -5\n33 1 -5\n")
            write_permuted_dataset(source, destination, artifact)
            self.assertEqual(
                destination.read_text(),
                "64 7\n33 1 -5\n1 33 -5\n",
            )
            dataset = IsingDataset.load(destination)
            validate_schedule(dataset, geometry, artifact.schedule())

    def test_sparse_text_loader_omits_zero_edges_and_uses_magnitudes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "graph.txt"
            path.write_text("64 0\n1 2 -3\n2 3 0\n")
            spin_count, known_cut, src, dst, weight = (
                load_sparse_dataset_arrays(path)
            )
            self.assertEqual((spin_count, known_cut), (64, 0))
            self.assertEqual(src.tolist(), [0])
            self.assertEqual(dst.tolist(), [1])
            self.assertEqual(weight.tolist(), [3.0])


if __name__ == "__main__":
    unittest.main()
