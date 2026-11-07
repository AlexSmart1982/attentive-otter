"""Amber Canvas palettes, system appearance tracking and native Windows title bars."""

import os
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, QByteArray, Qt
from PyQt5.QtGui import QColor, QPalette, QPixmap, QPainter, QIcon
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication


PALETTES = {
    "dark": dict(bg="#171819", sidebar="#1d1e20", surface="#242628", raised="#2c2e30", text="#f0eee9",
                 muted="#aaa8a3", border="#393b3d", accent="#f0a34b", accent_text="#26190d",
                 selection="#4b3926", good="#b6dc99", good_bg="#30422d", recommended_border="#626d59",
                 recommended_bg="#2a3028", bad="#edb0a7", bad_bg="#46302e"),
    "light": dict(bg="#f8f9fa", sidebar="#f1f3f4", surface="#ffffff", raised="#f8f9fa", text="#282c30",
                  muted="#697076", border="#e1e5e8", accent="#3c7257", accent_text="#ffffff",
                  selection="#eef2ef", good="#2f673e", good_bg="#f0f7f1", recommended_border="#b6c8ba",
                  recommended_bg="#f3f7f4", bad="#8e3e32", bad_bg="#faf0ed")}


def system_dark(fallback=False):
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return fallback
    return fallback


def history_icon(name, colors):
    source = (Path(__file__).parent/"assets"/(name+".svg")).read_text(encoding="utf-8")
    icon = QIcon()
    for mode, color, opacity in ((QIcon.Normal, colors["text"], 1), (QIcon.Disabled, colors["muted"], .4)):
        renderer = QSvgRenderer(QByteArray(source.replace("currentColor", color).encode("utf-8")))
        pixmap = QPixmap(44, 44)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(opacity)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon.addPixmap(pixmap, mode)
    return icon


