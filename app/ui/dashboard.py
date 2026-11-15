"""Read-page pieces: level meter, live-area preview, transcript, cast list, menus, toast."""
from __future__ import annotations

import time
from collections import deque

from PIL import Image
from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..config import NARRATOR
from . import theme
from .components import Avatar, EmptyState, IconLabel, SearchField, fade_in, icon_button
from .settings_view import pil_to_pixmap


# ------------------------------------------------------------------ menus
def character_menu(parent: QWidget, name: str, others: list[str], on_edit=None,
                   on_merge=None, on_delete=None, on_rename=None) -> QMenu:
    m = QMenu(parent)
    if on_edit:
        m.addAction(theme.icon("mic", "TEXT_2", active="ON_ACCENT"), "Edit voice…",
                    lambda: on_edit(name))
    if name != NARRATOR and on_rename:
        m.addAction(theme.icon("pencil", "TEXT_2", active="ON_ACCENT"), "Rename…",
                    lambda: on_rename(name))
    if name != NARRATOR and on_merge:
        sub = m.addMenu(theme.icon("merge", "TEXT_2", active="ON_ACCENT"), "Merge into")
        targets = [o for o in others if o not in (name, NARRATOR)]
        if not targets:
            sub.setEnabled(False)
        for o in targets:
            a = QAction(o, sub)
            a.triggered.connect(lambda _=False, o=o: on_merge(name, o))
            sub.addAction(a)
    if name != NARRATOR and on_delete:
        m.addSeparator()
        m.addAction(theme.icon("trash-2", "DANGER", active="ON_ACCENT"), "Delete character…",
                    lambda: on_delete(name))
    return m


# ------------------------------------------------------------- level meter
class LevelMeter(QWidget):
    """Thin white bars that move with the voice."""

    def __init__(self, bars: int = 16):
        super().__init__()
        self.setFixedSize(bars * 5, 20)
        self.hist = deque([0.0] * bars, maxlen=bars)

    def push(self, level: float):
        self.hist.append(min(1.0, level * 6))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        h = self.height()
        for i, v in enumerate(self.hist):
            bh = max(2.0, v * h)
            c = QColor(theme.ACCENT if v > 0.02 else theme.BORDER_HOVER)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(i * 5, (h - bh) / 2, 2, bh), 1, 1)


# --------------------------------------------------------- now-playing bits
class ProgressEdge(QWidget):
    """A thin progress line (along the bottom edge of the now-playing card)."""

    def __init__(self, height: int = 3):
        super().__init__()
        self.setFixedHeight(height)
        self._v = 0.0

    def set_value(self, v: float):
        v = max(0.0, min(1.0, float(v)))
        if abs(v - self._v) > 0.002:
            self._v = v
            self.update()

    def value(self) -> float:
        return self._v

    def paintEvent(self, _e):
        if self._v <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        h = self.height()
        p.drawRoundedRect(QRectF(0, 0, self.width() * self._v, h), h / 2, h / 2)


def sentence_html(parts: list[str], current: int) -> str:
    """The line with the sentence being spoken in full colour, the others dimmed."""
    import html
    out = []
    for j, s in enumerate(parts):
        col = theme.TEXT if j == current else theme.TEXT_3
        out.append(f"<span style='color:{col}'>{html.escape(s)}</span>")
    return " ".join(out)


def match_sentence(spoken: str, parts: list[str]) -> int:
    """Which shown sentence a spoken sentence is (the spoken text has stage directions
    removed and may start with the name), by shared words. -1 = none."""
    import re
    words = lambda s: set(re.findall(r"\w+", s.lower()))       # noqa: E731
    w = words(spoken)
    if not w:
        return -1
    best, score = -1, 0.0
    for j, s in enumerate(parts):
        ws = words(s)
        if not ws:
            continue
        sc = len(w & ws) / max(1, len(w))
        if sc > score:
            best, score = j, sc
    return best if score >= 0.5 else -1


