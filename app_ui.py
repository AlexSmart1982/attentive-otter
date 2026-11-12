"""Comparsion V2 desktop: multiple sources, comparison and file actions.

python app_ui.py [--project archive.cmp.json | --review review.json] [--sources folder ...]
"""

import argparse
from collections import OrderedDict
from datetime import datetime
from html import escape
from copy import deepcopy
import os
from pathlib import Path
import sys
import threading
import uuid

from PIL import Image
import numpy as np
import pillow_heif
from PyQt5.QtCore import (Qt, QThread, pyqtSignal, QObject, QRunnable, QThreadPool,
                          QStandardPaths, QUrl, QSize, QTimer)
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QDesktopServices, QIcon, QKeySequence
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTreeWidgetItem, QTableWidgetItem,
                            QFileDialog, QMessageBox, QDialog, QShortcut, QAbstractItemView)

from action_plan import ActionPlan
from pipeline import run_pipeline
from prepare_sample import open_rgb
from review_ui import pixmap
from scan import ScanCancelled
from preferences import (Preferences, RULE_LABELS, DATE_LABELS, best_parameters,
                         preferred_date, recommendations, recommendation_reasons, exif_completeness, parse_date)
from settings_ui import SettingsDialog
from about_ui import APP_NAME, APP_VERSION, AboutDialog
from canvas_layout import build as build_canvas
from theme import PALETTES, ThemeController
from exif_data import read_exif
from workspace_session import WorkspaceSession
from review_keys import ReviewKeys
from execution import apply_files
from progress_eta import ScanETA
from system_trash import TRASH_LABEL, TRASH_DESTINATION


STATUS = {"undecided": "Не решено", "keep": "Оставить", "recycle": "В корзину",
          "replaced": "Заменить", "replacement_source": "Источник замены",
          "move": "Переместить", "applied": "Убрано"}


class FileTableItem(QTableWidgetItem):
    """Sort source values rather than formatted labels, rounded sizes or A marks."""
    def __init__(self, text, value):
        super().__init__(text)
        self.sort_key = (value is not None, value if value is not None else 0)

    def __lt__(self, other):
        if self.column() == 0:
            return self.checkState() < other.checkState()
        return self.sort_key < other.sort_key


class ScanThread(QThread):
    progress = pyqtSignal(object)
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, sources, output, cache, workers, search_mode="strict"):
        super().__init__()
        self.arguments = sources, output, cache, workers
        self.cancel_event = threading.Event()
        self.search_mode = search_mode

    def run(self):
        try:
            self.result.emit(run_pipeline(*self.arguments, progress=self.progress.emit,
                                          cancel_event=self.cancel_event, search_mode=self.search_mode))
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class PreviewSignals(QObject):
    ready = pyqtSignal(int, object)


class ApplyThread(QThread):
    progress = pyqtSignal(int, int)
    prepared = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, model, filename, view):
        super().__init__()
        self.model = model
        self.filename, self.view = filename, view
        self.cancel_event = threading.Event()

    def run(self):
        try:
            apply_files(self.model, self.filename, self.view, self.progress.emit, self.cancel_event)
            self.prepared.emit(self.model)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class PreviewCache:
    def __init__(self):
        self.entries, self.lock = OrderedDict(), threading.Lock()

    def get(self, path):
        with self.lock:
            if path in self.entries:
                self.entries.move_to_end(path)
                return self.entries[path].copy()
        image = open_rgb(path)
        image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        with self.lock:
            self.entries[path] = image
            if len(self.entries) > 6:
                self.entries.popitem(last=False)
        return image.copy()


class PreviewJob(QRunnable):
    def __init__(self, token, a, b, cache):
        super().__init__()
        self.token, self.a, self.b, self.cache = token, a, b, cache
        self.signals = PreviewSignals()

    def run(self):
        try:
            a = self.cache.get(self.a) if self.a else None
            b = self.cache.get(self.b) if self.b else None
            if a is not None and b is not None:
                reference = np.asarray(a.resize((72, 96))).astype(float)
                variants = [b.rotate(angle, expand=True) for angle in (0, 90, 180, 270)]
                b = min(variants, key=lambda image: np.mean((np.asarray(image.resize((72, 96)))-reference)**2))
            self.signals.ready.emit(self.token, {"a": a, "b": b})
        except Exception as error:
            self.signals.ready.emit(self.token, {"error": str(error)})


class ExifJob(QRunnable):
    def __init__(self, token, paths, cache, lock):
        super().__init__()
        self.token, self.paths, self.cache = token, paths, cache
        self.lock = lock
        self.signals = PreviewSignals()

    def run(self):
        values = []
        for path in self.paths:
            try:
                if path is None:
                    values.append({"rows": {}})
                    continue
                stat = Path(path).stat()
                key = (path, stat.st_size, stat.st_mtime_ns)
                with self.lock:
                    rows = self.cache.get(key)
                if rows is None:
                    rows = read_exif(path)
                    with self.lock:
                        if len(self.cache) >= 64:
                            self.cache.pop(next(iter(self.cache)))
                        self.cache[key] = rows
                values.append({"rows": rows})
            except Exception as error:
                values.append({"rows": {}, "error": str(error)})
        self.signals.ready.emit(self.token, values)


class CoverJob(QRunnable):
    def __init__(self, token, group, path):
        super().__init__()
        self.token, self.group, self.path = token, group, path
        self.signals = PreviewSignals()

    def run(self):
        try:
            image = open_rgb(self.path)
            image.thumbnail((60, 44), Image.Resampling.LANCZOS)
            self.signals.ready.emit(self.token, {"group": self.group, "image": image})
        except Exception:
            self.signals.ready.emit(self.token, {"group": self.group})


