"""Log of every line read this session. Double-click to hear a line again."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ..config import NARRATOR

MAX_ENTRIES = 2000


class HistoryView(QWidget):
    replay_requested = Signal(str, str)      # speaker, text

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAlternatingRowColors(True)
        self.list.itemDoubleClicked.connect(self._replay)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_play = QPushButton("▶ Play selected")
        b_play.clicked.connect(lambda: self._replay(self.list.currentItem()))
        b_copy = QPushButton("Copy")
        b_copy.clicked.connect(self._copy)
        b_export = QPushButton("Export…")
        b_export.clicked.connect(self._export)
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(self.list.clear)
        for b in (b_play, b_copy, b_export, b_clear):
            row.addWidget(b)
        lay.addLayout(row)

    def add(self, speaker: str, text: str) -> None:
        at_bottom = self.list.verticalScrollBar().value() >= self.list.verticalScrollBar().maximum() - 4
        stamp = time.strftime("%H:%M:%S")
        label = text if speaker == NARRATOR else f"{speaker}: {text}"
        item = QListWidgetItem(f"[{stamp}]  {label}")
        item.setData(Qt.UserRole, (speaker, text))
        self.list.addItem(item)
        while self.list.count() > MAX_ENTRIES:
            self.list.takeItem(0)
        if at_bottom:
            self.list.scrollToBottom()

    def _replay(self, item):
        if item is not None:
            speaker, text = item.data(Qt.UserRole)
            self.replay_requested.emit(speaker, text)

    def _lines(self, items) -> list[str]:
        return [i.text() for i in items]

    def _copy(self):
        items = self.list.selectedItems() or [self.list.item(i) for i in range(self.list.count())]
        QGuiApplication.clipboard().setText("\n".join(self._lines(items)))

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export history", "vn_history.txt",
                                              "Text files (*.txt)")
        if path:
            items = [self.list.item(i) for i in range(self.list.count())]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._lines(items)) + "\n")
