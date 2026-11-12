from system_trash import FILE_SYSTEM_LABEL
"""Offscreen integration checks with file application in disposable fixtures."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import shutil
import threading
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PyQt5.QtCore import Qt, QPoint, QEvent
from PyQt5.QtGui import QFont, QFontDatabase, QKeyEvent, QMovie
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from app_ui import MainWindow
from about_ui import APP_NAME
from preferences import Preferences
from settings_ui import SettingsDialog
from pipeline import run_pipeline
from scan import ScanCancelled
from action_plan import ActionPlan
from project import load_project
from execution import apply_files


class AppUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not QFontDatabase().families():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
            cls.app.setFont(QFont("Arial", 10))

    def setUp(self):
        # Exercise real filesystem changes while keeping UI test trash inspectable.
        temporary = tempfile.TemporaryDirectory(prefix="v2_ui_trash_")
        self.addCleanup(temporary.cleanup)
        self.trash = Path(temporary.name).resolve()
        def recycle(path):
            source = Path(path).resolve()
            self.assertIn(Path(tempfile.gettempdir()).resolve(), source.parents)
            self.assertTrue(source.is_file())
            target = self.trash/(uuid.uuid4().hex+"-"+source.name)
            shutil.move(str(source), target)
            return str(target)
        backend = patch("execution.recycle_file", side_effect=recycle)
        backend.start()
        self.addCleanup(backend.stop)

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("UI operation timed out")
            time.sleep(.01)
        self.app.processEvents()

    def close_window(self, window):
        if window.apply_thread:
            window.apply_thread.cancel_event.set()
            self.wait_for(lambda: window.apply_thread is None)
        if window.scan_thread:
            window.stop_scan()
            self.wait_for(lambda: window.scan_thread is None)
        window.dirty = False
        # Disposal must not save stale windows over another window's checkpoint.
        # Persistence and close prompts are exercised explicitly by their tests.
        window.project_path = None
        window.close()

    def test_scan_global_choices_undo_and_simulated_apply(self):
        with tempfile.TemporaryDirectory(prefix="v2_ui_") as folder:
            root = Path(folder)
            sources = [root / "sorted", root / "incoming"]
            for source in sources:
                source.mkdir()
            pixels = np.random.default_rng(9).integers(0, 256, (96, 72, 3), dtype=np.uint8)
            paths = [sources[0] / "a.png", sources[1] / "b.png", sources[1] / "c.png"]
            for path in paths:
                Image.fromarray(pixels).save(path)
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            window = MainWindow(sources=sources, state_dir=root / "state")
            window.show()
            try:
                with patch("app_ui.QMessageBox.warning", side_effect=AssertionError("Unexpected UI warning")), \
                     patch("app_ui.QMessageBox.information", side_effect=AssertionError("Unexpected UI message")):
                    window.start_scan()
                    self.wait_for(lambda: window.scan_thread is None and not window.loading_preview)
                    self.assertIsNotNone(window.model)
                    self.assertEqual(len(window.model.files), 3)
                    self.assertTrue(all(b.isEnabled() for b in window.pair_buttons))
                    a, b = window.a, window.b
                    window.pair_buttons[2].click()  # Keep B: remove A everywhere.
                    self.wait_for(lambda: not window.loading_preview)
                    self.assertIn(a, window.model.inactive)
                    self.assertEqual(window.a, b)
                    self.assertNotIn(a, (window.a, window.b))
                    self.assertEqual(window.files.rowCount(), 2)
                    window.undo()
                    self.wait_for(lambda: not window.loading_preview)
                    self.assertEqual((window.a, window.b), (a, b))
                    self.assertEqual(window.files.rowCount(), 3)
                    window.pair_buttons[4].click()  # Keep B in A's sorted location.
                    self.wait_for(lambda: not window.loading_preview)
                    export = root / "archive.cmp.json"
                    with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(export), "")):
                        self.assertTrue(window.save_project())
                    saved = json.loads(export.read_text(encoding="utf-8"))
                    self.assertEqual(saved["mode"], "comparison")
                    restored, view = load_project(export)
                    self.assertEqual(restored.build()["actions"][0]["kind"], "replace")
                    self.assertEqual(restored.export_state(), window.model.export_state())
                    self.assertEqual((view["a"], view["b"]), (window.a, window.b))
                    self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
            finally:
                self.close_window(window)

    def fixture_window(self, root, names=("0.png", "1.png", "2.png", "3.png")):
        source = root / "photos"
        source.mkdir()
        rows = []
        for index, name in enumerate(names):
            path = source / name
            Image.new("RGB", (72, 96), (index*30, 100, 200)).save(path)
            rows.append({"group_id": 1, "path": str(path), "filename": path.name,
                "stored_width": 72, "stored_height": 96, "detected_format": "PNG",
                "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        document = root / "review.json"
        document.write_text(json.dumps({"source_root": str(source), "files": rows, "pairs": [],
            "scan_report": str(root / "report.json")}), encoding="utf-8")
        window = MainWindow(review=document, state_dir=root / "state")
        window.show()
        self.wait_for(lambda: not window.loading_preview)
        return window, rows

    def test_delete_card_only_removes_that_file_without_keeping_the_other(self):
        for mode in ("recycle_bin", "folder"):
            for side in (0, 1):
                with self.subTest(mode=mode, side=side), tempfile.TemporaryDirectory(prefix="v2_card_delete_") as folder:
                    root = Path(folder)
                    window, rows = self.fixture_window(root)
                    try:
                        discarded = root/"discarded"
                        discarded.mkdir()
                        window.model.configure_deletion(mode, str(discarded) if mode == "folder" else "")
                        window.update_actions()
                        a,b = window.a,window.b
                        target,other = ((a,b) if side == 0 else (b,a))
                        seen = window.model.seen.copy()
                        before_other = window.model.status(other)
                        window.delete_side_buttons[side].click()
                        self.wait_for(lambda: not window.loading_preview)
                        self.assertEqual(window.model.recycles,{target})
                        self.assertEqual(window.model.status(other),before_other)
                        self.assertNotIn(other,window.model.kept)
                        self.assertEqual(window.model.seen,seen)
                        self.assertTrue(all(Path(row["path"]).is_file() for row in rows))
                        window.undo()
                        self.wait_for(lambda: not window.loading_preview)
                        self.assertNotIn(target,window.model.inactive)
                        self.assertEqual(window.model.status(other),before_other)
                        window.redo()
                        self.wait_for(lambda: not window.loading_preview)
                        self.assertIn(target,window.model.recycles)
                        self.assertNotIn(other,window.model.kept)
                        self.assertEqual(window.model.delete_mode,mode)
                    finally:
                        self.close_window(window)

    def test_delete_card_is_disabled_while_preview_is_loading_and_without_model(self):
        with tempfile.TemporaryDirectory(prefix="v2_delete_loading_") as folder:
            window,rows = self.fixture_window(Path(folder))
            try:
                window.loading_preview = True
                window.update_actions()
                self.assertTrue(all(not button.isEnabled() for button in window.delete_side_buttons))
                before = window.model.export_state()
                window.delete_side(0)
                self.assertEqual(window.model.export_state(),before)
                window.loading_preview = False
                window.model = None
                window.update_actions()
                self.assertTrue(all(not button.isEnabled() for button in window.delete_side_buttons))
            finally:
                self.close_window(window)

    def test_checked_deletions_delete_both_and_unwanted_group_are_undoable(self):
        with tempfile.TemporaryDirectory(prefix="v2_bulk_ui_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                self.assertFalse(window.checked_files)  # Recommendations never tick boxes.
                self.assertFalse(window.model.build()["actions"])
                self.assertFalse(window.delete_selected_button.isEnabled())
                window.files.item(0, 0).setCheckState(Qt.Checked)
                window.files.item(2, 0).setCheckState(Qt.Checked)
                self.assertTrue(window.delete_selected_button.isEnabled())
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(set(window.model.live(1)), {rows[i]["path"] for i in (1, 3)})
                self.assertEqual(window.model.recycles, {rows[i]["path"] for i in (0, 2)})
                self.assertEqual({window.a, window.b}, {rows[i]["path"] for i in (1, 3)})
                self.assertTrue(all(window.model.status(rows[i]["path"]) == "undecided" for i in (1, 3)))
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.files.rowCount(), 4)
                window.pair_buttons[5].click()  # Delete both, without removing others in group.
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(len(window.model.live(1)), 2)
                self.assertEqual(len(window.model.recycles), 2)
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                window.discard_group_button.click()
                self.assertFalse(window.model.live(1))
                self.assertEqual(len(window.model.recycles), 4)
                self.assertIsNone(window.a)
                self.assertFalse(window.original_buttons[0].isEnabled())
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                for row in rows:
                    self.assertEqual(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest(), row["sha256"])
            finally:
                self.close_window(window)

    def test_column_sorting_uses_typed_values_and_keeps_file_identity(self):
        with tempfile.TemporaryDirectory(prefix="v2_sort_ui_") as folder:
            window, rows = self.fixture_window(Path(folder), ("z.png", "a.png", "10.png", "2.png"))
            try:
                dimensions = [(10, 100), (2, 300), (300, 1), (3, 300)]
                sizes = [20000, 900, 1000, 1001]
                dates = ["2020:01:01 00:30:00+03:00", "2019-12-31T22:00:00Z", "2019:12:31 20:00:00", "invalid"]
                tags = [20, 9, 100, 2]
                for index, row in enumerate(rows):
                    window.model.files[row["path"]].update(stored_width=dimensions[index][0],
                        stored_height=dimensions[index][1], bytes=sizes[index], captured_at=dates[index], exif_tags=tags[index])
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                pair = (window.a, window.b)
                paths = [r["path"] for r in rows]
                expected_orders = {1: [2, 3, 1, 0], 4: [2, 1, 3, 0], 5: [1, 2, 3, 0],
                                   6: [3, 2, 0, 1], 8: [3, 1, 0, 2]}
                for column, ascending in expected_orders.items():
                    for order, indices in ((Qt.AscendingOrder, ascending), (Qt.DescendingOrder, ascending[::-1])):
                        window.files.sortItems(column, order)
                        actual = [window.files.item(i, 0).data(Qt.UserRole) for i in range(4)]
                        self.assertEqual(actual, [paths[i] for i in indices], (column, order))
                        self.assertEqual((window.a, window.b), pair)
                        for i, path in enumerate(actual):
                            self.assertTrue(all(window.files.item(i, j).data(Qt.UserRole) == path for j in range(10)))
                # Check an object after sorting, then refresh and change the sort again.
                target = paths[2]
                item = next(window.files.item(i, 0) for i in range(4)
                            if window.files.item(i, 0).data(Qt.UserRole) == target)
                item.setCheckState(Qt.Checked)
                window.files.sortItems(4, Qt.DescendingOrder)
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual([window.files.item(i, 0).data(Qt.UserRole) for i in range(4)],
                                 [paths[i] for i in expected_orders[4][::-1]])
                window.files.sortItems(0, Qt.DescendingOrder)
                self.assertEqual(window.files.item(0, 0).data(Qt.UserRole), target)
                self.assertEqual(window.checked_files, {target})
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.model.recycles, {target})
                self.assertEqual(set(window.model.live(1)), set(paths)-{target})
                self.assertTrue(all(Path(path).exists() for path in paths))
            finally:
                self.close_window(window)

    def test_primary_action_uses_one_checkbox_and_not_the_highlighted_row(self):
        with tempfile.TemporaryDirectory(prefix="v2_primary_ui_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                self.assertFalse(window.reference_button.isEnabled())
                window.files.item(2, 0).setCheckState(Qt.Checked)
                window.files.selectRow(3)  # Preview selection is independent of action targets.
                self.wait_for(lambda: not window.loading_preview)
                self.assertTrue(window.reference_button.isEnabled())
                window.reference_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, rows[2]["path"])
                self.assertFalse(window.model.build()["actions"])
                window.files.item(0, 0).setCheckState(Qt.Checked)
                self.assertFalse(window.reference_button.isEnabled())
                window.make_reference()
                self.assertEqual(window.a, rows[2]["path"])
            finally:
                self.close_window(window)

    def test_reference_starts_at_sorted_first_file_and_follows_the_survivor(self):
        with tempfile.TemporaryDirectory(prefix="v2_reference_ui_") as folder:
            window, rows = self.fixture_window(Path(folder), ("z.png", "a.png", "b.png", "c.png"))
            try:
                self.assertEqual(window.a, rows[1]["path"])
                self.assertEqual(window.files.item(0, 0).data(Qt.UserRole), window.a)
                self.assertIn("опорный", window.titles[0].text())
                old_reference, winner = window.a, window.b
                window.choose("remove_a")
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, winner)
                self.assertIn(old_reference, window.model.inactive)
                self.assertEqual(window.model.references["1"], winner)
                window.files.sortItems(1, Qt.DescendingOrder)
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, winner)
                self.assertNotIn(old_reference, (window.a, window.b))
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, old_reference)
            finally:
                self.close_window(window)

    def test_history_icons_return_to_exact_pair_across_groups_and_resume_redo(self):
        with tempfile.TemporaryDirectory(prefix="v2_history_ui_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root, tuple(f"{n}.png" for n in range(8)))
            reopened = None
            try:
                self.assertTrue(window.undo_button.isVisible())
                self.assertTrue(window.redo_button.isVisible())
                self.assertFalse(window.undo_button.isEnabled())
                self.assertFalse(window.redo_button.isEnabled())
                self.assertFalse(window.undo_button.icon().isNull())
                self.assertFalse(window.redo_button.icon().isNull())
                document = window.model.document
                for index, row in enumerate(document["files"]):
                    row["group_id"] = 1 if index < 2 else 2 if index < 4 else 3
                window.attach_model(ActionPlan(document))
                window.current_group = "3"
                window.refresh()
                self.wait_for(lambda: not window.loading_preview)
                window.files.selectRow(3)  # Compare 4.png with 7.png, skipping the queue's 5.png.
                self.wait_for(lambda: not window.loading_preview)
                pair = window.a, window.b
                self.assertEqual(pair, (rows[4]["path"], rows[7]["path"]))
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                window.navigate_group(-1)  # Initialize a previously untouched group after the decision.
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.current_group, "2")
                window.undo_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.current_group, "3")
                self.assertEqual((window.a, window.b), pair)
                self.assertTrue(window.redo_button.isEnabled())
                self.assertFalse(window.model.build()["actions"])
                filename = root / "archive.cmp.json"
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    self.assertTrue(window.save_project())
                reopened = MainWindow(state_dir=root / "state")
                reopened.show()
                reopened.set_project(filename)
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual((reopened.a, reopened.b), pair)
                self.assertTrue(reopened.redo_button.isEnabled())
                reopened.redo_button.click()
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.current_group, "3")
                self.assertEqual(reopened.model.recycles, {pair[1]})
                reopened.choose("remove_a")
                self.wait_for(lambda: not reopened.loading_preview)
                final = reopened.model.export_state()
                for _ in range(2):
                    reopened.undo_button.click()
                    self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual((reopened.a, reopened.b), pair)
                for _ in range(2):
                    reopened.redo_button.click()
                    self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.model.export_state(), final)
                self.assertFalse(reopened.redo_button.isEnabled())
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_initial_reference_uses_rule_priority_and_advice_has_a_subtle_marker(self):
        with tempfile.TemporaryDirectory(prefix="v2_ranked_reference_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                document = window.model.document
                document["files"][2].update(stored_width=8000, stored_height=6000)
                document["files"][3].update(detected_format="HEIF", stored_width=36, stored_height=48)
                window.attach_model(ActionPlan(document))
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, rows[3]["path"])  # HEIC precedes resolution.
                self.assertTrue(window.photo_cards[0].property("recommended"))
                self.assertFalse(window.photo_cards[1].property("recommended"))
                item = next(window.files.item(i, 1) for i in range(4)
                            if window.files.item(i, 0).data(Qt.UserRole) == window.a)
                self.assertEqual(item.background().color().name(), window.colors["recommended_bg"])
                window.model.set_reference(rows[1]["path"])
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, rows[1]["path"])  # Respect the user's explicit choice.
                window.settings.suggest_best = False
                window.model.references.clear()
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, rows[3]["path"])  # Ranking still works with advice hidden.
                self.assertTrue(all(not card.property("recommended") for card in window.photo_cards))
                window.settings.rules[0], window.settings.rules[1] = window.settings.rules[1], window.settings.rules[0]
                window.model.references.clear()
                window.refresh_group()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.a, rows[2]["path"])  # Custom order: resolution before HEIC.
            finally:
                self.close_window(window)

    def test_skip_advances_without_a_completion_screen_and_resume_stays_on_next_pair(self):
        with tempfile.TemporaryDirectory(prefix="v2_skip_resume_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root)
            reopened = None
            try:
                document = window.model.document
                for row in document["files"][2:]:
                    row["group_id"] = 2
                window.attach_model(ActionPlan(document))
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.pair_buttons[0].text(), "Пропустить")
                window.pair_buttons[0].click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.current_group, "2")
                self.assertTrue(window.comparison_page.isVisible())
                self.assertTrue(all(view.isVisible() for view in window.views))
                self.assertLess(window.next_button.width(), 48)
                self.assertIn("просмотрена", window.group_tree.topLevelItem(0).text(0))
                self.assertIn("50%", window.review_progress.text())
                self.assertFalse(window.model.build()["actions"])
                filename = root / "archive.cmp.json"
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    self.assertTrue(window.save_project())
                reopened = MainWindow(state_dir=root / "state")
                reopened.show()
                reopened.set_project(filename)
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.current_group, "2")
                self.assertTrue(reopened.comparison_page.isVisible())
                self.assertIn("50%", reopened.review_progress.text())
                self.assertEqual(reopened.model.export_state(), window.model.export_state())
                reopened.pair_buttons[0].click()
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.current_page, "plan")
                self.assertIn("100%", reopened.review_progress.text())
                reopened.undo_button.click()
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.current_group, "2")
                self.assertEqual(reopened.current_page, "review")
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_long_paths_wrap_and_both_photo_grids_keep_matching_fields(self):
        with tempfile.TemporaryDirectory(prefix="v2_paths_ui_") as folder:
            root = Path(folder).resolve()
            window, rows = self.fixture_window(root)
            try:
                destination = root / ("длинное_имя_папки_"*4) / ("поездка_с_семьёй_"*3)
                destination.mkdir(parents=True)
                first = rows[0]["path"]
                window.model.files[first].update(captured_at="2020:08:08 17:56:30", camera="iPhone 7")
                window.files.setColumnWidth(9, 350)
                window.move_selected({first}, destination)
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.path_fields[0].toPlainText(), str(destination / "0.png"))
                row = next(i for i in range(window.files.rowCount()) if window.files.item(i, 0).data(Qt.UserRole) == first)
                self.assertEqual(window.files.item(row, 9).text(), str(destination))
                self.assertGreater(window.files.rowHeight(row), 34)
                self.assertEqual(window.files.columnWidth(9), 350)
                self.assertEqual(window.files.horizontalHeader().visualIndex(9), 2)
                for table in window.parameter_tables:
                    self.assertEqual((table.rowCount(), table.columnCount()), (4, 4))
                    self.assertEqual(table.item(0, 0).text(), "Формат")
                    self.assertEqual(table.item(0, 2).text(), "Разрешение")
                    self.assertEqual(table.item(1, 2).text(), "Съёмка")
                self.assertEqual(window.parameter_tables[0].item(1, 3).text(), "08.08.2020\n17:56:30")
                self.assertEqual(window.parameter_tables[0].item(2, 1).text(), "iPhone 7")
                window.show_plan()
                self.assertGreater(window.plan_table.rowHeight(0), 34)
                self.assertGreaterEqual(window.plan_table.columnWidth(2), 350)
                self.assertTrue(Path(first).exists())
                self.assertFalse((destination / "0.png").exists())
            finally:
                self.close_window(window)

    def test_removal_folder_settings_update_ui_apply_and_saved_comparison(self):
        with tempfile.TemporaryDirectory(prefix="v2_folder_ui_") as folder:
            root = Path(folder).resolve()
            discarded = root/"Отобранные для удаления"
            discarded.mkdir()
            window, rows = self.fixture_window(root)
            reopened = None
            try:
                dialog = SettingsDialog(window.settings)
                self.assertFalse(dialog.delete_folder.isEnabled())
                dialog.delete_mode.setCurrentIndex(dialog.delete_mode.findData("folder"))
                self.assertTrue(dialog.delete_folder.isEnabled())
                with patch("settings_ui.QMessageBox.warning") as warning:
                    dialog.accept()
                warning.assert_called_once()
                self.assertNotEqual(dialog.result(), QDialog.Accepted)
                with patch("settings_ui.QFileDialog.getExistingDirectory", return_value=str(discarded)):
                    dialog.browse_delete_folder.click()
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                settings = dialog.value()
                with patch("app_ui.SettingsDialog") as factory:
                    factory.return_value.exec_.return_value = QDialog.Accepted
                    factory.return_value.value.return_value = settings
                    window.open_settings()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.model.delete_mode, "folder")
                self.assertEqual(Preferences.load(root/"state"/"settings.json").delete_folder, str(discarded))
                self.assertIn(str(discarded), window.pair_buttons[1].toolTip())
                self.assertIn(str(discarded), window.pair_buttons[3].toolTip())
                self.assertNotIn("корзин", window.pair_buttons[3].toolTip())
                removed = window.b
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                self.assertIn("В папку: 1", window.plan_counts.text())
                self.assertIn(str(discarded), window.plan_description.text())
                window.show_plan()  # The plan table is now populated on demand.
                removal_row = next(i for i in range(window.plan_table.rowCount())
                                   if window.plan_table.item(i, 0).text() == "В папку")
                target = discarded/Path(removed).name
                self.assertEqual(window.plan_table.item(removal_row, 2).text(), str(target))
                window.project_path = root/"comparison.cmp.json"
                self.assertTrue(window.save_project())
                # A different global default must not change a restored comparison's policy.
                reopened = MainWindow(state_dir=root/"other-state")
                reopened.set_project(window.project_path)
                reopened.show()
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertEqual(reopened.settings.delete_mode, "recycle_bin")
                self.assertEqual(reopened.model.delete_mode, "folder")
                self.assertTrue(reopened.apply_changes())
                self.wait_for(lambda: not reopened.loading_preview)
                self.assertFalse(Path(removed).exists())
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), window.model.files[removed]["sha256"])
                self.assertFalse(list(self.trash.iterdir()))
                self.assertIn("В папку: 1", reopened.outcome_body.text())
                self.assertIn(str(discarded), reopened.outcome_detail.text())
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_original_opens_full_source_path_and_settings_persist_without_actions(self):
        with tempfile.TemporaryDirectory(prefix="v2_settings_ui_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root)
            try:
                self.assertEqual(window.original_buttons[1].text(), "Полный размер")
                self.assertEqual(window.pair_buttons[3].text(), "Перенести A→B")
                self.assertIn("B будет удалён в корзину", window.pair_buttons[3].toolTip())
                self.assertIn("move-a-to-b.svg", window.pair_buttons[3].toolTip())
                with patch("app_ui.QDesktopServices.openUrl", return_value=True) as opening:
                    window.original_buttons[1].click()
                    self.assertEqual(Path(opening.call_args.args[0].toLocalFile()), Path(window.b))
                dialog = SettingsDialog(window.settings)
                dialog.cpu.setValue(15)
                dialog.rules.setCurrentRow(1)
                dialog.move_rule(-1)  # Resolution before HEIC.
                dialog.rules.item(1).setCheckState(Qt.Unchecked)
                dialog.date_source.setCurrentIndex(dialog.date_source.findData("modified_at"))
                dialog.highlight.setChecked(False)
                dialog.suggest.setChecked(False)
                candidate = dialog.value()
                dialog.close()
                self.assertEqual(candidate.rules[0]["key"], "resolution")
                self.assertFalse(candidate.rules[1]["enabled"])
                with patch("app_ui.SettingsDialog") as factory:
                    factory.return_value.exec_.return_value = QDialog.Accepted
                    factory.return_value.value.return_value = candidate
                    window.open_settings()
                self.wait_for(lambda: not window.loading_preview)
                restored = Preferences.load(root / "state" / "settings.json")
                self.assertEqual(restored.cpu_percent, 15)
                self.assertEqual(window.setup_cpu.value(), 15)
                window.setup_cpu.setValue(65)
                self.assertEqual(window.settings.cpu_percent, 65)
                self.assertEqual(Preferences.load(root / "state" / "settings.json").cpu_percent, 65)
                synced_dialog = SettingsDialog(window.settings)
                self.assertEqual(synced_dialog.cpu.value(), 65)
                synced_dialog.close()
                self.assertEqual(restored.rules, candidate.rules)
                self.assertEqual(restored.date_source, "modified_at")
                self.assertFalse(window.suggested)
                self.assertFalse(window.best_fields)
                self.assertFalse(window.model.build()["actions"])
                self.assertFalse(window.dirty)
                self.assertFalse(window.checked_files)
            finally:
                self.close_window(window)

    def test_empty_result_has_no_active_comparison(self):
        with tempfile.TemporaryDirectory(prefix="v2_empty_ui_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            document = root / "review.json"
            document.write_text(json.dumps({"source_root": str(source), "files": [], "pairs": [],
                "scan_report": str(root / "report.json")}), encoding="utf-8")
            window = MainWindow(review=document, state_dir=root / "state")
            try:
                self.assertIsNone(window.a)
                self.assertIsNone(window.b)
                self.assertEqual(window.files.rowCount(), 0)
                self.assertTrue(all(not b.isEnabled() for b in window.pair_buttons))
                self.assertEqual(window.current_page, "outcome")
                self.assertIn("не найдено", window.outcome_title.text())
            finally:
                self.close_window(window)

    def test_project_restores_virtual_move_current_pair_and_open_exif_without_scan(self):
        with tempfile.TemporaryDirectory(prefix="v2_session_ui_") as folder:
            root = Path(folder).resolve()
            window, rows = self.fixture_window(root)
            reopened = None
            try:
                destination = root / "destination"
                destination.mkdir()
                window.add_source(destination)
                moved = rows[0]["path"]
                window.move_selected({moved}, destination)
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(Path(window.model.effective_path(moved)).parent, destination)
                self.assertIn(str(destination), window.path_fields[0].toPlainText())
                with patch("app_ui.QDesktopServices.openUrl", return_value=True) as opening:
                    window.open_original(0)
                    self.assertEqual(Path(opening.call_args.args[0].toLocalFile()), Path(moved))
                window.files.selectRow(3)
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.b, rows[3]["path"])
                window.files.item(2, 0).setCheckState(Qt.Checked)
                window.files.sortItems(5, Qt.DescendingOrder)
                window.files.setColumnWidth(9, 440)
                window.plan_table.setColumnWidth(1, 575)
                window.exif_button.setChecked(True)
                self.wait_for(lambda: not window.exif_loading)
                filename = root / "project.cmp.json"
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    self.assertTrue(window.save_project())
                (root / "review.json").unlink()
                with patch("app_ui.run_pipeline", side_effect=AssertionError("Unexpected rescan")):
                    reopened = MainWindow(state_dir=root / "state")
                    reopened.show()
                    reopened.set_project(filename)
                    self.wait_for(lambda: not reopened.loading_preview and not reopened.exif_loading)
                self.assertEqual((reopened.a, reopened.b), (window.a, window.b))
                self.assertEqual(reopened.checked_files, window.checked_files)
                self.assertEqual(reopened.sources.count(), 2)
                self.assertEqual(reopened.files.item(reopened.files.currentRow(), 0).data(Qt.UserRole), reopened.b)
                self.assertEqual(reopened.files.horizontalHeader().sortIndicatorSection(), 5)
                self.assertEqual(reopened.files.columnWidth(9), 440)
                self.assertEqual(reopened.plan_table.columnWidth(1), 575)
                self.assertTrue(reopened.exif_button.isChecked())
                self.assertFalse(reopened.exif_panel.isHidden())
                self.assertEqual(reopened.model.effective_path(moved), str(destination / "0.png"))
                self.assertEqual(reopened.model.physical_path(moved), moved)
                self.assertIn("EXIF", reopened.exif_names.text())
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_real_apply_updates_project_and_hides_completed_group_only_after_apply(self):
        with tempfile.TemporaryDirectory(prefix="v2_apply_ui_") as folder:
            root = Path(folder).resolve()
            window, rows = self.fixture_window(root)
            reopened = None
            try:
                for index in (0, 1, 2):
                    window.files.item(index, 0).setCheckState(Qt.Checked)
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.group_tree.topLevelItemCount(), 1)
                self.assertIn("1", window.group_tree.topLevelItem(0).text(0))
                filename = root / "project.cmp.json"
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    window.save_project()
                self.assertTrue(window.apply_changes())
                self.assertEqual(window.group_tree.topLevelItemCount(), 0)
                self.assertFalse(window.model.build()["actions"])
                self.assertFalse(window.apply_button.isEnabled())
                data = json.loads(filename.read_text(encoding="utf-8"))
                self.assertEqual(len(data["state"]["applied_batches"]), 1)
                reopened = MainWindow(state_dir=root / "state")
                reopened.set_project(filename)
                self.assertEqual(reopened.group_tree.topLevelItemCount(), 0)
                self.assertFalse(reopened.apply_changes())
                self.assertTrue(all(not Path(row["path"]).exists() for row in rows[:3]))
                self.assertEqual(hashlib.sha256(Path(rows[3]["path"]).read_bytes()).hexdigest(), rows[3]["sha256"])
                self.assertEqual(len(list(self.trash.iterdir())), 3)
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_failed_apply_checkpoint_does_not_discard_pending_decisions(self):
        with tempfile.TemporaryDirectory(prefix="v2_apply_failure_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                window.files.item(0, 0).setCheckState(Qt.Checked)
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                window.choose("keep_both")
                self.wait_for(lambda: not window.loading_preview)
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                before = window.model.export_state()
                redo_before = [window.model.export_state(s) for s in window.model.redo_history]
                with patch("project.save_project", side_effect=OSError("disk failure")), \
                     patch("workspace_session.QMessageBox.warning"):
                    self.assertFalse(window.apply_changes())
                self.assertEqual(window.model.export_state(), before)
                self.assertEqual([window.model.export_state(s) for s in window.model.redo_history], redo_before)
                self.assertTrue(window.redo_button.isEnabled())
                self.assertEqual(window.group_tree.topLevelItemCount(), 1)
                self.assertTrue(window.apply_button.isEnabled())
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                self.close_window(window)

    def test_system_theme_changes_live_and_full_exif_shows_both_files(self):
        with tempfile.TemporaryDirectory(prefix="v2_theme_exif_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                for index in (0, 1):
                    exif = Image.Exif()
                    exif[272] = f"Camera {index}"
                    Image.new("RGB", (72, 96), "green").save(rows[index]["path"], exif=exif)
                window.exif_button.setChecked(True)
                self.wait_for(lambda: not window.exif_loading)
                self.assertGreater(window.exif_tree.topLevelItemCount(), 0)
                camera = window.exif_tree.topLevelItem(0).child(0)
                self.assertEqual(camera.text(1), "Camera 0")
                self.assertEqual(camera.text(2), "Camera 1")
                self.assertTrue(all(view.width() > 0 for view in window.views))
                pair = window.a, window.b
                with patch("theme.system_dark", return_value=False):
                    window.theme.set_mode("system")
                    self.assertEqual(window.theme.current, "light")
                with patch("theme.system_dark", return_value=True):
                    window.theme.update()
                    self.assertEqual(window.theme.current, "dark")
                self.assertEqual((window.a, window.b), pair)
                self.wait_for(lambda: not window.exif_loading)
            finally:
                self.close_window(window)

    def test_apply_button_checks_snapshot_in_background_then_saves_checkpoint(self):
        with tempfile.TemporaryDirectory(prefix="v2_async_apply_") as folder:
            root = Path(folder).resolve()
            window, rows = self.fixture_window(root)
            try:
                window.files.item(0, 0).setCheckState(Qt.Checked)
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                window.apply_button.click()
                self.assertIsNotNone(window.apply_thread)
                self.assertFalse(window.delete_selected_button.isEnabled())
                self.assertFalse(window.save_button.isEnabled())
                self.wait_for(lambda: window.apply_thread is None)
                self.assertFalse(window.model.recycles)
                self.assertEqual(window.model.committed_recycles, {rows[0]["path"]})
                self.assertTrue(window.project_path.exists())
                self.assertFalse(window.model.build()["actions"])
                self.assertFalse(Path(rows[0]["path"]).exists())
                for row in rows[1:]:
                    self.assertEqual(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest(), row["sha256"])
            finally:
                self.close_window(window)

    def test_setup_operation_and_review_show_only_relevant_controls(self):
        with tempfile.TemporaryDirectory(prefix="v2_phases_scan_") as folder:
            root = Path(folder)
            source = root / "photos"
            source.mkdir()
            for name in ("a.png", "b.png"):
                Image.new("RGB", (72, 96), "green").save(source / name)
            window = MainWindow(state_dir=root / "state")
            window.show()
            started, release = threading.Event(), threading.Event()
            def held_scan(*args, **kwargs):
                started.set()
                release.wait(5)
                return run_pipeline(*args, **kwargs)
            try:
                self.assertEqual(window.current_page, "setup")
                self.assertTrue(window.sources.isVisible())
                self.assertFalse(window.progress.isVisible())
                self.assertFalse(window.files.isVisible())
                self.assertFalse(window.save_button.isVisible())
                self.assertFalse(window.start_button.isEnabled())
                self.assertFalse(window.windowIcon().isNull())
                self.assertEqual(window.scan_scene.movie.state(), QMovie.NotRunning)
                window.add_source(source)
                self.assertTrue(window.start_button.isEnabled())
                with patch("app_ui.run_pipeline", side_effect=held_scan):
                    window.start_button.click()
                    self.wait_for(started.is_set)
                    self.assertEqual(window.current_page, "operation")
                    self.assertTrue(window.progress.isVisible())
                    self.assertTrue(window.operation_eta.isVisible())
                    self.assertTrue(window.eta_timer.isActive())
                    self.assertTrue(window.stop_button.isVisible())
                    self.assertFalse(window.sources.isVisible())
                    self.assertFalse(window.setup_cpu.isEnabled())
                    self.assertFalse(window.apply_button.isVisible())
                    for name in ("dark", "light"):
                        window.theme.set_mode(name)
                        self.app.processEvents()
                        for animation in (window.scan_scene,):
                            self.assertTrue(animation.movie.isValid())
                            self.assertEqual(animation.movie.state(), QMovie.Running)
                            self.assertIn(name, animation.movie.fileName())
                    self.assertLess(window.scan_visuals.geometry().bottom(), window.progress.parent().geometry().top())
                    release.set()
                    self.wait_for(lambda: window.scan_thread is None and not window.loading_preview)
                self.assertEqual(window.current_page, "review")
                self.assertTrue(window.files.isVisible())
                self.assertTrue(window.views[0].isVisible())
                self.assertFalse(window.progress.isVisible())
                self.assertFalse(window.sources.isVisible())
                self.assertFalse(window.statusBar().isVisible())
                self.assertFalse(window.delete_selected_button.isVisible())
                self.assertEqual(window.scan_scene.movie.state(), QMovie.NotRunning)
                self.assertFalse(window.eta_timer.isActive())
                pair, model = (window.a, window.b), window.model
                window.source_overview.click()
                self.assertEqual(window.current_page, "setup")
                self.assertTrue(window.setup_back.isVisible())
                self.assertEqual(window.setup_back.objectName(), "primary")
                self.assertNotEqual(window.start_button.objectName(), "primary")
                self.assertEqual(window.start_button.text(), "Начать новый поиск")
                window.setup_back.click()
                self.assertEqual(window.current_page, "review")
                self.assertIs(window.model, model)
                self.assertEqual((window.a, window.b), pair)
                window.files.item(0, 0).setCheckState(Qt.Checked)
                self.assertTrue(window.delete_selected_button.isVisible())
                self.assertTrue(window.reference_button.isVisible())
            finally:
                release.set()
                self.close_window(window)

    def test_new_comparison_can_cancel_or_save_then_restore_previous_progress(self):
        with tempfile.TemporaryDirectory(prefix="v2_new_comparison_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root)
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                removed = set(window.model.recycles)
                window.undo()
                self.wait_for(lambda: not window.loading_preview)
                before = window.model.export_state()
                current_model = window.model
                with patch("app_ui.QMessageBox.exec_", return_value=QMessageBox.Cancel):
                    window.new_button.click()
                self.assertIs(window.model, current_model)
                self.assertEqual(window.model.export_state(), before)
                self.assertTrue(window.redo_button.isEnabled())
                filename = root / "comparison.cmp.json"
                with patch("app_ui.QMessageBox.exec_", return_value=QMessageBox.Save), \
                     patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    window.new_button.click()
                self.assertIsNone(window.model)
                self.assertIsNone(window.project_path)
                self.assertEqual(window.current_page, "setup")
                self.assertEqual(window.sources.count(), 0)
                self.assertEqual(window.group_tree.topLevelItemCount(), 0)
                self.assertFalse(window.start_button.isEnabled())
                self.assertFalse(window.redo_button.isEnabled())
                self.assertTrue(window.new_button.isVisible())
                self.assertEqual(APP_NAME, window.windowTitle())
                saved, _ = load_project(filename)
                self.assertEqual(saved.export_state(), before)
                self.assertTrue(saved.redo_history)
                with patch("workspace_session.QFileDialog.getOpenFileName", return_value=(str(filename), "")):
                    window.open_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.model.export_state(), before)
                self.assertTrue(window.redo_button.isEnabled())
                window.redo_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.model.recycles, removed)
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                self.close_window(window)

    def test_plan_is_separate_screen_and_restores_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="v2_phase_plan_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root)
            reopened = None
            try:
                pair = window.a, window.b
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                window.plan_button.click()
                self.assertEqual(window.current_page, "plan")
                self.assertTrue(window.plan_table.isVisible())
                self.assertTrue(window.apply_button.isVisible())
                self.assertFalse(window.views[0].isVisible())
                self.assertFalse(window.progress.isVisible())
                filename = root / "plan-project.json"
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(filename), "")):
                    window.save_project()
                reopened = MainWindow(state_dir=root / "state")
                reopened.show()
                reopened.set_project(filename)
                self.assertEqual(reopened.current_page, "plan")
                self.assertTrue(reopened.plan_table.isVisible())
                reopened.show_review()
                self.assertEqual(reopened.current_page, "review")
                self.assertEqual(reopened.a, pair[0])
                self.assertIn(pair[1], reopened.model.inactive)
                reopened.show_setup()
                self.assertTrue(reopened.sources.isVisible())
                reopened.setup_back.click()
                self.assertEqual(reopened.current_page, "review")
            finally:
                if reopened:
                    self.close_window(reopened)
                self.close_window(window)

    def test_close_confirmation_is_russian_and_cancel_preserves_progress(self):
        with tempfile.TemporaryDirectory(prefix="v2_close_confirmation_") as folder:
            window, rows = self.fixture_window(Path(folder))
            labels = {}
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                before = window.model.export_state()
                def answer(dialog, role):
                    labels.update({code: dialog.button(code).text() for code in
                                   (QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel)})
                    return role
                with patch("app_ui.QMessageBox.exec_", new=lambda dialog: answer(dialog, QMessageBox.Cancel)):
                    self.assertFalse(window.close())
                self.assertTrue(window.isVisible())
                self.assertEqual(window.model.export_state(), before)
                self.assertEqual(labels, {QMessageBox.Save: "Сохранить прогресс",
                                         QMessageBox.Discard: "Не сохранять", QMessageBox.Cancel: "Отмена"})
                with patch("app_ui.QMessageBox.exec_", new=lambda dialog: answer(dialog, QMessageBox.Save)), \
                     patch("workspace_session.QFileDialog.getSaveFileName", return_value=("", "")):
                    self.assertFalse(window.close())
                self.assertEqual(window.model.export_state(), before)
                with patch("app_ui.QMessageBox.exec_", new=lambda dialog: answer(dialog, QMessageBox.Discard)):
                    self.assertTrue(window.close())
                self.assertFalse(window.isVisible())
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                self.close_window(window)

    def test_save_click_writes_progress_without_changing_pair_or_photo_geometry(self):
        with tempfile.TemporaryDirectory(prefix="v2_save_click_") as folder:
            root = Path(folder)
            window, rows = self.fixture_window(root)
            filename = root / "progress.cmp.json"
            try:
                before_files = {row["path"]: Path(row["path"]).read_bytes() for row in rows}
                for first_save in (True, False):
                    window.choose("remove_b")
                    self.wait_for(lambda: not window.loading_preview)
                    before_state = window.model.export_state()
                    before_pair = (window.current_page, window.current_group, window.a, window.b)
                    before_geometry = [(v.mapTo(window, QPoint(0, 0)), v.size()) for v in window.views]
                    before_notice = (window.notice.isVisible(), window.notice.text())
                    position = window.save_button.mapTo(window, QPoint(0, 0))
                    with patch("workspace_session.QFileDialog.getSaveFileName",
                               return_value=(str(filename), "")) as chooser:
                        QTest.mouseClick(window.save_button, Qt.LeftButton)
                        self.app.processEvents()
                    self.assertEqual(chooser.call_count, int(first_save))
                    self.assertTrue(window.save_button.text().startswith("Сохранено · "))
                    self.assertIn(str(filename.resolve()), window.save_button.toolTip())
                    self.assertEqual(window.save_button.mapTo(window, QPoint(0, 0)), position)
                    saved = json.loads(filename.read_text(encoding="utf-8"))
                    self.assertEqual(saved["state"], before_state)
                    self.assertEqual((saved["view"]["page"], saved["view"]["group"],
                                      saved["view"]["a"], saved["view"]["b"]), before_pair)
                    self.assertEqual(window.model.export_state(), before_state)
                    self.assertEqual((window.current_page, window.current_group, window.a, window.b), before_pair)
                    self.assertEqual([(v.mapTo(window, QPoint(0, 0)), v.size()) for v in window.views], before_geometry)
                    self.assertEqual((window.notice.isVisible(), window.notice.text()), before_notice)
                    self.assertFalse(window.dirty)
                self.assertEqual({p: Path(p).read_bytes() for p in before_files}, before_files)
            finally:
                self.close_window(window)

    def test_card_reasons_use_group_ranking_and_update_after_settings_change(self):
        with tempfile.TemporaryDirectory(prefix="v2_card_reasons_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                self.assertTrue(all("Равноценны" in label.text() for label in window.reason_labels))
                original = window.a, window.b
                third = next(p for p in window.model.live(window.current_group) if p not in original)
                window.model.files[third]["stored_width"] = 720
                window.refresh_group(preserve_pair=True)
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual((window.a, window.b), original)
                self.assertTrue(all("Уступает в группе" in label.toolTip() for label in window.reason_labels))
                self.assertTrue(all(Path(third).name in label.toolTip() for label in window.reason_labels))
                window.settings.suggest_best = False
                window.refresh_group(preserve_pair=True)
                self.assertTrue(all("выключены" in label.text() for label in window.reason_labels))
                self.assertFalse(window.suggested)
            finally:
                self.close_window(window)

    def test_card_date_reason_fits_small_window_with_exif_without_overlap(self):
        with tempfile.TemporaryDirectory(prefix="v2_reason_layout_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                for row in window.model.files.values():
                    row["captured_at"] = None
                    row["created_at"] = ("2016-01-10T14:49:27" if row["path"] == window.a
                                         else "2018-02-27T21:46:21")
                for theme in ("light", "dark"):
                    window.theme.set_mode(theme)
                    window.resize(1160, 760)
                    window.exif_button.setChecked(True)
                    window.refresh_group(preserve_pair=True)
                    self.wait_for(lambda: not window.loading_preview and not window.exif_loading)
                    for table, label, card, view, path in zip(window.parameter_tables, window.reason_labels,
                                                             window.photo_cards, window.views, window.path_fields):
                        self.assertLess(label.geometry().bottom(), view.y())
                        self.assertLess(path.geometry().right(), label.x())
                        self.assertLessEqual(card.minimumSizeHint().height(), card.height())
                        needed = label.fontMetrics().boundingRect(
                            label.rect().adjusted(0, 0, 0, 2000), Qt.TextWordWrap, label.text()).height()
                        self.assertLessEqual(needed, label.height())
                        self.assertIn(FILE_SYSTEM_LABEL, label.text())
                        self.assertIn("≠ съёмка", label.text())
            finally:
                self.close_window(window)

    def test_card_actions_are_one_row_with_transfer_between_delete_and_keep(self):
        with tempfile.TemporaryDirectory(prefix="v2_card_action_row_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                for width, height in ((1160, 760), (1600, 1040)):
                    window.resize(width, height)
                    self.app.processEvents()
                    for side in (0, 1):
                        keep = window.pair_buttons[side+1]
                        transfer = window.pair_buttons[side+3]
                        delete = window.delete_side_buttons[side]
                        ordered = (delete, transfer, keep) if side == 0 else (keep, transfer, delete)
                        self.assertEqual(len({button.y() for button in ordered}), 1)
                        self.assertLess(ordered[0].geometry().right(), ordered[1].x())
                        self.assertLess(ordered[1].geometry().right(), ordered[2].x())
                        for button in ordered:
                            available = button.width()-12  # Compact 5px padding and 1px borders.
                            self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()), available)
                    self.assertLess(window.original_buttons[1].x(), window.titles[1].x())
            finally:
                self.close_window(window)

    def test_save_stays_in_place_and_comparison_toolbar_is_compact(self):
        with tempfile.TemporaryDirectory(prefix="v2_footer_ui_") as folder:
            window, _ = self.fixture_window(Path(folder))
            try:
                window.resize(1366, 768)
                self.app.processEvents()
                position = window.save_button.mapTo(window, QPoint(0, 0))
                table_bottom = window.files.mapTo(window, QPoint(0, window.files.height()))
                card_top = window.views[0].parentWidget().mapTo(window, QPoint(0, 0))
                self.assertLessEqual(card_top.y()-table_bottom.y(), 55)
                self.assertTrue(window.plan_button.isVisible())  # Zero actions still occupies its fixed slot.
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.save_button.mapTo(window, QPoint(0, 0)), position)
                for page in (window.show_plan, window.show_review, window.show_plan):
                    page()
                    self.app.processEvents()
                    self.assertTrue(window.save_button.isVisible())
                    self.assertEqual(window.save_button.mapTo(window, QPoint(0, 0)), position)
                self.assertTrue(window.apply_button.isVisible())
            finally:
                self.close_window(window)

    def test_finished_group_and_final_outcome_do_not_show_empty_photo_frames(self):
        with tempfile.TemporaryDirectory(prefix="v2_phase_done_") as folder:
            window, rows = self.fixture_window(Path(folder))
            try:
                for index in (0, 1, 2):
                    window.files.item(index, 0).setCheckState(Qt.Checked)
                window.delete_selected_button.click()
                self.wait_for(lambda: not window.loading_preview)
                self.assertEqual(window.current_page, "plan")
                self.assertFalse(window.views[1].isVisible())
                self.assertEqual(window.work_progress, (1, 1))
                self.assertTrue(window.apply_changes())
                self.assertEqual(window.current_page, "outcome")
                self.assertIn("завершено", window.outcome_title.text())
                self.assertFalse(window.outcome_continue.isVisible())
                self.assertFalse(window.progress.isVisible())
                self.assertFalse(window.files.isVisible())
                self.assertTrue(all(not Path(row["path"]).exists() for row in rows[:3]))
                self.assertTrue(Path(rows[3]["path"]).exists())
            finally:
                self.close_window(window)

    def test_arrow_keys_animate_buttons_advance_groups_and_undo_across_transitions(self):
        with tempfile.TemporaryDirectory(prefix="v2_arrow_actions_") as folder:
            window, rows = self.fixture_window(Path(folder), tuple(f"{n}.png" for n in range(6)))
            try:
                document = window.model.document
                for index, row in enumerate(document["files"]):
                    row["group_id"] = index//2+1
                window.attach_model(ActionPlan(document))
                self.wait_for(lambda: not window.loading_preview)
                window.activateWindow()
                window.files.setFocus()
                self.app.processEvents()
                self.assertEqual(window.undo_button.text(), "Отменить")
                self.assertEqual(window.redo_button.text(), "Повторить")
                self.assertLess(window.undo_button.mapTo(window, QPoint()).y(), window.files.mapTo(window, QPoint()).y())
                def press(key, button, history_size):
                    QTest.keyPress(window.files, key)
                    self.assertTrue(button.isDown())
                    QTest.keyRelease(window.files, key)
                    self.wait_for(lambda: window.review_keys.pending is None
                                  and len(window.model.history) == history_size and not window.loading_preview)
                    self.assertFalse(button.isDown())
                press(Qt.Key_Left, window.pair_buttons[1], 1)
                self.assertEqual(window.current_group, "2")
                self.assertEqual(window.model.recycles, {rows[1]["path"]})
                repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier, "", True)
                self.app.sendEvent(window.files, repeat)
                self.assertIsNone(window.review_keys.pending)
                self.assertEqual(len(window.model.history), 1)
                press(Qt.Key_Right, window.pair_buttons[2], 2)
                self.assertEqual(window.current_group, "3")
                self.assertEqual(window.model.recycles, {rows[1]["path"], rows[2]["path"]})
                press(Qt.Key_Down, window.pair_buttons[0], 3)
                self.assertEqual(window.current_page, "plan")
                # The final screen also accepts Up, returning directly to the last pair.
                QTest.keyPress(window, Qt.Key_Up)
                self.assertTrue(window.undo_button.isDown())
                QTest.keyRelease(window, Qt.Key_Up)
                self.wait_for(lambda: window.review_keys.pending is None and not window.loading_preview)
                self.assertEqual((window.current_page, window.current_group), ("review", "3"))
                self.assertEqual((window.a, window.b), (rows[4]["path"], rows[5]["path"]))
                press(Qt.Key_Up, window.undo_button, 1)
                self.assertEqual(window.current_group, "2")
                self.assertEqual(window.model.recycles, {rows[1]["path"]})
                self.assertTrue(all(Path(row["path"]).exists() for row in rows))
            finally:
                self.close_window(window)

    def test_arrow_actions_ignore_loading_and_allow_navigation_in_settings(self):
        with tempfile.TemporaryDirectory(prefix="v2_arrow_context_") as folder:
            window, _ = self.fixture_window(Path(folder))
            dialog = None
            try:
                window.activateWindow()
                window.files.setFocus()
                self.app.processEvents()
                window.loading_preview = True
                window.update_actions()
                QTest.keyClick(window.files, Qt.Key_Right)
                self.assertIsNone(window.review_keys.pending)
                self.assertFalse(window.model.history)
                window.loading_preview = False
                window.update_actions()
                dialog = SettingsDialog(window.settings, window)
                dialog.show()
                dialog.activateWindow()
                dialog.cpu.setFocus()
                self.app.processEvents()
                value = dialog.cpu.value()
                QTest.keyClick(dialog.cpu, Qt.Key_Up)
                self.assertEqual(dialog.cpu.value(), value+5)
                self.assertIsNone(window.review_keys.pending)
                self.assertFalse(window.model.history)
            finally:
                if dialog:
                    dialog.close()
                self.close_window(window)

    def test_stopped_scan_keeps_old_review_and_has_a_clear_way_back(self):
        with tempfile.TemporaryDirectory(prefix="v2_phase_stop_") as folder:
            window, rows = self.fixture_window(Path(folder))
            started = threading.Event()
            before = window.model.export_state()
            def cancelled_scan(*args, **kwargs):
                started.set()
                kwargs["cancel_event"].wait(5)
                raise ScanCancelled()
            try:
                window.show_setup()
                with patch("app_ui.run_pipeline", side_effect=cancelled_scan):
                    window.start_scan()
                    self.wait_for(started.is_set)
                    window.stop_button.click()
                    self.wait_for(lambda: window.scan_thread is None)
                self.assertEqual(window.current_page, "outcome")
                self.assertIn("остановлен", window.outcome_title.text())
                self.assertFalse(window.progress.isVisible())
                self.assertEqual(window.model.export_state(), before)
                window.outcome_continue.click()
                self.assertEqual(window.current_page, "review")
            finally:
                self.close_window(window)

    def test_scan_error_is_inline_and_sources_remain_available(self):
        with tempfile.TemporaryDirectory(prefix="v2_phase_error_") as folder:
            root = Path(folder)
            window = MainWindow(sources=[root], state_dir=root / "state")
            window.show()
            try:
                with patch("app_ui.run_pipeline", side_effect=RuntimeError("source unavailable")):
                    window.start_scan()
                    self.wait_for(lambda: window.scan_thread is None)
                self.assertEqual(window.current_page, "outcome")
                self.assertEqual(window.outcome_body.text(), "source unavailable")
                self.assertFalse(window.progress.isVisible())
                self.assertFalse(window.outcome_continue.isVisible())
                window.outcome_sources.click()
                self.assertEqual(window.current_page, "setup")
                self.assertEqual(window.sources.count(), 1)
            finally:
                self.close_window(window)

    def test_read_errors_are_visible_without_groups_and_survive_save(self):
        for readable_count in (0, 1, 2):
            with self.subTest(readable_count=readable_count), tempfile.TemporaryDirectory(prefix="v2_ui_read_errors_") as folder:
                root = Path(folder)
                source = root/"photos"
                source.mkdir()
                (source/"bad.jpg").write_bytes(b"not a photo")
                for index in range(readable_count):
                    Image.new("RGB", (72, 96), "green").save(source/(str(index)+".png"))
                window = MainWindow(sources=[source], state_dir=root/"state")
                window.show()
                try:
                    window.start_scan()
                    self.wait_for(lambda: window.scan_thread is None and not window.loading_preview)
                    self.assertEqual(len(window.model.document["scan_errors"]), 1)
                    self.assertIn("ошибок чтения: 1", window.notice.text())
                    self.assertTrue(window.notice.isVisible())
                    if readable_count < 2:
                        self.assertEqual(window.current_page, "outcome")
                        self.assertIn("ошибками чтения", window.outcome_title.text())
                        self.assertIn("bad.jpg", window.outcome_detail.text())
                    else:
                        self.assertEqual(window.current_page, "review")
                    filename = root/"comparison.cmp.json"
                    from project import save_project
                    save_project(window.model, filename, window.session_view())
                    restored, _ = load_project(filename)
                    self.assertEqual(restored.document["scan_errors"], window.model.document["scan_errors"])
                finally:
                    self.close_window(window)

    def test_cancelled_deferred_scan_close_does_not_close_the_next_scan(self):
        with tempfile.TemporaryDirectory(prefix="v2_ui_deferred_close_") as folder:
            window, _ = self.fixture_window(Path(folder))
            started = threading.Event()
            def cancelled_scan(*args, **kwargs):
                started.set()
                kwargs["cancel_event"].wait(5)
                raise ScanCancelled()
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                with patch("app_ui.run_pipeline", side_effect=cancelled_scan), \
                     patch.object(window, "confirm_new_result", side_effect=[True, False]):
                    window.start_scan()
                    self.wait_for(started.is_set)
                    self.assertFalse(window.close())
                    self.wait_for(lambda: window.scan_thread is None)
                self.assertTrue(window.isVisible())
                self.assertFalse(window.close_after_scan)
                with patch("app_ui.run_pipeline", side_effect=RuntimeError("next scan")), \
                     patch.object(window, "confirm_new_result", return_value=True), \
                     patch.object(window, "close", wraps=window.close) as close:
                    window.start_scan()
                    self.wait_for(lambda: window.scan_thread is None)
                    close.assert_not_called()
                self.assertTrue(window.isVisible())
            finally:
                self.close_window(window)

    def test_cancelled_deferred_apply_close_consumes_the_close_flag(self):
        with tempfile.TemporaryDirectory(prefix="v2_ui_deferred_apply_") as folder:
            window, _ = self.fixture_window(Path(folder))
            started = threading.Event()
            def held_apply(model, filename, view, progress, cancel_event):
                started.set()
                cancel_event.wait(5)
                raise InterruptedError("cancelled")
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                window.show_plan()
                with patch("app_ui.apply_files", side_effect=held_apply), \
                     patch.object(window, "confirm_new_result", return_value=False):
                    window.apply_button.click()
                    self.wait_for(started.is_set)
                    self.assertFalse(window.close())
                    self.wait_for(lambda: window.apply_thread is None)
                self.assertTrue(window.isVisible())
                self.assertFalse(window.close_after_apply)
                window.show_plan()
                with patch.object(window, "close", wraps=window.close) as close:
                    window.apply_button.click()
                    self.wait_for(lambda: window.apply_thread is None)
                    close.assert_not_called()
                self.assertTrue(window.isVisible())
            finally:
                self.close_window(window)

    def test_failed_saved_apply_can_be_retried_after_rollback(self):
        with tempfile.TemporaryDirectory(prefix="v2_ui_apply_retry_") as folder:
            root = Path(folder)
            window, _ = self.fixture_window(root)
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                with patch("workspace_session.QFileDialog.getSaveFileName", return_value=(str(root/"progress.cmp.json"), "")):
                    self.assertTrue(window.save_project())
                before = window.model.export_state()
                window.show_plan()
                with patch("execution.recycle_file", side_effect=OSError("temporary shell error")):
                    window.start_apply_changes()
                    self.wait_for(lambda: window.apply_thread is None)
                self.assertEqual(window.model.export_state(), before)
                self.assertIn("temporary shell error", window.outcome_body.text())
                window.show_plan()
                window.start_apply_changes()
                self.wait_for(lambda: window.apply_thread is None and not window.loading_preview)
                self.assertTrue(window.model.applied_batches, window.outcome_body.text())
                self.assertFalse(window.model.build()["actions"])
            finally:
                self.close_window(window)

    def test_cancelled_apply_returns_to_plan_without_losing_decisions(self):
        with tempfile.TemporaryDirectory(prefix="v2_phase_cancel_apply_") as folder:
            window, rows = self.fixture_window(Path(folder))
            started, release = threading.Event(), threading.Event()
            real_apply = apply_files
            def held_apply(model, *args, **kwargs):
                started.set()
                release.wait(5)
                return real_apply(model, *args, **kwargs)
            try:
                window.choose("remove_b")
                self.wait_for(lambda: not window.loading_preview)
                window.show_plan()
                before = window.model.export_state()
                with patch("app_ui.apply_files", side_effect=held_apply):
                    window.apply_button.click()
                    self.wait_for(started.is_set)
                    self.assertTrue(window.progress.isVisible())
                    window.stop_button.click()
                    release.set()
                    self.wait_for(lambda: window.apply_thread is None)
                self.assertEqual(window.current_page, "outcome")
                self.assertIn("отменено", window.outcome_title.text())
                self.assertEqual(window.model.export_state(), before)
                window.outcome_continue.click()
                self.assertEqual(window.current_page, "plan")
                self.assertFalse(window.progress.isVisible())
            finally:
                release.set()
                self.close_window(window)


if __name__ == "__main__":
    unittest.main()
