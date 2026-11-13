"""Reusable building blocks of the VN Reader design system."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from ..config import NARRATOR


# ------------------------------------------------------------------ basics
def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    return f


def vline() -> QFrame:
    f = QFrame()
    f.setObjectName("vline")
    return f


def label(text: str = "", kind: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if kind:
        lb.setObjectName(kind)
    lb.setWordWrap(wrap)
    return lb


def section(text: str) -> QLabel:
    """Small uppercase mono section header."""
    return label(text.upper(), "section")


def kbd(text: str) -> QLabel:
    return label(text, "kbd")


def button(text: str = "", icon: str = "", kind: str = "", tip: str = "",
           slot=None, checkable: bool = False) -> QPushButton:
    """kind: '' (secondary), 'primary', 'ghost', 'danger', 'icon'."""
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon:
        col = theme.BG if kind == "primary" else (theme.DANGER if kind == "danger" else theme.TEXT)
        b.setIcon(theme.icon(icon, col, 16))
        b.setIconSize(QSize(15, 15))
    if tip:
        b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def icon_button(icon: str, tip: str = "", slot=None, checkable: bool = False,
                size: int = 16) -> QPushButton:
    b = QPushButton()
    b.setObjectName("icon")
    b.setIcon(theme.icon(icon, theme.TEXT_2, size, active=theme.TEXT))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(size + 14, size + 14)
    if slot:
        b.clicked.connect(slot)
    return b


def set_button_icon(b: QPushButton, icon: str, primary: bool = False):
    col = theme.BG if primary else theme.TEXT
    b.setIcon(theme.icon(icon, col, 16))


class SegmentedControl(QWidget):
    """A row of mutually exclusive options (like a desktop segmented control)."""
    changed = Signal(object)

    def __init__(self, options: list[tuple[object, str]], value=None, icons: dict | None = None):
        super().__init__()
        self.setObjectName("segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._btns: dict = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            if icons and key in icons:
                b.setIcon(theme.icon(icons[key], theme.TEXT_2, 14, active=theme.TEXT))
            b.clicked.connect(lambda _=False, k=key: self.set_value(k, emit=True))
            lay.addWidget(b)
            self._btns[key] = b
        self._value = None
        self.set_value(value if value is not None else options[0][0])

    def value(self):
        return self._value

    def set_value(self, key, emit: bool = False):
        self._value = key
        for k, b in self._btns.items():
            b.setChecked(k == key)
        if emit:
            self.changed.emit(key)


class SearchField(QLineEdit):
    def __init__(self, placeholder: str = "Search"):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.addAction(theme.icon("search", theme.TEXT_3, 14), QLineEdit.LeadingPosition)
        self.setClearButtonEnabled(True)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, hint: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(theme.pixmap(icon, theme.TEXT_3, 28))
        ic.setAlignment(Qt.AlignCenter)
        t = label(title)
        t.setAlignment(Qt.AlignCenter)
        h = label(hint, "muted", wrap=True)
        h.setAlignment(Qt.AlignCenter)
        h.setMaximumWidth(360)
        for w in (ic, t, h):
            lay.addWidget(w, 0, Qt.AlignHCenter)
        if not hint:
            h.hide()


class Avatar(QLabel):
    """Monochrome initials avatar with an optional small gender dot."""

    def __init__(self, name: str, size: int = 32):
        super().__init__()
        self.setFixedSize(size, size)
        self._name, self._ring, self._size = name, "", size
        self.set_name(name, size)

    def set_name(self, name: str, size: int | None = None, ring: str = ""):
        self._name = name
        self._size = size or self._size
        self._ring = ring
        self.setFixedSize(self._size, self._size)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        p.setPen(QPen(QColor(theme.BORDER_HOVER), 1))
        p.setBrush(QColor(theme.ELEVATED))
        p.drawEllipse(1, 1, s - 2, s - 2)
        initial = "N" if self._name == NARRATOR else (self._name.strip()[:1].upper() or "?")
        f = QFont(theme.SANS)
        f.setPixelSize(max(9, int(s * 0.4)))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(self.rect(), Qt.AlignCenter, initial)
        if self._ring:
            d = max(7, s // 4)
            p.setPen(QPen(QColor(theme.BG), 2))
            p.setBrush(QColor(self._ring))
            p.drawEllipse(s - d - 1, s - d - 1, d, d)


class Dot(QLabel):
    """A small status dot (optionally pulsing)."""

    def __init__(self, color: str = theme.TEXT_3, size: int = 8):
        super().__init__()
        self._color, self._size = color, size
        self.setFixedSize(size + 4, size + 4)
        self._a = 1.0
        self._t = QTimer(self, interval=60, timeout=self._tick)
        self._phase = 0

    def set_color(self, c: str, pulse: bool = False):
        self._color = c
        if pulse and not self._t.isActive():
            self._t.start()
        elif not pulse:
            self._t.stop()
            self._a = 1.0
        self.update()

    def _tick(self):
        import math
        self._phase = (self._phase + 1) % 30
        self._a = 0.45 + 0.55 * (0.5 + 0.5 * math.cos(self._phase / 30 * 2 * math.pi))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        c.setAlphaF(self._a)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(2, 2, self._size, self._size)


def fade_in(widget: QWidget, ms: int = 180):
    fx = widget.graphicsEffect()
    if not isinstance(fx, QGraphicsOpacityEffect):
        fx = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(fx)
    a = QPropertyAnimation(fx, b"opacity", widget)
    a.setDuration(ms)
    a.setStartValue(0.2)
    a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.start(QPropertyAnimation.DeleteWhenStopped)


# ----------------------------------------------------------------- dialogs
class Dialog(QDialog):
    """Themed modal dialog: title, body, and a right-aligned button row."""

    def __init__(self, parent, title: str, body: str = "", icon: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(10)
        if icon:
            ic = QLabel()
            ic.setPixmap(theme.pixmap(icon, theme.TEXT, 18))
            head.addWidget(ic, 0, Qt.AlignTop)
        t = label(title, "pageTitle")
        head.addWidget(t, 1)
        lay.addLayout(head)
        self.body = label(body, "muted", wrap=True)
        self.body.setTextFormat(Qt.PlainText)
        lay.addWidget(self.body)
        if not body:
            self.body.hide()
        self.content = QVBoxLayout()
        lay.addLayout(self.content)
        lay.addSpacing(4)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)
        lay.addLayout(self.buttons)

    def add_button(self, text: str, kind: str = "", accept: bool = True) -> QPushButton:
        b = button(text, kind=kind)
        b.clicked.connect(self.accept if accept else self.reject)
        self.buttons.addWidget(b)
        return b


def confirm(parent, title: str, body: str, ok: str = "OK", destructive: bool = False,
            icon: str = "") -> bool:
    d = Dialog(parent, title, body, icon or ("trash-2" if destructive else "circle-help"))
    d.add_button("Cancel", accept=False)
    b = d.add_button(ok, "danger" if destructive else "primary")
    b.setDefault(True)
    return d.exec() == QDialog.Accepted


def prompt(parent, title: str, body: str, text: str = "", ok: str = "Save",
           placeholder: str = "") -> tuple[str, bool]:
    d = Dialog(parent, title, body, "square-pen")
    ed = QLineEdit(text)
    ed.setPlaceholderText(placeholder)
    ed.selectAll()
    d.content.addWidget(ed)
    d.add_button("Cancel", accept=False)
    b = d.add_button(ok, "primary")
    b.setDefault(True)
    ok_ = d.exec() == QDialog.Accepted
    return ed.text(), ok_


def info(parent, title: str, body: str, icon: str = "info"):
    d = Dialog(parent, title, body, icon)
    d.add_button("OK", "primary").setDefault(True)
    d.exec()