# ------------------------------------------------------------ live preview
class AreaPreview(QLabel):
    """Thumbnail of the reading area: name line / name area outlined in the name colour,
    dialogue lines in the dialogue colour."""

    def __init__(self):
        super().__init__("Start reading to see the live area")
        self.setObjectName("areaPreview")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: Image.Image | None = None
        self._boxes: list = []
        self._name_on_top = False

    def set_frame(self, img: Image.Image, boxes: list | None = None,
                  name_img: Image.Image | None = None):
        """name_img: the separate name area (shown above the dialogue)."""
        self._name_box = None
        self._name_area = name_img is not None
        if name_img is not None:
            gap = 6
            combo = Image.new("RGB", (max(img.width, name_img.width),
                                      name_img.height + gap + img.height), (0, 0, 0))
            combo.paste(name_img.convert("RGB"), (0, 0))
            combo.paste(img.convert("RGB"), (0, name_img.height + gap))
            self._name_box = (name_img.width, name_img.height)
            self._offset = name_img.height + gap
            img = combo
        else:
            self._offset = 0
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
        w, h = max(50, self.width() - 10), max(30, self.height() - 10)
        scaled = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        k = scaled.width() / max(1, pm.width())
        p = QPainter(scaled)
        p.setRenderHint(QPainter.Antialiasing)
        off = getattr(self, "_offset", 0)
        if getattr(self, "_name_box", None):
            nw, nh = self._name_box
            p.setPen(QPen(QColor(theme.NAME), 1.5))
            p.drawRoundedRect(QRectF(1, 1, nw * k - 2, nh * k - 2), 3, 3)
        for i, b in enumerate(self._boxes):
            bh, top, left, bw = b
            if not bw:
                continue
            top += off
            name_line = i == 0 and self._name_on_top and not getattr(self, "_name_area", False)
            p.setPen(QPen(QColor(theme.NAME if name_line else theme.DIALOGUE), 1.5))
            p.drawRoundedRect(QRectF((left - 3) * k, (top - 3) * k, (bw + 6) * k, (bh + 6) * k),
                              3, 3)
        p.end()
        self.setPixmap(scaled)


# --------------------------------------------------------------- transcript
class Transcript(QListWidget):
    """The last lines read, newest at the top. Click a line to hear it again."""
    replay = Signal(str, str)
    MAX = 40

    def __init__(self):
        super().__init__()
        self.setObjectName("transcriptList")
        self.setFrameShape(QFrame.NoFrame)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setCursor(Qt.PointingHandCursor)
        self.itemClicked.connect(lambda it: self.replay.emit(*it.data(Qt.UserRole)))
        self._empty = QListWidgetItem("Lines you hear appear here.")
        self._empty.setFlags(Qt.NoItemFlags)
        self.addItem(self._empty)

    def add(self, speaker: str, text: str):
        if self._empty is not None:
            self.takeItem(self.row(self._empty))
            self._empty = None
        who = "Narrator" if speaker == NARRATOR else speaker
        it = QListWidgetItem(f"{who}:  {text}")
        it.setData(Qt.UserRole, (speaker, text))
        it.setToolTip("Click to hear it again")
        self.insertItem(0, it)
        while self.count() > self.MAX:
            self.takeItem(self.count() - 1)


