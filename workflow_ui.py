"""Phase-specific screens: sources, machine work, review, plan and outcome."""

from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QListWidget, QVBoxLayout,
                            QHBoxLayout, QAbstractItemView, QProgressBar, QSizePolicy, QSpinBox)

from preferences import SEARCH_MODES
from execution import outcome_for_batch
from scan_visuals import ScanAnimation


def text(value, name=None):
    widget = QLabel(value)
    widget.setWordWrap(True)
    widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    if name:
        widget.setObjectName(name)
    return widget


def action(value, slot, primary=False):
    widget = QPushButton(value)
    widget.clicked.connect(slot)
    if primary:
        widget.setObjectName("primary")
    return widget


def centered_page():
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.addStretch(1)
    line = QHBoxLayout()
    line.addStretch(1)
    card = QFrame()
    card.setObjectName("phaseCard")
    card.setMaximumWidth(980)
    card.setMinimumWidth(660)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(32, 28, 32, 28)
    layout.setSpacing(12)
    line.addWidget(card, 8, Qt.AlignVCenter)
    line.addStretch(1)
    outer.addLayout(line, 8)
    outer.addStretch(1)
    return page, layout


def build_pages(w):
    w.setup_page, setup = centered_page()
    w.setup_title = text("Начнём с фотографий", "hero")
    w.setup_intro = text("Выберите папки для поиска похожих кадров или загрузите ранее сохранённое сравнение.", "muted")
    setup.addWidget(w.setup_title)
    setup.addWidget(w.setup_intro)
    w.sources = QListWidget()
    w.sources.setObjectName("sourcesEditor")
    w.sources.setSelectionMode(QAbstractItemView.ExtendedSelection)
    w.sources.setMinimumHeight(170)
    setup.addWidget(w.sources, 1)
    source_actions = QHBoxLayout()
    w.add_source_button = action("+ Добавить папку", w.add_folder)
    w.remove_source_button = action("Убрать выбранные", w.remove_folders)
    source_actions.addWidget(w.add_source_button)
    source_actions.addWidget(w.remove_source_button)
    source_actions.addStretch()
    setup.addLayout(source_actions)
    cpu_row = QHBoxLayout()
    cpu_label = text("Бюджет CPU:")
    cpu_row.addWidget(cpu_label)
    w.setup_cpu = QSpinBox()
    w.setup_cpu.setFixedWidth(130)
    w.setup_cpu.setRange(5, 100)
    w.setup_cpu.setSingleStep(5)
    w.setup_cpu.setSuffix(" %")
    w.setup_cpu.setKeyboardTracking(False)
    w.setup_cpu.setValue(w.settings.cpu_percent)
    w.setup_cpu.setToolTip("Доля логических процессоров для подготовки фотографий.\n"
                           "Фактическая загрузка может быть ниже; применяется при следующем поиске.")
    cpu_label.setBuddy(w.setup_cpu)
    cpu_row.addWidget(w.setup_cpu)
    cpu_row.addWidget(text("Меньше — свободнее компьютер; больше — быстрее подготовка фото.", "muted"), 1)
    w.setup_cpu.valueChanged.connect(w.change_cpu_budget)
    setup.addLayout(cpu_row)
    w.setup_mode = text("", "muted")
    setup.addWidget(w.setup_mode)
    w.setup_context = text("", "muted")
    setup.addWidget(w.setup_context)
    setup_buttons = QHBoxLayout()
    w.setup_back = action("← Вернуться к сравнению", w.show_review)
    w.setup_back.setMinimumWidth(235)
    w.setup_back.setToolTip("Продолжить текущее сравнение с того же места")
    setup_buttons.addWidget(action("Загрузить сравнение…", w.open_project))
    setup_buttons.addStretch()
    w.start_button = action("Найти похожие", w.start_scan, True)
    setup_buttons.addWidget(w.start_button)
    setup_buttons.addWidget(w.setup_back)
    setup.addLayout(setup_buttons)
    w.pages.addWidget(w.setup_page)

    w.operation_page = QWidget()
    operation_outer = QVBoxLayout(w.operation_page)
    operation_outer.setContentsMargins(0, 0, 0, 0)
    operation_outer.addStretch(1)
    operation_row = QHBoxLayout()
    operation_row.addStretch(1)
    operation_column = QWidget()
    operation_column.setMinimumWidth(660)
    operation_column.setMaximumWidth(980)
    column = QVBoxLayout(operation_column)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    w.scan_visuals = QWidget()
    artwork = QVBoxLayout(w.scan_visuals)
    artwork.setContentsMargins(24, 0, 24, 0)
    w.scan_scene = ScanAnimation('scan-scene', QSize(960, 220))
    w.scan_scene.setFixedHeight(220)
    w.scan_scene.setAccessibleName("Логотип следит за сканированием и сортировкой файлов")
    artwork.addWidget(w.scan_scene)
    column.addWidget(w.scan_visuals)
    card = QFrame()
    card.setObjectName("phaseCard")
    operation = QVBoxLayout(card)
    operation.setContentsMargins(24, 18, 24, 18)
    operation.setSpacing(9)
    column.addWidget(card)
    operation_row.addWidget(operation_column, 8, Qt.AlignVCenter)
    operation_row.addStretch(1)
    operation_outer.addLayout(operation_row)
    operation_outer.addStretch(1)
    w.operation_title = text("Поиск похожих фотографий", "hero")
    w.operation_message = text("", "heading")
    w.operation_counter = text("", "muted")
    w.operation_counter.setWordWrap(False)
    w.operation_counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    w.operation_detail = text("", "muted")
    operation.addWidget(w.operation_title)
    counters = QHBoxLayout()
    counters.addWidget(w.operation_message, 1)
    counters.addWidget(w.operation_counter)
    operation.addLayout(counters)
    w.progress = QProgressBar()
    w.progress.setRange(0, 100)
    w.progress.setTextVisible(False)
    w.progress.setFixedHeight(14)
    operation.addWidget(w.progress)
    w.operation_eta = text("", "muted")
    operation.addWidget(w.operation_eta)
    operation.addWidget(w.operation_detail)
    stop_row = QHBoxLayout()
    stop_row.addStretch()
    w.stop_button = action("Остановить поиск", w.stop_current_operation)
    stop_row.addWidget(w.stop_button)
    operation.addLayout(stop_row)
    w.pages.addWidget(w.operation_page)

    w.plan_page = QWidget()
    plan = QVBoxLayout(w.plan_page)
    plan.setContentsMargins(14, 14, 14, 14)
    plan.setSpacing(16)
    plan_heading = QHBoxLayout()
    plan_heading.addWidget(text("Изменения перед применением", "hero"), 1)
    plan_heading.addWidget(action("← К сравнению", w.show_review))
    plan.addLayout(plan_heading)
    w.plan_description = text("", "muted")
    plan.addWidget(w.plan_description)
    w.plan_counts = text("", "heading")
    plan.addWidget(w.plan_counts)
    plan.addWidget(w.execution_label)
    plan.addWidget(w.plan_table, 1)
    w.plan_empty = text("Удалений и перемещений пока нет. Просмотренные группы и решения сохраняются в прогрессе сравнения.", "muted")
    plan.addWidget(w.plan_empty)
    plan.addStretch()
    w.pages.addWidget(w.plan_page)

    w.outcome_page, outcome = centered_page()
    w.outcome_title = text("", "hero")
    w.outcome_body = text("", "heading")
    w.outcome_detail = text("", "muted")
    outcome.addWidget(w.outcome_title)
    outcome.addWidget(w.outcome_body)
    outcome.addWidget(w.outcome_detail)
    outcome.addStretch()
    outcome_actions = QHBoxLayout()
    w.outcome_sources = action("Источники и поиск", w.show_setup)
    w.outcome_continue = action("Продолжить сравнение", w.continue_from_outcome, True)
    outcome_actions.addWidget(w.outcome_sources)
    outcome_actions.addStretch()
    outcome_actions.addWidget(w.outcome_continue)
    outcome.addLayout(outcome_actions)
    w.pages.addWidget(w.outcome_page)


