"""Editor for word replacements applied before speaking (and to speaker names)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

COLS = ["Find", "Replace with", "Whole word", "Match case"]


class FixupsView(QWidget):
    changed = Signal()

    def __init__(self, cfg, game_getter, selection_getter):
        super().__init__()
        self.cfg = cfg
        self.game_getter = game_getter
        self.selection_getter = selection_getter
        self._loading = False

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Replace words before they're spoken: fix names the voice says "
                             "wrong, or common OCR mistakes. Also applied to character names."))
        lay.itemAt(0).widget().setWordWrap(True)
        top = QHBoxLayout()
        self.rb_game = QRadioButton("This game")
        self.rb_global = QRadioButton("All games")
        self.rb_game.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_game)
        g.addButton(self.rb_global)
        self.rb_game.toggled.connect(lambda _: self.reload())
        top.addWidget(self.rb_game)
        top.addWidget(self.rb_global)
        top.addStretch(1)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._save)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        b_add = QPushButton("Add")
        b_add.clicked.connect(lambda: self._add_row())
        b_from = QPushButton("Add from selected text")
        b_from.setToolTip("Select a word in the current line on the Read tab, then click this")
        b_from.clicked.connect(self._add_from_selection)
        b_del = QPushButton("Remove")
        b_del.clicked.connect(self._remove)
        for b in (b_add, b_from, b_del):
            row.addWidget(b)
        lay.addLayout(row)
        self.reload()

    def _rules(self) -> list[dict]:
        if self.rb_global.isChecked():
            return self.cfg.settings.fixups_global
        return self.cfg.game_fixups(self.game_getter())

    def reload(self):
        self._loading = True
        self.table.setRowCount(0)
        for r in self._rules():
            self._add_row(r, save=False)
        self._loading = False

    def _add_row(self, rule: dict | None = None, save: bool = True):
        rule = rule or {"find": "", "replace": "", "whole_word": True, "case_sensitive": False}
        was = self._loading
        self._loading = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(rule.get("find", "")))
        self.table.setItem(row, 1, QTableWidgetItem(rule.get("replace", "")))
        for col, key in ((2, "whole_word"), (3, "case_sensitive")):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if rule.get(key) else Qt.Unchecked)
            self.table.setItem(row, col, it)
        self._loading = was
        if save:
            self._save()
            self.table.editItem(self.table.item(row, 0))

    def _add_from_selection(self):
        word = (self.selection_getter() or "").strip()
        self._add_row({"find": word, "replace": word, "whole_word": True,
                       "case_sensitive": False})

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._save()

    def _save(self, *_):
        if self._loading:
            return
        rules = []
        for r in range(self.table.rowCount()):
            get = lambda c: self.table.item(r, c)   # noqa: E731
            rules.append({
                "find": get(0).text() if get(0) else "",
                "replace": get(1).text() if get(1) else "",
                "whole_word": bool(get(2) and get(2).checkState() == Qt.Checked),
                "case_sensitive": bool(get(3) and get(3).checkState() == Qt.Checked),
            })
        target = self._rules()
        target[:] = rules
        self.cfg.save()
        self.changed.emit()
