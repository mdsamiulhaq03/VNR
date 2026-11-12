"""Full-screen translucent overlay: drag a rectangle to pick a screen region."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionPicker(QWidget):
    # left, top, width, height in *physical* pixels (what mss expects)
    picked = Signal(list)
    cancelled = Signal()

    def __init__(self, hint: str = "Drag around the text box. Esc to cancel."):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.hint = hint
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        geo = QRect()
        for s in QGuiApplication.screens():
            geo = geo.united(s.geometry())
        self.setGeometry(geo)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.setPen(QColor(255, 255, 255))
        p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop, self.hint)
        if self._start and self._end:
            r = QRect(self._start, self._end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(80, 200, 255), 2))
            p.drawRect(r)

    def mousePressEvent(self, e):
        self._start = self._end = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self._start:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._start:
            return
        r = QRect(self._start, e.position().toPoint()).normalized()
        self.close()
        if r.width() < 8 or r.height() < 8:
            self.cancelled.emit()
            return
        g = r.translated(self.geometry().topLeft())   # logical global coords
        screen = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio()
        sg = screen.geometry()
        # Map logical -> physical relative to that screen's origin.
        left = int(sg.left() + (g.left() - sg.left()) * dpr)
        top = int(sg.top() + (g.top() - sg.top()) * dpr)
        self.picked.emit([left, top, int(g.width() * dpr), int(g.height() * dpr)])

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()
