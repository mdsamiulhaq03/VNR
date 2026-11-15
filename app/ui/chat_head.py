"""Floating control bubble: a round "chat head" that stays on top of the game.

Drag it anywhere (it snaps to the nearest screen edge), click it for the essential controls,
double-click to open VN Reader, right-click for more. Clicking it never takes focus away from
the game, and the OCR screen capture never sees it.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QMenu, QWidget

from .. import winapi
from . import theme
from .components import icon_button, vline

_FLAGS = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
          | Qt.WindowDoesNotAcceptFocus)


def _overlay(w: QWidget):
    """Frameless, top-most, translucent, never activated, hidden from screen capture."""
    w.setAttribute(Qt.WA_TranslucentBackground)
    w.setAttribute(Qt.WA_ShowWithoutActivating)
    w.setAttribute(Qt.WA_AlwaysShowToolTips)     # tooltips even though we're never "active"
    winapi.make_overlay(int(w.winId()))


class ControlPill(QWidget):
    """The row of controls that opens beside the bubble."""

    HIDE_MS = 6000

    def __init__(self, actions: dict[str, Callable[[], None]], key_for: Callable[[str], str]):
        super().__init__(None, _FLAGS)
        self.actions, self.key_for = actions, key_for
        _overlay(self)
        frame = QFrame(self)
        frame.setObjectName("chatPill")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        self.buttons: dict = {}
        spec = [("toggle", "play", False), ("pause", "pause", False),
                ("replay", "rotate-ccw", False), ("stop", "x", False), None,
                ("auto", "arrow-right", True), ("mute", "volume-2", True), None,
                ("open", "app-window", False)]
        for item in spec:
            if item is None:
                lay.addWidget(vline())
                continue
            name, ic, checkable = item
            b = icon_button(ic, checkable=checkable)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, n=name: self._fire(n))
            lay.addWidget(b)
            self.buttons[name] = b

        self._timer = QTimer(self, singleShot=True, interval=self.HIDE_MS, timeout=self.hide)
        self.set_state(False, False, False, False)
        self.adjustSize()

    def _fire(self, name: str):
        fn = self.actions.get(name)
        if fn:
            fn()
        if name == "open":
            self.hide()
        else:
            self._timer.start()

    def _tip(self, text: str, action: str) -> str:
        k = self.key_for(action)
        return f"{text}   {k}" if k else text

    @staticmethod
    def _icon(b, name: str):
        b.setIcon(theme.icon(name, "TEXT_2", 16, active="TEXT"))

    def set_state(self, reading: bool, paused: bool, auto: bool, muted: bool):
        b = self.buttons
        self._icon(b["toggle"], "square" if reading else "play")
        b["toggle"].setToolTip(self._tip("Stop reading" if reading else "Start reading",
                                         "toggle"))
        self._icon(b["pause"], "play" if paused else "pause")
        b["pause"].setToolTip(self._tip("Resume" if paused else "Pause", "pause"))
        b["replay"].setToolTip(self._tip("Replay last line", "replay"))
        b["stop"].setToolTip(self._tip("Stop speaking (skip this line)", "stop"))
        for name, on, tip in (("auto", auto, "Auto-advance"), ("mute", muted, "Mute")):
            b[name].blockSignals(True)
            b[name].setChecked(on)
            b[name].blockSignals(False)
            b[name].setToolTip(self._tip(f"{tip}: {'on' if on else 'off'}",
                                         "advance" if name == "auto" else ""))
        self._icon(b["mute"], "volume-x" if muted else "volume-2")
        b["open"].setToolTip("Open VN Reader")

    def popup_beside(self, g: QRect):
        """Open next to the bubble's window geometry, on the side facing the screen."""
        self.adjustSize()
        scr = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        ag = scr.availableGeometry()
        gap = 2
        if g.center().x() > ag.center().x():
            x = g.left() + ChatHead.PAD - self.width() - gap
        else:
            x = g.right() - ChatHead.PAD + gap
        y = g.center().y() - self.height() // 2
        x = max(ag.left(), min(x, ag.right() - self.width() + 1))
        y = max(ag.top(), min(y, ag.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        winapi.keep_topmost(int(self.winId()))
        self._timer.start()

    def enterEvent(self, e):
        self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._timer.start()
        super().leaveEvent(e)


class ChatHead(QWidget):
    """The bubble. `actions`: toggle, pause, replay, stop, auto, mute, open, hide, quit.
    `on_moved(pos)` is called with the new top-left [x, y] after a drag."""

    SIZE = 56          # circle diameter
    PAD = 8            # room around the circle for the speaking ring
    EDGE = 4           # gap between the window and the screen edge when snapped

    def __init__(self, actions: dict[str, Callable[[], None]], icon_path: str,
                 key_for: Callable[[str], str] = lambda _a: "",
                 on_moved: Callable[[list[int]], None] = lambda _p: None):
        super().__init__(None, _FLAGS)
        self.actions, self.on_moved = actions, on_moved
        _overlay(self)
        side = self.SIZE + 2 * self.PAD
        self.setFixedSize(side, side)
        self.setCursor(Qt.PointingHandCursor)
        self._logo = QPixmap(icon_path)
        self.reading = self.paused = self.auto = self.muted = False
        self.speaker = ""
        self.ring = theme.TEXT_2
        self._level = 0.0
        self._press: QPoint | None = None
        self._start = QPoint()
        self._dragging = False
        self._anim: QPropertyAnimation | None = None
        self.pill = ControlPill(actions, key_for)
        self._top_timer = QTimer(self, interval=1500, timeout=self._stay_on_top)
        self._update_tip()

    # ------------------------------------------------------------ state
    def update_state(self, reading: bool, paused: bool, auto: bool, muted: bool,
                     speaker: str | None = None, ring: str | None = None):
        self.reading, self.paused, self.auto, self.muted = reading, paused, auto, muted
        if speaker is not None:
            self.speaker = speaker
        if ring is not None:
            self.ring = ring or theme.TEXT_2
        self.pill.set_state(reading, paused, auto, muted)
        self._update_tip()
        self.update()

    def set_level(self, level: float):
        """Voice loudness (0..1, like Player.level). Drives the pulsing ring."""
        v = min(1.0, level * 6)
        v = max(v, self._level * 0.6)          # fall off smoothly between lines
        if abs(v - self._level) > 0.01 or (v == 0) != (self._level == 0):
            self._level = v if v > 0.02 else 0.0
            self.update()

    def _update_tip(self):
        state = "Paused" if self.paused else ("Reading" if self.reading else "Stopped")
        who = f" · {self.speaker}" if self.speaker else ""
        self.setToolTip(f"VN Reader · {state}{who}\nClick: controls · Drag: move · "
                        "Double-click: open · Right-click: more")

    # ------------------------------------------------------------ show / place
    def place(self, pos: list[int] | None):
        """Put the bubble at a saved top-left, or at the right edge a third of the way down
        the main screen. Always ends up fully on a screen, snapped to an edge."""
        if pos and len(pos) == 2 and QGuiApplication.screenAt(
                QPoint(pos[0] + self.width() // 2, pos[1] + self.height() // 2)):
            self.move(int(pos[0]), int(pos[1]))
        else:
            ag = QGuiApplication.primaryScreen().availableGeometry()
            self.move(ag.right() - self.width(), ag.top() + ag.height() // 3)
        self.snap(animate=False, notify=False)

    def set_shown(self, on: bool):
        if on:
            self.show()
            self._stay_on_top()
            self._top_timer.start()
        else:
            self._top_timer.stop()
            self.pill.hide()
            self.hide()

    def _stay_on_top(self):
        if self.isVisible():
            winapi.keep_topmost(int(self.winId()))
            if self.pill.isVisible():
                winapi.keep_topmost(int(self.pill.winId()))

    def snap_target(self) -> QPoint:
        """Where a release at the current position ends up: the nearest left/right edge of
        that screen, kept inside it vertically."""
        c = self.frameGeometry().center()
        scr = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        ag = scr.availableGeometry()
        if c.x() < ag.center().x():
            x = ag.left() + self.EDGE
        else:
            x = ag.right() - self.width() - self.EDGE + 1
        y = max(ag.top() + self.EDGE, min(self.y(), ag.bottom() - self.height() - self.EDGE + 1))
        return QPoint(x, y)

    def snap(self, animate: bool = True, notify: bool = True):
        target = self.snap_target()
        if self._anim:
            self._anim.stop()
        if animate and target != self.pos():
            self._anim = QPropertyAnimation(self, b"pos", self)
            self._anim.setDuration(180)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.move(target)
        if notify:
            self.on_moved([target.x(), target.y()])

    def toggle_pill(self):
        if self.pill.isVisible():
            self.pill.hide()
        else:
            self.pill.popup_beside(self.frameGeometry())

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._start = self.pos()
            self._dragging = False
        elif e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        d = e.globalPosition().toPoint() - self._press
        if not self._dragging and d.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self.pill.hide()
            if self._anim:
                self._anim.stop()
        if self._dragging:
            self.move(self._start + d)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.snap()
        else:
            self.toggle_pill()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pill.hide()
            self._fire("open")

    def _fire(self, name: str):
        fn = self.actions.get(name)
        if fn:
            fn()

    def _menu(self, at: QPoint):
        m = QMenu(self)
        for text, name in (("Open VN Reader", "open"), ("Hide bubble", "hide"), (None, None),
                           ("Quit VN Reader", "quit")):
            if text is None:
                m.addSeparator()
            else:
                m.addAction(QAction(text, m, triggered=lambda _c=False, n=name: self._fire(n)))
        self.pill.hide()
        m.exec(at)

    # ------------------------------------------------------------ paint
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        s, pad = self.SIZE, self.PAD
        circle = QRectF(pad, pad, s, s)

        # Speaking ring: grows and brightens with the voice.
        if self._level > 0:
            col = QColor(self.ring)
            col.setAlphaF(0.35 + 0.55 * self._level)
            w = 2.0 + 4.0 * self._level
            p.setPen(QPen(col, w))
            p.setBrush(Qt.NoBrush)
            grow = 1.5 + w / 2
            p.drawEllipse(circle.adjusted(-grow, -grow, grow, grow))

        # Soft shadow + body.
        light = theme.MODE == "light"
        p.setPen(Qt.NoPen)
        for grow, a in ((3.0, 10 if light else 30), (1.5, 22 if light else 60)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawEllipse(circle.adjusted(-grow, -grow + 2, grow, grow + 2))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.PANEL if light else theme.ELEVATED))
        p.drawEllipse(circle)

        # The app logo, clipped to the circle.
        if not self._logo.isNull():
            inner = circle.adjusted(9, 9, -9, -9)
            path = QPainterPath()
            path.addEllipse(circle.adjusted(1, 1, -1, -1))
            p.save()
            p.setClipPath(path)
            pm = self._logo.scaled(int(inner.width() * self.devicePixelRatioF()),
                                   int(inner.height() * self.devicePixelRatioF()),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            p.drawPixmap(inner.topLeft(), pm)
            p.restore()

        # Status dot: green reading, amber paused, grey stopped.
        dot = theme.WARN if (self.paused and self.reading) else (
            theme.OK if self.reading else theme.TEXT_3)
        d = 14
        r = QRectF(pad + s - d + 1, pad + s - d + 1, d, d)
        p.setPen(QPen(QColor(theme.BG), 2.5))
        p.setBrush(QColor(dot))
        p.drawEllipse(r)
