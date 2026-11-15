"""Mini-player: a small strip that floats over the game.

Who is speaking (with their colour), how (mood), the line with the sentence being spoken
highlighted, and a progress line. The controls appear when the mouse is over it. Drag it
anywhere; it never takes focus from the game and the screen reader never sees it.
Same interface as ChatHead (update_state, set_level, place, set_shown), so either can be
the floating control.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from ..config import NARRATOR
from . import theme
from .chat_head import _FLAGS, _overlay
from .components import Avatar, icon_button, label
from .dashboard import ProgressEdge

W, H = 540, 112
RADIUS = 16
SHADOW = 12          # room around the strip for its shadow


class MiniPlayer(QWidget):
    def __init__(self, actions: dict[str, Callable[[], None]],
                 key_for: Callable[[str], str] = lambda _a: "",
                 on_moved: Callable[[list[int]], None] = lambda _p: None):
        super().__init__(None, _FLAGS)
        self.actions, self.key_for, self.on_moved = actions, key_for, on_moved
        _overlay(self)
        self.setFixedSize(W + 2 * SHADOW, H + 2 * SHADOW)
        self.reading = self.paused = self.auto = self.muted = False
        self.speaker = ""
        self._press: QPoint | None = None
        self._start = QPoint()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW + 16, SHADOW + 12, SHADOW + 16, SHADOW + 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.av = Avatar(NARRATOR, 32)
        top.addWidget(self.av)
        self.lbl_name = label("VN Reader", "strong")
        top.addWidget(self.lbl_name)
        self.mood = label("", "pill")
        self.mood.setProperty("tone", "accent")
        self.mood.hide()
        top.addWidget(self.mood, 0, Qt.AlignVCenter)
        top.addStretch(1)
        self.controls = QWidget()
        self.controls.setObjectName("transparent")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.buttons: dict = {}
        for name, ic, checkable in (("pause", "pause", False), ("replay", "rotate-ccw", False),
                                    ("stop", "x", False), ("auto", "arrow-right", True),
                                    ("toggle", "play", False), ("open", "app-window", False)):
            b = icon_button(ic, checkable=checkable, size=15)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, n=name: self._fire(n))
            cl.addWidget(b)
            self.buttons[name] = b
        self.controls.setVisible(False)
        top.addWidget(self.controls)
        lay.addLayout(top)
        self.text = QLabel("Start reading to see the lines here.")
        self.text.setObjectName("body")
        self.text.setTextFormat(Qt.RichText)
        self.text.setWordWrap(True)
        self.text.setMaximumHeight(44)
        self.text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.text, 1)
        self.progress = ProgressEdge(3)
        lay.addWidget(self.progress)
        lay.addSpacing(SHADOW // 2)
        self._top_timer = QTimer(self, interval=1500, timeout=self._stay_on_top)
        self.set_state_tips()

    # ------------------------------------------------------------ state
    def update_state(self, reading: bool, paused: bool, auto: bool, muted: bool,
                     speaker: str | None = None, ring: str | None = None):
        self.reading, self.paused, self.auto, self.muted = reading, paused, auto, muted
        if speaker is not None:
            self.speaker = speaker
            real = NARRATOR if speaker == "Narrator" else speaker
            self.av.set_name(real, 32, ring or "")
            self.lbl_name.setText(speaker or "VN Reader")
        b = self.buttons
        b["pause"].setIcon(theme.icon("play" if paused else "pause", "TEXT_2", 15,
                                      active="TEXT"))
        b["toggle"].setIcon(theme.icon("square" if reading else "play", "TEXT_2", 15,
                                       active="TEXT"))
        b["auto"].blockSignals(True)
        b["auto"].setChecked(auto)
        b["auto"].blockSignals(False)
        self.set_state_tips()

    def set_state_tips(self):
        def tip(text, action):
            k = self.key_for(action) if action else ""
            return f"{text}   {k}" if k else text
        b = self.buttons
        b["pause"].setToolTip(tip("Resume" if self.paused else "Pause", "pause"))
        b["replay"].setToolTip(tip("Replay last line", "replay"))
        b["stop"].setToolTip(tip("Stop speaking", "stop"))
        b["auto"].setToolTip(tip(f"Auto-advance: {'on' if self.auto else 'off'}", "advance"))
        b["toggle"].setToolTip(tip("Stop reading" if self.reading else "Start reading",
                                   "toggle"))
        b["open"].setToolTip("Open VN Reader")
        self.setToolTip("Drag to move · Right-click for more")

    def set_line(self, html: str, mood: str = ""):
        self.text.setText(html)
        self.mood.setText(mood)
        self.mood.setVisible(bool(mood))

    def set_progress(self, v: float):
        self.progress.set_value(v)

    def set_level(self, _level: float):
        """(The bubble pulses with the voice; the strip shows progress instead.)"""

    # ------------------------------------------------------------ show / place
    def place(self, pos: list[int] | None):
        if pos and len(pos) == 2 and QGuiApplication.screenAt(
                QPoint(pos[0] + self.width() // 2, pos[1] + self.height() // 2)):
            self.move(int(pos[0]), int(pos[1]))
        else:                               # bottom centre of the main screen
            ag = QGuiApplication.primaryScreen().availableGeometry()
            self.move(ag.center().x() - self.width() // 2, ag.bottom() - self.height() - 24)

    def set_shown(self, on: bool):
        if on:
            self.show()
            self._stay_on_top()
            self._top_timer.start()
        else:
            self._top_timer.stop()
            self.hide()

    def _stay_on_top(self):
        if self.isVisible():
            from .. import winapi
            winapi.keep_topmost(int(self.winId()))

    # ------------------------------------------------------------ mouse
    def enterEvent(self, e):
        self.controls.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.controls.setVisible(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._start = self.pos()
        elif e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._press is not None:
            self.move(self._start + e.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            moved = (e.globalPosition().toPoint() - self._press).manhattanLength()
            self._press = None
            if moved >= QApplication.startDragDistance():
                self.on_moved([self.x(), self.y()])

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._fire("open")

    def _fire(self, name: str):
        fn = self.actions.get(name)
        if fn:
            fn()

    def _menu(self, at: QPoint):
        m = QMenu(self)
        for text, name in (("Open VN Reader", "open"), ("Hide mini-player", "hide"), (None, None),
                           ("Quit VN Reader", "quit")):
            if text is None:
                m.addSeparator()
            else:
                m.addAction(text, lambda n=name: self._fire(n))
        m.exec(at)

    # ------------------------------------------------------------ paint
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(SHADOW, SHADOW, W, H)
        light = theme.MODE == "light"
        for grow, a in ((10, 10 if light else 26), (6, 16 if light else 40),
                        (3, 22 if light else 60)):                 # soft shadow
            path = QPainterPath()
            path.addRoundedRect(r.adjusted(-grow / 2, -grow / 3, grow / 2, grow), RADIUS + grow,
                                RADIUS + grow)
            p.fillPath(path, QColor(0, 0, 0, a))
        bg = QColor(theme.PANEL if light else theme.ELEVATED)
        bg.setAlphaF(0.94)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        p.fillPath(path, bg)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawPath(path)
