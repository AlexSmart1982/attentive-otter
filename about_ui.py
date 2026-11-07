"""Application identity and the About dialog."""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                            QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)


APP_NAME = "Attentive Otter"
APP_VERSION = "1.0 BETA"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(500)
        self.resize(560, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        from brand_wordmark import BrandWordmark
        from theme import PALETTES
        colors = getattr(parent, "colors", PALETTES["light"])
        layout.addWidget(BrandWordmark(colors, self, width=260))

        name = QLabel(APP_NAME)
        name.setObjectName("hero")
        layout.addWidget(name)
        version = QLabel(f"{APP_VERSION} · Тестовая версия")
        version.setWordWrap(True)
        layout.addWidget(version)

        description = QLabel(
            "Приложение для поиска дубликатов и похожих изображений по содержимому. "
            "Помогает сравнить найденные файлы и выбрать, какие сохранить.")
        description.setWordWrap(True)
        layout.addWidget(description)

        details = QFormLayout()
        details.setVerticalSpacing(10)
        for caption, value in (("Лицензия:", "GNU GPLv3 (GPL-3.0-only)"),
                               ("Автор:", "© 2026 Скрябин Александр"),
                               ("Почта:", "alexsmart@yandex.ru")):
            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            details.addRow(caption, text)
        layout.addLayout(details)
        permissions = QLabel("Бесплатное использование, включая коммерческое. "
                             "Изменение и распространение — на условиях GPLv3.")
        permissions.setWordWrap(True)
        layout.addWidget(permissions)
        warranty = QLabel("Программа предоставляется без гарантий в пределах, допускаемых законом.")
        warranty.setWordWrap(True)
        warranty.setObjectName("muted")
        layout.addWidget(warranty)
        layout.addStretch()

        actions = QHBoxLayout()
        self.thanks_button = QPushButton("Поблагодарить автора")
        self.thanks_button.setEnabled(False)
        self.thanks_button.setToolTip("Способ поблагодарить автора появится позже")
        self.thanks_button.setAccessibleDescription("Пока недоступно")
        actions.addWidget(self.thanks_button)
        actions.addStretch()
        self.license_button = QPushButton("Лицензия")
        self.license_button.clicked.connect(self.open_license)
        actions.addWidget(self.license_button)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    def open_license(self):
        root = Path(__file__).resolve().parent
        try:
            notice = (root / "COPYRIGHT.txt").read_text(encoding="utf-8")
            license_text = (root / "LICENSE").read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Не удалось открыть лицензию", str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Лицензия GNU GPLv3")
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        dialog.resize(760, 580)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(notice + "\n\n" + license_text)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()
