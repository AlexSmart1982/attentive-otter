"""Responsive GIF views, decoded only while the scan screen is visible."""
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QRectF
from PyQt5.QtGui import QMovie, QPainter
from PyQt5.QtWidgets import QWidget, QSizePolicy


class ScanAnimation(QWidget):
    def __init__(self, asset, size, parent=None):
        super().__init__(parent)
        self.asset, self.preferred_size = asset, size
        self.movie, self.theme_name = None, None
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sizeHint(self):
        return self.preferred_size

    def set_theme(self, name):
        if self.theme_name == name:
            return
        self.theme_name = name
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()
        path = Path(__file__).parent/'assets'/f'{self.asset}-{name}.gif'
        self.movie = QMovie(str(path), parent=self)
        # CacheAll would retain ~330 MB of unscaled scanner frames.
        self.movie.setCacheMode(QMovie.CacheNone)
        self.movie.frameChanged.connect(lambda frame: self.update())
        self.movie.jumpToFrame(0)
        if self.isVisible():
            self.movie.start()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.movie:
            self.movie.start()

    def hideEvent(self, event):
        if self.movie:
            self.movie.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if not self.movie:
            return
        frame = self.movie.currentPixmap()
        if frame.isNull():
            return
        size = frame.size().scaled(self.size(), Qt.KeepAspectRatio)
        target = QRectF((self.width()-size.width())/2, (self.height()-size.height())/2,
                        size.width(), size.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
