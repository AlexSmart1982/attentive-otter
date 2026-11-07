"""Real filesystem operations, isolated to disposable fixture folders."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import subprocess
import sys

from action_plan import ActionPlan
from execution import apply_files, journal_path, move_exclusive
from project import save_project, load_project
from storage import write_json
from windows_trash import recycle_file
from stable_files import LockedFile


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2_real_apply_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sorted, self.incoming, self.destination, self.trash = [self.root/name for name in
            ("sorted", "incoming", "destination", "trash")]
        for directory in (self.sorted, self.incoming, self.destination, self.trash):
            directory.mkdir()
        self.paths = [self.sorted/"кадр.jpg", self.incoming/"original.jpg", self.incoming/"third.png"]
        rows = []
        for index, path in enumerate(self.paths):
            path.write_bytes(b"fixture photo "+bytes([index]))
            rows.append({"path": str(path), "group_id": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.a, self.b, self.c = map(str, self.paths)
        self.model = ActionPlan({"source_roots": [str(self.sorted), str(self.incoming)],
                                "source_root": str(self.sorted), "scan_report": str(self.root/"scan.json"),
                                "files": rows, "pairs": []})
        self.original = {str(p): p.read_bytes() for p in self.paths}
        self.filename = self.root/"comparison.cmp.json"

    def recycle(self, path):
        path = Path(path).resolve()
        self.assertIn(self.root, path.parents)
        self.assertTrue(path.is_file())
        target = self.trash/(uuid.uuid4().hex+"-"+path.name)
        shutil.move(str(path), target)
        return str(target)

    def assert_restored(self):
        self.assertEqual({str(p): p.read_bytes() for p in self.paths}, self.original)
        self.assertFalse(journal_path(self.filename).exists())
        self.assertFalse(list(self.root.rglob("*.hold")))

    def test_replace_and_move_commit_real_paths_and_do_not_repeat_after_reload(self):
        self.model.choose(self.a, self.b, "replace_a")
        self.model.move_files(1, [self.c], self.destination)
        batch = apply_files(self.model, self.filename, recycler=self.recycle)
        self.assertEqual(batch["mode"], "applied")
        self.assertTrue(batch["execution_enabled"])
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.b])
        self.assertFalse(Path(self.b).exists())
        self.assertFalse(Path(self.c).exists())
        self.assertEqual((self.destination/"third.png").read_bytes(), self.original[self.c])
        self.assertEqual(self.model.physical_path(self.b), self.a)
        self.assertEqual(len(list(self.trash.iterdir())), 1)
        self.assertFalse(journal_path(self.filename).exists())
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.physical_path(self.b), self.a)
        self.assertFalse(restored.undo())
        self.assertIsNone(apply_files(restored, self.filename, recycler=self.recycle))
        # A second replacement uses the surviving file's current physical location.
        restored.choose(self.b, self.c, "replace_a")
        restored.files[self.c]["detected_format"] = "JPEG"
        apply_files(restored, self.filename, recycler=self.recycle)
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.c])
        self.assertEqual(restored.physical_path(self.c), self.a)

    def test_folder_policy_applies_replacement_and_removal_without_recycling(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.choose(self.a, self.b, "replace_a")
        self.model.remove_selected(1, [self.c])
        planned = self.model.build()
        self.assertEqual(planned["delete_policy"], "folder")
        with patch("execution.recycle_file", side_effect=AssertionError("Bin must not be used")):
            batch = apply_files(self.model, self.filename)
        self.assertEqual(batch["delete_policy"], "folder")
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.b])
        for path in (self.a, self.c):
            target = self.trash/Path(path).name
            self.assertEqual(target.read_bytes(), self.original[path])
            self.assertEqual(self.model.physical_path(path), str(target))
        restored, view = load_project(self.filename)
        self.assertEqual((restored.delete_mode, restored.delete_folder), ("folder", str(self.trash)))
        self.assertIn(str(self.trash), view["outcome"]["detail"])
        self.assertIsNone(apply_files(restored, self.filename))

    def test_folder_policy_disambiguates_names_without_overwriting_existing_files(self):
        same_name = self.incoming/Path(self.a).name
        same_name.write_bytes(b"another image with the same name")
        rows = deepcopy(self.model.document["files"])
        rows.append({"path": str(same_name), "group_id": 1,
                     "sha256": hashlib.sha256(same_name.read_bytes()).hexdigest()})
        self.model = ActionPlan(dict(self.model.document, files=rows))
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a, str(same_name)])
        occupied = self.trash/Path(self.a).name
        occupied.write_bytes(b"unrelated existing file")
        actions = self.model.build()["actions"]
        self.assertEqual(len({a["destination"] for a in actions}), 2)
        self.assertNotIn(str(occupied), {a["destination"] for a in actions})
        expected = {a["destination"]: Path(a["path"]).read_bytes() for a in actions}
        apply_files(self.model, self.filename)
        self.assertEqual(occupied.read_bytes(), b"unrelated existing file")
        self.assertEqual({p: Path(p).read_bytes() for p in expected}, expected)

    def test_folder_racing_collision_with_identical_bytes_preserves_foreign_file(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a])
        destination = Path(self.model.build()["actions"][0]["destination"])
        def create_collision(done, total):
            if not destination.exists():
                destination.write_bytes(self.original[self.a])
        with self.assertRaises(OSError):
            apply_files(self.model, self.filename, progress=create_collision)
        self.assert_restored()
        self.assertEqual(destination.read_bytes(), self.original[self.a])

    def test_unavailable_removal_folder_blocks_all_actions(self):
        self.model.configure_deletion("folder", str(self.root/"missing"))
        self.model.remove_selected(1, [self.a])
        self.model.move_files(1, [self.c], self.destination)
        with self.assertRaisesRegex(OSError, "недоступна"):
            apply_files(self.model, self.filename)
        self.assert_restored()
        self.assertFalse(list(self.destination.iterdir()))

    def test_folder_copy_fallback_preserves_bytes_times_and_tracks_identity(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a])
        before = Path(self.a).stat()
        real_rename = LockedFile.rename
        def cross_volume(file, destination):
            if Path(destination).parent == self.trash:
                error = OSError("cross-volume move")
                error.winerror = 17
                raise error
            return real_rename(file, destination)
        with patch.object(LockedFile, "rename", cross_volume):
            apply_files(self.model, self.filename)
        target = self.trash/Path(self.a).name
        self.assertEqual(target.read_bytes(), self.original[self.a])
        self.assertAlmostEqual(target.stat().st_mtime, before.st_mtime, places=4)
        self.assertAlmostEqual(target.stat().st_birthtime, before.st_birthtime, places=4)
        self.assertFalse(Path(self.a).exists())

    def test_folder_failed_checkpoint_restores_all_files_and_clears_folder(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.choose(self.a, self.b, "replace_a")
        real_save = save_project
        def fail_commit(model, filename, view=None):
            if model.applied_batches:
                raise OSError("disk full")
            return real_save(model, filename, view)
        with patch("project.save_project", side_effect=fail_commit):
            with self.assertRaises(OSError):
                apply_files(self.model, self.filename)
        self.assert_restored()
        self.assertFalse(list(self.trash.iterdir()))
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.delete_mode, "folder")
        apply_files(restored, self.filename)
        self.assertEqual((self.trash/Path(self.a).name).read_bytes(), self.original[self.a])

    def test_folder_cancel_after_placement_restores_the_whole_batch(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a, self.b])
        event = threading.Event()
        real_move = move_exclusive
        def cancel_after_placement(source, destination, expected, **options):
            result = real_move(source, destination, expected, **options)
            if Path(destination).parent == self.trash:
                event.set()
            return result
        with patch("execution.move_exclusive", side_effect=cancel_after_placement):
            with self.assertRaises(InterruptedError):
                apply_files(self.model, self.filename, cancel_event=event)
        self.assert_restored()
        self.assertFalse(list(self.trash.iterdir()))

    def test_folder_crash_after_move_before_journal_update_recovers(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a])
        real_move = move_exclusive
        def crash_after_placement(source, destination, expected, **options):
            result = real_move(source, destination, expected, **options)
            if Path(destination).parent == self.trash:
                raise SystemExit("simulated process termination")
            return result
        with patch("execution.move_exclusive", side_effect=crash_after_placement):
            with self.assertRaises(SystemExit):
                apply_files(self.model, self.filename)
        self.assertFalse(Path(self.a).exists())
        self.assertTrue(journal_path(self.filename).exists())
        restored, _ = load_project(self.filename)
        self.assert_restored()
        self.assertFalse(list(self.trash.iterdir()))
        self.assertEqual(restored.recycles, {self.a})

    def test_switch_from_folder_to_bin_uses_only_the_selected_policy(self):
        self.model.configure_deletion("folder", str(self.destination))
        self.model.remove_selected(1, [self.a])
        self.model.configure_deletion("recycle_bin", str(self.destination))
        with patch("execution.recycle_file", side_effect=self.recycle) as recycler:
            apply_files(self.model, self.filename)
        recycler.assert_called_once()
        self.assertFalse(list(self.destination.iterdir()))

    def test_folder_copy_fallback_crash_recovers_using_the_new_file_identity(self):
        self.model.configure_deletion("folder", str(self.trash))
        self.model.remove_selected(1, [self.a])
        real_rename, real_move = LockedFile.rename, move_exclusive
        def cross_volume(file, destination):
            if Path(destination).parent == self.trash:
                error = OSError("cross-volume move")
                error.winerror = 17
                raise error
            return real_rename(file, destination)
        def crash(source, destination, expected, **options):
            result = real_move(source, destination, expected, **options)
            if Path(destination).parent == self.trash:
                raise SystemExit("termination before journal update")
            return result
        with patch.object(LockedFile, "rename", cross_volume), patch("execution.move_exclusive", side_effect=crash):
            with self.assertRaises(SystemExit):
                apply_files(self.model, self.filename)
        record = json.loads(journal_path(self.filename).read_text(encoding="utf-8"))["removed"][0]
        placed = LockedFile(record["destination"])
        try:
            self.assertEqual(placed.identity(), record["placed_identity"])
        finally:
            placed.close()
        load_project(self.filename)
        self.assert_restored()
        self.assertFalse(list(self.trash.iterdir()))

    def test_copy_fallback_preserves_bytes_and_file_times(self):
        self.model.move_files(1, [self.c], self.destination)
        before = Path(self.c).stat()
        real_rename = LockedFile.rename
        def cross_volume(file, destination):
            if Path(destination).parent == self.destination:
                error = OSError("cross-volume move")
                error.winerror = 17
                raise error
            return real_rename(file, destination)
        with patch.object(LockedFile, "rename", cross_volume):
            apply_files(self.model, self.filename, recycler=self.recycle)
        target = self.destination/"third.png"
        self.assertEqual(target.read_bytes(), self.original[self.c])
        self.assertAlmostEqual(target.stat().st_mtime, before.st_mtime, places=4)
        if os.name == "nt":
            self.assertAlmostEqual(target.stat().st_birthtime, before.st_birthtime, places=4)
        self.assertFalse(Path(self.c).exists())

    def test_changed_source_or_destination_blocks_all_changes(self):
        self.model.remove_selected(1, [self.a])
        self.model.move_files(1, [self.c], self.destination)
        collision = self.destination/"third.png"
        collision.write_bytes(b"unrelated")
        with self.assertRaises((OSError, ValueError)):
            apply_files(self.model, self.filename, recycler=self.recycle)
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.a])
        self.assertEqual(collision.read_bytes(), b"unrelated")
        collision.unlink()
        Path(self.c).write_bytes(b"external edit")
        with self.assertRaises(OSError):
            apply_files(self.model, self.filename, recycler=self.recycle)
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.a])
        self.assertFalse(list(self.trash.iterdir()))

    def test_failed_recycle_restores_staged_source_and_original_target(self):
        self.model.choose(self.a, self.b, "replace_a")
        before = self.model.export_state()
        with self.assertRaises(OSError):
            apply_files(self.model, self.filename, recycler=lambda path: (_ for _ in ()).throw(OSError("trash unavailable")))
        self.assertEqual(self.model.export_state(), before)
        self.assert_restored()

    def test_failed_final_placement_restores_recycled_target(self):
        self.model.choose(self.a, self.b, "replace_a")
        real_move = move_exclusive
        def fail_destination(source, destination, expected):
            if str(destination) == self.a:
                raise OSError("destination is locked")
            return real_move(source, destination, expected)
        with patch("execution.move_exclusive", side_effect=fail_destination):
            with self.assertRaises(OSError):
                apply_files(self.model, self.filename, recycler=self.recycle)
        self.assert_restored()
        self.assertTrue(self.model.replacements)

    def test_failed_checkpoint_rolls_back_files_and_keeps_pending_project(self):
        self.model.choose(self.a, self.b, "replace_a")
        before = self.model.export_state()
        real_save = save_project
        def fail_commit(model, filename, view=None):
            if model.applied_batches:
                raise OSError("disk full")
            return real_save(model, filename, view)
        with patch("project.save_project", side_effect=fail_commit):
            with self.assertRaises(OSError):
                apply_files(self.model, self.filename, recycler=self.recycle)
        self.assertEqual(self.model.export_state(), before)
        self.assert_restored()
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.export_state(), before)

    def test_failed_checkpoint_restores_identical_full_copies_at_both_paths(self):
        Path(self.b).write_bytes(self.original[self.a])
        self.model.files[self.b]["sha256"] = self.model.files[self.a]["sha256"]
        self.original[self.b] = self.original[self.a]
        self.model.choose(self.a, self.b, "replace_a")
        real_save = save_project
        def fail_commit(model, filename, view=None):
            if model.applied_batches:
                raise OSError("disk full")
            return real_save(model, filename, view)
        with patch("project.save_project", side_effect=fail_commit):
            with self.assertRaises(OSError):
                apply_files(self.model, self.filename, recycler=self.recycle)
        self.assert_restored()

    def test_cancel_after_recycle_rolls_back_the_whole_batch(self):
        self.model.remove_selected(1, [self.a, self.b])
        cancelled = threading.Event()
        def cancel_after_first(path):
            result = self.recycle(path)
            cancelled.set()
            return result
        with self.assertRaises(InterruptedError):
            apply_files(self.model, self.filename, cancel_event=cancelled, recycler=cancel_after_first)
        self.assert_restored()
        self.assertEqual(self.model.recycles, {self.a, self.b})

    def test_interrupted_staging_is_recovered_when_loading_comparison(self):
        self.model.move_files(1, [self.c], self.destination)
        save_project(self.model, self.filename)
        transaction = uuid.uuid4().hex
        held = Path(self.c).with_name("."+Path(self.c).name+".comparison-"+transaction+".hold")
        record = {"id": self.c, "source": self.c, "held": str(held),
                  "destination": str(self.destination/"third.png"), "sha256": self.model.files[self.c]["sha256"]}
        write_json(journal_path(self.filename), {"kind": "comparison-file-transaction", "id": transaction,
            "project": str(self.filename), "before_state": self.model.export_state(), "moving": [record], "removed": []})
        Path(self.c).rename(held)
        restored, _ = load_project(self.filename)
        self.assert_restored()
        self.assertEqual(restored.moves, self.model.moves)

    def test_old_virtual_checkpoints_become_pending_real_changes(self):
        self.model.choose(self.a, self.b, "replace_a")
        self.model.simulate_apply()
        self.assertTrue(self.model.prepare_real_execution())
        self.assertFalse(self.model.committed_recycles)
        self.assertEqual(self.model.recycles, {self.a})
        apply_files(self.model, self.filename, recycler=self.recycle)
        self.assertEqual(Path(self.a).read_bytes(), self.original[self.b])
        self.assertFalse(Path(self.b).exists())

    def test_an_active_transaction_cannot_be_recovered_or_applied_again(self):
        self.model.remove_selected(1, [self.a])
        def check_exclusivity(path):
            with self.assertRaises(OSError):
                load_project(self.filename)
            with self.assertRaises(OSError):
                apply_files(deepcopy(self.model), self.filename, recycler=self.recycle)
            return self.recycle(path)
        apply_files(self.model, self.filename, recycler=check_exclusivity)
        self.assertFalse(Path(self.a).exists())
        self.assertTrue(Path(self.b).exists())

    def test_exclusion_starts_before_preflight_and_rejects_stale_model(self):
        self.model.move_files(1, [self.c], self.destination)
        save_project(self.model, self.filename)
        stale, _ = load_project(self.filename)
        entered, resume = threading.Event(), threading.Event()
        failures = []
        def hold_preflight(done, total):
            if not entered.is_set():
                entered.set()
                if not resume.wait(10):
                    raise TimeoutError("test barrier")
        def worker():
            try:
                apply_files(self.model, self.filename, progress=hold_preflight, recycler=self.recycle)
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            self.assertFalse(journal_path(self.filename).exists())
            with self.assertRaises(OSError):
                apply_files(stale, self.filename, recycler=self.recycle)
            with self.assertRaises(OSError):
                save_project(stale, self.filename)
        finally:
            resume.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        checkpoint = self.filename.read_bytes()
        stale.cancel_moves([self.c])
        stale.remove_selected(1, [self.a])
        with self.assertRaises(OSError):
            apply_files(stale, self.filename, recycler=self.recycle)
        with self.assertRaises(OSError):
            save_project(stale, self.filename)
        self.assertEqual(checkpoint, self.filename.read_bytes())
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.physical_path(self.c), str(self.destination/"third.png"))
        self.assertEqual(len(restored.applied_batches), 1)
        self.assertTrue(Path(self.a).exists())

    def test_project_lock_excludes_another_process_during_preflight(self):
        self.model.remove_selected(1, [self.a])
        checked = False
        def check_lock(done, total):
            nonlocal checked
            if checked:
                return
            checked = True
            code = ("import sys\nfrom project_lock import project_lock\n"
                    "try:\n    with project_lock(sys.argv[1]): pass\n"
                    "except OSError: sys.exit(0)\nelse: sys.exit(7)\n")
            result = subprocess.run([sys.executable, "-B", "-c", code, str(self.filename)],
                                    capture_output=True, timeout=10, cwd=Path(__file__).parent)
            self.assertEqual(result.returncode, 0, result.stderr)
        apply_files(self.model, self.filename, progress=check_lock, recycler=self.recycle)
        self.assertTrue(checked)

    def test_sources_cannot_be_written_or_substituted_after_hashing(self):
        self.model.move_files(1, [self.c], self.destination)
        self.model.remove_selected(1, [self.a])
        attempts = 0
        def edit_after_hash(done, total):
            nonlocal attempts
            if journal_path(self.filename).exists():
                return
            # Each already-checked source is held until it has been staged.
            for path in (self.a, self.c):
                from stable_files import protected_file
                if protected_file(path) is None:
                    continue
                attempts += 1
                with self.assertRaises(PermissionError):
                    Path(path).write_bytes(b"external edit")
                with self.assertRaises(PermissionError):
                    Path(path).rename(Path(path).with_suffix(".changed"))
        apply_files(self.model, self.filename, progress=edit_after_hash, recycler=self.recycle)
        self.assertGreater(attempts, 0)
        self.assertEqual((self.destination/"third.png").read_bytes(), self.original[self.c])
        self.assertEqual(next(self.trash.iterdir()).read_bytes(), self.original[self.a])

    def test_crash_after_recycle_before_return_recovers_without_recycled_path(self):
        self.model.choose(self.a, self.b, "replace_a")
        def crash(path):
            self.recycle(path)
            raise SystemExit("process stopped before recording shell result")
        with self.assertRaises(SystemExit):
            apply_files(self.model, self.filename, recycler=crash)
        journal = json.loads(journal_path(self.filename).read_text(encoding="utf-8"))
        self.assertNotIn("recycled", journal["removed"][0])
        self.assertTrue(journal["removed"][0]["backup_ready"])
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.replacements, {self.a: self.b})
        self.assert_restored()
        self.assertFalse(list(self.root.rglob("*.backup")))

    def test_crash_before_checkpoint_restores_all_staged_and_placed_files(self):
        self.model.choose(self.a, self.b, "replace_a")
        self.model.move_files(1, [self.c], self.destination)
        real_save = save_project
        def crash_commit(model, filename, view=None):
            if model.applied_batches:
                raise SystemExit("before checkpoint")
            return real_save(model, filename, view)
        with patch("project.save_project", side_effect=crash_commit):
            with self.assertRaises(SystemExit):
                apply_files(self.model, self.filename, recycler=self.recycle)
        load_project(self.filename)
        self.assert_restored()
        self.assertFalse((self.destination/"third.png").exists())

    def test_abrupt_process_exit_after_recycling_releases_lock_and_recovers(self):
        self.model.choose(self.a, self.b, "replace_a")
        save_project(self.model, self.filename)
        code = ("import os, sys, shutil\nfrom pathlib import Path\n"
                "from project import load_project\nfrom execution import apply_files\n"
                "model, _ = load_project(sys.argv[1])\n"
                "def crash(path):\n    shutil.move(path, str(Path(sys.argv[2])/Path(path).name))\n"
                "    os._exit(71)\n"
                "apply_files(model, sys.argv[1], recycler=crash)\n")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.filename), str(self.trash)],
                                capture_output=True, timeout=15, cwd=Path(__file__).parent)
        self.assertEqual(result.returncode, 71, result.stderr)
        self.assertFalse(Path(self.a).exists())
        restored, _ = load_project(self.filename)
        self.assertEqual(restored.replacements, {self.a: self.b})
        self.assert_restored()

    def test_direct_move_verifies_expected_bytes_before_rename(self):
        Path(self.c).write_bytes(b"edited after search")
        destination = self.destination/"third.png"
        with self.assertRaises(OSError):
            move_exclusive(self.c, destination, self.model.files[self.c]["sha256"])
        self.assertFalse(destination.exists())
        self.assertEqual(Path(self.c).read_bytes(), b"edited after search")

    @unittest.skipUnless(os.name == "nt", "Native Windows recycling")
    def test_windows_backend_returns_a_real_recycled_file(self):
        original = self.original[self.c]
        recycled = Path(recycle_file(self.c))
        self.assertFalse(Path(self.c).exists())
        self.assertTrue(recycled.is_file())
        self.assertEqual(recycled.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
