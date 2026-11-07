"""Desktop review of nearby frames; saves a per-file selection plan.

python review_ui.py experiments/review_03/review.json
No archive file actions are implemented in this viewer.
"""

import argparse
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageEnhance
import numpy as np
import pillow_heif
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPixmap, QPainter
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QSplitter, QComboBox, QPushButton, QLabel, QTableWidget,
                             QTableWidgetItem, QAbstractItemView, QTabWidget, QGraphicsView,
                             QGraphicsScene, QCheckBox, QSpinBox, QFileDialog, QMessageBox)

from prepare_sample import open_rgb
from review_model import ReviewSession


DECISION_TEXT = {"keep": "Оставить", "remove": "Убрать", "undecided": "Не решено"}


def pixmap(image):
    image = image.convert("RGB")
    data = image.tobytes()
    return QPixmap.fromImage(QImage(data, image.width, image.height,
                                   image.width*3, QImage.Format_RGB888).copy())


class ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = self.scene().addPixmap(QPixmap())
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.zoomed = False

    def show_pixmap(self, value, reset=True):
        self.item.setPixmap(value)
        self.scene().setSceneRect(self.item.boundingRect())
        if reset:
            self.fit()

    def fit(self):
        self.fitInView(self.item, Qt.KeepAspectRatio)
        self.zoomed = False

    def wheelEvent(self, event):
        self.scale(1.2 if event.angleDelta().y() > 0 else 1/1.2,
                   1.2 if event.angleDelta().y() > 0 else 1/1.2)
        self.zoomed = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.zoomed:
            self.fit()


