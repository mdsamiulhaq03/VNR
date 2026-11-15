"""Reusable building blocks of the VN Reader design system."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            Signal)
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
    """A section heading (sentence case)."""
    t = text.strip()
    if t.isupper():                          # older callers passed "NOW SPEAKING"
        t = t.capitalize()
    return label(t[:1].upper() + t[1:], "section")


def _role(color: str) -> str:
    """Keep a colour's theme role so it follows theme changes (literal if it has none)."""
    return theme.role_of(color) or color


class IconLabel(QLabel):
    """A Lucide icon that follows the theme (unlike a fixed pixmap)."""

    def __init__(self, name: str, color: str = "TEXT", size: int = 16):
        super().__init__()
        self._name, self._color, self._size = name, _role(color), size
        self.setFixedSize(size, size)

    def set_icon(self, name: str, color: str | None = None):
        self._name = name
        if color is not None:
            self._color = _role(color)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(self.rect(), theme.pixmap(self._name, self._color, self._size))


class ColorDot(QLabel):
    """A small filled circle in a theme colour (e.g. a gender colour)."""

    def __init__(self, color: str = "TEXT_3", size: int = 8):
        super().__init__()
        self._color, self._size = _role(color), size
        self.setFixedSize(size, size)

    def set_color(self, color: str):
        self._color = _role(color)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.resolve(self._color)))
        p.drawEllipse(0, 0, self._size, self._size)


def set_tone(w: QWidget, tone: str):
    """Re-style a widget whose look depends on a [tone="..."] stylesheet rule."""
    w.setProperty("tone", tone)
    w.style().unpolish(w)
    w.style().polish(w)


class Banner(QFrame):
    """An inline message with an optional fix button (errors, warnings, tips)."""

    def __init__(self, text: str, tone: str = "", icon: str = "info", action: str = "",
                 slot=None):
        super().__init__()
        self.setObjectName("banner")
        self.setProperty("tone", tone)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 10, 9)
        lay.setSpacing(10)
        col = {"danger": "DANGER", "warn": "WARN", "ok": "OK"}.get(tone, "TEXT_2")
        self.ic = IconLabel(icon, col, 16)
        lay.addWidget(self.ic, 0, Qt.AlignTop)
        self.text = label(text, wrap=True)
        lay.addWidget(self.text, 1)
        self.btn = button(action, kind="ghost", slot=slot) if action else None
        if self.btn:
            lay.addWidget(self.btn)

    def set_text(self, text: str):
        self.text.setText(text)


def kbd(text: str) -> QLabel:
    return label(text, "kbd")


def button(text: str = "", icon: str = "", kind: str = "", tip: str = "",
           slot=None, checkable: bool = False) -> QPushButton:
    """kind: '' (secondary), 'primary', 'ghost', 'danger', 'icon'."""
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon:
        col = {"primary": "ON_ACCENT", "danger": "DANGER", "ghost": "ACCENT"}.get(kind, "TEXT")
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
    b.setIcon(theme.icon(icon, "TEXT_2", size, active="TEXT"))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)            # a click doesn't leave a focus ring behind
    b.setFixedSize(size + 14, size + 14)
    if slot:
        b.clicked.connect(slot)
    return b


def set_button_icon(b: QPushButton, icon: str, primary: bool = False):
    b.setIcon(theme.icon(icon, "ON_ACCENT" if primary else "TEXT", 16))


class SegmentedControl(QWidget):
    """A row of mutually exclusive options (like a desktop segmented control)."""
    changed = Signal(object)

    def __init__(self, options: list[tuple[object, str]], value=None, icons: dict | None = None):
        super().__init__()
        self.setObjectName("segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)   # as wide as its options
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
                b.setIcon(theme.icon(icons[key], "TEXT_2", 14, active="TEXT"))
                if not text:
                    b.setFixedHeight(26)
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
        self.addAction(theme.icon("search", "TEXT_2", 14), QLineEdit.LeadingPosition)
        self.setClearButtonEnabled(True)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, hint: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)
        lay.setSpacing(8)
        ic = IconLabel(icon, "TEXT_3", 34)
        t = label(title, "headline")
        t.setAlignment(Qt.AlignCenter)
        h = label(hint, "muted", wrap=True)
        h.setAlignment(Qt.AlignCenter)
        h.setMaximumWidth(340)
        lay.addWidget(ic, 0, Qt.AlignHCenter)
        lay.addSpacing(4)
        for w in (t, h):
            lay.addWidget(w, 0, Qt.AlignHCenter)
        if not hint:
            h.hide()
        self.action = None

    def set_action(self, text: str, slot) -> QPushButton:
        """One clear next step under the hint."""
        self.action = button(text, kind="primary", slot=slot)
        self.layout().addSpacing(6)
        self.layout().addWidget(self.action, 0, Qt.AlignHCenter)
        return self.action