def stylesheet(name):
    p = PALETTES[name]
    check_icon = (Path(__file__).parent/"assets"/("check.svg" if name == "dark" else "check-light.svg")).as_posix()
    up_icon = (Path(__file__).parent/"assets"/"chevron-up.svg").as_posix()
    down_icon = (Path(__file__).parent/"assets"/"chevron-down.svg").as_posix()
    return f"""
    QWidget {{ color: {p['text']}; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {p['bg']}; }}
    QWidget#sidebar {{ background: {p['sidebar']}; border-radius: 16px; }}
    QFrame#photoCard, QFrame#exifPanel, QFrame#phaseCard {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 16px; }}
    QFrame#photoCard[recommended="true"] {{ background: {p['recommended_bg']}; border-color: {p['recommended_border']}; }}
    QFrame#photoCard[recommended="true"] QGraphicsView,
    QFrame#photoCard[recommended="true"] QTableWidget#parameters {{ background: {p['recommended_bg']}; }}
    QLabel#brand {{ font-size: 22px; font-weight: 650; }}
    QLabel#hero {{ font-size: 27px; font-weight: 600; }}
    QLabel#phaseBadge {{ color: {p['text']}; background: {p['raised']}; padding: 6px 10px; border-radius: 8px; }}
    QLabel#notice {{ color: {p['text']}; background: {p['raised']}; padding: 9px 12px; border-radius: 8px; }}
    QLabel#heading {{ font-size: 17px; font-weight: 600; }}
    QLabel#muted {{ color: {p['muted']}; }}
    QLabel#test {{ color: {p['accent']}; background: {p['selection']}; padding: 7px 12px; border-radius: 9px; }}
    QPushButton, QToolButton {{ background: {p['raised']}; border: 1px solid {p['border']}; border-radius: 9px; padding: 7px 12px; }}
    QPushButton:hover, QToolButton:hover {{ background: {p['selection']}; border-color: {p['accent']}; }}
    QPushButton:pressed {{ background: {p['selection']}; }}
    QPushButton:disabled {{ color: {p['muted']}; border-color: {p['border']}; background: transparent; }}
    QPushButton#primary {{ background: {p['accent']}; color: {p['accent_text']}; border-color: {p['accent']}; font-weight: 600; }}
    QPushButton#primary:disabled {{ background: {p['raised']}; color: {p['muted']}; border-color: {p['border']}; }}
    QPushButton#keep {{ font-weight: 600; }}
    QPushButton#history {{ padding: 6px 10px; font-weight: 600; }}
    QPushButton#history:enabled {{ background: {p['selection']}; border-color: {p['accent']}; }}
    QPushButton:pressed {{ border-color: {p['accent']}; border-width: 2px; }}
    QPlainTextEdit#pathDisplay {{ color: {p['muted']}; background: transparent; border: none; padding: 0; }}
    QTableWidget#parameters {{ border: none; border-radius: 0; }}
    QTableWidget#parameters::item {{ padding: 2px; border-bottom: 1px solid {p['border']}; }}
    QListWidget, QTreeWidget, QTableWidget {{ background: {p['surface']}; alternate-background-color: {p['raised']};
        border: 1px solid {p['border']}; border-radius: 10px; outline: 0; gridline-color: {p['border']}; }}
    QTreeWidget#groups, QListWidget#sources {{ background: transparent; border: none; }}
    QTreeWidget::item {{ padding: 5px 4px; }}
    QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{ background: {p['selection']}; color: {p['text']}; }}
    QTreeWidget::item:selected {{ border-radius: 9px; }}
    QTableWidget::item {{ padding: 4px; border-bottom: 1px solid {p['border']}; }}
    QHeaderView::section {{ background: {p['raised']}; color: {p['muted']}; padding: 7px 5px; border: none; font-weight: 500; }}
    QTableCornerButton::section {{ background: {p['raised']}; border: none; }}
    QGraphicsView {{ background: {p['bg']}; border: none; border-radius: 12px; }}
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator, QAbstractItemView::indicator {{ width: 15px; height: 15px; border: 1px solid {p['muted']}; border-radius: 4px; background: {p['surface']}; }}
    QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; image: url('{check_icon}'); }}
    QCheckBox::indicator:disabled, QAbstractItemView::indicator:disabled {{ border-color: {p['border']}; background: {p['raised']}; }}
    QComboBox, QSpinBox, QLineEdit {{ background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 7px; padding: 6px; }}
    QSpinBox {{ padding-right: 32px; min-height: 22px; }}
    QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 27px; height: 17px;
        background: {p['raised']}; border-left: 1px solid {p['border']}; border-bottom: 1px solid {p['border']}; border-top-right-radius: 7px; }}
    QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 27px; height: 17px;
        background: {p['raised']}; border-left: 1px solid {p['border']}; border-bottom-right-radius: 7px; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {p['selection']}; }}
    QSpinBox::up-arrow {{ image: url('{up_icon}'); width: 10px; height: 6px; }}
    QSpinBox::down-arrow {{ image: url('{down_icon}'); width: 10px; height: 6px; }}
    QComboBox {{ padding-right: 32px; }}
    QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: top right; width: 27px; border-left: 1px solid {p['border']}; }}
    QComboBox::down-arrow {{ image: url('{down_icon}'); width: 10px; height: 6px; }}
    QComboBox QAbstractItemView {{ background: {p['surface']}; color: {p['text']}; selection-background-color: {p['selection']}; }}
    QGroupBox {{ border: 1px solid {p['border']}; border-radius: 10px; margin-top: 12px; padding: 8px; }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:hover {{ background: {p['border']}; border-radius: 2px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p['border']}; min-height: 28px; border-radius: 5px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {p['border']}; min-width: 28px; border-radius: 5px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QProgressBar {{ border: 1px solid {p['border']}; border-radius: 5px; background: {p['raised']}; text-align: center; }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 4px; }}
    QMenu {{ background: {p['surface']}; border: 1px solid {p['border']}; padding: 5px; }}
    QMenu::item {{ padding: 7px 18px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p['selection']}; }}
    QToolTip {{ color: {p['text']}; background: {p['raised']}; border: 1px solid {p['border']}; padding: 5px; }}
    QStatusBar {{ color: {p['muted']}; background: transparent; }}
    """


class ThemeController(QObject):
    def __init__(self, window, mode="system"):
        super().__init__(window)
        self.window, self.mode, self.current = window, mode, None
        self.fallback = QApplication.palette().color(QPalette.Window).lightness() < 128
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.update)
        self.timer.start()
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def update(self):
        name = ("dark" if system_dark(self.fallback) else "light") if self.mode == "system" else self.mode
        if name == self.current:
            return
        self.current = name
        p = PALETTES[name]
        palette = QPalette()
        for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
                          (QPalette.AlternateBase, "raised"), (QPalette.Text, "text"), (QPalette.Button, "raised"),
                          (QPalette.ButtonText, "text"), (QPalette.Highlight, "selection"), (QPalette.HighlightedText, "text"),
                          (QPalette.ToolTipBase, "raised"), (QPalette.ToolTipText, "text")):
            palette.setColor(role, QColor(p[key]))
        QApplication.instance().setPalette(palette)
        self.window.setStyleSheet(stylesheet(name))
        self.window.theme_changed(name)
        self.native_title()

    def native_title(self):
        if os.name == "nt":
            try:
                import ctypes
                value = ctypes.c_int(self.current == "dark")
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(self.window.winId())), 20, ctypes.byref(value), 4)
            except (OSError, AttributeError):
                pass
