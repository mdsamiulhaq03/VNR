"""System tray icon: control reading without the window, and keep running when closed."""
from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class Tray:
    def __init__(self, win, icon_path: str):
        self.win = win
        self.icon = QSystemTrayIcon(QIcon(icon_path), win)
        m = QMenu()
        self.a_show = QAction("Show VN Reader", m, triggered=self.show_window)
        self.a_read = QAction("", m, triggered=win._toggle_ocr)
        self.a_pause = QAction("", m, triggered=lambda: (win._toggle_pause(),
                                                         self.update_state()))
        self.a_auto = QAction("Auto-advance", m, checkable=True,
                              triggered=lambda on: win.cb_auto.setChecked(on))
        self.a_areas = QAction("", m, triggered=win._pick_areas_sequence)
        self.a_bubble = QAction("Floating control bubble", m, checkable=True,
                                triggered=lambda on: win._set_chat_head(on))
        self.a_quit = QAction("Quit VN Reader", m, triggered=win.quit_app)
        for a in (self.a_show, None, self.a_read, self.a_pause, self.a_auto, self.a_areas,
                  None, self.a_bubble, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m.aboutToShow.connect(self.update_state)
        self.menu = m
        self.icon.setContextMenu(m)
        self.icon.activated.connect(self._activated)
        self.update_state()
        self.icon.show()

    @staticmethod
    def _key(win, action: str) -> str:
        k = win.cfg.settings.hotkeys.get(action, "")
        return f"\t{k}" if k else ""

    def update_state(self):
        w = self.win
        reading = w.ocr.running
        self.a_read.setText(("■  Stop reading" if reading else "▶  Start reading") +
                            self._key(w, "toggle"))
        self.a_pause.setText(("▶  Resume" if w.player.paused else "⏸  Pause") +
                             self._key(w, "pause"))
        self.a_auto.setChecked(w.cfg.settings.auto_advance)
        self.a_bubble.setChecked(w.cfg.settings.show_chat_head)
        self.a_areas.setText("⌨  Pick name + dialogue areas" + self._key(w, "areas"))
        self.icon.setToolTip(f"VN Reader · {'Reading' if reading else 'Stopped'} · {w.game}")

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.win.isVisible() and not self.win.isMinimized():
                self.win.hide()
            else:
                self.show_window()

    def show_window(self):
        self.win._show_window()

    def notify(self, msg: str, ms: int = 4000):
        if self.icon.isVisible() and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage("VN Reader", msg, QSystemTrayIcon.Information, ms)

    def hide(self):
        self.icon.hide()