class WorkflowUI:
    def show_setup(self):
        self.show_page("setup")

    def show_review(self):
        self.show_page("review")

    def show_plan(self):
        self.show_page("plan")

    def show_page(self, page):
        if self.busy:
            return
        previous = self.current_page
        self.current_page = page
        self.refresh_workflow()
        if self.current_page == "review" and previous != "review":
            self.request_exif()
            QTimer.singleShot(0, self.load_visible_covers)

    def default_outcome(self):
        if self.model and self.model.groups:
            return {"kind": "complete", "title": "Сравнение завершено",
                    "body": "Групп для сравнения больше нет.",
                    "detail": "После применения в группах осталось не более одного файла. Прогресс сравнения сохранён."}
        errors = self.model.document.get("scan_errors", []) if self.model else []
        if errors:
            examples = "\n".join(Path(error["path"]).name + ": " + error["error"] for error in errors[:3])
            return {"kind": "scan_incomplete", "title": "Поиск завершён с ошибками чтения",
                    "body": f"Не удалось прочитать файлов: {len(errors)}. Среди прочитанных фотографий совпадений нет.",
                    "detail": examples + "\nПроверьте файлы и доступ к ним, затем повторите поиск."}
        empty = self.model and self.model.document.get("scanned_files") == 0
        return {"kind": "empty", "title": "Нет фотографий для поиска" if empty else "Похожих фотографий не найдено",
                "body": "Выберите папки с изображениями." if empty else "В пределах выбранного режима совпадений нет.",
                "detail": "Можно выбрать другие папки или расширить режим поиска в настройках."}

    def show_outcome(self, data):
        self.outcome_data = data
        self.show_page("outcome")

    def continue_from_outcome(self):
        kind = (self.outcome_data or {}).get("kind")
        if kind in ("apply_error", "apply_cancelled"):
            self.show_plan()
        else:
            self.show_review()

    def show_apply_result(self, batch):
        self.show_outcome(outcome_for_batch(self.model, batch))

    def advance_review(self, changed_groups=None):
        if not self.model or self.busy:
            return
        groups = self.model.visible_groups()
        position = groups.index(self.current_group) if self.current_group in groups else -1
        for group in groups[position+1:]+groups[:position+1]:
            if self.model.next_pair(group, initialize=False)[1] is not None:
                self.current_group = group
                self.refresh(changed_groups=changed_groups)
                self.show_review()
                return
        self.refresh(changed_groups=changed_groups)
        self.show_plan()

    def refresh_after_decision(self, changed_groups):
        if self.current_page == "review" and self.model.next_pair(self.current_group, initialize=False)[1] is None:
            self.advance_review(changed_groups=changed_groups)
        else:
            self.refresh(changed_groups=changed_groups)

    def after_decision(self):
        if self.model and self.current_page == "review" and self.b is None:
            self.advance_review()

    def stop_current_operation(self):
        if self.apply_thread:
            self.apply_thread.cancel_event.set()
            self.operation_message.setText("Отменяем применение и восстанавливаем файлы…")
        else:
            self.stop_scan()
            self.operation_message.setText("Останавливаем поиск…")
        self.stop_button.setEnabled(False)

    def scan_failed(self, error):
        self.scan_failure = error

    def scan_cancelled(self):
        self.scan_was_cancelled = True

    def status_notice(self, message):
        self.notice.setText(message)
        self.notice.setToolTip(message)
        self.notice.setVisible(bool(message) and not self.busy)
        if message and not self.busy:
            self.notice_timer.start(8000)

    def refresh_workflow(self):
        if not hasattr(self, "pages"):
            return
        if self.busy:
            if self.current_page != "operation":
                self.resume_page = self.current_page
            self.current_page = "operation"
        elif self.current_page == "operation":
            self.current_page = self.resume_page
        if self.current_page in ("review", "plan") and not self.model:
            self.current_page = "setup"
        if self.current_page == "review" and not self.model.visible_groups():
            self.outcome_data = self.outcome_data or self.default_outcome()
            self.current_page = "outcome"
        self.plan_open = self.current_page == "plan"
        if self.plan_open:
            self.refresh_plan()
        pages = {"setup": self.setup_page, "operation": self.operation_page, "review": self.review_page,
                 "plan": self.plan_page, "outcome": self.outcome_page}
        self.pages.setCurrentWidget(pages[self.current_page])
        self.scan_visuals.setVisible(self.current_page == "operation" and self.scan_thread is not None)
        self.operation_eta.setVisible(self.scan_thread is not None)
        names = {"setup": "Подготовка", "operation": "Проверка изменений" if self.apply_thread else "Поиск",
                 "review": "Сравнение", "plan": "Проверка изменений", "outcome": "Результат"}
        self.phase_badge.setText(names[self.current_page])
        self.save_button.setVisible(self.model is not None and not self.busy)
        self.action_footer.setVisible(self.model is not None and not self.busy)
        self.footer_action_stack.setCurrentWidget(self.apply_button if self.current_page == "plan" else self.plan_button)
        self.open_button.setEnabled(not self.busy)
        self.new_button.setEnabled(not self.busy)
        self.notice.setVisible(bool(self.notice.text()) and not self.busy)
        roots = [self.sources.item(i).text() for i in range(self.sources.count())]
        self.sources.setFixedHeight(min(320, max(104, len(roots)*42+12)))
        self.source_overview.setText(f"Источники · папок: {len(roots)}")
        self.source_overview.setToolTip("\n".join(roots))
        mode = SEARCH_MODES[self.settings.search_mode]
        self.setup_mode.setText(f"Режим: {mode[0]}\n{mode[2]}\nРежим поиска можно изменить в настройках.")
        signals_blocked = self.setup_cpu.blockSignals(True)
        self.setup_cpu.setValue(self.settings.cpu_percent)
        self.setup_cpu.blockSignals(signals_blocked)
        self.setup_cpu.setEnabled(not self.busy)
        self.setup_back.setVisible(self.model is not None)
        has_comparison = self.model is not None
        self.setup_title.setText("Папки-источники" if has_comparison else "Начнём с фотографий")
        self.setup_intro.setText("Вы можете изменить папки для следующего поиска или вернуться к текущему сравнению."
                                 if has_comparison else "Выберите папки и бюджет CPU для поиска похожих кадров или загрузите ранее сохранённое сравнение.")
        self.start_button.setText("Начать новый поиск" if has_comparison else "Найти похожие")
        for control, primary in ((self.setup_back, has_comparison), (self.start_button, not has_comparison)):
            name = "primary" if primary else ""
            if control.objectName() != name:
                control.setObjectName(name)
                control.style().unpolish(control)
                control.style().polish(control)
                control.update()
        self.setup_context.setVisible(self.model is not None)
        self.setup_context.setText("Найденные группы, решения и место остановки сохранены в текущей сессии.")
        self.start_button.setEnabled(bool(roots) and not self.busy)
        self.remove_source_button.setEnabled(bool(self.sources.selectedItems()) and not self.busy)
        self.add_source_button.setEnabled(not self.busy)
        self.sources.setEnabled(not self.busy)
        if self.model:
            counts = {"recycle": len(self.model.recycles), "replace": len(self.model.replacements),
                      "move": len(self.model.moves.keys()-self.model.replacements.keys())}
            action_count = sum(counts.values())
            self.plan_description.setText("Проверьте исходные и итоговые пути. Удаляемые фотографии отправятся " +
                                          self.model.deletion_description + ". Переносы и замены будут выполнены.")
            self.plan_counts.setText(f"{self.model.deletion_label}: {counts['recycle'] + counts['replace']} · замен: {counts['replace']} · перемещений: {counts['move']}")
            self.plan_empty.setVisible(not action_count)
            self.plan_table.setVisible(self.plan_table.rowCount() > 0)
            self.plan_table.setMaximumHeight(max(104, 48+sum(
                self.plan_table.rowHeight(i) for i in range(min(self.plan_table.rowCount(), 24)))))
            self.plan_button.setEnabled(not self.busy)
            self.plan_button.setText(f"Проверить изменения · {action_count}")
            self.apply_button.setEnabled(bool(action_count) and not self.busy)
            completed, total = getattr(self, "work_progress", (0, len(self.model.groups)))
            self.review_progress.setText(f"Сравнено групп: {completed} из {total} · {completed*100//total if total else 100}%")
            live = self.model.live(self.current_group) if self.current_group is not None else []
            done = self.b is None
            self.pair_caption.setVisible(not done)
            selected = self.checked_files & set(live)
            self.reference_button.setVisible(len(selected) == 1)
            self.delete_selected_button.setVisible(bool(selected))
            self.discard_group_button.setVisible(not selected)
            members = set(self.model.groups[self.current_group]) if self.current_group is not None else set()
            self.show_excluded.setVisible(bool(members & self.model.inactive))
            decisions = members & (self.model.recycles | self.model.kept | self.model.replacements.keys() | self.model.moves.keys())
            self.reset_button.setVisible(bool(decisions) or any(pair & members for pair in self.model.seen))
            self.comparison_stack.setVisible(self.a is not None)
            for card, path in zip(self.photo_cards, (self.a, self.b)):
                card.setVisible(path is not None)
            for button in self.pair_buttons:
                button.setVisible(not done)
            self.files.setVisible(bool(live) or self.show_excluded.isChecked())
            self.recommendation.setVisible(bool(self.recommendation.text()) and not done)
        if self.current_page == "outcome":
            result = self.outcome_data or self.default_outcome()
            self.outcome_title.setText(result["title"])
            self.outcome_body.setText(result["body"])
            self.outcome_detail.setText(result["detail"])
            resume = self.model is not None and bool(self.model.visible_groups())
            self.outcome_continue.setVisible(resume)
            self.outcome_continue.setText("Вернуться к изменениям" if result.get("kind") in ("apply_error", "apply_cancelled") else "Продолжить сравнение")
