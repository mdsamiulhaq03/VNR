"""Voices tab: the editable voice presets (built-in 3 male / 8 female + your own)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import voices
from . import theme


def shrinkable(c: QComboBox) -> QComboBox:
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(6)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return c


def nice(tag: str) -> str:
    if not tag:
        return "Normal"
    return tag.replace(" accent", "").replace(" pitch", "").capitalize()


def combo(items: list[str], current: str, first: tuple[str, str] | None = None) -> QComboBox:
    c = shrinkable(QComboBox())
    if first:
        c.addItem(first[0], first[1])
    for it in items:
        c.addItem(nice(it), it)
    i = c.findData(current)
    c.setCurrentIndex(max(0, i))
    return c


def gender_toggle(parent: QWidget, genders=("female", "male"), on_click=None):
    lay = QHBoxLayout()
    lay.setSpacing(0)
    grp = QButtonGroup(parent)
    btns = {}
    for i, g in enumerate(genders):
        b = QPushButton(theme.gender_icon(g))
        b.setCheckable(True)
        b.setToolTip({"female": "Female", "male": "Male"}.get(g, "Not sure"))
        b.setObjectName("segL" if i == 0 else "segR" if i == len(genders) - 1 else "seg")
        if on_click:
            b.clicked.connect(lambda _=False, gg=g: on_click(gg))
        grp.addButton(b)
        lay.addWidget(b)
        btns[g] = b
    return lay, btns


def icon_button(text: str, tip: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("icon")
    b.setToolTip(tip)
    return b


class PresetCard(QFrame):
    changed = Signal(str, dict)
    reroll = Signal(str)
    preview = Signal(str)
    duplicate = Signal(str)
    delete = Signal(str)

    def __init__(self, p: dict):
        super().__init__()
        self.setObjectName("card")
        self.pid = p["id"]
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        top.addWidget(self.dot)
        self.ed_name = QLineEdit(p.get("name", ""))
        self.ed_name.setStyleSheet("font-weight:700; font-size:11.5pt;")
        self.ed_name.editingFinished.connect(
            lambda: self._emit({"name": self.ed_name.text().strip() or "Voice"}))
        top.addWidget(self.ed_name, 1)
        g_lay, self.gbtn = gender_toggle(self, on_click=lambda g: self._emit({"gender": g}))
        top.addLayout(g_lay)
        b_new = icon_button("🎲", "Different person, same settings")
        b_new.clicked.connect(lambda: self.reroll.emit(self.pid))
        b_play = icon_button("▶", "Preview")
        b_play.clicked.connect(lambda: self.preview.emit(self.pid))
        b_dup = icon_button("⧉", "Duplicate")
        b_dup.clicked.connect(lambda: self.duplicate.emit(self.pid))
        b_del = icon_button("🗑", "Delete preset")
        b_del.clicked.connect(lambda: self.delete.emit(self.pid))
        for b in (b_new, b_play, b_dup, b_del):
            top.addWidget(b)
        lay.addLayout(top)

        row = QHBoxLayout()
        self.cb_age = combo(voices.AGES, p.get("age", ""))
        self.cb_pitch = combo(voices.PITCHES, p.get("pitch", ""))
        self.cb_accent = combo(voices.ACCENTS, p.get("accent", ""), ("Any accent", ""))
        self.cb_style = combo(["whisper"], p.get("style", ""), ("Normal", ""))
        for cb, key, tip in ((self.cb_age, "age", "Age"), (self.cb_pitch, "pitch", "Pitch"),
                             (self.cb_accent, "accent", "Accent"),
                             (self.cb_style, "style", "Style")):
            cb.setToolTip(tip)
            cb.currentIndexChanged.connect(lambda _=0, c=cb, k=key: self._emit({k: c.currentData()}))
            row.addWidget(cb)
        lay.addLayout(row)
        self.lbl_note = QLabel()
        self.lbl_note.setObjectName("muted")
        lay.addWidget(self.lbl_note)
        self._loading = False
        self.update_preset(p, False)

    def _emit(self, changes: dict):
        if not self._loading:
            self.changed.emit(self.pid, changes)

    def update_preset(self, p: dict, ready: bool):
        self._loading = True
        g = p.get("gender", "female")
        self.gbtn.get(g, self.gbtn["female"]).setChecked(True)
        self.dot.setStyleSheet(f"background:{theme.gender_color(g)}; border-radius:6px;")
        if self.ed_name.text() != p.get("name"):
            self.ed_name.setText(p.get("name", ""))
        for cb, key in ((self.cb_age, "age"), (self.cb_pitch, "pitch"),
                        (self.cb_accent, "accent"), (self.cb_style, "style")):
            i = cb.findData(p.get(key, ""))
            if i >= 0:
                cb.setCurrentIndex(i)
        state = "✓ voice ready" if ready else "voice not prepared yet (made on first use)"
        note = p.get("note") or ""
        self.lbl_note.setText(f"{note} · {state}" if note else state)
        self._loading = False


class PresetsView(QWidget):
    changed = Signal(str, dict)
    reroll = Signal(str)
    preview = Signal(str)
    duplicate = Signal(str)
    delete = Signal(str)
    add = Signal(str)              # gender
    reset = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        t = QLabel("Voice presets")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch(1)
        b_f = QPushButton("+ ♀ voice")
        b_f.clicked.connect(lambda: self.add.emit("female"))
        b_m = QPushButton("+ ♂ voice")
        b_m.clicked.connect(lambda: self.add.emit("male"))
        b_reset = QPushButton("Reset")
        b_reset.setToolTip("Restore the built-in 3 male + 8 female presets")
        b_reset.clicked.connect(self.reset.emit)
        for b in (b_f, b_m, b_reset):
            head.addWidget(b)
        lay.addLayout(head)
        hint = QLabel("New characters automatically get an unused preset of their gender. "
                      "Edit any preset here: every character using it changes too.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("page")
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 4, 4, 4)
        self.list.setSpacing(8)
        self.list.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)
        self.cards: dict[str, PresetCard] = {}

    def load(self, presets: list[dict], ready: set[str]):
        for c in self.cards.values():
            c.setParent(None)
            c.deleteLater()
        self.cards.clear()
        order = sorted(presets, key=lambda p: (p.get("gender") != "female", ))
        for p in order:
            card = PresetCard(p)
            card.changed.connect(self.changed.emit)
            card.reroll.connect(self.reroll.emit)
            card.preview.connect(self.preview.emit)
            card.duplicate.connect(self.duplicate.emit)
            card.delete.connect(self.delete.emit)
            card.update_preset(p, p["id"] in ready)
            self.list.insertWidget(self.list.count() - 1, card)
            self.cards[p["id"]] = card

    def update(self, p: dict, ready: bool):
        c = self.cards.get(p["id"])
        if c:
            c.update_preset(p, ready)
