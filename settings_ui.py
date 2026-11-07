"""Review preferences dialog; saving is handled by the main window."""

from PyQt5.QtCore import Qt
from pathlib import Path
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QSpinBox, QComboBox, QCheckBox, QListWidget, QListWidgetItem, QPushButton, QDialogButtonBox,
    QLineEdit, QFileDialog, QMessageBox)

from preferences import DATE_LABELS, RULE_LABELS, THEME_LABELS, SEARCH_MODES, DELETE_MODES, Preferences


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(680, 720)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.theme = QComboBox()
        for key, text in THEME_LABELS.items():
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        self.theme.setToolTip("Системная тема следует за темой операционной системы")
        form.addRow("Тема:", self.theme)
        self.search_mode = QComboBox()
        for key, (text, threshold, description) in SEARCH_MODES.items():
            self.search_mode.addItem(text, key)
            self.search_mode.setItemData(self.search_mode.count()-1, description, Qt.ToolTipRole)
        self.search_mode.setCurrentIndex(self.search_mode.findData(settings.search_mode))
        form.addRow("Режим поиска:", self.search_mode)
        search_note = QLabel("Режим расширяет список кандидатов при следующем поиске. Строгий сохраняет прежнюю "
                            "границу; остальные добавляют совпадения. Решение всегда за вами.")
        search_note.setWordWrap(True)
        form.addRow(search_note)
        self.cpu = QSpinBox()
        self.cpu.setFixedWidth(130)
        self.cpu.setRange(5, 100)
        self.cpu.setSingleStep(5)
        self.cpu.setSuffix(" %")
        self.cpu.setValue(settings.cpu_percent)
        self.cpu.setToolTip("Бюджет логических процессоров для подготовки признаков.\n"
                            "Фактическая загрузка может быть ниже; применяется со следующего поиска.")
        form.addRow("Бюджет CPU:", self.cpu)
        self.date_source = QComboBox()
        for key, text in DATE_LABELS.items():
            self.date_source.addItem(text, key)
        self.date_source.setCurrentIndex(self.date_source.findData(settings.date_source))
        form.addRow("Дата для сравнения:", self.date_source)
        self.delete_mode = QComboBox()
        for key, text in DELETE_MODES.items():
            self.delete_mode.addItem(text, key)
        self.delete_mode.setCurrentIndex(self.delete_mode.findData(settings.delete_mode))
        form.addRow("Удаляемые файлы:", self.delete_mode)
        folder_row = QHBoxLayout()
        self.delete_folder = QLineEdit(settings.delete_folder)
        self.delete_folder.setPlaceholderText("Выберите папку для удаляемых файлов")
        self.delete_folder.setAccessibleName("Папка для удаляемых файлов")
        folder_row.addWidget(self.delete_folder, 1)
        self.browse_delete_folder = QPushButton("Выбрать…")
        self.browse_delete_folder.clicked.connect(self.choose_delete_folder)
        folder_row.addWidget(self.browse_delete_folder)
        form.addRow("Папка:", folder_row)
        self.delete_mode.currentIndexChanged.connect(self.update_delete_controls)
        self.update_delete_controls()
        deletion_note = QLabel("Сюда попадают и удаляемые файлы, и старые файлы при замене. "
                               "В выбранной папке совпадающие имена получают номер; существующие файлы не заменяются.")
        deletion_note.setWordWrap(True)
        form.addRow(deletion_note)
        layout.addLayout(form)
        description = QLabel("Приоритет правил: сверху вниз. Следующее правило используется при равенстве "
                             "предыдущего. Снимите галочку, чтобы отключить правило.")
        description.setWordWrap(True)
        layout.addWidget(description)
        order = QHBoxLayout()
        self.rules = QListWidget()
        for rule in settings.rules:
            item = QListWidgetItem(RULE_LABELS[rule["key"]])
            item.setData(Qt.UserRole, rule["key"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if rule["enabled"] else Qt.Unchecked)
            self.rules.addItem(item)
        self.rules.setCurrentRow(0)
        order.addWidget(self.rules, 1)
        moves = QVBoxLayout()
        for text, step in (("Выше", -1), ("Ниже", 1)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, step=step: self.move_rule(step))
            moves.addWidget(button)
        moves.addStretch()
        order.addLayout(moves)
        layout.addLayout(order, 1)
        self.highlight = QCheckBox("Подсвечивать лучшие параметры")
        self.highlight.setChecked(settings.highlight_best)
        layout.addWidget(self.highlight)
        self.suggest = QCheckBox("Предлагать файл для сохранения")
        self.suggest.setChecked(settings.suggest_best)
        layout.addWidget(self.suggest)
        note = QLabel("Совет не отмечает файлы и не создаёт действий. При полном равенстве правил "
                      "приложение покажет несколько равноценных вариантов.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_delete_controls(self):
        enabled = self.delete_mode.currentData() == "folder"
        self.delete_folder.setEnabled(enabled)
        self.browse_delete_folder.setEnabled(enabled)

    def choose_delete_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка для удаляемых файлов", self.delete_folder.text())
        if folder:
            self.delete_folder.setText(str(Path(folder).resolve()))

    def accept(self):
        if self.delete_mode.currentData() == "folder":
            folder = Path(self.delete_folder.text().strip())
            if not folder.is_absolute() or not folder.is_dir():
                QMessageBox.warning(self, "Выберите папку", "Укажите существующую доступную папку для удаляемых файлов.")
                return
            self.delete_folder.setText(str(folder.resolve()))
        super().accept()

    def move_rule(self, step):
        row = self.rules.currentRow()
        target = row + step
        if row >= 0 and 0 <= target < self.rules.count():
            item = self.rules.takeItem(row)
            self.rules.insertItem(target, item)
            self.rules.setCurrentRow(target)

    def value(self):
        return Preferences(cpu_percent=self.cpu.value(), date_source=self.date_source.currentData(),
            theme=self.theme.currentData(), search_mode=self.search_mode.currentData(),
            delete_mode=self.delete_mode.currentData(), delete_folder=self.delete_folder.text().strip(),
            rules=[{"key": self.rules.item(i).data(Qt.UserRole),
                    "enabled": self.rules.item(i).checkState() == Qt.Checked} for i in range(self.rules.count())],
            highlight_best=self.highlight.isChecked(), suggest_best=self.suggest.isChecked())