class Avatar(QLabel):
    """Initials avatar. `ring` (a character's colour) draws a thin ring around it."""

    def __init__(self, name: str, size: int = 32):
        super().__init__()
        self.setFixedSize(size, size)
        self._name, self._ring, self._size = name, "", size
        self.set_name(name, size)

    def set_name(self, name: str, size: int | None = None, ring: str = ""):
        self._name = name
        self._size = size or self._size
        self._ring = _role(ring) if ring else ""
        self.setFixedSize(self._size, self._size)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        ring = QColor(theme.resolve(self._ring)) if self._ring else None
        w = max(1.5, s / 18) if ring else 0
        if ring:
            p.setPen(QPen(ring, w))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(w / 2, w / 2, s - w, s - w))
        gap = w + (1.5 if ring else 0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.avatar_color(self._name)))
        p.drawEllipse(QRectF(gap, gap, s - 2 * gap, s - 2 * gap))
        initial = "N" if self._name == NARRATOR else (self._name.strip()[:1].upper() or "?")
        f = QFont(theme.SANS)
        f.setPixelSize(max(9, int(s * 0.38)))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_2 if self._name == NARRATOR else theme.TEXT))
        p.drawText(self.rect(), Qt.AlignCenter, initial)


class Dot(QLabel):
    """A small status dot (optionally pulsing)."""

    def __init__(self, color: str = "TEXT_3", size: int = 8):
        super().__init__()
        self._color, self._size = _role(color), size
        self.setFixedSize(size + 4, size + 4)
        self._a = 1.0
        self._t = QTimer(self, interval=60, timeout=self._tick)
        self._phase = 0

    def set_color(self, c: str, pulse: bool = False):
        self._color = _role(c)
        pulse = pulse and not theme.reduced_motion()
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
        c = QColor(theme.resolve(self._color))
        c.setAlphaF(self._a)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(2, 2, self._size, self._size)


def fade_in(widget: QWidget, ms: int = 180):
    ms = theme.motion(ms)
    if not ms:
        return
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
def float_shadow(w: QWidget, blur: int = 28, y: int = 8):
    """The soft shadow under floating things (sheets, popovers, toasts)."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, y)
    c = QColor(0, 0, 0)
    c.setAlphaF(0.16 if theme.MODE == "light" else 0.55)
    fx.setColor(c)
    w.setGraphicsEffect(fx)
    return fx


def _blurred(pm, radius: float = 18.0):
    """A blurred copy of a window snapshot (done small: fast, and softer)."""
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene)
    small = pm.scaled(max(1, pm.width() // 4), max(1, pm.height() // 4),
                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    fx = QGraphicsBlurEffect()
    fx.setBlurRadius(radius / 4)
    fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    img = QImage(small.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    scene.render(p, QRectF(img.rect()), QRectF(small.rect()))
    p.end()
    return QPixmap.fromImage(img).scaled(pm.size(), Qt.IgnoreAspectRatio,
                                         Qt.SmoothTransformation)


class Dialog(QDialog):
    """A modal sheet: the window behind it is blurred and dimmed, the card fades in.
    Esc cancels, Enter presses the default (primary) button. Without a visible parent
    window it is an ordinary themed dialog."""

    def __init__(self, parent, title: str, body: str = "", icon: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        host = parent.window() if parent is not None else None
        self._sheet = bool(host is not None and host.isVisible() and host.width() > 300
                           and not host.isMinimized())
        self._backdrop = None
        if self._sheet:
            self.setObjectName("sheet")
            self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            snap = host.grab()
            self._backdrop = _blurred(snap)
            g = host.geometry()
            self.setGeometry(g)
            outer = QVBoxLayout(self)
            outer.setContentsMargins(24, 24, 24, 24)
            outer.addStretch(1)
            card = QFrame()
            card.setObjectName("sheetCard")
            card.setMinimumWidth(440)
            card.setMaximumWidth(560)
            float_shadow(card, 40, 14)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(card)
            row.addStretch(1)
            outer.addLayout(row)
            outer.addStretch(1)
            lay = QVBoxLayout(card)
        else:
            self.setMinimumWidth(440)
            lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        if icon:
            head.addWidget(IconLabel(icon, "ACCENT" if icon not in ("trash-2",) else "DANGER",
                                     20), 0, Qt.AlignTop)
        t = label(title, "title")
        t.setWordWrap(True)
        head.addWidget(t, 1)
        lay.addLayout(head)
        self.body = label(body, "muted", wrap=True)
        self.body.setTextFormat(Qt.PlainText)
        lay.addWidget(self.body)
        if not body:
            self.body.hide()
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        lay.addLayout(self.content)
        lay.addSpacing(8)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.addStretch(1)
        lay.addLayout(self.buttons)

    def paintEvent(self, e):
        if not self._sheet or self._backdrop is None:
            return super().paintEvent(e)
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._backdrop)
        p.fillRect(self.rect(), QColor(0, 0, 0, 46 if theme.MODE == "light" else 115))

    def showEvent(self, e):
        super().showEvent(e)
        ms = theme.motion(200)
        if ms:
            self.setWindowOpacity(0.0)
            a = QPropertyAnimation(self, b"windowOpacity", self)
            a.setDuration(ms)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.start(QPropertyAnimation.DeleteWhenStopped)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)

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