class ReviewWindow(QMainWindow):
    def __init__(self, document):
        super().__init__()
        pillow_heif.register_heif_opener()
        self.session = ReviewSession(document)
        self.dirty = False
        self.groups = {}
        for row in self.session.files.values():
            self.groups.setdefault(row["group_id"], []).append(row["path"])
        self.a = self.b = None
        self.images = []
        self.blink_index = 0
        self.setWindowTitle("Comparsion V2 — отбор кадров")
        self.resize(1450, 950)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.group_box = QComboBox()
        for group, members in self.groups.items():
            self.group_box.addItem(f"Группа {group}: {len(members)} файлов", group)
        toolbar.addWidget(self.group_box)
        for text, handler in [("Открыть план", self.load_plan), ("Сохранить выбор", self.save_plan)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.relation_label = QLabel()
        toolbar.addWidget(self.relation_label)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Файл", "Выбор", "Дата съёмки"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        left_layout.addWidget(self.table, 1)
        reference = QPushButton("Сравнивать с выбранным файлом")
        reference.clicked.connect(self.set_reference)
        left_layout.addWidget(reference)
        for choice in ("keep", "remove", "undecided"):
            button = QPushButton("Отметить: " + DECISION_TEXT[choice].lower())
            button.clicked.connect(lambda checked=False, choice=choice: self.choose(choice))
            left_layout.addWidget(button)
        left_layout.addWidget(QLabel("Результат работы — план отбора файлов."))
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.info = QLabel()
        self.info.setWordWrap(True)
        right_layout.addWidget(self.info)
        tabs = QTabWidget()
        right_layout.addWidget(tabs, 1)
        side = QWidget()
        side_layout = QHBoxLayout(side)
        self.side_a, self.side_b = ImageView(), ImageView()
        side_layout.addWidget(self.side_a)
        side_layout.addWidget(self.side_b)
        tabs.addTab(side, "Рядом: A слева, B справа")
        blink = QWidget()
        blink_layout = QVBoxLayout(blink)
        controls = QHBoxLayout()
        blink_layout.addLayout(controls)
        toggle = QPushButton("Переключить A / B")
        toggle.clicked.connect(self.toggle_image)
        controls.addWidget(toggle)
        self.automatic = QCheckBox("Переключать каждые 0,5 с")
        controls.addWidget(self.automatic)
        self.blink_caption = QLabel()
        controls.addWidget(self.blink_caption)
        self.blink = ImageView()
        blink_layout.addWidget(self.blink, 1)
        tabs.addTab(blink, "Переключение A ↔ B")
        diff = QWidget()
        diff_layout = QVBoxLayout(diff)
        diff_controls = QHBoxLayout()
        diff_layout.addLayout(diff_controls)
        diff_controls.addWidget(QLabel("Усиление различий:"))
        self.gain = QSpinBox()
        self.gain.setRange(1, 20)
        self.gain.setValue(4)
        self.gain.valueChanged.connect(self.show_difference)
        diff_controls.addWidget(self.gain)
        diff_controls.addStretch()
        self.difference = ImageView()
        diff_layout.addWidget(self.difference, 1)
        tabs.addTab(diff, "Карта различий")
        fit = QPushButton("Уместить изображения в окно")
        fit.clicked.connect(lambda: [view.fit() for view in
                                    (self.side_a, self.side_b, self.blink, self.difference)])
        right_layout.addWidget(fit)
        splitter.addWidget(right)
        splitter.setSizes([400, 1050])
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.toggle_image)
        self.automatic.toggled.connect(lambda active: self.timer.start() if active else self.timer.stop())
        self.group_box.currentIndexChanged.connect(self.load_group)
        self.table.currentCellChanged.connect(lambda row, *_: self.select_file(row))
        self.load_group()

    def load_group(self):
        self.timer.stop()
        self.automatic.setChecked(False)
        members = self.groups.get(self.group_box.currentData(), [])
        self.table.blockSignals(True)
        self.table.setRowCount(len(members))
        for i, path in enumerate(members):
            row = self.session.files[path]
            for j, text in enumerate((row["filename"], DECISION_TEXT[self.session.decisions[path]],
                                      row.get("captured_at") or "—")):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, path)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self.a = members[0] if members else None
        if members:
            selected = min(1, len(members)-1)
            self.table.selectRow(selected)
            self.select_file(selected)
        else:
            self.info.setText("В отчёте нет групп для просмотра.")
        self.update_choices()

    def select_file(self, row):
        if row < 0 or not self.table.item(row, 0):
            return
        self.b = self.table.item(row, 0).data(Qt.UserRole)
        self.show_pair()

    def set_reference(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.a = self.table.item(row, 0).data(Qt.UserRole)
        members = self.groups[self.group_box.currentData()]
        target = next((path for path in members if path != self.a), self.a)
        self.table.selectRow(members.index(target))
        self.b = target
        self.show_pair()

    def show_pair(self):
        if not self.a or not self.b:
            return
        try:
            a, b = open_rgb(self.a), open_rgb(self.b)
            # Align quarter turns for display. This does not change originals.
            reference = np.array(a.resize((72, 96))).astype(float)
            variants = [b.rotate(angle, expand=True) for angle in (0, 90, 180, 270)]
            b = min(variants, key=lambda image: np.mean((np.array(image.resize((72, 96)))-reference)**2))
            a.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            b = b.resize(a.size, Image.Resampling.LANCZOS)
            self.images = [a, b]
            self.pixmaps = [pixmap(a), pixmap(b)]
            self.side_a.show_pixmap(self.pixmaps[0])
            self.side_b.show_pixmap(self.pixmaps[1])
            self.blink_index = 0
            self.blink.show_pixmap(self.pixmaps[0])
            self.blink_caption.setText("A: " + Path(self.a).name)
            self.show_difference()
            lines = []
            for label, path in (("A", self.a), ("B", self.b)):
                row = self.session.files[path]
                lines.append(f"{label}: {row['filename']} | {row['stored_width']}×{row['stored_height']} | "
                             f"{row['bytes']/1024:.0f} КБ | {row.get('camera') or 'камера не указана'} | "
                             f"Съёмка: {row.get('captured_at') or 'не указана'} | "
                             f"Изменение файла: {row['modified_at']} | EXIF-тегов: {row['exif_tags']}")
            self.info.setText("\n".join(lines))
            relation = next((p for p in self.session.document["pairs"]
                             if {p.get("path1"), p.get("path2")} == {self.a, self.b}), None)
            text = {"burst": "Серия", "copy_candidate": "Кандидат в копии", "unrelated": "Разные сцены"}
            value = text.get(relation.get("relation"), "Нет ручной метки") if relation else "Нет ручной метки"
            if relation and relation.get("annotation_confidence") in ("ambiguous", "working_label"):
                value += " (рабочая метка)"
            self.relation_label.setText(value)
        except Exception as error:
            QMessageBox.warning(self, "Не удалось открыть изображение", str(error))

    def toggle_image(self):
        if not self.images:
            return
        self.blink_index = 1-self.blink_index
        self.blink.show_pixmap(self.pixmaps[self.blink_index], reset=False)
        self.blink_caption.setText(("A: " if self.blink_index == 0 else "B: ")
                                  + Path(self.a if self.blink_index == 0 else self.b).name)

    def show_difference(self):
        if len(self.images) == 2:
            difference = ImageChops.difference(*self.images)
            difference = ImageEnhance.Brightness(difference).enhance(self.gain.value())
            self.difference.show_pixmap(pixmap(difference))

    def choose(self, decision):
        row = self.table.currentRow()
        if row >= 0:
            self.session.choose(self.table.item(row, 0).data(Qt.UserRole), decision)
            self.dirty = True
            self.update_choices()

    def update_choices(self):
        colors = {"keep": QColor(220, 245, 220), "remove": QColor(255, 225, 225),
                  "undecided": QColor("white")}
        for i in range(self.table.rowCount()):
            choice = self.session.decisions[self.table.item(i, 0).data(Qt.UserRole)]
            self.table.item(i, 1).setText(DECISION_TEXT[choice])
            for j in range(self.table.columnCount()):
                self.table.item(i, j).setBackground(colors[choice])
        counts = self.session.summary()
        self.statusBar().showMessage(f"Оставить: {counts['keep']} | Убрать: {counts['remove']} | "
                                     f"Не решено: {counts['undecided']}")
        self.setWindowTitle("Comparsion V2 — отбор кадров" + (" *" if self.dirty else ""))

    def save_plan(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Сохранить выбор",
                    str(self.session.document_path.with_name("selection_plan.json")), "План JSON (*.json)")
        if not filename:
            return False
        try:
            self.session.export(filename)
            self.dirty = False
            self.update_choices()
            return True
        except Exception as error:
            QMessageBox.warning(self, "Не удалось сохранить выбор", str(error))
            return False

    def load_plan(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Открыть сохранённый выбор", "", "План JSON (*.json)")
        if filename:
            try:
                self.session.load(filename)
                self.dirty = False
                self.update_choices()
            except Exception as error:
                QMessageBox.warning(self, "Не удалось открыть выбор", str(error))

    def closeEvent(self, event):
        if self.dirty:
            answer = QMessageBox.question(self, "Выбор не сохранён", "Сохранить выбор перед закрытием?",
                                         QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self.save_plan()):
                event.ignore()
                return
        self.timer.stop()
        event.accept()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    arguments = parser.parse_args()
    app = QApplication(sys.argv[:1])
    window = ReviewWindow(arguments.review)
    window.show()
    sys.exit(app.exec_())
