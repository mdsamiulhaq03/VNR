"""Full-screen overlay: drag a rectangle to pick a screen region.

Shows a banner (optional "Step 1 of 2" chip, title and hint), draws the box in the
step's colour with a live size readout, and keeps earlier boxes visible (e.g. the name
area while the dialogue area is drawn).

Keys: Esc = cancel · Enter/Space = skip (only when skippable) · right-click or
Backspace = start this box again.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


def _sans() -> str:
    from . import theme
    return theme.SANS


def _mono() -> str:
    from . import theme
    return theme.MONO


def physical_to_logical(rect: list[int]) -> QRect:
    """[l, t, w, h] physical px -> logical Qt coords (per screen DPI)."""
    l, t, w, h = rect
    for s in QGuiApplication.screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        pl, pt = g.left(), g.top()
        if pl <= l < pl + g.width() * dpr and pt <= t < pt + g.height() * dpr:
            return QRect(int(pl + (l - pl) / dpr), int(pt + (t - pt) / dpr),
                         int(w / dpr), int(h / dpr))
    return QRect(l, t, w, h)


class RegionPicker(QWidget):
    # left, top, width, height in *physical* pixels (what mss expects)
    picked = Signal(list)
    cancelled = Signal()
    skipped = Signal()

    def __init__(self, hint: str = "Drag around the text box.", title: str = "",
                 color: str = "#7c9cff", step: int = 0, total: int = 0,
                 skippable: bool = False, keep: list[tuple[list[int], str, str]] | None = None):
        """keep: earlier boxes to show, as (physical rect, label, colour)."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.hint, self.title = hint, title
        self.color = QColor(color)
        self.step, self.total = step, total
        self.skippable = skippable
        self.keep = keep or []
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        geo = QRect()
        for s in QGuiApplication.screens():
            geo = geo.united(s.geometry())
        self.setGeometry(geo)

    def showEvent(self, e):
        super().showEvent(e)
        self.activateWindow()
        self.setFocus()

    # ---- drawing ----
    def _banner(self, p: QPainter):
        scr = QGuiApplication.primaryScreen().geometry().translated(
            -self.geometry().topLeft()).intersected(self.rect())
        if scr.isEmpty():
            scr = self.rect()
        w = min(760, scr.width() - 40)
        box = QRectF(scr.center().x() - w / 2, scr.top() + 28, w, 112)
        p.setPen(QPen(QColor(38, 38, 38), 1))
        p.setBrush(QColor(10, 10, 10, 245))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(box.left() + 14, box.top() + 20, 3, box.height() - 40), 1.5, 1.5)
        x = box.left() + 30
        if self.total:
            chip = QRectF(x, box.top() + 18, 104, 26)
            p.setBrush(self.color)
            p.drawRoundedRect(chip, 13, 13)
            p.setPen(QColor("#0d1020"))
            p.setFont(QFont(_mono(), 8, QFont.Medium))
            p.drawText(chip, Qt.AlignCenter, f"STEP {self.step} / {self.total}")
            tx = chip.right() + 14
        else:
            tx = x
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont(_sans(), 14, QFont.DemiBold))
        p.drawText(QRectF(tx, box.top() + 12, box.right() - tx - 16, 38),
                   Qt.AlignLeft | Qt.AlignVCenter, self.title or "Pick an area")
        p.setPen(QColor("#EDEDED"))
        p.setFont(QFont(_sans(), 10))
        keys = ["Esc cancel", "Right-click redo"]
        if self.skippable:
            keys.insert(0, "Enter skip")
        p.drawText(QRectF(x, box.top() + 52, box.width() - 52, 26),
                   Qt.AlignLeft | Qt.AlignVCenter, self.hint)
        p.setPen(QColor("#888888"))
        p.setFont(QFont(_mono(), 8.5))
        p.drawText(QRectF(x, box.top() + 78, box.width() - 52, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, "   ·   ".join(keys))

    def _box(self, p: QPainter, r: QRect, color: QColor, label: str, clear: bool):
        if clear:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        fill = QColor(color)
        fill.setAlpha(28)
        p.fillRect(r, fill)
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        if label:
            p.setFont(QFont(_mono(), 8, QFont.Medium))
            tw = p.fontMetrics().horizontalAdvance(label) + 16
            tag = QRectF(r.left(), r.top() - 24, tw, 20)
            if tag.top() < 0:
                tag.moveTop(r.bottom() + 4)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(tag, 6, 6)
            p.setPen(QColor("#0d1020"))
            p.drawText(tag, Qt.AlignCenter, label)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))
        origin = self.geometry().topLeft()
        for rect, label, col in self.keep:
            self._box(p, physical_to_logical(rect).translated(-origin), QColor(col), label, True)
        if self._start and self._end:
            r = QRect(self._start, self._end).normalized()
            dpr = (QGuiApplication.screenAt(r.center() + origin) or
                   QGuiApplication.primaryScreen()).devicePixelRatio()
            self._box(p, r, self.color, f"{int(r.width() * dpr)} × {int(r.height() * dpr)}",
                      True)
        self._banner(p)

    # ---- input ----
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._restart()
            return
        self._start = self._end = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self._start:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self._start:
            return
        r = QRect(self._start, e.position().toPoint()).normalized()
        if r.width() < 8 or r.height() < 8:          # a click, not a box: try again
            self._restart()
            return
        self.close()
        g = r.translated(self.geometry().topLeft())   # logical global coords
        screen = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio()
        sg = screen.geometry()
        # Map logical -> physical relative to that screen's origin.
        left = int(sg.left() + (g.left() - sg.left()) * dpr)
        top = int(sg.top() + (g.top() - sg.top()) * dpr)
        self.picked.emit([left, top, int(g.width() * dpr), int(g.height() * dpr)])

    def _restart(self):
        self._start = self._end = None
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.skippable:
            self.close()
            self.skipped.emit()
        elif k == Qt.Key_Backspace:
            self._restart()