class MainWindow(WorkspaceSession, QMainWindow):
    def __init__(self, review=None, sources=None, state_dir=None):
        super().__init__()
        pillow_heif.register_heif_opener()
        self.state_dir = Path(state_dir or QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        self.settings = Preferences.load(self.state_dir/"settings.json")
        self.checked_files = set()
        self.best_fields, self.suggested = {}, set()
        self.model, self.scan_thread = None, None
        self.apply_thread, self.prepared_model = None, None
        self.apply_path = None
        self.apply_error, self.close_after_apply = None, False
        self.current_group, self.a, self.b = None, None, None
        self.dirty, self.close_after_scan = False, False
        self.preview_token = 0
        self.loading_preview, self.preview_error = False, False
        self.preview_cache = PreviewCache()
        self.preview_pool = QThreadPool(self)
        self.preview_pool.setMaxThreadCount(2)
        self.cover_pool = QThreadPool(self)
        self.cover_pool.setMaxThreadCount(1)
        self.project_path = None
        self.plan_open = False
        self.current_page, self.resume_page = "setup", "review"
        self.outcome_data = None
        self.scan_failure, self.scan_was_cancelled = None, False
        self.scan_summary = ""
        self.work_progress = (0, 0)
        self.scan_eta = None
        self.cover_icons, self.cover_pending = {}, set()
        self.exif_cache, self.exif_token = {}, 0
        self.exif_lock = threading.Lock()
        self.exif_loading = False
        self.review_token = 0
        self.colors = PALETTES["dark"]
        build_canvas(self)
        self.theme = ThemeController(self, self.settings.theme)
        self.review_keys = ReviewKeys(self)
        self.save_feedback_timer = QTimer(self)
        self.save_feedback_timer.setSingleShot(True)
        self.save_feedback_timer.timeout.connect(self.reset_save_feedback)
        self.eta_timer = QTimer(self)
        self.eta_timer.setInterval(1000)
        self.eta_timer.timeout.connect(self.update_scan_eta)
        QShortcut(QKeySequence.Save, self, activated=lambda: self.save_project())
        QShortcut(QKeySequence.Open, self, activated=self.open_project)
        QShortcut(QKeySequence.Undo, self, activated=lambda: self.review_keys.press(self.undo_button))
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=lambda: self.review_keys.press(self.redo_button))
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=lambda: self.review_keys.press(self.redo_button))
        self.group_tree.verticalScrollBar().valueChanged.connect(self.load_visible_covers)
        for path in sources or []:
            self.add_source(path)
        self.update_actions()
        if review:
            self.set_review(review)

    def add_source(self, path):
        path = str(Path(path).resolve())
        if path not in [self.sources.item(i).text() for i in range(self.sources.count())]:
            self.sources.addItem(path)
            self.sources.item(self.sources.count()-1).setToolTip(path)
            self.sources.item(self.sources.count()-1).setSizeHint(QSize(400, 38))
        self.refresh_workflow()

    @property
    def busy(self):
        return self.scan_thread is not None or self.apply_thread is not None

    def start_apply_changes(self):
        if self.busy or not self.model or not (self.model.recycles or self.model.replacements or self.model.moves):
            return
        self.prepared_model = self.apply_error = None
        self.apply_path = self.project_path or self.state_dir/"projects"/("session-"+uuid.uuid4().hex[:12]+".cmp.json")
        self.apply_thread = ApplyThread(deepcopy(self.model), self.apply_path, self.session_view())
        self.apply_thread.prepared.connect(self.apply_prepared)
        self.apply_thread.failed.connect(self.apply_failed)
        self.apply_thread.progress.connect(self.apply_progress)
        self.apply_thread.finished.connect(self.apply_finished)
        self.progress.setRange(0, 0)
        self.operation_title.setText("Проверка и применение изменений")
        self.operation_message.setText("Проверяем исходные фотографии…")
        self.operation_counter.setText("Подготавливаем проверку")
        self.operation_detail.setText("Удаляемые файлы отправляются " + self.model.deletion_description +
                                      ". Переносы и замены будут применены, прогресс сохранится автоматически.")
        self.stop_button.setText("Отменить применение")
        self.stop_button.setEnabled(True)
        self.statusBar().showMessage("Проверяем и применяем выбранные изменения")
        self.start_button.setEnabled(False)
        self.update_actions()
        self.apply_thread.start()

    def apply_prepared(self, model):
        self.prepared_model = model

    def invalidate_previews(self):
        self.preview_token += 1
        self.review_token += 1
        self.exif_token += 1
        self.preview_pool.clear()
        self.cover_pool.clear()
        self.preview_cache = PreviewCache()
        self.exif_cache = {}
        self.exif_loading = False
        self.cover_icons.clear()
        self.cover_pending.clear()

    def apply_failed(self, error):
        self.apply_error = error

    def apply_progress(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.operation_counter.setText(f"Обработано: {done:,} из {total:,}".replace(",", " "))

    def apply_finished(self):
        if self.prepared_model is None and self.model is not None:
            # A rolled-back worker may have saved a new baseline. Keep its
            # revision so a retry is not mistaken for an external overwrite.
            self.model._project_versions = deepcopy(getattr(self.apply_thread.model, "_project_versions", {}))
        self.apply_thread = None
        self.statusBar().clearMessage()
        self.progress.setRange(0, 100)
        self.start_button.setEnabled(True)
        if self.prepared_model is not None:
            self.apply_changes(prepared=self.prepared_model)
            self.progress.setValue(100)
        elif self.apply_error:
            self.show_outcome({"kind": "apply_error", "title": "Не удалось применить изменения", "body": self.apply_error,
                               "detail": "Решения остались в текущем сравнении. Проверьте подробности ошибки перед повторным применением."})
        else:
            self.show_outcome({"kind": "apply_cancelled", "title": "Применение отменено",
                               "body": "Все решения остались в рабочей сессии.",
                               "detail": "Можно вернуться к изменениям или сохранить прогресс. Файловые изменения отменены."})
        self.prepared_model = None
        self.update_actions()
        if self.close_after_apply:
            self.close_after_apply = False
            self.close()

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Добавить папку-источник")
        if path:
            self.add_source(path)

    def remove_folders(self):
        for item in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(item))
        self.refresh_workflow()

    def open_about(self):
        AboutDialog(self).exec_()

    def open_settings(self):
        if self.busy:
            return
        current = deepcopy(self.settings)
        if self.model:
            current.delete_mode, current.delete_folder = self.model.delete_mode, self.model.delete_folder
        dialog = SettingsDialog(current, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        settings = dialog.value()
        try:
            settings.save(self.state_dir/"settings.json")
        except OSError as error:
            QMessageBox.warning(self, "Настройки не сохранены", str(error))
            return
        self.settings = settings
        self.theme.set_mode(settings.theme)
        if self.model:
            if (self.model.delete_mode, self.model.delete_folder) != (settings.delete_mode, settings.delete_folder):
                self.model.configure_deletion(settings.delete_mode, settings.delete_folder)
                self.dirty = True
            self.refresh()
        self.refresh_workflow()

    def change_cpu_budget(self, value):
        if self.busy or value == self.settings.cpu_percent:
            self.refresh_workflow()
            return
        settings = deepcopy(self.settings)
        settings.cpu_percent = value
        try:
            settings.save(self.state_dir/"settings.json")
        except OSError as error:
            QMessageBox.warning(self, "Настройки не сохранены", str(error))
        else:
            self.settings = settings
        self.refresh_workflow()

    def open_original(self, side):
        path = (self.a, self.b)[side]
        path = self.model.physical_path(path) if path and self.model else None
        if path and not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, "Не удалось открыть фото", "Назначьте программу просмотра для этого формата.\n"+path)

    def confirm_new_result(self):
        if not self.dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Есть несохранённые решения")
        dialog.setText("Сохранить прогресс сравнения перед закрытием или началом нового сравнения?")
        dialog.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        for role, text in ((QMessageBox.Save, "Сохранить прогресс"),
                           (QMessageBox.Discard, "Не сохранять"), (QMessageBox.Cancel, "Отмена")):
            dialog.button(role).setText(text)
        dialog.setDefaultButton(QMessageBox.Save)
        dialog.setEscapeButton(QMessageBox.Cancel)
        try:
            answer = dialog.exec_()
        finally:
            dialog.deleteLater()
        return answer == QMessageBox.Discard or (answer == QMessageBox.Save and self.save_project())

    def new_comparison(self):
        if self.busy or not self.confirm_new_result():
            return
        self.model = self.project_path = None
        self.current_group = self.a = self.b = None
        self.review_token += 1
        self.preview_token += 1
        self.exif_token += 1
        self.preview_pool.clear()
        self.cover_pool.clear()
        self.preview_cache = PreviewCache()
        self.cover_icons.clear()
        self.cover_pending.clear()
        self.checked_files.clear()
        self.loading_preview = self.preview_error = self.exif_loading = False
        self.sources.clear()
        self.group_tree.clear()
        self.files.setRowCount(0)
        self.plan_table.setRowCount(0)
        self.exif_button.setChecked(False)
        self.exif_tree.clear()
        self.exif_names.clear()
        self.statusBar().clearMessage()
        self.current_page = self.resume_page = "setup"
        self.outcome_data = self.scan_failure = None
        self.scan_was_cancelled = False
        self.scan_summary = ""
        self.work_progress = (0, 0)
        self.dirty = False
        self.update_project_caption()
        self.update_actions()

    def start_scan(self):
        if self.busy or not self.confirm_new_result():
            return
        sources = [self.sources.item(i).text() for i in range(self.sources.count())]
        if not sources:
            QMessageBox.information(self, "Папки-источники", "Добавьте хотя бы одну папку.")
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run = self.state_dir/"runs"/(datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
        workers = max(1, (os.cpu_count() or 1)*self.settings.cpu_percent//100)
        self.scan_thread = ScanThread(sources, run, self.state_dir/"features.sqlite", workers, self.settings.search_mode)
        self.scan_thread.progress.connect(self.scan_progress)
        self.scan_thread.result.connect(self.scan_result)
        self.scan_thread.failed.connect(self.scan_failed)
        self.scan_thread.cancelled.connect(self.scan_cancelled)
        self.scan_thread.finished.connect(self.scan_finished)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.add_source_button.setEnabled(False)
        self.remove_source_button.setEnabled(False)
        self.sources.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.scan_failure, self.scan_was_cancelled = None, False
        self.scan_summary = ""
        self.progress.setRange(0, 0)
        self.operation_title.setText("Поиск похожих фотографий")
        self.scan_eta = ScanETA()
        self.update_scan_eta()
        self.eta_timer.start()
        self.operation_message.setText("Собираем список фотографий…")
        self.operation_counter.setText(f"Папок-источников: {len(sources)}")
        self.operation_detail.setText("Фотографии читаются без изменений. После поиска можно будет выбрать, какие варианты оставить.")
        self.stop_button.setText("Остановить поиск")
        self.statusBar().clearMessage()
        self.update_actions()
        self.scan_thread.start()

    def stop_scan(self):
        if self.scan_thread:
            self.scan_thread.cancel_event.set()
            self.statusBar().showMessage("Остановка поиска…")

    def scan_progress(self, value):
        stage = value["stage"]
        if self.scan_eta:
            self.scan_eta.update(stage, value['done'], value['total'])
            self.update_scan_eta()
        names = {"prepare": "Читаем фотографии", "index": "Готовим поиск", "compare": "Ищем похожие кадры", "metadata": "Готовим результаты"}
        ranges = {"prepare": (0, 65), "index": (65, 5), "compare": (70, 20), "metadata": (90, 10)}
        start, span = ranges[stage]
        self.progress.setRange(0, 100)
        self.progress.setValue(start+round(span*value["done"]/max(1, value["total"])))
        self.operation_message.setText(names[stage])
        self.operation_counter.setText(f"Обработано файлов: {value['done']:,} из {value['total']:,}".replace(",", " "))

    def update_scan_eta(self):
        if self.scan_eta:
            self.operation_eta.setText(self.scan_eta.text())

    def scan_result(self, result):
        self.set_review(result["review_path"])
        self.dirty = True
        self.update_project_caption()
        self.progress.setValue(100)
        errors = len(result["report"]["errors"])
        self.scan_summary = f"Найдено групп: {result['groups']} · кандидатов: {result['review_files']} · ошибок чтения: {errors}"

    def scan_finished(self):
        self.eta_timer.stop()
        self.scan_thread = None
        self.statusBar().clearMessage()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.add_source_button.setEnabled(True)
        self.remove_source_button.setEnabled(True)
        self.sources.setEnabled(True)
        self.apply_button.setEnabled(self.model is not None)
        self.update_actions()
        if self.scan_failure:
            self.show_outcome({"kind": "scan_error", "title": "Поиск не завершён", "body": self.scan_failure,
                               "detail": "Проверьте папки-источники и доступ к файлам. Текущее сравнение и прежние решения сохранены в рабочей сессии."})
        elif self.scan_was_cancelled:
            self.show_outcome({"kind": "scan_cancelled", "title": "Поиск остановлен",
                               "body": "Уже подготовленные данные будут использованы при следующем поиске.",
                               "detail": "Можно вернуться к выбору папок. Текущее сравнение и прежние решения остались в рабочей сессии."})
        else:
            self.show_review()
            if self.current_page == "review":
                self.statusBar().showMessage(self.scan_summary)
            if self.model and self.model.document.get("scan_errors"):
                self.status_notice(self.scan_summary)
        if self.close_after_scan:
            self.close_after_scan = False
            self.close()

    def set_review(self, path):
        model = ActionPlan(path)
        model.configure_deletion(self.settings.delete_mode, self.settings.delete_folder)
        self.attach_model(model)

    def attach_model(self, model):
        self.model = model
        self.current_group = self.a = self.b = None
        self.current_page = self.resume_page = "review"
        self.outcome_data = None
        self.project_path = None
        self.review_token += 1
        self.cover_icons.clear()
        self.cover_pending.clear()
        self.checked_files.clear()
        self.dirty = False
        self.sources.clear()
        for root in self.model.document.get("source_roots") or [self.model.document["source_root"]]:
            self.add_source(root)
        self.current_group = next(iter(self.model.visible_groups()), None)
        self.refresh()

    def select_group(self, item, previous=None):
        if item and self.model:
            self.current_group = item.data(0, Qt.UserRole)
            self.refresh_group()

    def refresh(self, changed_groups=None):
        if not self.model:
            return
        visible = self.model.visible_groups()
        if self.current_group not in visible:
            self.current_group = next(iter(visible), None)
        full = changed_groups is None or not hasattr(self, "group_items")
        scroll = self.group_tree.verticalScrollBar().value() if full else None
        self.group_tree.blockSignals(True)
        if full:
            self.group_tree.clear()
            self.group_items = {}
            self.finished_groups = set()
        for group in visible if full else changed_groups:
            if group not in self.model.groups:
                continue
            members = self.model.groups[group]
            live = self.model.live(group)
            _, pending = self.model.next_pair(group, initialize=False)
            if pending is None:
                self.finished_groups.add(group)
            else:
                self.finished_groups.discard(group)
            planned = len(set(members) & (self.model.recycles | self.model.replacements.keys()))
            suffix = " · есть решения" if planned else ""
            finished = " · просмотрена" if pending is None else ""
            item = self.group_items.get(group)
            if item is None:
                item = QTreeWidgetItem()
                item.setSizeHint(0, QSize(220, 61))
                item.setData(0, Qt.UserRole, group)
                self.group_items[group] = item
                self.group_tree.addTopLevelItem(item)
            item.setText(0, f"Группа {group}{finished}\nОстанется {len(live)} из {len(members)}{suffix}")
            if group in self.cover_icons:
                item.setIcon(0, self.cover_icons[group])
        self.group_tree.setCurrentItem(self.group_items.get(self.current_group))
        self.group_tree.blockSignals(False)
        if scroll is not None:
            self.group_tree.verticalScrollBar().setValue(scroll)
        current_item = self.group_items.get(self.current_group)
        if current_item is not None:
            self.group_tree.scrollToItem(current_item, QAbstractItemView.EnsureVisible)
        completed = len(self.model.groups)-len(visible)+len(self.finished_groups)
        self.work_progress = (completed, len(self.model.groups))
        self.plan_dirty = True
        self.refresh_group()
        kept = len(self.model.kept - set(self.model.replacements.values()))
        actions = len(self.model.recycles)+len(self.model.replacements)+len(self.model.moves.keys()-self.model.replacements.keys())
        self.summary.setText(f"Групп для сравнения: {len(visible)}\n"
                             f"Оставить: {kept}; действий: {actions}\n"
                             f"Исключено из сравнений: {len(self.model.inactive)}")
        scanned = self.model.document.get("scanned_files")
        self.archive_summary.setText((f"Проверено файлов: {scanned}\n" if scanned is not None else "")+
                                    f"Кандидатов: {len(self.model.files)} · групп: {len(visible)}")
        applied = sum(len(b["actions"]) for b in self.model.applied_batches)
        self.execution_label.setText("До применения файлы остаются на месте"+(f" · уже выполнено действий: {applied}" if applied else ""))
        self.apply_button.setEnabled(not self.busy and bool(actions))
        self.save_button.setEnabled(not self.busy)
        self.update_project_caption()
        self.refresh_workflow()
        QTimer.singleShot(0, self.load_visible_covers)

    def refresh_plan(self):
        if not self.model or not getattr(self, "plan_dirty", True):
            return
        data = self.model.build(check_disk=False)
        rows = []
        for path in data["kept_files"]:
            rows.append(("Оставить", path, "На месте"))
        for action in data["actions"]:
            if action["kind"] == "recycle":
                rows.append((self.model.deletion_label, action["path"],
                             TRASH_LABEL if action["destination"] == "recycle_bin" else action["destination"]))
            elif action["kind"] == "replace":
                removed_to = TRASH_LABEL if action["old_file_destination"] == "recycle_bin" else action["old_file_destination"]
                rows.append(("Заменить", action["source"]+" → вместо "+action["target"],
                             action["destination"] + "\nУбираемый файл → " + removed_to))
            else:
                rows.append(("Переместить", action["path"], action["destination"]))
        self.plan_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.plan_table.setItem(i, j, item)
        self.plan_table.resizeRowsToContents()
        self.plan_dirty = False

    def refresh_group(self, preserve_pair=False):
        if not self.model:
            return
        if self.current_group is None:
            self.a = self.b = None
            self.loading_preview = False
            self.preview_token += 1
            self.files.setRowCount(0)
            for table, path_field in zip(self.parameter_tables, self.path_fields):
                table.clearContents()
                path_field.clear()
            for reason in self.reason_labels:
                reason.clear()
                reason.setToolTip("")
            self.group_caption.setText("Групп для сравнения нет")
            self.recommendation.clear()
            self.pair_caption.setText("Похожих изображений не найдено")
            for view, title, metadata in zip(self.views, self.titles, self.metadata):
                view.show_pixmap(pixmap(Image.new("RGB", (1, 1), self.colors["bg"])))
                title.clear()
                metadata.clear()
            self.update_actions()
            self.request_exif()
            return
        pair = (self.a, self.b)
        live = self.model.live(self.current_group)
        if live and self.model.references.get(self.current_group) not in live:
            preferred = recommendations([self.model.files[p] for p in live], self.settings)
            header = self.files.horizontalHeader()
            column = header.sortIndicatorSection()
            def order_key(path):
                value = self.file_sort_values(path)[column]
                return value is not None, value
            candidates = [p for p in live if p in preferred] or live
            first = sorted(candidates, key=order_key, reverse=header.sortIndicatorOrder() == Qt.DescendingOrder)[0]
            self.model.set_reference(first)
        self.a, self.b = self.model.next_pair(self.current_group)
        if (preserve_pair and pair[0] in self.model.live(self.current_group)
                and pair[1] in self.model.live(self.current_group) and pair[0] != pair[1]):
            self.a, self.b = pair
        live = self.model.live(self.current_group)
        self.group_caption.setText(f"Группа {self.current_group} · {len(live)} активных кандидатов")
        self.checked_files.difference_update(self.model.inactive)
        active_rows = [self.model.files[p] for p in live]
        self.best_fields = best_parameters(active_rows, self.settings) if self.settings.highlight_best else {}
        self.suggested = recommendations(active_rows, self.settings) if self.settings.suggest_best and len(live) > 1 else set()
        if len(self.suggested) == 1:
            path = next(iter(self.suggested))
            self.recommendation.setText("Совет: " + Path(path).name)
        elif self.suggested:
            self.recommendation.setText(f"Равноценных вариантов: {len(self.suggested)}")
        else:
            self.recommendation.clear()
        self.recommendation.setToolTip(self.recommendation.text()+"\nРешение за вами. Приоритет: " +
                                      " → ".join(RULE_LABELS[k] for k in self.settings.enabled_rules))
        members = self.model.groups[self.current_group] if self.show_excluded.isChecked() else live
        self.files.blockSignals(True)
        self.files.setSortingEnabled(False)
        self.files.setRowCount(len(members))
        removals = (self.model.removal_destinations(check_disk=False)
                    if any(p in self.model.inactive for p in members) else {})
        for i, path in enumerate(members):
            row = self.model.files[path]
            state = self.model.status(path)
            date, date_key = preferred_date(row, self.settings)
            effective = Path(self.model.effective_path(path))
            removed_to = removals.get(path)
            if removed_to and removed_to != "recycle_bin":
                effective = Path(removed_to)
            destination_text = TRASH_LABEL if removed_to == "recycle_bin" else str(effective)
            values = ("", ("A · опорный · " if path == self.a else "")+effective.name,
                      self.file_status_text(path)+(" · совет" if path in self.suggested else ""),
                      row.get("detected_format") or Path(path).suffix,
                      f"{row['stored_width']}×{row['stored_height']}", f"{row['bytes']/1024:.0f} КБ",
                      date or "—", row.get("camera") or "—", str(exif_completeness(row)),
                      TRASH_LABEL if removed_to == "recycle_bin" else str(effective.parent))
            rule_columns = {3: "heic", 4: "resolution", 6: "date", 7: "camera", 8: "exif"}
            sort_values = self.file_sort_values(path)
            for j, text in enumerate(values):
                item = FileTableItem(text, sort_values[j])
                item.setData(Qt.UserRole, path)
                item.setToolTip(f"Расположение после применения: {destination_text}\nИсходный файл: {self.model.physical_path(path)}")
                if state in ("recycle", "replaced", "applied"):
                    item.setBackground(QColor(self.colors["bad_bg"]))
                if j == 0:
                    if path not in self.model.inactive:
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        item.setCheckState(Qt.Checked if path in self.checked_files else Qt.Unchecked)
                        item.setToolTip("Выбрать файл для действия: удалить или сделать опорным")
                    else:
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                if path in self.best_fields.get(rule_columns.get(j), set()):
                    item.setBackground(QColor(self.colors["good_bg"]))
                    item.setForeground(QColor(self.colors["good"]))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip(RULE_LABELS[rule_columns[j]]+" · лучший параметр в группе\n"+path)
                if j == 6 and date_key:
                    item.setToolTip(item.toolTip()+"\nДата: "+DATE_LABELS[date_key])
                if j == 8:
                    item.setToolTip(item.toolTip()+"\n"+("Заполненных EXIF-тегов" if "exif_filled_tags" in row else
                                                       "Число EXIF-тегов (старый результат поиска)"))
                if j == 1 and path in self.suggested:
                    item.setBackground(QColor(self.colors["recommended_bg"]))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QColor(self.colors["good"]))
                self.files.setItem(i, j, item)
        self.files.setSortingEnabled(True)
        self.files.resizeRowsToContents()
        for i in range(self.files.rowCount()):
            if self.files.item(i, 0).data(Qt.UserRole) == self.b:
                self.files.selectRow(i)
                break
        self.files.blockSignals(False)
        self.present_pair()

    def file_sort_values(self, path):
        row = self.model.files[path]
        effective = Path(self.model.effective_path(path))
        date, _ = preferred_date(row, self.settings)
        return (path in self.checked_files, effective.name.casefold(), self.file_status_text(path).casefold(),
                (row.get("detected_format") or Path(path).suffix).casefold(),
                row["stored_width"]*row["stored_height"], row["bytes"], parse_date(date),
                (row.get("camera") or "").casefold(), exif_completeness(row), str(effective.parent).casefold())

    def check_file(self, item):
        if item.column() != 0 or not self.model:
            return
        path = item.data(Qt.UserRole)
        if path not in self.model.inactive:
            if item.checkState() == Qt.Checked:
                self.checked_files.add(path)
            else:
                self.checked_files.discard(path)
        self.update_actions()

    def delete_selected(self):
        if not self.model or self.current_group is None or self.busy:
            return
        selected = self.checked_files & set(self.model.live(self.current_group))
        try:
            self.model.remove_selected(self.current_group, selected)
            self.dirty = True
            self.refresh_after_decision({self.current_group})
        except ValueError as error:
            QMessageBox.information(self, "Намерение не добавлено", str(error))

    def delete_side(self, side):
        if (not self.model or self.busy or self.loading_preview or self.preview_error
                or side not in (0, 1)):
            return
        path = (self.a, self.b)[side]
        if path is not None and path not in self.model.inactive:
            self.remove_files({path})

    def discard_group(self):
        if self.model and self.current_group is not None and not self.busy:
            self.model.discard_group(self.current_group)
            self.dirty = True
            self.refresh_after_decision({self.current_group})

    def select_file(self, row, *_):
        if row < 0 or not self.model or not self.files.item(row, 0):
            return
        path = self.files.item(row, 0).data(Qt.UserRole)
        if path != self.a and path not in self.model.inactive:
            self.b = path
            self.present_pair()

    def make_reference(self):
        if not self.model or self.current_group is None or self.busy:
            return
        selected = self.checked_files & set(self.model.live(self.current_group))
        if len(selected) == 1:
            try:
                self.model.set_reference(next(iter(selected)), record=True)
                self.dirty = True
                self.refresh(changed_groups={self.current_group})
            except ValueError as error:
                QMessageBox.information(self, "Опорный кадр", str(error))

    def present_pair(self):
        self.preview_token += 1
        self.preview_pool.clear()
        self.loading_preview, self.preview_error = bool(self.a), False
        rows = [self.model.files[p] for p in (self.a, self.b) if p]
        pair_best = best_parameters(rows, self.settings) if self.settings.highlight_best else {}
        reasons = recommendation_reasons(
            [self.model.files[p] for p in self.model.live(self.current_group)],
            self.settings, [p for p in (self.a, self.b) if p])
        def date_text(value):
            if parse_date(value) is None:
                return value or "—"
            normalized = value.strip()
            if normalized[4] == ":":
                normalized = normalized[:4]+"-"+normalized[5:7]+"-"+normalized[8:]
            return datetime.fromisoformat(normalized).strftime("%d.%m.%Y\n%H:%M:%S")
        for i, path in enumerate((self.a, self.b)):
            card = self.photo_cards[i]
            recommended = bool(path and path in self.suggested)
            if card.property("recommended") != recommended:
                card.setProperty("recommended", recommended)
                card.style().unpolish(card)
                card.style().polish(card)
                card.update()
                for child in (self.views[i], self.parameter_tables[i]):
                    child.style().unpolish(child)
                    child.style().polish(child)
                    child.update()
            self.parameter_tables[i].clearContents()
            self.path_fields[i].clear()
            reason = reasons.get(path)
            self.reason_labels[i].setText("Основание:\n"+reason.get("short_text", reason["text"]) if reason else "")
            self.reason_labels[i].setToolTip(reason["text"]+"\n"+reason["detail"] if reason else "")
            self.views[i].show_pixmap(pixmap(Image.new("RGB", (1, 1), self.colors["bg"])))
            if path:
                row = self.model.files[path]
                effective = Path(self.model.effective_path(path))
                recommended = " · рекомендуем" if path in self.suggested else ""
                self.titles[i].setText(("A · опорный" if i == 0 else "B · кандидат")+" · "+effective.name+recommended)
                self.titles[i].setToolTip(f"Расположение после применения: {effective}\nИсходный файл: {self.model.physical_path(path)}")
                self.path_fields[i].setPlainText(str(effective))
                self.path_fields[i].setToolTip(self.titles[i].toolTip())
                date, date_key = preferred_date(row, self.settings)
                values = (("Формат", row.get("detected_format") or effective.suffix, "heic", None),
                          ("Разрешение", f"{row['stored_width']}×{row['stored_height']}", "resolution", None),
                          ("Размер", f"{row['bytes']/1024:.0f} КБ", None, None),
                          ("Съёмка", date_text(row.get("captured_at")), "date" if date_key == "captured_at" else None, row.get("captured_at")),
                          ("Камера", row.get("camera") or "—", "camera", None),
                          ("EXIF", str(exif_completeness(row)), "exif", None),
                          ("Создан", date_text(row.get("created_at")), "date" if date_key == "created_at" else None, row.get("created_at")),
                          ("Изменён", date_text(row.get("modified_at")), "date" if date_key == "modified_at" else None, row.get("modified_at")))
                for n, (label, value, key, full) in enumerate(values):
                    grid_row, column = n//2, (n % 2)*2
                    name = QTableWidgetItem(label)
                    name.setForeground(QColor(self.colors["muted"]))
                    self.parameter_tables[i].setItem(grid_row, column, name)
                    item = QTableWidgetItem(value)
                    item.setToolTip(full or value)
                    if path in pair_best.get(key, set()):
                        item.setForeground(QColor(self.colors["good"]))
                        item.setBackground(QColor(self.colors["good_bg"]))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    self.parameter_tables[i].setItem(grid_row, column+1, item)
            else:
                self.titles[i].setText("Других активных кандидатов не осталось")
                self.metadata[i].clear()
                self.views[i].show_pixmap(pixmap(Image.new("RGB", (1, 1), self.colors["bg"])))
        pair = next((p for p in self.model.document["pairs"]
                     if {p.get("path1"), p.get("path2")} == {self.a, self.b}), None) if self.b else None
        self.pair_caption.setText((f"Похожесть: {pair['similarity']*100:.2f}%" if pair else
                                  "Сравнение активных кадров внутри группы") if self.b else
                                 "Сравнение группы завершено. Выберите следующую группу или проверьте изменения.")
        self.update_actions()
        if self.a:
            job = PreviewJob(self.preview_token, self.model.physical_path(self.a),
                             self.model.physical_path(self.b) if self.b else None, self.preview_cache)
            job.signals.ready.connect(self.preview_ready)
            self.preview_pool.start(job, 1)
        self.request_exif()

    def start_exif_job(self, token, paths):
        self.exif_loading = True
        job = ExifJob(token, paths, self.exif_cache, self.exif_lock)
        job.signals.ready.connect(self.finish_exif)
        self.preview_pool.start(job, 0)

    def finish_exif(self, token, result):
        if token == self.exif_token:
            self.exif_loading = False
        self.exif_ready(token, result)

    def preview_ready(self, token, result):
        if token != self.preview_token:
            return
        self.loading_preview = False
        if "error" in result:
            self.preview_error = True
            self.statusBar().showMessage("Не удалось открыть превью: "+result["error"])
            self.update_actions()
            return
        for view, image in zip(self.views, (result["a"], result["b"])):
            if image is not None:
                view.show_pixmap(pixmap(image))
        if result["a"] is not None and self.current_group is not None:
            self.cover_icons[self.current_group] = QIcon(pixmap(result["a"]).scaled(60, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            item = self.group_items.get(self.current_group)
            if item is not None:
                item.setIcon(0, self.cover_icons[self.current_group])
        self.update_actions()

    def load_visible_covers(self, *args):
        if not self.model or not self.isVisible() or self.current_page != "review":
            return
        # Only walk rows intersecting the viewport; the other thousands of rows
        # must not be visited for every scroll/preview completion.
        viewport = self.group_tree.viewport()
        item = self.group_tree.itemAt(1, 1)
        if item is None and self.group_tree.topLevelItemCount():
            item = self.group_tree.topLevelItem(0)
        while item is not None:
            current = item
            item = self.group_tree.itemBelow(item)
            rect = self.group_tree.visualItemRect(current)
            if rect.top() > viewport.height():
                break
            if not rect.intersects(viewport.rect()):
                continue
            group = current.data(0, Qt.UserRole)
            if group in self.cover_icons or group in self.cover_pending:
                continue
            live = self.model.live(group)
            if not live:
                continue
            self.cover_pending.add(group)
            token = self.review_token
            job = CoverJob(token, group, self.model.physical_path(live[0]))
            job.signals.ready.connect(self.cover_ready)
            self.cover_pool.start(job)

    def cover_ready(self, token, result):
        if token != self.review_token:
            return
        group = result["group"]
        self.cover_pending.discard(group)
        if "image" not in result:
            return
        icon = QIcon(pixmap(result["image"]))
        self.cover_icons[group] = icon
        item = self.group_items.get(group)
        if item is not None:
            item.setIcon(0, icon)

    def file_status_text(self, path):
        state = self.model.status(path)
        return self.model.deletion_label if state == "recycle" else STATUS[state]

    def update_actions(self):
        if self.dirty and hasattr(self, "save_feedback_timer") and self.save_feedback_timer.isActive():
            self.save_feedback_timer.stop()
            self.reset_save_feedback()
        enabled = bool(self.model and self.a and self.b and not self.busy
                       and not self.loading_preview and not self.preview_error)
        for button in self.pair_buttons:
            button.setEnabled(enabled)
        for button, path in zip(self.original_buttons, (self.a, self.b)):
            button.setEnabled(bool(path))
        live = self.model.live(self.current_group) if self.model and self.current_group is not None else []
        selected = self.checked_files & set(live)
        self.reference_button.setEnabled(len(selected) == 1 and not self.busy)
        self.delete_selected_button.setEnabled(bool(selected) and not self.busy)
        self.delete_selected_button.setText(f"Удалить выбранные ({len(selected)})")
        deletion = self.model.deletion_description if self.model else TRASH_DESTINATION
        for button, path, letter in zip(self.delete_side_buttons, (self.a, self.b), ("A", "B")):
            button.setEnabled(bool(self.model and path and path not in self.model.inactive
                                   and not self.busy and not self.loading_preview and not self.preview_error))
            button.setToolTip("При применении отправить только файл " + letter + " " + deletion +
                              ". Решение по другому файлу не меняется.")
        self.delete_selected_button.setToolTip("При применении отправить " + deletion + " только файлы, отмеченные галочками")
        self.discard_group_button.setEnabled(bool(live) and not self.busy)
        self.save_button.setEnabled(self.model is not None and not self.busy)
        pending = bool(self.model and (self.model.recycles or self.model.replacements or self.model.moves))
        self.apply_button.setEnabled(pending and not self.busy)
        self.exif_button.setEnabled(bool(self.a))
        self.undo_button.setEnabled(bool(self.model and self.model.history) and not self.busy)
        self.redo_button.setEnabled(bool(self.model and self.model.redo_history) and not self.busy)
        self.undo_button.setToolTip("Отменить последнее действие · ↑ / Ctrl+Z"+
                                   (f"\nДоступно шагов: {len(self.model.history)}" if self.model else ""))
        self.redo_button.setToolTip("Повторить отменённое действие · Ctrl+Y / Ctrl+Shift+Z"+
                                   (f"\nДоступно шагов: {len(self.model.redo_history)}" if self.model else ""))
        self.reset_button.setEnabled(self.current_group is not None and not self.busy)
        self.settings_button.setEnabled(not self.busy)
        groups = self.model.visible_groups() if self.model else []
        position = groups.index(self.current_group) if self.current_group in groups else -1
        self.previous_button.setEnabled(position > 0)
        self.next_button.setEnabled(0 <= position < len(groups)-1)
        if enabled:
            self.pair_buttons[0].setToolTip("↓ · Оставить оба файла и перейти к следующему сравнению")
            self.pair_buttons[1].setToolTip("← · При применении A останется на месте, B отправится " + deletion + ".")
            self.pair_buttons[2].setToolTip("→ · При применении B останется на месте, A отправится " + deletion + ".")
            for button, source, target, letter, other in (
                    (self.pair_buttons[3], self.a, self.b, "A", "B"),
                    (self.pair_buttons[4], self.b, self.a, "B", "A")):
                owner = next((t for t, s in self.model.replacements.items() if s == target), target)
                destination = self.model.destination(owner, source)
                display_path = escape(destination).replace("\\", "\\\u200b").replace("/", "/\u200b")
                diagram = QUrl.fromLocalFile(str(Path(__file__).parent/"assets"/f"move-{letter.lower()}-to-{other.lower()}.svg")).toString()
                button.setToolTip(f'<b>Перенести {letter} в папку {other}</b><br>'
                    f'<img src="{diagram}" width="344" height="124"><br>'
                    f'При применении файл {other} будет удалён {escape(deletion)}.<br>'
                    f'Файл {letter} займёт его место и имя.<br><br>'
                    f'Итоговый путь:<br>{display_path}')
        self.refresh_workflow()

    def choose(self, action):
        if not self.model or not self.a or not self.b or self.busy or self.loading_preview or self.preview_error:
            return
        try:
            self.model.choose(self.a, self.b, action)
            self.dirty = True
            self.refresh_after_decision({self.current_group})
        except ValueError as error:
            QMessageBox.information(self, "Намерение не добавлено", str(error))

    def undo(self):
        self.step_history(False)

    def redo(self):
        self.step_history(True)

    def step_history(self, forward):
        if not self.model or self.busy:
            return
        before = self.model._snapshot()
        before_inactive = set(self.model.inactive)
        if not (self.model.redo() if forward else self.model.undo()):
            return
        after = self.model._snapshot()
        changed_pairs = before[4] ^ after[4]
        pair_files = set().union(*changed_pairs) if changed_pairs else set()
        changed = (before[0] ^ after[0]) | (before[1] ^ after[1])
        for index in (2, 5):
            for path in before[index].keys() | after[index].keys():
                if before[index].get(path) != after[index].get(path):
                    changed.add(path)
        reference_groups = {g for g in before[3].keys() & after[3].keys() if before[3][g] != after[3][g]}
        references = {p for g in reference_groups for p in (before[3][g], after[3][g])}
        affected = pair_files or changed or references
        target = next((p for p in self.model.files if p in affected), None)
        if target:
            self.current_group = str(self.model.files[target]["group_id"])
        self.current_page = self.resume_page = "review"
        self.outcome_data = None
        self.dirty = True
        self.refresh(changed_groups={self.current_group} | {str(self.model.files[p]["group_id"]) for p in affected})
        if not forward:
            # Restore the actual pair, even if B was selected manually out of queue order.
            undone_pairs = before[4] - after[4]
            pair = next(iter(undone_pairs)) if len(undone_pairs) == 1 else set()
            candidates = pair or (before_inactive - self.model.inactive)
            restored_b = next((p for p in self.model.files if p in candidates and p != self.a
                               and p not in self.model.inactive
                               and str(self.model.files[p]["group_id"]) == self.current_group), None)
            if restored_b:
                self.b = restored_b
                self.files.blockSignals(True)
                for row in range(self.files.rowCount()):
                    if self.files.item(row, 0).data(Qt.UserRole) == restored_b:
                        self.files.selectRow(row)
                        break
                self.files.blockSignals(False)
                self.present_pair()
        else:
            self.after_decision()

    def reset_group(self):
        if self.model and self.current_group is not None and not self.busy:
            self.model.reset_group(self.current_group)
            self.dirty = True
            self.refresh(changed_groups={self.current_group})

    def closeEvent(self, event):
        if self.apply_thread:
            self.close_after_apply = True
            self.apply_thread.cancel_event.set()
            event.ignore()
            return
        if self.scan_thread:
            self.close_after_scan = True
            self.stop_scan()
            event.ignore()
            return
        self.close_after_scan = self.close_after_apply = False
        was_dirty = self.dirty
        if not self.confirm_new_result():
            event.ignore()
            return
        if self.project_path and not was_dirty and not self.save_project():
            event.ignore()
            return
        self.preview_pool.clear()
        self.preview_pool.waitForDone(5000)
        self.cover_pool.clear()
        self.cover_pool.waitForDone(5000)
        self.review_keys.close()
        self.save_feedback_timer.stop()
        self.theme.timer.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--sources", type=Path, nargs="*")
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ComparsionV2.PhotoComparison")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ComparsionV2")
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(Path(__file__).parent/"assets"/"application.ico")))
    app.setStyle("Fusion")
    if not QFontDatabase().families() and Path("C:/Windows/Fonts/arial.ttf").exists():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
        app.setFont(QFont("Arial", 10))
    window = MainWindow(args.review, args.sources, args.state_dir)
    if args.project:
        try:
            window.set_project(args.project)
        except Exception as error:
            QMessageBox.warning(window, "Не удалось загрузить сравнение", str(error))
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
