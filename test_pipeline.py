import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from pipeline import prepare_one, run_pipeline
from scan import ScanCancelled
from image_io import read_image
from features import appearance_vectors, read_orientations
import pillow_heif


class PipelineTests(unittest.TestCase):
    def test_changed_source_between_comparison_and_export_is_not_published(self):
        for stage in ("compare", "metadata"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(prefix="v2_export_identity_") as folder:
                root = Path(folder)
                source = root/"photos"
                source.mkdir()
                for name in ("a", "b"):
                    Image.new("RGB", (72, 96), "green").save(source/(name+".png"))
                def replace_after_progress(value):
                    if value['stage'] == stage and value['done'] == value['total']:
                        Image.new("RGB", (72, 96), "red").save(source/"b.png")
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
                    run_pipeline([source], root/"run", root/"cache.sqlite", workers=1, progress=replace_after_progress)
                self.assertFalse((root/"run"/"review"/"review.json").exists())

    def test_replaced_file_with_preserved_size_and_mtime_invalidates_cache(self):
        with tempfile.TemporaryDirectory(prefix="v2_cache_identity_") as folder:
            root = Path(folder)
            source = root/"photos"
            source.mkdir()
            path = source/"a.png"
            Image.new("RGB", (72, 96), "green").save(path)
            before = path.stat()
            with contextlib.redirect_stdout(io.StringIO()):
                run_pipeline([source], root/"run1", root/"cache.sqlite", workers=1)
                replacement = root/"replacement.png"
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                result = run_pipeline([source], root/"run2", root/"cache.sqlite", workers=1)
            self.assertEqual(path.stat().st_size, before.st_size)
            self.assertEqual(result["preparation"]["cached"], 0)
            self.assertEqual(result["preparation"]["decoded"], 1)

    def test_alpha_heic_is_decoded_as_three_channels_and_included_in_search(self):
        with tempfile.TemporaryDirectory(prefix="v2_heic_alpha_") as folder:
            root = Path(folder)
            source = root/"photos"
            source.mkdir()
            image = Image.new("RGBA", (72, 96), (60, 90, 120, 128))
            pillow_heif.from_pillow(image).save(source/"a.heic", quality=95)
            (source/"b.heic").write_bytes((source/"a.heic").read_bytes())
            self.assertEqual(pillow_heif.read_heif(source/"a.heic").mode, "RGBA")
            self.assertEqual(read_image(source/"a.heic").shape, (96, 72, 3))
            self.assertEqual(appearance_vectors(read_orientations(source/"a.heic")).shape, (2, 96*72*3))
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_pipeline([source], root/"run", root/"cache.sqlite", workers=1)
            self.assertEqual(result["report"]["errors"], [])
            self.assertEqual(result["groups"], 1)

    def test_search_modes_expand_pairs_without_losing_strict_results(self):
        with tempfile.TemporaryDirectory(prefix="v2_search_modes_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            base = np.random.default_rng(31).integers(40, 180, (96, 72, 3), dtype=np.uint8)
            for name, delta in (("original", 0), ("copy", 0), ("near", 8), ("farther", 18)):
                Image.fromarray(base+delta).save(source / (name+".png"))
            pairs = []
            with contextlib.redirect_stdout(io.StringIO()):
                for mode in ("strict", "close", "extended"):
                    result = run_pipeline([source], root / mode, root / "cache.sqlite", workers=1, search_mode=mode)
                    document = json.loads(Path(result["review_path"]).read_text(encoding="utf-8"))
                    pairs.append({frozenset((p["path1"], p["path2"])) for p in document["pairs"]})
                    self.assertEqual(document["search_mode"], mode)
            self.assertEqual([len(p) for p in pairs], [1, 4, 6])
            self.assertLess(pairs[0], pairs[1])
            self.assertLess(pairs[1], pairs[2])

    def test_multiple_overlapping_sources_decode_once_then_reuse_cache(self):
        with tempfile.TemporaryDirectory(prefix="v2_pipeline_") as folder:
            root = Path(folder)
            source, second = root / "photos", root / "incoming"
            nested = source / "album"
            nested.mkdir(parents=True)
            second.mkdir()
            image = np.random.default_rng(4).integers(0, 256, (64, 48, 3), dtype=np.uint8)
            paths = [nested / "original.png", second / "copy.png", source / "third.png"]
            for path in paths:
                Image.fromarray(image).save(path)
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            sources = [source, nested, second]
            with contextlib.redirect_stdout(io.StringIO()):
                with patch("pipeline.prepare_one", wraps=prepare_one) as decoding:
                    first = run_pipeline(sources, root / "run1", root / "cache.sqlite", workers=2)
                self.assertEqual(decoding.call_count, 3)
                with patch("pipeline.prepare_one", side_effect=AssertionError("Repeated decode")):
                    second_result = run_pipeline(sources, root / "run2", root / "cache.sqlite", workers=2)
            self.assertEqual(first["preparation"]["decoded"], 3)
            self.assertEqual(first["report"]["source_decodes_initial"], 0)
            self.assertEqual(first["report"]["source_decodes_verification"], 0)
            self.assertEqual(second_result["preparation"]["cached"], 3)
            self.assertEqual(first["groups"], 1)
            self.assertEqual(first["review_files"], 3)
            document = json.loads(Path(first["review_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(document["source_roots"]), 3)
            self.assertTrue(all(r["detected_format"] == "PNG" for r in document["files"]))
            self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_cancelled_run_leaves_sources_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="v2_cancel_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            event = threading.Event()
            event.set()
            with self.assertRaises(ScanCancelled):
                run_pipeline([source], root / "run", root / "cache.sqlite", cancel_event=event)
            self.assertEqual(list(source.iterdir()), [])

    def test_empty_source_and_invalid_output(self):
        with tempfile.TemporaryDirectory(prefix="v2_empty_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            with self.assertRaises(ValueError):
                run_pipeline([source], source / "output", root / "cache.sqlite")
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_pipeline([source], root / "run", root / "cache.sqlite", workers=1)
            self.assertEqual(result["review_files"], 0)
            self.assertEqual(result["groups"], 0)


if __name__ == "__main__":
    unittest.main()