# ---------------------------------------------------------------- cast list
class CastRow(QFrame):
    preview = Signal(str)
    menu = Signal(str, object)       # name, global position

    def __init__(self, name: str, rec: dict, voice_label: str):
        super().__init__()
        self.name = name
        self.setObjectName("castRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 2, 5)
        lay.setSpacing(10)
        av = Avatar(name, 30)
        av.set_name(name, 30, ring="" if name == NARRATOR else theme.char_colour(rec))
        lay.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(1)
        nm = QLabel("Narrator" if name == NARRATOR else name)
        nm.setObjectName("strong")
        nm.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v = QLabel(voice_label)
        v.setObjectName("footnote")
        v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(nm)
        col.addWidget(v)
        lay.addLayout(col, 1)
        cnt = int(rec.get("lines", 0))
        n = QLabel(str(cnt))
        n.setObjectName("number")
        n.setToolTip(f"{cnt} line{'s' if cnt != 1 else ''}")
        lay.addWidget(n)
        lay.addWidget(icon_button("play", "Preview voice", lambda: self.preview.emit(self.name),
                                  size=14))
        b_more = icon_button("ellipsis", "Edit voice, rename, merge or delete", size=14)
        b_more.clicked.connect(lambda: self.menu.emit(
            self.name, b_more.mapToGlobal(b_more.rect().bottomLeft())))
        lay.addWidget(b_more)


class CastList(QWidget):
    preview = Signal(str)
    menu = Signal(str, object)

    SORTS = [("lines", "Most lines"), ("az", "A–Z"), ("recent", "Recent")]

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = SearchField("Search")
        self.search.textChanged.connect(lambda _t: self._render())
        self.sort = QComboBox()
        for k, text in self.SORTS:
            self.sort.addItem(text, k)
        self.sort.currentIndexChanged.connect(lambda _i: self._render())
        top.addWidget(self.search, 1)
        top.addWidget(self.sort)
        lay.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(100)
        inner = QWidget()
        inner.setObjectName("transparent")
        self.rows = QVBoxLayout(inner)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(1)
        self.rows.addStretch(1)
        self.scroll.setWidget(inner)
        self.scroll.setObjectName("clear")
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
                w.hide()                 # gone now, not when Qt gets round to deleting it
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
            self.rows.insertWidget(0, EmptyState(
                "users", "No characters yet" if not q else "No match",
                "They appear when they first speak." if not q else ""))
            return
        for i, n in enumerate(names):
            row = CastRow(n, self._chars[n], self._labels.get(n, ""))
            row.preview.connect(self.preview.emit)
            row.menu.connect(self.menu.emit)
            self.rows.insertWidget(i, row)


# -------------------------------------------------------------------- stats
def stats_chips(n_chars: int, n_lines: int, reading: bool, last: float = 0
                ) -> list[tuple[str, bool]]:
    chips = [(f"{n_chars} character{'s' if n_chars != 1 else ''}", False),
             (f"{n_lines} line{'s' if n_lines != 1 else ''} read", False)]
    if reading:
        chips.append(("● Reading", True))
    elif last:
        chips.append(("last " + time.strftime("%d %b", time.localtime(last)), False))
    return chips


class StatsLabel(QLabel):
    """Mono metadata next to the page title: '5 characters · 342 lines read'."""

    def __init__(self):
        super().__init__()
        self.setObjectName("footnote")

    def set_chips(self, chips: list[tuple[str, bool]]):
        self.setText(",  ".join(t for t, live in chips if not live))


# -------------------------------------------------------------------- toast
class Toast(QFrame):
    """A short message: a pill that rises in at the bottom centre of the window and
    fades (above the player controls, so it never covers the line being read)."""

    def target(self) -> QPoint:
        p = self.parentWidget()
        return QPoint(max(8, (p.width() - self.width()) // 2),
                      p.height() - self.height() - 64)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toastFrame")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 16, 9)
        lay.setSpacing(9)
        self.ic = IconLabel("circle-check", "OK", 16)
        lay.addWidget(self.ic, 0, Qt.AlignVCenter)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        self.lbl.setMaximumWidth(420)
        lay.addWidget(self.lbl)
        self.hide()
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(theme.motion(200))
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(theme.motion(200))
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._timer = QTimer(self, singleShot=True, timeout=self._fade_out)

    def text(self) -> str:
        return self.lbl.text()

    def show_message(self, text: str, ms: int = 3200, color: str = ""):
        role = theme.role_of(color) if color else ""
        warn = role in ("WARN", "DANGER")
        self.ic.set_icon("triangle-alert" if warn else "circle-check",
                         role or ("WARN" if warn else "OK"))
        self.lbl.setText(text)
        self.adjustSize()
        end = self.target()
        self.raise_()
        self.show()
        self._slide.stop()
        self._slide.setStartValue(end + QPoint(0, 8))
        self._slide.setEndValue(end)
        self._slide.start()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self):
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._hide_once)
        self._anim.start()

    def _hide_once(self):
        try:
            self._anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        if self._fx.opacity() < 0.05:
            self.hide()
