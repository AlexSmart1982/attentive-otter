import contextlib
import hashlib
import io
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("MPLBACKEND", "Agg")

import cv2
import numpy as np
from scipy.spatial import cKDTree

from scan import SCHEMA, candidate_ids, coarse_vectors, decode_thumbnail, encode_thumbnail, index_vectors, scan, similarity
from features import appearance_vectors, read_orientations


class ScanTests(unittest.TestCase):
    def test_constant_pixels_at_the_exact_threshold_survive_filter_and_cache(self):
        with tempfile.TemporaryDirectory(prefix="v2_boundary_") as folder:
            root = Path(folder)
            source = root/"photos"
            source.mkdir()
            for name, value in (("a", 60), ("b", 64)):
                cv2.imwrite(str(source/(name+".png")), np.full((96, 72, 3), value, dtype=np.uint8))
            threshold = similarity(*(appearance_vectors(read_orientations(source/(name+".png"))) for name in ("a", "b")))
            with contextlib.redirect_stdout(io.StringIO()):
                for index in range(2):
                    result = scan(source, root/str(index), root/"cache.sqlite", threshold=threshold)
                    self.assertEqual(result["matches"], 1)
                    self.assertEqual(result["verified_candidates"], 1)
                    self.assertEqual(result["cached_files"], 2 if index else 0)

    def test_strict_boundary_on_real_pngs_survives_candidate_projection(self):
        with tempfile.TemporaryDirectory(prefix="v2_strict_boundary_") as folder:
            root = Path(folder)
            source = root/"photos"
            source.mkdir()
            original = np.full((96, 72, 3), 60, dtype=np.uint8)
            cv2.imwrite(str(source/"a.png"), original)
            a = appearance_vectors(read_orientations(source/"a.png"))
            lo, hi = 0, 96*72
            while lo < hi:
                count = (lo+hi+1)//2
                changed = original+7
                changed.reshape(-1, 3)[:count] += 1
                cv2.imwrite(str(source/"b.png"), changed)
                score = similarity(a, appearance_vectors(read_orientations(source/"b.png")))
                if score >= .97:
                    lo = count
                else:
                    hi = count-1
            changed = original+7
            changed.reshape(-1, 3)[:lo] += 1
            cv2.imwrite(str(source/"b.png"), changed)
            score = similarity(a, appearance_vectors(read_orientations(source/"b.png")))
            self.assertGreaterEqual(score, .97)
            self.assertLess(score, .97001)
            with contextlib.redirect_stdout(io.StringIO()):
                result = scan(source, root/"scan", root/"cache.sqlite", review_threshold=.97)
            self.assertEqual(result["review_matches"], 1)

    def test_float32_bound_accepts_all_exact_thresholds_in_both_indices(self):
        rng = np.random.default_rng(190)
        vectors = [np.full((2, 96*72*3), value, np.float32)/255 for value in (0, 1, 60, 64, 128, 255)]
        base = rng.integers(0, 245, (4, 96*72*3), dtype=np.uint8)
        vectors += [base.astype(np.float32)/255, (base+7).astype(np.float32)/255]
        coarse = [coarse_vectors(v) for v in vectors]
        owners = np.repeat(np.arange(len(vectors)), [len(v) for v in vectors])
        for points in (np.concatenate(coarse), index_vectors(np.concatenate(coarse))):
            tree = cKDTree(points)
            for i in range(len(vectors)):
                for j in range(i+1, len(vectors)):
                    threshold = similarity(vectors[i], vectors[j])
                    if threshold > 0:
                        self.assertIn(j, candidate_ids(tree, coarse[i], owners, i, threshold))

    def test_persistent_thumbnail_is_lossless(self):
        pixels = np.random.default_rng(17).integers(0, 256, (4, 96*72*3), dtype=np.uint8)
        fine = pixels.astype(np.float32)/255
        np.testing.assert_array_equal(decode_thumbnail(encode_thumbnail(fine)), fine)

    def test_existing_coarse_only_database_is_upgraded(self):
        with tempfile.TemporaryDirectory(prefix="v2_cache_upgrade_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            path = source / "photo.png"
            cv2.imwrite(str(path), np.zeros((64, 48, 3), dtype=np.uint8))
            stat = path.stat()
            database = root / "legacy.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE features "
                                   "(path TEXT PRIMARY KEY, bytes INTEGER, mtime INTEGER, schema TEXT, coarse BLOB)")
                connection.execute("INSERT INTO features VALUES (?,?,?,?,?)",
                                   (str(path), stat.st_size, stat.st_mtime_ns, SCHEMA,
                                    np.zeros((2, 36), dtype=np.float32).tobytes()))
                connection.commit()
            finally:
                connection.close()
            with contextlib.redirect_stdout(io.StringIO()):
                result = scan(source, root / "upgraded", database)
                with patch("scan.read_orientations", side_effect=AssertionError("Original was reopened")):
                    repeated = scan(source, root / "repeated", database)
            self.assertEqual(result["source_decodes_initial"], 1)
            self.assertGreater(result["thumbnail_payload_bytes"], 0)
            self.assertEqual(repeated["cached_files"], 1)
            self.assertEqual(repeated["source_decodes_initial"], 0)

    def test_similar_frames_are_visible_without_merging_copy_groups(self):
        with tempfile.TemporaryDirectory(prefix="v2_review_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            original = np.zeros((64, 48, 3), dtype=np.uint8)
            original[12:30] = 255
            neighbour = original.copy()
            neighbour[20:24, 20:24] = 200
            for name, image in [("a", original), ("copy", original), ("nearby", neighbour)]:
                cv2.imwrite(str(source / (name + ".png")), image)
            with contextlib.redirect_stdout(io.StringIO()):
                result = scan(source, root / "report", root / "index.sqlite",
                              threshold=.9999, review_threshold=.95)
            self.assertEqual(result["matches"], 1)
            self.assertEqual(result["similar_frame_pairs"], 2)
            self.assertEqual(len(result["candidate_groups"][0]), 2)
            self.assertEqual(len(result["review_groups"][0]), 3)
            self.assertEqual(len(list(source.iterdir())), 3)

    def test_coarse_filter_does_not_lose_close_pairs(self):
        rng = np.random.default_rng(27)
        base = rng.uniform(.1, .9, (2, 96*72*3)).astype(np.float32)
        vectors = [base, np.clip(base + rng.normal(0, .002, base.shape), 0, 1).astype(np.float32),
                   rng.uniform(0, 1, base.shape).astype(np.float32)]
        coarse = [coarse_vectors(vector) for vector in vectors]
        owners = np.repeat(np.arange(3), 2)
        for points in (np.concatenate(coarse), index_vectors(np.concatenate(coarse))):
            tree = cKDTree(points)
            for i in range(3):
                candidates = candidate_ids(tree, coarse[i], owners, i, .99)
                for j in range(i+1, 3):
                    if similarity(vectors[i], vectors[j]) >= .99:
                        self.assertIn(j, candidates)

    def test_cache_reuse_invalidation_and_read_only_sources(self):
        with tempfile.TemporaryDirectory(prefix="v2_scan_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            a = np.zeros((64, 48, 3), dtype=np.uint8)
            a[12:30] = 255
            b = np.zeros_like(a)
            b[:, 12:30] = 255
            for name, image in [("a", a), ("copy", a), ("other", b)]:
                cv2.imwrite(str(source / (name + ".png")), image)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                first = scan(source, root / "run1", root / "index.sqlite")
                with patch("scan.read_orientations", side_effect=AssertionError("Original was reopened")):
                    second = scan(source, root / "run2", root / "index.sqlite")
            self.assertEqual(first["matches"], 1)
            self.assertEqual(second["cached_files"], 3)
            self.assertEqual(second["source_decodes_initial"], 0)
            self.assertEqual(second["source_decodes_verification"], 0)
            self.assertGreater(second["thumbnail_disk_hits"], 0)
            self.assertEqual(first["candidate_groups"], second["candidate_groups"])
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()})
            new_image = np.random.default_rng(4).integers(0, 256, a.shape, dtype=np.uint8)
            cv2.imwrite(str(source / "copy.png"), new_image)
            with contextlib.redirect_stdout(io.StringIO()):
                third = scan(source, root / "run3", root / "index.sqlite")
            self.assertEqual(third["cached_files"], 2)
            self.assertEqual(third["matches"], 0)


if __name__ == "__main__":
    unittest.main()
