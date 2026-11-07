"""Readable paths in compact comparison cards and resizable tables."""

from PyQt5.QtCore import Qt, QRect, QSize, QTimer
from PyQt5.QtGui import QTextOption
from PyQt5.QtWidgets import (QApplication, QPlainTextEdit, QStyledItemDelegate,
                            QStyle, QStyleOptionViewItem)


class PathDisplay(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setObjectName("pathDisplay")
        self.setReadOnly(True)
        self.setWordWrapMode(QTextOption.WrapAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().setDocumentMargin(2)
        self.setTabChangesFocus(True)
        self.setMinimumWidth(0)
        self.textChanged.connect(self.fit_height)

    def fit_height(self):
        width = max(80, self.viewport().width()-4)
        height = self.fontMetrics().boundingRect(
            QRect(0, 0, width, 10000), Qt.TextWrapAnywhere, self.toPlainText()).height()
        self.setFixedHeight(min(4*self.fontMetrics().lineSpacing()+8, max(26, height+8)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()


class PathDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        value, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setFont(opt.font)
        role = opt.palette.HighlightedText if opt.state & QStyle.State_Selected else opt.palette.Text
        painter.setPen(opt.palette.color(role))
        painter.drawText(opt.rect.adjusted(7, 5, -7, -5), Qt.TextWrapAnywhere | Qt.AlignVCenter, value)
        painter.restore()

    def sizeHint(self, option, index):
        table = self.parent()
        width = max(40, table.columnWidth(index.column())-14)
        height = option.fontMetrics.boundingRect(
            QRect(0, 0, width, 100000), Qt.TextWrapAnywhere, str(index.data() or "")).height()
        return QSize(width+14, max(34, height+12))


def readable_paths(table, columns, widths):
    """Keep paths wide enough to read; wrap again after the user resizes a column."""
    table.horizontalHeader().setStretchLastSection(False)
    table.setTextElideMode(Qt.ElideNone)
    table.setWordWrap(True)
    for column, width in enumerate(widths):
        table.setColumnWidth(column, width)
    for column in columns:
        table.setItemDelegateForColumn(column, PathDelegate(table))
    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.setInterval(100)
    timer.timeout.connect(table.resizeRowsToContents)
    table.horizontalHeader().sectionResized.connect(lambda *_: timer.start())
