"""Application shell: sidebar navigation, page toolbars, page stack and status bar."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QSize, Qt, Signal)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .components import Dot, IconLabel, icon_button, label

SIDEBAR_W, SIDEBAR_MINI = 208, 56


class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, shortcut: str):
        super().__init__(text)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._icon, self._text, self._sc = icon, text, shortcut
        self.setIconSize(QSize(16, 16))
        self.setToolTip(f"{text}   {shortcut}")
        self._update_icon()
        self.toggled.connect(lambda _on: self._update_icon())

    def _update_icon(self):
        col = "ACCENT" if self.isChecked() else "TEXT_2"
        self.setIcon(theme.icon(self._icon, col, 16, active="ACCENT" if self.isChecked()
                                else "TEXT"))

    def set_compact(self, on: bool):
        self.setText("" if on else self._text)


class Sidebar(QWidget):
    page_requested = Signal(int)
    collapse_toggled = Signal(bool)

    def __init__(self, logo_path: str):
        super().__init__()
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(SIDEBAR_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 8)
        self.logo = QLabel()
        pm = QPixmap(logo_path)
        if not pm.isNull():
            self.logo.setPixmap(pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top.addWidget(self.logo)
        self.brand = label("VN Reader", "brand")
        top.addWidget(self.brand, 1)
        self.b_collapse = icon_button("panel-left", "Collapse sidebar   Ctrl+B",
                                      self.toggle, size=15)
        top.addWidget(self.b_collapse)
        lay.addLayout(top)
        self.items: list[NavButton] = []
        self.nav = QVBoxLayout()
        self.nav.setSpacing(2)
        lay.addLayout(self.nav)
        lay.addStretch(1)
        self.bottom = QVBoxLayout()
        self.bottom.setSpacing(2)
        lay.addLayout(self.bottom)
        self.compact = False
        self._anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def add(self, icon: str, text: str, shortcut: str, bottom: bool = False) -> int:
        b = NavButton(icon, text, shortcut)
        i = len(self.items)
        b.clicked.connect(lambda _=False, i=i: self.page_requested.emit(i))
        self.items.append(b)
        (self.bottom if bottom else self.nav).addWidget(b)
        return i

    def select(self, i: int):
        for k, b in enumerate(self.items):
            b.setChecked(k == i)

    def toggle(self):
        self.set_compact(not self.compact, animate=True)
        self.collapse_toggled.emit(self.compact)

    def set_compact(self, on: bool, animate: bool = False):
        self.compact = on
        w = SIDEBAR_MINI if on else SIDEBAR_W
        self.setMinimumWidth(0)
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self.width())
            self._anim.setEndValue(w)
            self._anim.finished.connect(lambda w=w: self.setFixedWidth(w))
            self._anim.start()
        else:
            self.setFixedWidth(w)
        self.brand.setVisible(not on)
        self.logo.setVisible(not on)
        for b in self.items:
            b.set_compact(on)
        self.b_collapse.setIcon(theme.icon("panel-left", "TEXT_2", 15, active="TEXT"))
        self.b_collapse.setToolTip(("Expand" if on else "Collapse") + " sidebar   Ctrl+B")
        for extra in getattr(self, "compact_hide", []):
            extra.setVisible(not on)
        for extra in getattr(self, "compact_show", []):
            extra.setVisible(on)


class PageHeader(QWidget):
    """Top toolbar of a page: title on the left, contextual actions on the right."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("toolbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(52)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(8)
        self.title = label(title, "pageTitle")
        lay.addWidget(self.title)
        self.subtitle = label(subtitle, "mono")
        lay.addWidget(self.subtitle)
        if not subtitle:
            self.subtitle.hide()
        lay.addStretch(1)
        self.actions = lay

    def add(self, *widgets):
        for w in widgets:
            if w is None:
                self.actions.addSpacing(6)
            else:
                self.actions.addWidget(w)

    def set_subtitle(self, text: str):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))


class Page(QWidget):
    """A page = header + body."""

    def __init__(self, title: str, body: QWidget | None = None, subtitle: str = ""):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PageHeader(title, subtitle)
        lay.addWidget(self.header)
        self.body_layout = lay
        if body is not None:
            lay.addWidget(body, 1)

    def set_body(self, body: QWidget):
        self.body_layout.addWidget(body, 1)


class FadeStack(QStackedWidget):
    """Page stack with a short fade when switching (180 ms; none with reduced motion)."""

    def set_page(self, i: int):
        if i == self.currentIndex():
            return
        self.setCurrentIndex(i)
        ms = theme.motion(180)
        if not ms:
            return
        w = self.currentWidget()
        fx = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(fx)
        a = QPropertyAnimation(fx, b"opacity", w)
        a.setDuration(ms)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.finished.connect(lambda w=w: w.setGraphicsEffect(None))
        a.start(QPropertyAnimation.DeleteWhenStopped)


class TabsAdapter:
    """Keeps the old QTabWidget-style calls working (setCurrentWidget, currentWidget,
    currentChanged, setCurrentIndex, indexOf) on top of the sidebar + page stack."""

    def __init__(self, stack: FadeStack, sidebar: Sidebar, pages: list[QWidget],
                 content_of: dict):
        self.stack, self.sidebar, self.pages = stack, sidebar, pages
        self._content_of = content_of       # inner widget -> page index
        self.currentChanged = stack.currentChanged

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self.pages):
            self.stack.set_page(i)
            self.sidebar.select(i)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def indexOf(self, w) -> int:
        if w in self.pages:
            return self.pages.index(w)
        return self._content_of.get(id(w), -1)

    def setCurrentWidget(self, w):
        i = self.indexOf(w)
        if i >= 0:
            self.setCurrentIndex(i)

    def currentWidget(self):
        """The page's main content widget (e.g. the Screenshot view), like QTabWidget."""
        i = self.stack.currentIndex()
        for wid, idx in self._content_of.items():
            if idx == i:
                return self._widgets[wid]
        return self.pages[i] if 0 <= i < len(self.pages) else None

    _widgets: dict = {}

    def register(self, content, index: int):
        self._content_of[id(content)] = index
        self._widgets[id(content)] = content

    def count(self) -> int:
        return len(self.pages)


class StatusSegment(QWidget):
    """A piece of the status bar: optional dot or icon + text. Emits clicked when it
    has a click action (set_clickable)."""
    clicked = Signal(object)            # global position

    def __init__(self, icon: str = "", text: str = "", dot: bool = False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)
        self.dot = Dot() if dot else None
        if self.dot:
            lay.addWidget(self.dot)
        if icon:
            self.ic = IconLabel(icon, "TEXT_2", 12)
            lay.addWidget(self.ic)
        self.text = QLabel(text)
        lay.addWidget(self.text)
        self._clickable = False

    def set(self, text: str, color: str | None = None, pulse: bool = False, tip: str = ""):
        self.text.setText(text)
        if self.dot is not None and color:
            self.dot.set_color(color, pulse)
        self.setToolTip(tip)

    def set_clickable(self, on: bool = True):
        self._clickable = on
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if self._clickable and e.button() == Qt.LeftButton:
            self.clicked.emit(self.mapToGlobal(self.rect().topLeft()))
            return
        super().mousePressEvent(e)
