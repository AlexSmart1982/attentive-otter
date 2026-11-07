import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from action_plan import ActionPlan
from project import load_project, save_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2_project_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.first, self.second, self.destination = [self.root / name for name in ("sorted", "incoming", "destination")]
        for folder in (self.first, self.second, self.destination):
            folder.mkdir()
        self.a, self.b, self.c, self.d = map(str, (self.first / "event.jpg", self.second / "event.jpg",
                                                self.second / "better.png", self.second / "other.png"))
        rows = []
        for index, path in enumerate((self.a, self.b, self.c, self.d)):
            Path(path).write_bytes(b"unchanged photo "+bytes([index]))
            rows.append({"path": path, "group_id": 1, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
        self.review = self.root / "review.json"
        self.review.write_text(json.dumps({"source_root": str(self.first), "source_roots": [str(self.first), str(self.second)],
                                          "scan_report": str(self.root / "report.json"), "files": rows, "pairs": []}), encoding="utf-8")
        self.plan = ActionPlan(self.review)
        self.fingerprints = {r["path"]: r["sha256"] for r in rows}

    def assert_originals_unchanged(self):
        self.assertEqual(self.fingerprints, {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in self.fingerprints})

    def test_move_changes_only_virtual_location_and_undo_restores_it(self):
        self.plan.move_files(1, [self.a], self.destination)
        self.assertEqual(self.plan.effective_path(self.a), str(self.destination / "event.jpg"))
        self.assertEqual(self.plan.physical_path(self.a), self.a)
        self.assertEqual(self.plan.build()["actions"][0]["kind"], "move")
        self.assertEqual(self.plan.next_pair(1), (self.a, self.b))
        self.assert_originals_unchanged()
        self.plan.undo()
        self.assertEqual(self.plan.effective_path(self.a), self.a)
        self.assertFalse(self.plan.moves)

    def test_saved_review_restores_both_history_directions_and_old_files_still_load(self):
        self.plan.next_pair(1)
        self.plan.move_files(1, [self.c], self.destination)
        moved = self.plan.export_state()
        self.plan.choose(self.a, self.b, "replace_a")
        replaced = self.plan.export_state()
        self.plan.remove_selected(1, [self.d])
        removed = self.plan.export_state()
        self.plan.undo()
        self.plan.undo()
        filename = self.root / "history.cmp.json"
        save_project(self.plan, filename)
        restored, _ = load_project(filename)
        self.assertEqual(restored.export_state(), moved)
        self.assertEqual(len(restored.history), 1)
        self.assertEqual(len(restored.redo_history), 2)
        self.assertTrue(restored.redo())
        self.assertEqual(restored.export_state(), replaced)
        self.assertTrue(restored.redo())
        self.assertEqual(restored.export_state(), removed)
        data = json.loads(filename.read_text(encoding="utf-8"))
        del data["redo"]
        filename.write_text(json.dumps(data), encoding="utf-8")
        legacy, _ = load_project(filename)
        self.assertFalse(legacy.redo_history)
        self.assertTrue(legacy.undo())
        self.assert_originals_unchanged()

    def test_successful_apply_clears_both_history_directions(self):
        self.plan.choose(self.a, self.b, "remove_b")
        self.plan.remove_selected(1, [self.d])
        self.plan.undo()
        self.assertTrue(self.plan.history)
        self.assertTrue(self.plan.redo_history)
        self.plan.simulate_apply()
        self.assertFalse(self.plan.undo())
        self.assertFalse(self.plan.redo())
        self.assert_originals_unchanged()

    def test_same_named_files_cannot_move_to_one_destination(self):
        before = self.plan.export_state()
        with self.assertRaises(ValueError):
            self.plan.move_files(1, [self.a, self.b], self.destination)
        self.assertEqual(self.plan.export_state(), before)

    def test_replace_at_moved_target_and_move_replacement_source(self):
        self.plan.move_files(1, [self.a], self.destination)
        self.plan.choose(self.a, self.b, "replace_a")
        self.assertEqual(self.plan.effective_path(self.b), str(self.destination / "event.jpg"))
        self.assertEqual([a["kind"] for a in self.plan.build()["actions"]], ["replace"])
        another = self.root / "another"
        another.mkdir()
        self.plan.move_files(1, [self.b], another)
        self.assertEqual(self.plan.effective_path(self.b), str(another / "event.jpg"))
        self.plan.undo()
        self.assertEqual(self.plan.effective_path(self.b), str(self.destination / "event.jpg"))
        self.plan.simulate_apply()
        self.assertEqual(self.plan.effective_path(self.b), str(self.destination / "event.jpg"))
        self.assert_originals_unchanged()

    def test_latest_replacement_supersedes_source_move(self):
        self.plan.move_files(1, [self.b], self.destination)
        self.plan.choose(self.a, self.b, "replace_a")
        self.assertNotIn(self.b, self.plan.moves)
        self.assertEqual(self.plan.effective_path(self.b), self.a)
        self.plan.remove_selected(1, [self.c])
        self.assertEqual(self.plan.effective_path(self.b), self.a)

    def test_deletion_supersedes_move_and_undo_restores_both_decisions(self):
        self.plan.move_files(1, [self.c], self.destination)
        self.plan.remove_selected(1, [self.c])
        self.assertNotIn(self.c, self.plan.moves)
        self.plan.undo()
        self.assertIn(self.c, self.plan.moves)
        self.assertNotIn(self.c, self.plan.inactive)

    def test_embedded_project_restores_progress_without_review_or_scan(self):
        self.plan.move_files(1, [self.c], self.destination)
        self.plan.choose(self.a, self.b, "remove_a")
        self.plan.set_reference(self.d)
        view = {"group": "1", "a": self.d, "b": self.c, "sort_column": 6, "exif_open": True}
        filename = self.root / "session.cmp.json"
        save_project(self.plan, filename, view)
        self.review.unlink()
        restored, restored_view = load_project(filename)
        self.assertEqual(restored_view, view)
        self.assertEqual(restored.export_state(), self.plan.export_state())
        self.assertEqual(restored.build(), self.plan.build())
        self.assertEqual(len(restored.history), len(self.plan.history))
        restored.undo()
        self.assertNotIn(self.a, restored.inactive)
        self.assertIn(self.c, restored.moves)
        self.assert_originals_unchanged()

    def test_saved_pending_group_stays_but_applied_singleton_disappears(self):
        self.plan.remove_selected(1, [self.a, self.b, self.d])
        filename = self.root / "session.cmp.json"
        save_project(self.plan, filename)
        restored, _ = load_project(filename)
        self.assertEqual(restored.visible_groups(), ["1"])
        self.assertEqual(restored.live(1), [self.c])
        batch = restored.simulate_apply()
        self.assertEqual(batch["mode"], "simulation")
        self.assertFalse(batch["execution_enabled"])
        self.assertFalse(restored.visible_groups())
        self.assertFalse(restored.build()["actions"])
        self.assertIsNone(restored.simulate_apply())
        save_project(restored, filename)
        again, _ = load_project(filename)
        self.assertEqual(again.export_state(), restored.export_state())
        self.assertFalse(again.visible_groups())
        self.assert_originals_unchanged()

    def test_multiple_checkpoints_and_move_back_to_original_location(self):
        self.plan.move_files(1, [self.c], self.destination)
        self.plan.simulate_apply()
        self.plan.move_files(1, [self.c], self.second)
        self.plan.simulate_apply()
        self.assertEqual(self.plan.effective_path(self.c), self.c)
        self.assertNotIn(self.c, self.plan.committed_paths)
        self.assertEqual(len(self.plan.applied_batches), 2)
        self.assertFalse(self.plan.history)
        self.assert_originals_unchanged()

    def test_source_changed_after_review_blocks_entire_simulation(self):
        self.plan.remove_selected(1, [self.a, self.b])
        before = self.plan.export_state()
        Path(self.b).write_bytes(b"changed externally")
        with self.assertRaises(ValueError):
            self.plan.simulate_apply()
        self.assertEqual(self.plan.export_state(), before)
        self.assertEqual(hashlib.sha256(Path(self.a).read_bytes()).hexdigest(), self.fingerprints[self.a])

    def test_external_destination_collision_can_be_loaded_but_not_applied(self):
        self.plan.move_files(1, [self.c], self.destination)
        filename = self.root / "session.json"
        save_project(self.plan, filename)
        collision = self.destination / "better.png"
        collision.write_bytes(b"unrelated file")
        restored, _ = load_project(filename)
        before = restored.export_state()
        with self.assertRaises(ValueError):
            restored.simulate_apply()
        self.assertEqual(restored.export_state(), before)
        restored.cancel_moves([self.c])
        self.assertFalse(restored.build()["actions"])
        self.assertEqual(collision.read_bytes(), b"unrelated file")

    def test_atomic_save_failure_keeps_previous_project(self):
        filename = self.root / "session.json"
        save_project(self.plan, filename)
        before = filename.read_bytes()
        self.plan.remove_selected(1, [self.a])
        with patch("storage.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save_project(self.plan, filename)
        self.assertEqual(filename.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_legacy_plan_round_trip_includes_moves(self):
        self.plan.move_files(1, [self.c], self.destination)
        self.plan.choose(self.a, self.b, "replace_a")
        filename = self.root / "legacy-plan.json"
        self.plan.export(filename)
        restored = ActionPlan(self.review)
        restored.load(filename)
        self.assertEqual(restored.build(), self.plan.build())

    def test_project_cannot_overwrite_source_or_review(self):
        for filename in (self.first / "session.json", self.second / "session.json", self.review):
            with self.assertRaises(ValueError):
                save_project(self.plan, filename)
        self.assert_originals_unchanged()

    def test_replacement_can_be_replaced_again_after_checkpoint(self):
        self.plan.choose(self.a, self.b, "replace_a")
        self.plan.simulate_apply()
        self.plan.files[self.c]["detected_format"] = "JPEG"
        self.plan.choose(self.b, self.c, "replace_a")
        self.assertEqual(self.plan.effective_path(self.c), self.a)
        self.plan.simulate_apply()
        self.assertEqual(self.plan.effective_path(self.c), self.a)
        self.assert_originals_unchanged()

    def test_cancelled_verification_preserves_complete_plan(self):
        self.plan.remove_selected(1, [self.a, self.b])
        before = self.plan.export_state()
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            self.plan.simulate_apply(cancel_event=event)
        self.assertEqual(self.plan.export_state(), before)
        self.assert_originals_unchanged()


if __name__ == "__main__":
    unittest.main()
