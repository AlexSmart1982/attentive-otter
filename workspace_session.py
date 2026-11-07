"""Project, planned file locations and docked EXIF behaviour for the workspace."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QMenu, QTreeWidgetItem

from project import save_project as write_project, load_project as read_project
from theme import PALETTES, history_icon
from workflow_ui import WorkflowUI
from execution import apply_files
from about_ui import APP_NAME


class WorkspaceSession(WorkflowUI):
    def theme_changed(self, name):
        self.colors = PALETTES[name]
        self.brand_wordmark.set_colors(self.colors)
        self.scan_scene.set_theme(name)
        for button, icon in ((self.undo_button, "undo"), (self.redo_button, "redo")):
            button.setIcon(history_icon(icon, self.colors))
        if self.model:
            self.refresh_group(preserve_pair=True)

    def navigate_group(self, step):
        if not self.model:
            return
        groups = self.model.visible_groups()
        if self.current_group in groups:
            position = groups.index(self.current_group)+step
            if 0 <= position < len(groups):
                self.current_group = groups[position]
                self.refresh(changed_groups=set())

    def toggle_plan(self):
        self.show_review() if self.current_page == "plan" else self.show_plan()

    def session_view(self):
        return {"group": self.current_group, "a": self.a, "b": self.b, "checked": sorted(self.checked_files),
                "sources": [self.sources.item(i).text() for i in range(self.sources.count())],
                "sort_column": self.files.horizontalHeader().sortIndicatorSection(),
                "sort_order": int(self.files.horizontalHeader().sortIndicatorOrder()),
                "file_widths": [self.files.columnWidth(i) for i in range(self.files.columnCount())],
                "plan_widths": [self.plan_table.columnWidth(i) for i in range(self.plan_table.columnCount())],
                "exif_open": self.exif_button.isChecked(), "plan_open": self.plan_open,
                "outer_sizes": self.outer.sizes(), "content_sizes": self.content_splitter.sizes(),
                "window_size": [self.width(), self.height()],
                "page": self.resume_page if self.busy else self.current_page,
                "outcome": self.outcome_data}

    def restore_view(self, view):
        sources = view.get("sources")
        if isinstance(sources, list) and all(isinstance(p, str) for p in sources):
            self.sources.clear()
            for source in sources:
                self.add_source(source)
        if view.get("group") in self.model.visible_groups():
            self.current_group = view["group"]
        self.checked_files = set(view.get("checked", [])) & (self.model.files.keys()-self.model.inactive)
        for table, key in ((self.files, "file_widths"), (self.plan_table, "plan_widths")):
            widths = view.get(key)
            if isinstance(widths, list) and len(widths) == table.columnCount() and all(type(v) is int and 40 <= v <= 3000 for v in widths):
                for column, width in enumerate(widths):
                    table.setColumnWidth(column, width)
        column, order = view.get("sort_column", 1), view.get("sort_order", 0)
        if type(column) is int and 0 <= column < self.files.columnCount() and order in (0, 1):
            self.files.horizontalHeader().setSortIndicator(column, Qt.SortOrder(order))
        self.plan_open = bool(view.get("plan_open"))
        self.refresh()
        a, b = view.get("a"), view.get("b")
        if (self.current_group is not None and a in self.model.live(self.current_group)
                and (b is None or b in self.model.live(self.current_group)) and a != b):
            self.a, self.b = a, b
            self.files.blockSignals(True)
            for index in range(self.files.rowCount()):
                if self.files.item(index, 0).data(Qt.UserRole) == b:
                    self.files.selectRow(index)
                    break
            self.files.blockSignals(False)
            self.present_pair()
        size = view.get("window_size")
        if isinstance(size, list) and len(size) == 2 and all(type(v) is int and 0 < v <= 10000 for v in size):
            self.resize(*size)
        self.exif_button.setChecked(bool(view.get("exif_open")))
        for splitter, key in ((self.outer, "outer_sizes"), (self.content_splitter, "content_sizes")):
            sizes = view.get(key)
            if isinstance(sizes, list) and len(sizes) == splitter.count() and all(type(v) is int and 0 <= v <= 30000 for v in sizes):
                splitter.setSizes(sizes)
        page = view.get("page", "plan" if view.get("plan_open") else "review")
        if page in ("setup", "review", "plan", "outcome"):
            outcome = view.get("outcome")
            self.outcome_data = None
            if isinstance(outcome, dict) and all(isinstance(outcome.get(k), str) for k in ("kind", "title", "body", "detail")):
                self.outcome_data = outcome
            self.show_page(page)

    def reset_save_feedback(self):
        self.save_button.setText("Сохранить прогресс сравнения")
        self.save_button.setToolTip("Сохранить найденные группы, все решения и место остановки одним файлом"+
                                   ("\nФайл: "+str(self.project_path) if self.project_path else ""))

    def save_project(self, save_as=False):
        if not self.model or self.busy:
            return False
        filename = self.project_path
        if save_as or filename is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Сохранить прогресс сравнения",
                str(filename or self.state_dir/"projects"/"comparison.cmp.json"), "Сохранённое сравнение (*.cmp.json *.json)")
            if not filename:
                return False
            if not Path(filename).suffix:
                filename += ".cmp.json"
        self.save_feedback_timer.stop()
        self.save_button.setMinimumWidth(max(self.save_button.minimumWidth(),
                                             self.save_button.width(), self.save_button.sizeHint().width()))
        self.save_button.setEnabled(False)
        self.save_button.setText("Сохраняю…")
        self.save_button.repaint()
        try:
            write_project(self.model, filename, self.session_view())
            self.project_path = Path(filename).resolve()
            self.dirty = False
            self.update_project_caption()
            self.save_button.setText("Сохранено · "+datetime.now().strftime("%H:%M:%S"))
            self.save_button.setToolTip("Прогресс сравнения сохранён:\n"+str(self.project_path)+
                                       "\nПовторное нажатие сохранит прогресс в этот же файл.")
            self.save_feedback_timer.start(6000)
            return True
        except Exception as error:
            self.reset_save_feedback()
            QMessageBox.warning(self, "Прогресс сравнения не сохранён", str(error))
            return False
        finally:
            self.save_button.setEnabled(self.model is not None and not self.busy)

    def set_project(self, filename):
        model, view = read_project(filename)
        migrated = model.prepare_real_execution()
        if migrated:
            view = dict(view, page="plan", outcome=None)
        self.attach_model(model)
        self.project_path = Path(filename).resolve()
        self.restore_view(view)
        if self.current_page == "review" and self.b is None:
            self.after_decision()
        self.dirty = False
        self.update_project_caption()
        self.statusBar().showMessage("Имитации прежней версии возвращены в список изменений" if migrated else
                                    "Сравнение загружено целиком · продолжайте с места остановки")

    def open_project(self):
        if self.busy or not self.confirm_new_result():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Загрузить ранее сохранённое сравнение", str(self.state_dir/"projects"),
                                                 "Сохранённое сравнение (*.cmp.json *.json)")
        if filename:
            try:
                self.set_project(filename)
            except Exception as error:
                QMessageBox.warning(self, "Не удалось загрузить сравнение", str(error))

    def update_project_caption(self):
        name = self.project_path.name if self.project_path else ""
        self.setWindowTitle(APP_NAME+(" · "+name if name else ""))

    def apply_changes(self, prepared=None):
        if not self.model or self.busy:
            return False
        filename = (self.apply_path if prepared is not None else self.project_path) or self.state_dir/"projects"/("session-"+uuid.uuid4().hex[:12]+".cmp.json")
        try:
            if prepared is not None:
                candidate = prepared
                batch = candidate.applied_batches[-1]
            else:
                candidate = deepcopy(self.model)
                batch = apply_files(candidate, filename, self.session_view())
            if batch is None:
                return False
            self.model = candidate
            self.project_path = Path(filename).resolve()
            self.invalidate_previews()
            self.refresh()
            self.show_apply_result(batch)
            self.dirty = False
            self.update_project_caption()
            self.statusBar().showMessage(f"Применено действий: {len(batch['actions'])}. Прогресс сохранён: {self.project_path}")
            return True
        except Exception as error:
            if prepared is None and "candidate" in locals():
                self.model._project_versions = deepcopy(getattr(candidate, "_project_versions", {}))
            QMessageBox.warning(self, "Не удалось завершить применение", str(error))
            return False

    def file_context_menu(self, position):
        item = self.files.itemAt(position)
        if not item or not self.model or self.busy:
            return
        path = item.data(Qt.UserRole)
        if path in self.model.inactive:
            return
        checked = self.checked_files & set(self.model.live(self.current_group))
        targets = checked if path in checked else {path}
        menu = QMenu(self)
        menu.addAction("Переместить файл…" if len(targets) == 1 else f"Переместить выбранные ({len(targets)})…",
                       lambda: self.move_selected(targets))
        menu.addAction("Отменить перемещение", lambda: self.cancel_selected_moves(targets))
        menu.addSeparator()
        if len(targets) == 1:
            menu.addAction("Сделать опорным", lambda: self.reference_file(path))
        menu.addAction("Удалить при применении", lambda: self.remove_files(targets))
        menu.exec_(self.files.viewport().mapToGlobal(position))

    def reference_file(self, path):
        if self.busy:
            return
        self.model.set_reference(path, record=True)
        self.dirty = True
        self.refresh(changed_groups={str(self.model.files[path]["group_id"])})

    def remove_files(self, paths):
        if self.busy:
            return
        try:
            self.model.remove_selected(self.current_group, paths)
            self.dirty = True
            self.refresh_after_decision({self.current_group})
        except ValueError as error:
            QMessageBox.information(self, "Намерение не добавлено", str(error))

    def move_selected(self, paths=None, folder=None):
        if not self.model or self.busy or self.current_group is None:
            return
        selected = set(paths) if paths is not None else self.checked_files & set(self.model.live(self.current_group))
        if not selected:
            return
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Папка назначения после применения")
        if not folder:
            return
        try:
            self.model.move_files(self.current_group, selected, folder)
            self.dirty = True
            self.refresh(changed_groups={self.current_group})
        except ValueError as error:
            QMessageBox.information(self, "Перемещение не добавлено", str(error))

    def cancel_selected_moves(self, paths):
        if self.busy:
            return
        try:
            self.model.cancel_moves(paths)
            self.dirty = True
            self.refresh(changed_groups={self.current_group})
        except ValueError as error:
            QMessageBox.information(self, "Не удалось отменить перемещение", str(error))

    def toggle_exif(self, opened):
        self.exif_panel.setVisible(opened)
        if opened:
            sizes = self.outer.sizes()
            self.outer.setSizes([sizes[0], max(650, self.width()-sizes[0]-390), 390])
            self.request_exif()
        else:
            self.exif_token += 1
            self.exif_loading = False

    def request_exif(self):
        if self.exif_panel.isHidden() or self.current_page != "review":
            self.exif_loading = False
            return
        self.exif_token += 1
        self.exif_tree.clear()
        self.exif_names.setText("Чтение метаданных…")
        paths = [self.model.physical_path(p) if p and self.model else None for p in (self.a, self.b)]
        self.start_exif_job(self.exif_token, paths)

    def exif_ready(self, token, values):
        if token != self.exif_token or self.exif_panel.isHidden():
            return
        names = []
        for letter, path, value in zip(("A", "B"), (self.a, self.b), values):
            name = Path(self.model.effective_path(path)).name if path else "—"
            suffix = " · "+value["error"] if "error" in value else " · EXIF отсутствует" if not value["rows"] else ""
            names.append(letter+" · "+name+suffix)
        self.exif_names.setText("\n".join(names))
        self.exif_tree.clear()
        a, b = values[0]["rows"], values[1]["rows"]
        groups = {}
        order = {"Камера": 0, "Съёмка": 1, "Даты": 2, "Изображение": 3, "GPS": 4, "Совместимость": 5}
        for key in sorted(a.keys() | b.keys(), key=lambda k: (order.get((a.get(k) or b[k])["group"], 6), (a.get(k) or b[k])["label"])):
            row = a.get(key) or b[key]
            group = row["group"]
            if group not in groups:
                groups[group] = QTreeWidgetItem([group])
                self.exif_tree.addTopLevelItem(groups[group])
                groups[group].setExpanded(True)
            item = QTreeWidgetItem([row["label"], a.get(key, {}).get("value", "—"), b.get(key, {}).get("value", "—")])
            for column in range(3):
                item.setToolTip(column, row["tag"]+"\n"+item.text(column))
            groups[group].addChild(item)
