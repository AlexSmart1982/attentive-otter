import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from action_plan import ActionPlan


class ActionPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2_actions_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = [self.root / "sorted", self.root / "incoming"]
        for source in self.sources:
            source.mkdir()
        self.a = str(self.sources[0] / "event.jpg")
        self.b = str(self.sources[1] / "original.heic")
        self.c = str(self.sources[1] / "better.png")
        self.d = str(self.sources[1] / "other.png")
        self.rows = []
        for i, path in enumerate((self.a, self.b, self.c, self.d)):
            Path(path).write_bytes(b"unchanged original " + bytes([i]))
            self.rows.append({"path": path, "group_id": 1,
                              "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
        self.document = self.root / "review.json"
        self.document.write_text(json.dumps({"source_root": str(self.sources[0]),
            "source_roots": [str(p) for p in self.sources], "files": self.rows,
            "scan_report": str(self.root / "report.json"), "pairs": []}), encoding="utf-8")
        self.plan = ActionPlan(self.document)

    def test_removal_is_global_and_next_comparison_uses_winner(self):
        self.assertEqual(self.plan.next_pair(1), (self.a, self.b))
        self.plan.choose(self.a, self.b, "remove_a")
        self.assertEqual(self.plan.next_pair(1), (self.b, self.c))
        self.assertNotIn(self.a, self.plan.live(1))
        self.plan.choose(self.b, self.c, "remove_a")
        self.assertEqual(self.plan.next_pair(1), (self.c, self.d))
        self.assertEqual(self.plan.inactive, {self.a, self.b})
        with self.assertRaises(ValueError):
            self.plan.choose(self.a, self.d, "remove_b")
        self.plan.undo()
        self.assertEqual(self.plan.next_pair(1), (self.b, self.c))
        self.plan.undo()
        self.assertEqual(self.plan.next_pair(1), (self.a, self.b))

    def test_folder_plan_export_roundtrip_and_undo_keep_selected_removal_policy(self):
        folder = self.root.resolve()/"discarded"
        folder.mkdir()
        self.plan.choose(self.a, self.b, "remove_a")
        self.plan.configure_deletion("folder", str(folder))
        self.plan.undo()
        self.assertEqual(self.plan.delete_mode, "folder")
        self.plan.redo()
        filename = self.root/"plan.json"
        exported = self.plan.export(filename)
        self.assertEqual(exported["actions"][0]["destination"], str(folder/Path(self.a).name))
        restored = ActionPlan(self.document)
        restored.load(filename)
        self.assertEqual(restored.build()["actions"], self.plan.build()["actions"])
        self.assertEqual((restored.delete_mode, restored.delete_folder), ("folder", str(folder)))

    def test_keep_both_skips_pair_and_retains_files(self):
        self.plan.choose(self.a, self.b, "keep_both")
        self.assertEqual(self.plan.next_pair(1), (self.a, self.c))
        self.assertEqual(self.plan.kept, {self.a, self.b})
        self.assertFalse(self.plan.inactive)
        self.plan.set_reference(self.b)
        self.assertEqual(self.plan.next_pair(1), (self.b, self.c))

    def test_multiple_undo_redo_restore_mixed_decisions_in_order(self):
        self.plan.next_pair(1)
        states = [self.plan.export_state()]
        self.plan.move_files(1, [self.c], self.sources[0])
        states.append(self.plan.export_state())
        self.plan.choose(self.a, self.b, "replace_a")
        states.append(self.plan.export_state())
        self.plan.choose(self.b, self.c, "remove_a")
        states.append(self.plan.export_state())
        self.plan.discard_group(1)
        states.append(self.plan.export_state())
        for expected in states[-2::-1]:
            self.assertTrue(self.plan.undo())
            self.assertEqual(self.plan.export_state(), expected)
        self.assertFalse(self.plan.undo())
        for expected in states[1:]:
            self.assertTrue(self.plan.redo())
            self.assertEqual(self.plan.export_state(), expected)
        self.assertFalse(self.plan.redo())
        for row in self.rows:
            self.assertEqual(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_failed_and_empty_actions_preserve_redo_but_new_choice_clears_it(self):
        self.plan.next_pair(1)
        self.plan.choose(self.a, self.b, "remove_b")
        self.plan.undo()
        with self.assertRaises(ValueError):
            self.plan.move_files(1, [self.a], self.root / "missing")
        self.plan.cancel_moves([])
        self.assertTrue(self.plan.redo_history)
        self.plan.choose(self.a, self.c, "keep_both")
        self.assertFalse(self.plan.redo())
        self.plan.set_reference(self.d, record=True)
        self.assertEqual(self.plan.references["1"], self.d)
        self.plan.undo()
        self.assertEqual(self.plan.references["1"], self.a)
        self.plan.redo()
        self.assertEqual(self.plan.references["1"], self.d)

    def test_replacement_chain_preserves_sorted_slot(self):
        self.plan.choose(self.a, self.b, "replace_a")
        self.assertEqual(self.plan.destination(self.a, self.b), str(self.sources[0] / "event.heic"))
        self.plan.choose(self.b, self.c, "replace_a")
        self.assertEqual(self.plan.replacements, {self.a: self.c})
        self.assertEqual(self.plan.recycles, {self.b})
        self.assertEqual(self.plan.next_pair(1), (self.c, self.d))
        action = next(a for a in self.plan.build()["actions"] if a["kind"] == "replace")
        self.assertEqual(action["destination"], str(self.sources[0] / "event.png"))
        self.assertEqual(action["old_file_destination"], "recycle_bin")
        self.plan.undo()
        self.assertEqual(self.plan.replacements, {self.a: self.b})
        self.assertFalse(self.plan.recycles)

    def test_remove_previous_replacement_source_retargets_intention(self):
        self.plan.choose(self.a, self.b, "replace_a")
        self.plan.choose(self.b, self.c, "remove_a")
        self.assertEqual(self.plan.replacements, {self.a: self.c})
        self.assertEqual(self.plan.recycles, {self.b})

    def test_collision_is_rejected_atomically(self):
        (self.sources[0] / "event.heic").write_bytes(b"unrelated")
        before = self.plan.build()
        with self.assertRaises(ValueError):
            self.plan.choose(self.a, self.b, "replace_a")
        self.assertEqual(self.plan.build(), before)
        self.assertFalse(self.plan.history)

    def test_one_source_cannot_move_into_two_slots(self):
        self.plan.choose(self.a, self.b, "replace_a")
        before = self.plan.build()
        with self.assertRaises(ValueError):
            self.plan.choose(self.b, self.c, "replace_b")
        self.assertEqual(self.plan.build(), before)

    def test_destination_uses_actual_format_for_mislabeled_source(self):
        self.plan.files[self.b]["detected_format"] = "PNG"
        self.assertEqual(self.plan.destination(self.a, self.b), str(self.sources[0] / "event.png"))

    def test_export_restore_and_reset_leave_originals_intact(self):
        self.plan.choose(self.a, self.b, "replace_a")
        self.plan.choose(self.b, self.c, "keep_both")
        exported = self.plan.export(self.root / "plan.json")
        self.assertFalse(exported["execution_enabled"])
        self.assertEqual(exported["mode"], "simulation")
        restored = ActionPlan(self.document)
        restored.load(self.root / "plan.json")
        self.assertEqual(restored.build(), self.plan.build())
        restored.reset_group(1)
        self.assertFalse(restored.inactive)
        self.assertFalse(restored.kept)
        self.assertEqual(restored.next_pair(1), (self.a, self.b))
        restored.undo()
        self.assertEqual(restored.build(), self.plan.build())
        for row in self.rows:
            self.assertEqual(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_export_cannot_write_into_either_source_or_review(self):
        for target in [s / "plan.json" for s in self.sources] + [self.document, self.root / "report.json"]:
            with self.assertRaises(ValueError):
                self.plan.export(target)

    def test_invalid_import_does_not_change_current_intentions(self):
        self.plan.choose(self.a, self.b, "remove_a")
        before = self.plan.build()
        data = json.loads(json.dumps(before))
        data["review_state"]["references"] = {"1": self.a}
        filename = self.root / "invalid.json"
        filename.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.plan.load(filename)
        self.assertEqual(self.plan.build(), before)

    def test_keep_checked_files_is_a_single_global_undoable_decision(self):
        before = self.plan.build()
        self.plan.keep_only(1, [self.b, self.d])
        self.assertEqual(set(self.plan.live(1)), {self.b, self.d})
        self.assertEqual(self.plan.recycles, {self.a, self.c})
        self.assertEqual(self.plan.kept, {self.b, self.d})
        self.assertEqual(self.plan.next_pair(1), (self.b, None))
        self.plan.undo()
        self.assertEqual(self.plan.build(), before)

    def test_bulk_decision_retargets_previous_replacement(self):
        self.plan.choose(self.a, self.b, "replace_a")
        before = self.plan.build()
        self.plan.keep_only(1, [self.c])
        self.assertEqual(self.plan.replacements, {self.a: self.c})
        self.assertEqual(self.plan.recycles, {self.b, self.d})
        self.plan.undo()
        self.assertEqual(self.plan.build(), before)

    def test_empty_selection_does_not_mean_delete_group(self):
        before = self.plan.build()
        with self.assertRaises(ValueError):
            self.plan.keep_only(1, [])
        self.assertEqual(self.plan.build(), before)

    def test_pair_remove_both_advances_to_remaining_files(self):
        self.plan.choose(self.a, self.b, "remove_both")
        self.assertEqual(self.plan.recycles, {self.a, self.b})
        self.assertFalse(self.plan.kept)
        self.assertEqual(self.plan.next_pair(1), (self.c, self.d))
        self.plan.choose(self.c, self.d, "remove_both")
        self.assertEqual(self.plan.next_pair(1), (None, None))
        self.plan.undo()
        self.assertEqual(self.plan.next_pair(1), (self.c, self.d))

    def test_discard_group_supersedes_replacements_and_can_be_restored(self):
        self.plan.choose(self.a, self.b, "replace_a")
        before = self.plan.build()
        self.plan.discard_group(1)
        self.assertFalse(self.plan.replacements)
        self.assertEqual(self.plan.recycles, {self.a, self.b, self.c, self.d})
        self.assertEqual(self.plan.next_pair(1), (None, None))
        self.plan.export(self.root / "discarded.json")
        restored = ActionPlan(self.document)
        restored.load(self.root / "discarded.json")
        self.assertEqual(restored.build(), self.plan.build())
        self.plan.undo()
        self.assertEqual(self.plan.build(), before)
        self.assertTrue(all(Path(p).exists() for p in (self.a, self.b, self.c, self.d)))

    def test_remove_both_cannot_leave_replacement_without_source(self):
        self.plan.choose(self.a, self.b, "replace_a")
        before = self.plan.build()
        with self.assertRaises(ValueError):
            self.plan.choose(self.b, self.c, "remove_both")
        self.assertEqual(self.plan.build(), before)

    def test_bulk_replacement_conflict_is_rejected_as_one_transaction(self):
        self.plan.choose(self.a, self.b, "replace_a")
        self.plan.choose(self.c, self.d, "replace_a")
        before = self.plan.build()
        with self.assertRaises(ValueError):
            self.plan.keep_only(1, [self.b])
        self.assertEqual(self.plan.build(), before)

    def test_discard_group_does_not_affect_other_groups(self):
        document = json.loads(self.document.read_text(encoding="utf-8"))
        document["files"][-1]["group_id"] = 2
        self.document.write_text(json.dumps(document), encoding="utf-8")
        plan = ActionPlan(self.document)
        plan.discard_group(1)
        self.assertEqual(plan.live(2), [self.d])
        self.assertNotIn(self.d, plan.inactive)

    def test_remove_selected_only_changes_checked_files_and_undoes_once(self):
        self.plan.choose(self.a, self.b, "keep_both")
        before = self.plan.build()
        self.plan.remove_selected(1, [self.a, self.c])
        self.assertEqual(self.plan.recycles, {self.a, self.c})
        self.assertEqual(self.plan.kept, {self.b})
        self.assertEqual(self.plan.status(self.d), "undecided")
        self.assertEqual(self.plan.next_pair(1), (self.b, self.d))
        self.plan.undo()
        self.assertEqual(self.plan.build(), before)

    def test_remove_selection_can_cover_entire_active_group(self):
        self.plan.remove_selected(1, [self.a, self.b, self.c, self.d])
        self.assertEqual(self.plan.next_pair(1), (None, None))
        self.assertEqual(self.plan.recycles, set(self.plan.files))
        self.assertTrue(all(Path(p).exists() for p in self.plan.files))

    def test_invalid_selection_and_replacement_source_do_not_change_intentions(self):
        self.plan.choose(self.a, self.b, "replace_a")
        before = self.plan.build()
        for selected in ([], [self.a], [self.b, self.c], [self.root / "not-in-group.png"]):
            with self.assertRaises(ValueError):
                self.plan.remove_selected(1, selected)
            self.assertEqual(self.plan.build(), before)


if __name__ == "__main__":
    unittest.main()
