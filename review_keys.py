"""Arrow-key decisions share the real buttons and their pressed feedback."""

from PyQt5.QtCore import QObject, QEvent, Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QWidget, QLineEdit, QPlainTextEdit,
                            QAbstractSpinBox, QComboBox)


class ReviewKeys(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = self.context = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.release)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        w = self.window
        if event.type() != QEvent.KeyPress or not isinstance(watched, QWidget) or watched.window() is not w:
            return False
        if (event.modifiers() != Qt.NoModifier or QApplication.activeWindow() is not w
                or QApplication.activeModalWidget() or w.busy or not w.model):
            return False
        if w.current_page not in ("review", "plan", "outcome"):
            return False
        keys = {Qt.Key_Down: w.pair_buttons[0], Qt.Key_Left: w.pair_buttons[1],
                Qt.Key_Right: w.pair_buttons[2], Qt.Key_Up: w.undo_button}
        if event.key() not in keys or (w.current_page != "review" and event.key() != Qt.Key_Up):
            return False
        focus = QApplication.focusWidget()
        if (isinstance(focus, (QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox))
                or (focus and w.exif_panel.isAncestorOf(focus))):
            return False
        event.accept()
        if not event.isAutoRepeat():
            self.press(keys[event.key()])
        return True

    def press(self, button):
        if self.pending or not button.isEnabled():
            return
        w = self.window
        self.pending = button
        self.context = (w.model, w.current_page, w.current_group, w.a, w.b)
        button.setDown(True)
        button.repaint()
        self.timer.start()

    def release(self):
        w = self.window
        button, context = self.pending, self.context
        self.pending = self.context = None
        if not button:
            return
        button.setDown(False)
        if (context == (w.model, w.current_page, w.current_group, w.a, w.b)
                and button.isEnabled() and w.isVisible() and not w.busy
                and QApplication.activeWindow() is w and not QApplication.activeModalWidget()):
            button.click()

    def close(self):
        self.timer.stop()
        if self.pending:
            self.pending.setDown(False)
        self.pending = self.context = None
        QApplication.instance().removeEventFilter(self)
