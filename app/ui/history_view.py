"""History page: every line read this session, as a table. Double-click to hear it again."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHeaderView, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..config import NARRATOR
from . import theme
from .components import EmptyState, SearchField, button, icon_button

MAX_ENTRIES = 2000


class HistoryView(QWidget):
    replay_requested = Signal(str, str)      # speaker, text

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["TIME", "SPEAKER", "LINE", "MOOD"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._replay_row(r))
        self.empty = EmptyState("history", "Nothing read yet",
                                "Every line you hear this session is listed here. "
                                "Double-click one to hear it again.")
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.table)
        lay.addWidget(self.stack)
        # (back-compat: older code used a QListWidget called .list)
        self.list = self.table

    def install_toolbar(self, header):
        self.search = SearchField("Search lines")
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._filter)
        header.add(self.search, None,
                   icon_button("play", "Play the selected line",
                               lambda: self._replay_row(self.table.currentRow())),
                   icon_button("copy", "Copy (selected lines, or all)", self._copy),
                   icon_button("file-down", "Export to a text file…", self._export),
                   icon_button("eraser", "Clear the history", self._clear))

    def add(self, speaker: str, text: str, mood: str = "") -> None:
        sb = self.table.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        r = self.table.rowCount()
        self.table.insertRow(r)
        t = QTableWidgetItem(time.strftime("%H:%M:%S"))
        t.setFont(theme.mono(8.5))
        t.setForeground(Qt.gray)
        who = QTableWidgetItem("Narrator" if speaker == NARRATOR else speaker)
        who.setForeground(Qt.gray)
        line = QTableWidgetItem(text)
        line.setData(Qt.UserRole, (speaker, text))
        md = QTableWidgetItem(mood)
        md.setForeground(Qt.gray)
        for c, it in enumerate((t, who, line, md)):
            self.table.setItem(r, c, it)
        while self.table.rowCount() > MAX_ENTRIES:
            self.table.removeRow(0)
        self.stack.setCurrentWidget(self.table)
        if at_bottom:
            self.table.scrollToBottom()
        if getattr(self, "search", None) and self.search.text():
            self._filter(self.search.text())

    def count(self) -> int:
        return self.table.rowCount()

    def _replay_row(self, r: int):
        it = self.table.item(r, 2) if r >= 0 else None
        if it is not None:
            self.replay_requested.emit(*it.data(Qt.UserRole))

    def _text(self, r: int) -> str:
        sp, text = self.table.item(r, 2).data(Qt.UserRole)
        stamp = self.table.item(r, 0).text()
        mood = self.table.item(r, 3).text() if self.table.item(r, 3) else ""
        return f"[{stamp}]  " + (text if sp == NARRATOR else f"{sp}: {text}") + \
            (f"  ({mood})" if mood else "")

    def _rows(self, selected_only: bool) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) if selected_only else []
        return rows or [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    def _copy(self):
        QGuiApplication.clipboard().setText("\n".join(self._text(r) for r in self._rows(True)))

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export history", "vn_history.txt",
                                              "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._text(r) for r in range(self.table.rowCount())) + "\n")

    def _clear(self):
        self.table.setRowCount(0)
        self.stack.setCurrentWidget(self.empty)

    def _filter(self, q: str):
        q = q.strip().lower()
        for r in range(self.table.rowCount()):
            hay = " ".join(self.table.item(r, c).text() for c in (1, 2, 3)
                           if self.table.item(r, c)).lower()
            self.table.setRowHidden(r, bool(q) and q not in hay)
