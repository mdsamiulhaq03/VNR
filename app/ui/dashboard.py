"""Dashboard pieces: level meter, live-area preview, cast list, character ⋯ menu."""
from __future__ import annotations

import time
from collections import deque

from PIL import Image
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..config import NARRATOR
from . import theme
from .characters_view import Avatar
from .settings_view import pil_to_pixmap


# ------------------------------------------------------------------ helpers
def character_menu(parent: QWidget, name: str, others: list[str], on_edit=None,
                   on_merge=None, on_delete=None) -> QMenu:
    m = QMenu(parent)
    if on_edit:
        a = QAction("🎙  Edit voice…", m)
        a.triggered.connect(lambda: on_edit(name))
        m.addAction(a)
    if name != NARRATOR and on_merge:
        sub = m.addMenu("⇢  Merge into…")
        targets = [o for o in others if o not in (name, NARRATOR)]
        if not targets:
            sub.setEnabled(False)
        for o in targets:
            a = QAction(o, sub)
            a.triggered.connect(lambda _=False, o=o: on_merge(name, o))
            sub.addAction(a)
    if name != NARRATOR and on_delete:
        m.addSeparator()
        a = QAction("🗑  Delete character", m)
        a.triggered.connect(lambda: on_delete(name))
        m.addAction(a)
    return m


class ResponsiveRow(QWidget):
    """Lays its children side by side when wide, stacked when narrow."""

    def __init__(self, breakpoint: int = 700):
        super().__init__()
        self.breakpoint = breakpoint
        self.box = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(12)

    def add(self, w: QWidget, stretch: int = 1):
        self.box.addWidget(w, stretch)

    def resizeEvent(self, e):
        d = QBoxLayout.LeftToRight if self.width() >= self.breakpoint else QBoxLayout.TopToBottom
        if self.box.direction() != d:
            self.box.setDirection(d)
        super().resizeEvent(e)


class LevelMeter(QWidget):
    """Little animated bars that move with the voice."""

    def __init__(self, bars: int = 14):
        super().__init__()
        self.setFixedSize(bars * 6, 22)
        self.hist = deque([0.0] * bars, maxlen=bars)

    def push(self, level: float):
        self.hist.append(min(1.0, level * 6))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        h = self.height()
        for i, v in enumerate(self.hist):
            bh = max(2.0, v * h)
            p.drawRoundedRect(QRectF(i * 6, (h - bh) / 2, 4, bh), 2, 2)


class AreaPreview(QLabel):
    """Thumbnail of the reading area with the name line (pink) and dialogue (blue)."""

    def __init__(self):
        super().__init__("Start reading to see\nthe live area here")
        self.setObjectName("muted")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: Image.Image | None = None
        self._boxes: list = []
        self._name_on_top = False

    def set_frame(self, img: Image.Image, boxes: list | None = None):
        self._img = img
        if boxes:
            self._boxes = boxes
        self._render()

    def set_name_on_top(self, on: bool):
        self._name_on_top = on
        self._render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()

    def _render(self):
        if self._img is None:
            return
        pm: QPixmap = pil_to_pixmap(self._img)
        w, h = max(50, self.width() - 8), max(30, self.height() - 8)
        scaled = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        k = scaled.width() / max(1, pm.width())
        p = QPainter(scaled)
        p.setRenderHint(QPainter.Antialiasing)
        for i, b in enumerate(self._boxes):
            bh, top, left, bw = b
            if not bw:
                continue
            color = theme.FEMALE if (i == 0 and self._name_on_top) else theme.ACCENT
            p.setPen(QPen(QColor(color), 2))
            p.drawRoundedRect(QRectF((left - 3) * k, (top - 3) * k, (bw + 6) * k, (bh + 6) * k),
                              4, 4)
        p.end()
        self.setPixmap(scaled)


# ---------------------------------------------------------------- cast list
class CastRow(QFrame):
    preview = Signal(str)
    menu = Signal(str, object)       # name, global position

    def __init__(self, name: str, rec: dict, voice_label: str):
        super().__init__()
        self.name = name
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.addWidget(Avatar(name, 30))
        g = rec.get("gender", "unknown")
        nm = QLabel(("Narrator" if name == NARRATOR else name) +
                    (f"  <span style='color:{theme.gender_color(g)}'>{theme.gender_icon(g)}</span>"
                     if name != NARRATOR and g != "unknown" else ""))
        nm.setStyleSheet("font-weight:600;")
        nm.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(nm, 3)
        v = QLabel(voice_label)
        v.setObjectName("muted")
        v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(v, 2)
        cnt = int(rec.get("lines", 0))
        n = QLabel(f"{cnt} line{'s' if cnt != 1 else ''}")
        n.setObjectName("muted")
        n.setFixedWidth(64)
        n.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(n)
        b_play = QPushButton("▶")
        b_play.setObjectName("icon")
        b_play.setToolTip("Preview voice")
        b_play.clicked.connect(lambda: self.preview.emit(self.name))
        b_more = QPushButton("⋯")
        b_more.setObjectName("icon")
        b_more.setToolTip("Edit, merge or delete")
        b_more.clicked.connect(lambda: self.menu.emit(
            self.name, b_more.mapToGlobal(b_more.rect().bottomLeft())))
        lay.addWidget(b_play)
        lay.addWidget(b_more)


class CastList(QWidget):
    preview = Signal(str)
    menu = Signal(str, object)

    SORTS = [("lines", "Most lines"), ("az", "A–Z"), ("recent", "Recently seen")]

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Search characters…")
        self.search.textChanged.connect(lambda _t: self._render())
        self.sort = QComboBox()
        for k, label in self.SORTS:
            self.sort.addItem(label, k)
        self.sort.currentIndexChanged.connect(lambda _i: self._render())
        top.addWidget(self.search, 1)
        top.addWidget(self.sort)
        lay.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(120)
        inner = QWidget()
        inner.setObjectName("cardInner")
        self.rows = QVBoxLayout(inner)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.rows.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)
        self._chars: dict = {}
        self._labels: dict = {}

    def set_characters(self, chars: dict[str, dict], voice_labels: dict[str, str]):
        self._chars, self._labels = chars, voice_labels
        self._render()

    def _render(self):
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        q = self.search.text().strip().lower()
        names = [n for n in self._chars if not q or q in n.lower()]
        key = self.sort.currentData()
        if key == "az":
            names.sort(key=lambda n: (n != NARRATOR, n.lower()))
        elif key == "recent":
            names.sort(key=lambda n: -float(self._chars[n].get("last_seen", 0)))
        else:
            names.sort(key=lambda n: -int(self._chars[n].get("lines", 0)))
        if not names:
            empty = QLabel("No characters yet: they appear when they first speak."
                           if not q else "No match.")
            empty.setObjectName("muted")
            self.rows.insertWidget(0, empty)
            return
        for i, n in enumerate(names):
            row = CastRow(n, self._chars[n], self._labels.get(n, ""))
            row.preview.connect(self.preview.emit)
            row.menu.connect(self.menu.emit)
            self.rows.insertWidget(i, row)


def stats_text(n_chars: int, n_lines: int, reading: bool, last: float = 0) -> str:
    parts = [f"{n_chars} character{'s' if n_chars != 1 else ''}",
             f"{n_lines} line{'s' if n_lines != 1 else ''} read"]
    if reading:
        parts.append("<span style='color:#8be28b'>● reading</span>")
    elif last:
        parts.append("last played " + time.strftime("%d %b", time.localtime(last)))
    return "  ·  ".join(parts)
