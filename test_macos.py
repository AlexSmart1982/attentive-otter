"""Native macOS regression checks using disposable files only."""

import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from action_plan import ActionPlan
from execution import apply_files, journal_path
from stable_files import LockedFile
from system_trash import recycle_file


@unittest.skipUnless(sys.platform == "darwin", "Native macOS filesystem")
class MacOSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otter_macos_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        photos = self.root / "photos"
        photos.mkdir()
        self.source = photos / "фото.jpg"
        self.source.write_bytes(b"disposable macOS fixture")
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_exclusive_rename_keeps_existing_file_and_symlink(self):
        target = self.root / "occupied.jpg"
        target.write_bytes(b"someone else's photo")
        guard = LockedFile(self.source)
        self.addCleanup(guard.close)
        for symlink in (False, True):
            if symlink:
                target.unlink()
                target.symlink_to(self.root / "missing")
            with self.assertRaises(FileExistsError):
                guard.move_to(target, self.expected)
            self.assertTrue(self.source.is_file())
            if not symlink:
                self.assertEqual(target.read_bytes(), b"someone else's photo")

    def test_symlinks_and_substituted_source_are_rejected(self):
        link = self.root / "link.jpg"
        link.symlink_to(self.source)
        with self.assertRaises(OSError):
            LockedFile(link)
        guard = LockedFile(self.source)
        self.addCleanup(guard.close)
        self.source.rename(self.root / "original.jpg")
        self.source.write_bytes(b"different object")
        with self.assertRaises(OSError):
            guard.move_to(self.root / "target.jpg", self.expected)
        self.assertEqual(self.source.read_bytes(), b"different object")

    def test_cross_volume_copy_preserves_metadata(self):
        target = self.root / "target.jpg"
        subprocess.run(["/usr/bin/xattr", "-w", "com.attentiveotter.fixture", "test metadata", str(self.source)], check=True)
        before = self.source.stat()
        guard = LockedFile(self.source)
        self.addCleanup(guard.close)
        with patch.object(LockedFile, "rename", side_effect=OSError(errno.EXDEV, "cross volume")):
            guard.move_to(target, self.expected)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), self.expected)
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertAlmostEqual(target.stat().st_birthtime, before.st_birthtime, places=4)
        self.assertEqual(subprocess.check_output([
            "/usr/bin/xattr", "-p", "com.attentiveotter.fixture", str(target)]).strip(), b"test metadata")

    def test_edit_after_preflight_is_detected_before_move(self):
        destination = self.root / "destination"
        destination.mkdir()
        path = str(self.source)
        model = ActionPlan({"source_roots": [str(self.source.parent)], "scan_report": str(self.root / "scan.json"), "files": [
            {"path": path, "group_id": 1, "sha256": self.expected}], "pairs": []})
        model.move_files(1, [path], destination)
        project = self.root / "session.cmp.json"
        def edit(done, total):
            if not journal_path(project).exists():
                self.source.write_bytes(b"external edit")
        with self.assertRaises(OSError):
            apply_files(model, project, progress=edit)
        self.assertEqual(self.source.read_bytes(), b"external edit")
        self.assertFalse((destination / self.source.name).exists())

    def test_native_trash_returns_actual_recoverable_file(self):
        recycled = Path(recycle_file(self.source))
        try:
            self.assertFalse(self.source.exists())
            self.assertTrue(recycled.is_file())
            self.assertEqual(hashlib.sha256(recycled.read_bytes()).hexdigest(), self.expected)
        finally:
            if recycled.is_file():
                recycled.unlink()  # This test owns exactly this disposable fixture.

    def test_edit_after_backup_blocks_native_trash_and_preserves_changed_bytes(self):
        path = str(self.source)
        model = ActionPlan({"source_roots": [str(self.source.parent)], "scan_report": str(self.root / "scan.json"),
                            "files": [{"path": path, "group_id": 1, "sha256": self.expected}], "pairs": []})
        model.remove_selected(1, [path])
        project = self.root / "session.cmp.json"
        changed = []
        def edit(done, total):
            if journal_path(project).exists() and not changed:
                record = json.loads(journal_path(project).read_text())["removed"][0]
                if record.get("backup_ready"):
                    held = Path(record["held"])
                    held.write_bytes(b"external edit after backup")
                    changed.append(held)
        with patch("execution.recycle_file") as recycle:
            with self.assertRaises(OSError):
                apply_files(model, project, progress=edit)
            recycle.assert_not_called()
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].read_bytes(), b"external edit after backup")
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.expected)
