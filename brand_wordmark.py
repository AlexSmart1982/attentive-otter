"""Approved Attentive Otter lettering, painted in the current UI palette."""

from pathlib import Path

import numpy as np
from PIL import Image
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QWidget

from about_ui import APP_NAME


class BrandWordmark(QWidget):
    def __init__(self, colors, parent=None, width=174):
        super().__init__(parent)
        self.setObjectName("brandWordmark")
        self.setAccessibleName(APP_NAME)
        self.setToolTip(APP_NAME)
        # Preserve the selected artwork. Trim transparent margins only in the
        # display buffers; keep the original PNG intact as the source asset.
        with Image.open(Path(__file__).parent/"assets"/"attentive-otter-wordmark.png") as source:
            rgba = np.array(source.convert("RGBA"))
        alpha = rgba[:, :, 3]
        y, x = np.nonzero(alpha >= 128)
        rgba = rgba[max(0, y.min()-2):y.max()+3, max(0, x.min()-2):x.max()+3]
        self.alpha = rgba[:, :, 3].copy()
        rgb = rgba[:, :, :3].astype(np.uint16)
        self.accent = (rgb[:, :, 0] > rgb[:, :, 1]*1.15) & (rgb[:, :, 1] > rgb[:, :, 2]*1.3)
        self.setFixedSize(width, round(width*self.alpha.shape[0]/self.alpha.shape[1]))
        self.set_colors(colors)

    def set_colors(self, colors):
        pixels = np.empty((*self.alpha.shape, 4), dtype=np.uint8)
        for mask, name in ((self.accent, "accent"), (~self.accent, "text")):
            color = QColor(colors[name])
            pixels[mask, :3] = (color.red(), color.green(), color.blue())
        pixels[:, :, 3] = self.alpha
        height, width = self.alpha.shape
        self.image = QImage(pixels.data, width, height, width*4, QImage.Format_RGBA8888).copy()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(QRectF(self.rect()), self.image)
