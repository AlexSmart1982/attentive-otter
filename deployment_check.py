"""Explicit, isolated end-to-end verification of a deployed executable."""

import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import time
import traceback


def run(destination):
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False))}
    windows = []
    temporary = tempfile.TemporaryDirectory(prefix="photo_comparison_deployment_")
    try:
        import ctypes
        import hashlib
        import numpy as np
        import pillow_heif
        from PIL import Image
        from PyQt5.QtCore import QStandardPaths, QTimer, QPoint, QPointF, Qt, QEvent
        from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QMouseEvent
        from PyQt5.QtWidgets import QApplication, QLabel, QPlainTextEdit, QFileDialog
        from about_ui import AboutDialog, APP_NAME, APP_VERSION
        from app_ui import MainWindow
        from project import load_project

        app = QApplication.instance() or QApplication([])
        app.setApplicationName("ComparsionV2")
        app.setStyle("Fusion")
        if not QFontDatabase().families():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
            app.setFont(QFont("Arial", 10))
        root = Path(temporary.name).resolve()
        source, organized = root/"Фотографии для проверки", root/"Отобранные"
        source.mkdir()
        organized.mkdir()
        pillow_heif.register_heif_opener()
        pixels = np.random.default_rng(17).integers(0, 256, (256, 192, 3), dtype=np.uint8)
        picture = Image.fromarray(pixels)
        paths = [source/"01-original.png", source/"02-forwarded.jpg", source/"03-native.heic"]
        picture.save(paths[0])
        picture.save(paths[1], quality=90)
        picture.save(paths[2], quality=95)
        for path in paths:
            with Image.open(path) as image:
                image.load()
        assets = Path(__file__).parent/"assets"
        for name in ("application.svg", "undo.svg", "redo.svg", "move-a-to-b.svg", "attentive-otter-wordmark.png"):
            if not (assets/name).is_file():
                raise RuntimeError("Missing bundled asset: "+name)
        for name in ("scan-scene-dark.gif", "scan-scene-light.gif"):
            if not (assets/name).is_file():
                raise RuntimeError("Missing bundled animation: "+name)
        if QIcon(str(assets/"application.svg")).isNull():
            raise RuntimeError("Qt SVG plugin is unavailable")
        report["formats"] = ["PNG", "JPEG", "HEIC"]
        report["assets"] = True
        for name in ("LICENSE", "COPYRIGHT.txt"):
            if not (assets.parent/name).is_file():
                raise RuntimeError("Missing bundled license: " + name)
        about = AboutDialog()
        labels = "\n".join(label.text() for label in about.findChildren(QLabel))
        if APP_NAME != "Attentive Otter" or APP_VERSION != "1.0 BETA" or APP_NAME not in labels:
            raise RuntimeError("Application identity is incorrect")
        if "GPL-3.0-only" not in labels or about.thanks_button.isEnabled():
            raise RuntimeError("About dialog is incorrect")
        license_checks = []
        def inspect_license():
            dialog = app.activeModalWidget()
            if dialog is not None:
                text = dialog.findChild(QPlainTextEdit)
                license_checks.append(bool(text is not None and text.isReadOnly()
                                          and "GNU GENERAL PUBLIC LICENSE" in text.toPlainText()
                                          and "alexsmart@yandex.ru" in text.toPlainText()))
                dialog.accept()
        QTimer.singleShot(0, inspect_license)
        about.open_license()
        about.close()
        if license_checks != [True]:
            raise RuntimeError("Bundled license dialog failed")
        report.update(app=APP_NAME, version=APP_VERSION, about=True, license=True)
        w = MainWindow(sources=[source], state_dir=root/"state")
        windows.append(w)
        w.show()
        if w.brand_wordmark.image.isNull():
            raise RuntimeError("The approved wordmark is unavailable")
        report["wordmark"] = True
        def wait_for(predicate):
            deadline = time.monotonic()+45
            while not predicate():
                app.processEvents()
                if time.monotonic() > deadline:
                    raise TimeoutError("Deployment verification timed out")
                time.sleep(.01)
            app.processEvents()
        w.start_scan()
        if not w.scan_scene.movie.isValid():
            raise RuntimeError("Qt GIF plugin or scan animations are unavailable")
        wait_for(lambda: w.scan_thread is None and not w.loading_preview)
        if not w.model or len(w.model.files) != 3 or w.b is None:
            raise RuntimeError("PNG/JPEG/HEIC candidate scan failed: "+w.outcome_body.text())
        report["scan_files"] = len(w.model.files)
        report["groups"] = len(w.model.groups)
        if w.undo_button.icon().isNull() or w.redo_button.icon().isNull():
            raise RuntimeError("History icons are unavailable")
        if (not all(label.text().startswith("Основание:") and label.toolTip() for label in w.reason_labels)
                or "Рекомендуем" not in w.reason_labels[0].toolTip()
                or "формат" not in w.reason_labels[0].text()):
            raise RuntimeError("Group recommendation explanations are missing or incorrect")
        report["recommendation_reasons"] = True
        for side in (0, 1):
            keep, transfer, delete = w.pair_buttons[side+1], w.pair_buttons[side+3], w.delete_side_buttons[side]
            ordered = (delete, transfer, keep) if side == 0 else (keep, transfer, delete)
            if (len({button.y() for button in ordered}) != 1
                    or not ordered[0].x() < ordered[1].x() < ordered[2].x()
                    or w.reason_labels[side].x() <= w.path_fields[side].geometry().right()
                    or w.reason_labels[side].geometry().bottom() >= w.views[side].y()):
                raise RuntimeError("Compact mirrored cards are arranged incorrectly")
        report["compact_cards"] = True
        before_state = w.model.export_state()
        before_pair = w.current_page, w.current_group, w.a, w.b
        before_geometry = [(view.mapTo(w, QPoint(0, 0)), view.size()) for view in w.views]
        checkpoint = root/"button_save.cmp.json"
        original_chooser = QFileDialog.getSaveFileName
        chooser_calls = []
        def choose_save(*args, **kwargs):
            chooser_calls.append(True)
            return str(checkpoint), ""
        QFileDialog.getSaveFileName = staticmethod(choose_save)
        try:
            for _ in range(2):
                point = QPointF(w.save_button.rect().center())
                app.sendEvent(w.save_button, QMouseEvent(QEvent.MouseButtonPress, point,
                              Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                app.sendEvent(w.save_button, QMouseEvent(QEvent.MouseButtonRelease, point,
                              Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
                app.processEvents()
                saved_data = json.loads(checkpoint.read_text(encoding="utf-8"))
                if not w.save_button.text().startswith("Сохранено · "):
                    raise RuntimeError("Save confirmation is not visible on the button")
                if (saved_data["state"] != before_state or w.model.export_state() != before_state
                        or before_pair != (w.current_page, w.current_group, w.a, w.b)
                        or before_geometry != [(view.mapTo(w, QPoint(0, 0)), view.size()) for view in w.views]):
                    raise RuntimeError("Saving changed the pair, decisions or photo layout")
            if len(chooser_calls) != 1:
                raise RuntimeError("Repeat save did not use the existing checkpoint")
        finally:
            QFileDialog.getSaveFileName = original_chooser
        report["save_button"] = True
        report["save_button_feedback"] = True
        winner, loser = w.a, w.b
        other = next(p for p in w.model.live(w.current_group) if p not in (winner, loser))
        w.delete_side_buttons[1].click()
        wait_for(lambda: not w.loading_preview)
        if loser not in w.model.recycles or winner in w.model.kept:
            raise RuntimeError("Deleting B changed the decision for A")
        w.undo()
        wait_for(lambda: not w.loading_preview)
        if (w.a, w.b) != (winner, loser) or w.model.recycles:
            raise RuntimeError("Individual deletion did not undo correctly")
        report["individual_delete"] = True
        w.choose("replace_b")
        wait_for(lambda: not w.loading_preview)
        w.move_selected({winner, other}, organized)
        wait_for(lambda: not w.loading_preview)
        expected = {p: (w.model.effective_path(p), w.model.files[p]["sha256"]) for p in (winner, other)}
        w.start_apply_changes()
        wait_for(lambda: w.apply_thread is None and not w.loading_preview)
        if not w.project_path or not w.model.applied_batches or w.model.applied_batches[-1]["mode"] != "applied":
            raise RuntimeError("Real application failed: "+w.outcome_body.text())
        if Path(loser).exists():
            raise RuntimeError("The discarded temporary file was not recycled")
        for identity, (target, fingerprint) in expected.items():
            if hashlib.sha256(Path(target).read_bytes()).hexdigest() != fingerprint:
                raise RuntimeError("Transferred file hash does not match")
            if w.model.physical_path(identity) != target:
                raise RuntimeError("Physical file location was not updated")
        saved, _ = load_project(w.project_path)
        if saved.build()["actions"]:
            raise RuntimeError("Applied file actions remained pending")
        reopened = MainWindow(state_dir=root/"state")
        windows.append(reopened)
        reopened.set_project(w.project_path)
        reopened.show()
        wait_for(lambda: not reopened.loading_preview)
        if reopened.model.physical_paths != saved.physical_paths:
            raise RuntimeError("Saved comparison did not restore moved files")
        discarded = root/"Папка для удаляемых"
        discarded.mkdir()
        from settings_ui import SettingsDialog
        dialog = SettingsDialog(reopened.settings)
        dialog.delete_mode.setCurrentIndex(dialog.delete_mode.findData("folder"))
        dialog.delete_folder.setText(str(discarded))
        dialog.accept()
        configured = dialog.value()
        reopened.model.configure_deletion(configured.delete_mode, configured.delete_folder)
        reopened.refresh()
        wait_for(lambda: not reopened.loading_preview)
        removed = reopened.b
        if removed is None:
            raise RuntimeError("No remaining pair for folder removal verification")
        occupied = discarded/Path(reopened.model.physical_path(removed)).name
        occupied.write_bytes(b"unrelated existing file")
        expected_hash = reopened.model.files[removed]["sha256"]
        reopened.delete_side_buttons[1].click()
        wait_for(lambda: not reopened.loading_preview)
        removal = next(a for a in reopened.model.build()["actions"] if a["kind"] == "recycle")
        target = Path(removal["destination"])
        reopened.start_apply_changes()
        wait_for(lambda: reopened.apply_thread is None and not reopened.loading_preview)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError("Folder removal did not preserve the expected file")
        if occupied.read_bytes() != b"unrelated existing file":
            raise RuntimeError("Folder removal overwrote an existing file")
        folder_saved, _ = load_project(reopened.project_path)
        if folder_saved.delete_mode != "folder" or folder_saved.physical_path(removed) != str(target):
            raise RuntimeError("Folder removal checkpoint did not restore")
        report.update(recycle_apply=True, folder_apply=True, folder_resume=True)
        previous = os.environ.get("ATTENTIVE_VERIFY_COMPARISON")
        if previous:
            # Explicit deployment diagnostics only. Never load or rewrite a
            # personal comparison during the ordinary synthetic verification.
            previous = Path(previous)
            fingerprint = hashlib.sha256(previous.read_bytes()).hexdigest()
            data = json.loads(previous.read_text(encoding="utf-8-sig"))
            copied = root/"previous-release.cmp.json"
            shutil.copy2(previous, copied)
            old = MainWindow(state_dir=root/"compatibility-state")
            windows.append(old)
            # Verify metadata, state and navigation without reading real photos.
            old.present_pair = lambda: None
            old.request_exif = lambda: None
            old.load_visible_covers = lambda *args: None
            old.set_project(copied)
            state = old.model.export_state()
            expected = dict(data["state"])
            for value in (state, expected):
                value["seen"] = sorted(sorted(pair) for pair in value["seen"])
            if (old.model.document != data["review"] or state != expected
                    or (old.current_group, old.a, old.b) !=
                       (data["view"]["group"], data["view"]["a"], data["view"]["b"])
                    or len(old.model.history) != len(data.get("undo", [])[-50:])
                    or len(old.model.redo_history) != len(data.get("redo", [])[-50:])):
                raise RuntimeError("Previous-release comparison did not restore exactly")
            if hashlib.sha256(previous.read_bytes()).hexdigest() != fingerprint:
                raise RuntimeError("Original saved comparison changed")
            report["previous_comparison"] = dict(ok=True, files=len(old.model.files),
                groups=len(old.model.groups), kept=len(old.model.kept), removed=len(old.model.recycles),
                undo=len(old.model.history), redo=len(old.model.redo_history),
                original_sha256=fingerprint, navigation_preserved=True, original_unchanged=True)
        report.update(ok=True, real_apply=True, resume=True, console=bool(ctypes.windll.kernel32.GetConsoleWindow()),
                      user_data=QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        for w in reversed(windows):
            if w.apply_thread:
                w.apply_thread.cancel_event.set()
                w.apply_thread.wait(10000)
            if w.scan_thread:
                w.stop_scan()
                w.scan_thread.wait(10000)
            w.dirty = False
            w.project_path = None
            w.close()
        temporary.cleanup()
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return 0 if report["ok"] else 1
