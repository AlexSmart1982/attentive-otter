import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("NUMBA_NUM_THREADS", "2")

import cv2
import numpy as np

from evaluation import evaluate, group_threshold
from features import PairEvidence, build_descriptors, local_ranges, pair_scores


class FeatureTests(unittest.TestCase):
    def test_windows_include_last_valid_position_and_rotate_consistently(self):
        image = np.repeat(np.arange(64, dtype=np.uint8).reshape(8, 8, 1), 3, axis=-1)
        actual = local_ranges(image, 2, 2)
        self.assertEqual(actual.shape, (4, 4, 3))
        for i in range(4):
            for j in range(4):
                block = image[i*2:i*2+2, j*2:j*2+2]
                np.testing.assert_array_equal(actual[i, j], block.max(axis=(0, 1)) - block.min(axis=(0, 1)))
        rotated = local_ranges(np.ascontiguousarray(image[::-1, ::-1]), 2, 2)
        np.testing.assert_array_equal(rotated, actual[::-1, ::-1])

    def test_weighted_dice_has_valid_normalization(self):
        counts = np.zeros((2, 4, 3), dtype=int)
        counts[0, 0] = [4, 2, 2]
        counts[1, 0] = [4, 4, 4]
        intersection = np.zeros((1, 4, 4, 3), dtype=np.uint32)
        intersection[0, 0, 0] = [4, 0, 0]
        evidence = PairEvidence(intersection, counts, np.zeros(1))
        self.assertEqual(evidence.scores([1, 0, 0])[0], 1)
        self.assertEqual(evidence.scores([0, 1, 1])[0], 0)
        self.assertEqual(evidence.scores()[0], 0.4)
        evidence.counts[:] = 0
        evidence.intersections[:] = 0
        self.assertEqual(evidence.scores()[0], 0)

    def test_square_quarter_turn_and_pair_order(self):
        with tempfile.TemporaryDirectory(prefix="v2_features_") as folder:
            rng = np.random.default_rng(31)
            image = cv2.resize(rng.integers(0, 256, (8, 8, 3), dtype=np.uint8), (64, 64))
            rows = []
            for i, content in enumerate([image, cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
                                         cv2.resize(255-image, (48, 64))]):
                path = Path(folder) / f"{i}.png"
                cv2.imwrite(str(path), content)
                rows.append({"path": str(path), "source_id": "one" if i < 2 else "two"})
            with contextlib.redirect_stdout(io.StringIO()):
                descriptors = build_descriptors(rows, [{"height": 64, "width": 48}])[0]
            edges, appearance = pair_scores(descriptors)
            inverse_edges, inverse_appearance = pair_scores(descriptors[::-1])
            self.assertEqual(edges[0], 1)
            self.assertEqual(appearance[0], 1)
            np.testing.assert_allclose(inverse_edges, edges[::-1])
            np.testing.assert_allclose(inverse_appearance, appearance[::-1])
            self.assertTrue(np.all((edges >= 0) & (edges <= 1)))

    def test_group_recovery_does_not_require_every_pair(self):
        rows = [{"source_id": source, "scene_id": "same_scene"}
                for source in ["one", "one", "one", "two"]]
        scores = np.array([.9, .1, .8, .4, .3, .2])
        cutoff = group_threshold(rows, scores)
        self.assertEqual(cutoff, .8)
        result = evaluate(rows, scores, cutoff)
        self.assertEqual(result["group_recall"], 1)
        self.assertEqual(result["recall"], 2/3)
        self.assertEqual(result["top1_correct"], 1)
        self.assertEqual(result["all_copies_before_series"], 1/3)


if __name__ == "__main__":
    unittest.main()
