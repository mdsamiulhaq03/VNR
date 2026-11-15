"""Setup guide: four steps from nothing to hearing a voice.

1 Pick the game window, 2 mark where the name and dialogue appear, 3 check VoiceStudio
(and pick speed / quality), 4 hear a test line. Shown on first launch and from "Setup
guide" on the Read page. Every step can be skipped; nothing here is required.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import winapi
from . import theme
from .components import IconLabel, button, label

STEPS = [("Pick your game", "gamepad-2"), ("Mark the text", "crosshair"),
         ("Connect VoiceStudio", "audio-lines"), ("Hear it", "play")]


class _StepRow(QWidget):
    """One step in the left rail: number (or tick) and title."""

    def __init__(self, n: int, title: str):
        super().__init__()
        self.setObjectName("transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        self.num = QLabel(str(n))
        self.num.setObjectName("stepNum")
        self.num.setAlignment(Qt.AlignCenter)
        self.num.setFixedSize(24, 24)
        lay.addWidget(self.num)
        self.title = QLabel(title)
        lay.addWidget(self.title, 1)
        self.n = n

    def set_state(self, current: bool, done: bool):
        self.num.setText("✓" if done else str(self.n))
        self.num.setProperty("state", "current" if current else ("done" if done else ""))
        self.title.setObjectName("strong" if current else ("" if done else "muted"))
        for w in (self.num, self.title):
            w.style().unpolish(w)
            w.style().polish(w)


class SetupWizard(QDialog):
    def __init__(self, main):
        super().__init__(main, Qt.Window)
        self.main = main
        self.setWindowTitle("Set up VN Reader")
        self.setMinimumSize(760, 460)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("navPane")
        rail.setAttribute(Qt.WA_StyledBackground, True)
        rail.setFixedWidth(220)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(16, 22, 12, 16)
        rl.setSpacing(4)
        rl.addWidget(label("Setup", "title"))
        rl.addWidget(label("Four steps, about a minute.", "footnote"))
        rl.addSpacing(14)
        self.rows = [_StepRow(i + 1, t) for i, (t, _ic) in enumerate(STEPS)]
        for r in self.rows:
            rl.addWidget(r)
        rl.addStretch(1)
        root.addWidget(rail)

        right = QVBoxLayout()
        right.setContentsMargins(32, 28, 28, 20)
        right.setSpacing(12)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.b_skip = button("Skip setup", kind="ghost", slot=self._finish)
        bar.addWidget(self.b_skip)
        bar.addStretch(1)
        self.b_back = button("Back", slot=lambda: self.go(self.stack.currentIndex() - 1))
        self.b_next = button("Continue", kind="primary", slot=self._next)
        self.b_next.setDefault(True)
        bar.addWidget(self.b_back)
        bar.addWidget(self.b_next)
        right.addLayout(bar)
        root.addLayout(right, 1)

        self.stack.addWidget(self._step_game())
        self.stack.addWidget(self._step_areas())
        self.stack.addWidget(self._step_voices())
        self.stack.addWidget(self._step_test())
        self._timer = QTimer(self, interval=1000, timeout=self.refresh)
        self.go(0)

    # ------------------------------------------------------------ pages
    def _page(self, title: str, icon: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("transparent")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconLabel(icon, "ACCENT", 24))
        head.addWidget(label(title, "pageTitle"), 1)
        lay.addLayout(head)
        lay.addWidget(label(text, "muted", wrap=True))
        lay.addSpacing(8)
        return w, lay

    def _status(self) -> QLabel:
        s = label("", "", wrap=True)
        s.setTextFormat(Qt.RichText)
        return s

    def _step_game(self):
        w, lay = self._page("Pick your game", "gamepad-2",
                            "Start the game, then choose its window. VN Reader follows the "
                            "window when it moves, and keeps characters and voices per game.")
        row = QHBoxLayout()
        self.cb_win = QComboBox()
        self.cb_win.setMinimumContentsLength(30)
        self.cb_win.activated.connect(self._win_chosen)
        row.addWidget(self.cb_win, 1)
        row.addWidget(button("Refresh", "refresh-cw", slot=self._fill_windows))
        lay.addLayout(row)
        lay.addWidget(button("Or pick the game's folder…", "folder-open", "ghost",
                             slot=self.main._choose_game_folder), 0, Qt.AlignLeft)
        self.st_game = self._status()
        lay.addWidget(self.st_game)
        lay.addStretch(1)
        return w

    def _step_areas(self):
        w, lay = self._page("Mark the text", "crosshair",
                            "Show a line of dialogue in the game. Then draw a box around the "
                            "speaker's name (or press Enter to skip it), and one around the "
                            "dialogue.")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("Mark name and dialogue", "crosshair", "primary",
                             slot=self.main._pick_areas_sequence))
        row.addWidget(button("Find them for me", "scan-text",
                             tip="This window steps aside, then the game is read after 3 s",
                             slot=self.main._auto_detect_areas))
        row.addStretch(1)
        lay.addLayout(row)
        self.st_areas = self._status()
        lay.addWidget(self.st_areas)
        lay.addStretch(1)
        return w

    def _step_voices(self):
        w, lay = self._page("Connect VoiceStudio", "audio-lines",
                            "VoiceStudio makes the character voices on this PC. Without it "
                            "VN Reader uses the Windows voices.")
        self.st_vs = self._status()
        lay.addWidget(self.st_vs)
        self.b_start_vs = button("Start VoiceStudio", "play",
                                 slot=self.main._start_voicestudio)
        lay.addWidget(self.b_start_vs, 0, Qt.AlignLeft)
        lay.addSpacing(8)
        lay.addWidget(label("Speed and quality", "section"))
        from .settings_view import QUALITY
        self.cb_quality = QComboBox()
        for key, text in QUALITY:
            self.cb_quality.addItem(text, key)
        self.cb_quality.setCurrentIndex(max(0, self.cb_quality.findData(
            self.main.cfg.settings.vs_quality)))
        self.cb_quality.currentIndexChanged.connect(self._quality)
        lay.addWidget(self.cb_quality, 0, Qt.AlignLeft)
        lay.addWidget(label("You can change this later in Settings → Voice.", "footnote"))
        lay.addStretch(1)
        return w

    def _step_test(self):
        w, lay = self._page("Hear it", "play",
                            "Play a test line. If you hear it, you're ready: start reading "
                            "and play the game.")
        lay.addWidget(button("Play a test line", "play", "primary", slot=self._test),
                      0, Qt.AlignLeft)
        self.st_test = self._status()
        lay.addWidget(self.st_test)
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------ actions
    def _fill_windows(self):
        self.cb_win.clear()
        self.cb_win.addItem("Whole screen (no game window)", None)
        t = self.main.cfg.settings.target_window or {}
        sel = 0
        for win in sorted(winapi.list_windows(), key=lambda x: x.title.lower()):
            self.cb_win.addItem(win.label, win)
            if t and win.exe.lower() == (t.get("exe") or "").lower():
                sel = self.cb_win.count() - 1
        self.cb_win.setCurrentIndex(sel)

    def _win_chosen(self, _i):
        self.main._set_target_window(self.cb_win.currentData())
        self.refresh()

    def _quality(self):
        self.main.cfg.settings.vs_quality = self.cb_quality.currentData()
        self.main.cfg.save()
        self.main._vs_changed()

    def _test(self):
        from ..config import NARRATOR
        self.main._preview(NARRATOR)
        self._tested = True
        self.refresh()

    # ------------------------------------------------------------ flow
    def done_steps(self) -> list[bool]:
        s = self.main.cfg.settings
        return [bool(s.target_window), bool(self.main.ocr.spec.text),
                bool(getattr(self.main, "_vs_ok", False)), bool(getattr(self, "_tested", False))]

    def refresh(self):
        done = self.done_steps()
        cur = self.stack.currentIndex()
        for i, r in enumerate(self.rows):
            r.set_state(i == cur, done[i])
        ok = lambda t: f"<span style='color:{theme.OK}'>✓</span>&nbsp; {t}"      # noqa: E731
        todo = lambda t: f"<span style='color:{theme.TEXT_2}'>{t}</span>"       # noqa: E731
        t = self.main.cfg.settings.target_window or {}
        self.st_game.setText(ok(f"Following {t.get('title') or t.get('exe')}") if done[0]
                             else todo("No window picked yet: the whole screen is read."))
        self.st_areas.setText(ok("Dialogue area set") if done[1]
                              else todo("Not marked yet."))
        st = self.main.vs_starter
        self.st_vs.setText(ok("VoiceStudio is ready") if done[2] else
                           todo("Starting VoiceStudio…") if st.starting else
                           todo("VoiceStudio isn't running."))
        self.b_start_vs.setVisible(not done[2] and not st.starting)
        self.st_test.setText(ok("Played. Didn't hear it? Check the volume and the "
                                "VoiceStudio status at the bottom of the window.")
                             if done[3] else "")
        last = cur == len(STEPS) - 1
        self.b_next.setText("Start reading" if last else "Continue")
        self.b_back.setEnabled(cur > 0)

    def go(self, i: int):
        i = max(0, min(len(STEPS) - 1, i))
        self.stack.setCurrentIndex(i)
        if i == 0:
            self._fill_windows()
        self.refresh()

    def _next(self):
        i = self.stack.currentIndex()
        if i < len(STEPS) - 1:
            self.go(i + 1)
        else:
            self._finish()
            if not self.main.ocr.running:
                self.main._toggle_ocr()

    def _finish(self):
        self.main.cfg.settings.setup_done = True
        self.main.cfg.save()
        self.close()

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()
        self.refresh()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)
