"""Scale/IO regressions for review, without touching an actual photo archive."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from action_plan import ActionPlan
from app_ui import MainWindow
from project import save_project, load_project


def large_document(root, groups=5365):
    return {"source_root": str(root/"originals"), "scanned_files": 84616,
            "files": [{"path": str(root/"originals"/f"{group}-{index}.png"),
                       "group_id": group, "sha256": "0"*64, "detected_format": "PNG",
                       "stored_width": 96, "stored_height": 72, "bytes": 1024}
                      for group in range(groups) for index in range(2)], "pairs": []}


class ReviewPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_plain_decisions_do_not_query_archive_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ActionPlan(large_document(Path(folder)))
            with patch.object(Path, "resolve", side_effect=AssertionError("Per-file resolve on review click")), \
                 patch.object(Path, "exists", side_effect=AssertionError("Per-file exists on review click")):
                for group in list(model.groups)[:4]:
                    a,b = model.next_pair(group)
                    model.choose(a,b,"remove_b")
                self.assertTrue(model.undo())
                self.assertTrue(model.redo())

    def test_save_and_close_do_not_resolve_individual_archive_files(self):
        with tempfile.TemporaryDirectory(prefix="v2_fast_save_") as folder:
            root = Path(folder).resolve()
            document = large_document(root)
            document["scan_report"] = str(root/"report.json")
            with patch.object(MainWindow, "present_pair"), \
                 patch.object(MainWindow, "load_visible_covers"), \
                 patch.object(MainWindow, "request_exif"):
                window = MainWindow(state_dir=root/"state")
                try:
                    window.model = ActionPlan(document)
                    window.current_group = "0"
                    window.current_page = window.resume_page = "review"
                    window.refresh()
                    window.show()
                    window.choose("remove_b")
                    window.project_path = root/"saved.json"
                    resolve = Path.resolve
                    def guarded(path, *args, **kwargs):
                        if str(path) in window.model.files:
                            raise AssertionError("Saving restatted an individual archive path")
                        return resolve(path, *args, **kwargs)
                    with patch.object(Path, "resolve", guarded):
                        self.assertTrue(window.save_project())
                        self.assertTrue(window.save_button.text().startswith("Сохранено"))
                        window.navigate_group(1)
                        expected_pair = window.a, window.b
                        start = time.perf_counter()
                        self.assertTrue(window.close())
                        self.assertLess(time.perf_counter()-start, 2.0)
                    restored, view = load_project(window.project_path)
                    self.assertEqual(restored.export_state(), window.model.export_state())
                    self.assertEqual((view["a"], view["b"]), expected_pair)
                finally:
                    window.project_path = None
                    window.dirty = False
                    window.close()

    def test_fast_save_rejects_unknown_existing_file_and_source_hardlink(self):
        with tempfile.TemporaryDirectory(prefix="v2_save_protection_") as folder:
            root = Path(folder).resolve()
            document = large_document(root, 1)
            document["scan_report"] = str(root/"report.json")
            model = ActionPlan(document)
            source = Path(next(iter(model.files)))
            source.parent.mkdir()
            source.write_bytes(b"synthetic source image")
            alias = root/"photo_alias.json"
            os.link(source, alias)
            existing = root/"unrelated.json"
            existing.write_bytes(b"unrelated file")
            for destination in (alias, existing):
                before = destination.read_bytes()
                with self.assertRaises(OSError):
                    save_project(model, destination)
                self.assertEqual(destination.read_bytes(), before)
            self.assertEqual(source.read_bytes(), b"synthetic source image")

    def test_completed_group_refreshes_once_and_leaves_hidden_plan_unbuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(MainWindow,"present_pair"), \
                 patch.object(MainWindow,"load_visible_covers"), \
                 patch.object(MainWindow,"request_exif"):
                window = MainWindow(state_dir=root/"isolated-state")
                try:
                    window.model = ActionPlan(large_document(root))
                    window.model.configure_deletion("folder", str(root/"removed"))
                    window.current_group = "0"
                    window.current_page = window.resume_page = "review"
                    window.refresh()
                    untouched = window.group_tree.topLevelItem(100)
                    a,b = window.a,window.b
                    with patch.object(window,"refresh", wraps=window.refresh) as refresh, \
                         patch.object(window.model,"build", side_effect=AssertionError("Hidden plan built during review")), \
                         patch.object(Path,"resolve", side_effect=AssertionError("Filesystem resolve during plain choice")):
                        start = time.perf_counter()
                        window.choose("remove_b")
                        elapsed = time.perf_counter()-start
                    self.assertEqual(refresh.call_count,1)
                    self.assertLess(elapsed,2.0)  # Generous bound; old path took tens of seconds.
                    self.assertIs(window.group_tree.topLevelItem(100),untouched)
                    self.assertEqual(window.current_group,"1")
                    self.assertEqual(window.plan_table.rowCount(),0)
                    window.undo()
                    self.assertEqual((window.a,window.b),(a,b))
                    window.redo()
                    self.assertIn(b,window.model.inactive)
                    window.show_plan()
                    self.assertGreater(window.plan_table.rowCount(),0)
                finally:
                    window.project_path = None
                    window.dirty = False
                    window.close()

    def test_changed_destinations_still_reject_disk_collisions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root/"originals"
            original.mkdir()
            destination = root/"destination"
            destination.mkdir()
            model = ActionPlan(large_document(root,2))
            source = model.groups["0"][0]
            occupied = destination/Path(source).name
            occupied.write_bytes(b"unrelated")
            before = model.export_state()
            with self.assertRaises(ValueError):
                model.move_files("0",{source},destination)
            self.assertEqual(model.export_state(),before)
            self.assertEqual(occupied.read_bytes(),b"unrelated")

    def test_active_group_scrolls_into_view_on_navigation_choice_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(MainWindow,"present_pair"), patch.object(MainWindow,"load_visible_covers"), \
                 patch.object(MainWindow,"request_exif"):
                window = MainWindow(state_dir=root/"isolated-state")
                try:
                    window.model = ActionPlan(large_document(root,300))
                    window.current_group = "0"
                    window.current_page = window.resume_page = "review"
                    window.refresh()
                    window.show()
                    self.app.processEvents()
                    def visible():
                        item=window.group_items[window.current_group]
                        self.assertIs(window.group_tree.currentItem(),item)
                        self.assertTrue(window.group_tree.visualItemRect(item).intersects(window.group_tree.viewport().rect()))
                    window.current_group="250"
                    window.refresh(changed_groups=set())
                    self.app.processEvents()
                    visible()
                    window.group_tree.verticalScrollBar().setValue(0)
                    window.choose("remove_b")
                    self.app.processEvents()
                    self.assertEqual(window.current_group,"251")
                    visible()
                    window.group_tree.verticalScrollBar().setValue(0)
                    window.undo()
                    self.app.processEvents()
                    self.assertEqual(window.current_group,"250")
                    visible()
                    window.group_tree.verticalScrollBar().setValue(0)
                    window.navigate_group(1)
                    self.app.processEvents()
                    visible()
                finally:
                    window.project_path=None
                    window.dirty=False
                    window.close()

    def test_two_planned_destinations_with_canonical_alias_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            first,second = root/"destination",root/"alias"
            first.mkdir()
            second.mkdir()
            model = ActionPlan(large_document(root,2))
            resolve = Path.resolve
            def aliased(path,*args,**kwargs):
                if path.parent in (first,second):
                    return first/"same-physical-destination.png"
                return resolve(path,*args,**kwargs)
            with patch.object(Path,"resolve",aliased):
                model.move_files("0",{model.groups['0'][0]},first)
                before = model.export_state()
                with self.assertRaises(ValueError):
                    model.move_files("1",{model.groups['1'][0]},second)
                self.assertEqual(model.export_state(),before)

    def test_wordmark_keeps_artwork_and_follows_theme_palette(self):
        import numpy as np
        from brand_wordmark import BrandWordmark
        from theme import PALETTES
        wordmark = BrandWordmark(PALETTES['dark'])
        for mode in ('dark','light'):
            wordmark.set_colors(PALETTES[mode])
            for mask,key in ((wordmark.accent,'accent'),(~wordmark.accent,'text')):
                ys,xs = np.nonzero(mask & (wordmark.alpha>250))
                color = wordmark.image.pixelColor(int(xs[0]),int(ys[0])).name()
                self.assertEqual(color,PALETTES[mode][key])
        self.assertEqual(wordmark.accessibleName(),'Attentive Otter')
        self.assertGreater(wordmark.height(),30)
        self.assertLess(wordmark.height(),70)

    def test_preview_cache_is_not_execution_allocator(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            removed = root/"removed"
            removed.mkdir()
            model = ActionPlan(large_document(root,1))
            model.configure_deletion("folder",str(removed))
            a,b = model.next_pair("0")
            model.choose(a,b,"remove_b")
            preview = model.removal_destinations(check_disk=False)
            occupied = Path(preview[b])
            occupied.write_bytes(b"foreign file created after preview")
            strict = model.build()["actions"][0]["destination"]
            self.assertNotEqual(strict,str(occupied))
            self.assertEqual(occupied.read_bytes(),b"foreign file created after preview")
            model.undo()
            self.assertEqual(model.removal_destinations(check_disk=False),{})
            model.redo()
            self.assertNotEqual(model.removal_destinations(check_disk=False)[b],str(occupied))


if __name__ == "__main__":
    unittest.main()
