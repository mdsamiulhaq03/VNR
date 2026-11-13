"""Fix-ups page: word replacements applied before speaking (and to speaker names), plus
this game's word memory."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QRadioButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .components import SegmentedControl, button, icon_button, label, section

COLS = ["FIND", "REPLACE WITH", "WHOLE WORD", "MATCH CASE"]


class FixupsView(QWidget):
    changed = Signal()

    def __init__(self, cfg, game_getter, selection_getter):
        super().__init__()
        self.setObjectName("page")
        self.cfg = cfg
        self.game_getter = game_getter
        self.selection_getter = selection_getter
        self._loading = False

        # Scope (kept as radio buttons for the rest of the code; shown as a segmented control)
        self.rb_game = QRadioButton("This game")
        self.rb_global = QRadioButton("All games")
        self.rb_game.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_game)
        g.addButton(self.rb_global)
        self.rb_game.toggled.connect(lambda _: self.reload())
        self.scope = SegmentedControl([("game", "This game"), ("all", "All games")], "game")
        self.scope.changed.connect(lambda k: (self.rb_game if k == "game"
                                              else self.rb_global).setChecked(True))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        lay.addWidget(split)

        top = QWidget()
        top.setObjectName("page")
        tl = QVBoxLayout(top)
        tl.setContentsMargins(16, 14, 16, 12)
        tl.setSpacing(8)
        tl.addWidget(label("Replace words before they're spoken: names the voice says wrong, "
                           "or recurring OCR mistakes. Rules also apply to character names.",
                           "muted", wrap=True))
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFont(theme.mono(9.5))
        self.table.itemChanged.connect(self._save)
        tl.addWidget(self.table, 1)
        split.addWidget(top)

        # Game words: never auto-corrected (made-up names, places, slang).
        bottom = QWidget()
        bottom.setObjectName("page")
        wl = QVBoxLayout(bottom)
        wl.setContentsMargins(16, 12, 16, 14)
        wl.setSpacing(8)
        wh = QHBoxLayout()
        wh.addWidget(section("Game words"))
        wh.addWidget(label("never auto-corrected · learned after 3 lines (dim)", "faint"))
        wh.addStretch(1)
        wl.addLayout(wh)
        self.words = QListWidget()
        self.words.setObjectName("chips")
        self.words.setFlow(QListWidget.LeftToRight)
        self.words.setWrapping(True)
        self.words.setResizeMode(QListWidget.Adjust)
        self.words.setSpacing(4)
        self.words.setSelectionMode(QListWidget.ExtendedSelection)
        wl.addWidget(self.words, 1)
        wr = QHBoxLayout()
        self.ed_word = QLineEdit()
        self.ed_word.setPlaceholderText("Add a word, e.g. Eldoria")
        self.ed_word.returnPressed.connect(self._add_word)
        wr.addWidget(self.ed_word, 1)
        wr.addWidget(button("Add word", "plus", slot=self._add_word))
        wr.addWidget(button("Remove selected", "x", "ghost",
                            "Removed learned words aren't learned again",
                            slot=self._remove_words))
        wl.addLayout(wr)
        split.addWidget(bottom)
        split.setSizes([420, 220])
        self.reload()

    def install_toolbar(self, header):
        header.add(self.scope, None,
                   button("Add rule", "plus", slot=lambda: self._add_row()),
                   button("From selection", "text-cursor-input", "ghost",
                          "Select a word in the current line on the Read page, then click this",
                          slot=self._add_from_selection),
                   icon_button("trash-2", "Remove the selected rules", self._remove))

    # ---- game words ----
    def _reload_words(self):
        self.words.clear()
        for w, learned in self.cfg.word_entries(self.game_getter()):
            it = QListWidgetItem(w)
            it.setData(Qt.UserRole, w)
            if learned:
                it.setForeground(Qt.gray)
                it.setToolTip("Learned automatically")
            self.words.addItem(it)

    def _add_word(self):
        for w in self.ed_word.text().replace(",", " ").split():
            self.cfg.add_game_word(self.game_getter(), w)
        self.ed_word.clear()
        self._reload_words()

    def _remove_words(self):
        for it in self.words.selectedItems():
            self.cfg.remove_game_word(self.game_getter(), it.data(Qt.UserRole))
        self._reload_words()

    def _rules(self) -> list[dict]:
        if self.rb_global.isChecked():
            return self.cfg.settings.fixups_global
        return self.cfg.game_fixups(self.game_getter())

    def reload(self):
        if hasattr(self, "words"):
            self._reload_words()
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
