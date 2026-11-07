"""Regression checks for grayscale duplicates and sample tuning."""

import contextlib
import csv
import io
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("NUMBA_NUM_THREADS", "2")

import cv2
import numpy as np

import comparsion as cp
import tune


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="comparison_v2_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = []
        horizontal = np.zeros((64, 48, 3), dtype=np.uint8)
        horizontal[16:32] = 255
        vertical = np.zeros_like(horizontal)
        vertical[:, 12:24] = 255
        for name, image in [("original", horizontal), ("copy", horizontal),
                            ("different", vertical)]:
            path = self.root / (name + ".png")
            self.assertTrue(cv2.imwrite(str(path), image))
            self.files.append(str(path))
        self.config = {"height": 64, "width": 48, "kernel": 6, "desired_stride": 2}

    def test_l_recovers_grayscale_and_distinguishes_different_structure(self):
        ab = cp.Img(self.files[0], channels=("a", "b"), **self.config)
        lab = cp.Img(self.files[0], **self.config)
        self.assertEqual(ab.hashes.sum(), 0)
        self.assertGreater(lab.hashes.sum(), 0)
        rows = [{"path": path, "source_id": group}
                for path, group in zip(self.files, ["same", "same", "different"])]
        scores, labels = tune.score_pairs(rows, self.config)
        np.testing.assert_array_equal(labels, [True, False, False])
        self.assertEqual(scores[0], 1)
        self.assertTrue(np.all(scores[1:] < 0.8))

    def test_pipeline_uses_l_threshold_and_dense_sparse_agree(self):
        with contextlib.redirect_stdout(io.StringIO()):
            kwargs = {"paths": [str(self.root)], "height": 64, "width": 48}
            dense = cp.pipeline(**kwargs, sparse=False)
            sparse = cp.pipeline(**kwargs, sparse=True)
            disabled_l = cp.pipeline(**kwargs, l_threshold=255)
            ab = cp.pipeline(**kwargs, channels=("a", "b"))
        self.assertEqual(len(dense), 1)
        self.assertEqual(set(dense.iloc[0][["row", "col"]]), set(self.files[:2]))
        self.assertEqual(dense.iloc[0]["val"], 100)
        self.assertTrue(dense.equals(sparse))
        self.assertTrue(disabled_l.empty)
        self.assertTrue(ab.empty)

    def test_packed_tuning_scores_match_pipeline_scores(self):
        rng = np.random.default_rng(42)
        im = cv2.resize(rng.integers(0, 256, (8, 6, 3), dtype=np.uint8), (48, 64))
        cv2.imwrite(self.files[0], im)
        cv2.imwrite(self.files[1], cv2.rotate(im, cv2.ROTATE_180))
        rows = [{"path": p, "source_id": str(i)} for i, p in enumerate(self.files)]
        scores, _ = tune.score_pairs(rows, self.config)
        with contextlib.redirect_stdout(io.StringIO()):
            hashes, denominator = cp.create_hash_matrix(
                self.files, threshold=9, kernel=6, stride=2, height=64, width=48)
            raw = cp.calc_similarity(hashes, self.files, sparse=False).to_numpy()
        expected = (raw / denominator)[np.tril_indices(3, -1)]
        np.testing.assert_allclose(scores, expected)

    def test_empty_folder_is_an_empty_result(self):
        empty = self.root / "empty"
        empty.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            result = cp.pipeline([str(empty)])
        self.assertEqual(list(result.columns), ["row", "val", "col"])
        self.assertTrue(result.empty)

    def test_content_decoder_when_opencv_cannot_open_a_file(self):
        with patch.object(cp.cv2, "imread", return_value=None):
            loaded = cp.read_image(self.files[0])
        expected = cv2.imread(self.files[0])
        np.testing.assert_array_equal(loaded, expected)

    def test_validation_rejects_source_leakage(self):
        manifest = self.root / "sample.csv"
        with manifest.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["path", "source_id", "split"])
            writer.writerow([self.files[0], "one_exposure", "train"])
            writer.writerow([self.files[1], "one_exposure", "validation"])
        with self.assertRaisesRegex(ValueError, "both splits"):
            tune.read_sample(manifest)

    def test_threshold_fit_uses_recall_and_ignores_empty_maps(self):
        scores = np.array([0.9, 0.8, 0.85, 0.2])
        labels = np.array([True, True, False, False])
        fit = tune.choose_threshold(scores, labels, target_recall=1)
        self.assertEqual(fit["threshold"], 0.8)
        self.assertEqual(fit["recall"], 1)
        self.assertEqual(fit["fp"], 1)
        empty = tune.choose_threshold(np.zeros(4), labels, target_recall=1)
        self.assertEqual(empty["candidate_pairs"], 0)
        self.assertEqual(empty["recall"], 0)

    def test_validation_rejects_series_split_between_training_and_validation(self):
        manifest = self.root / "scenes.csv"
        with manifest.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["path", "source_id", "scene_id", "split"])
            writer.writerow([self.files[0], "first_exposure", "one_series", "train"])
            writer.writerow([self.files[2], "second_exposure", "one_series", "validation"])
        with self.assertRaisesRegex(ValueError, "scene_id"):
            tune.read_sample(manifest)


if __name__ == "__main__":
    unittest.main()
