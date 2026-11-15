"""Games tab: every game you've played with VN Reader. A list on the left; the
selected game's page on the right (play time, story progress, characters, sessions,
records, milestones, notes)."""
from __future__ import annotations

import csv
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
                               QFileIconProvider, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QScrollArea, QTableWidget, QTableWidgetItem,
                               QToolTip, QVBoxLayout, QWidget)

from ..config import NARRATOR
from ..game_scripts import renpy_hook
from ..game_scripts.detect import ENGINE_LABEL
from ..playtime import PlayLog, fmt_duration, milestones
from . import theme
from .components import EmptyState, button, label, section

SOURCE_LABEL = {"auto": "Automatic", "hook": "Game hook (live)", "files": "Script + OCR",
                "ocr": "OCR"}
WORDS_PER_MIN = 150           # speech rate, for "≈ hours of voice"


def _day(ts: float) -> str:
    return time.strftime("%d %b %Y", time.localtime(ts)) if ts else "–"


# ------------------------------------------------------------------ widgets
class DayBars(QWidget):
    """Play time per day as bars; hover a bar for its minutes."""

    def __init__(self):
        super().__init__()
        self.days: list[tuple[str, float]] = []
        self.setMinimumHeight(110)
        self.setMouseTracking(True)

    def set_days(self, days: list[tuple[str, float]]):
        self.days = days
        self.update()

    def _bar_rects(self):
        n = max(1, len(self.days))
        w, h = self.width(), self.height() - 18
        gap = 6
        bw = max(4.0, (w - gap * (n - 1)) / n)
        top = max((s for _, s in self.days), default=0) or 1
        for i, (_d, s) in enumerate(self.days):
            bh = max(2.0, (h - 4) * s / top) if s > 0 else 2.0
            yield i, QRectF(i * (bw + gap), h - bh, bw, bh)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        today = date.today().isoformat()
        for i, r in self._bar_rects():
            d, s = self.days[i]
            col = QColor(theme.ACCENT if d == today else theme.TEXT_3)
            if s <= 0:
                col = QColor(theme.BORDER)
            p.setBrush(col)
            p.drawRoundedRect(r, 3, 3)
        p.setPen(QColor(theme.TEXT_2))
        f = QFont(theme.SANS)
        f.setPointSizeF(8)
        p.setFont(f)
        if self.days:
            p.drawText(0, self.height() - 2, datetime.fromisoformat(self.days[0][0])
                       .strftime("%d %b"))
            last = "Today" if self.days[-1][0] == today else self.days[-1][0]
            p.drawText(QRectF(0, self.height() - 14, self.width(), 14),
                       Qt.AlignRight | Qt.AlignBottom, last)

    def mouseMoveEvent(self, e):
        for i, r in self._bar_rects():
            if r.left() <= e.position().x() <= r.right():
                d, s = self.days[i]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{datetime.fromisoformat(d).strftime('%a %d %b')}: "
                                  f"{fmt_duration(s, short=True) if s else 'not played'}", self)
                return
        QToolTip.hideText()


def _tile(value: str, caption: str) -> QWidget:
    w = QFrame()
    w.setObjectName("card")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(2)
    lay.addWidget(label(caption[:1].upper() + caption[1:], "footnote"))
    lay.addWidget(label(value, "stat"))
    return w


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    h = t.horizontalHeader()
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    h.setHighlightSections(False)
    t.verticalHeader().setDefaultSectionSize(32)
    for i in range(len(headers)):
        h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    h.setSectionResizeMode(0, QHeaderView.Stretch)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return t


def _fit(t: QTableWidget, max_rows: int = 12):
    rows = min(t.rowCount(), max_rows)
    t.setFixedHeight(t.horizontalHeader().height() + 4 +
                     sum(t.rowHeight(i) for i in range(rows)))
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if t.rowCount() > max_rows
                                 else Qt.ScrollBarAlwaysOff)


def _item(text, sort_value=None) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    if sort_value is not None:
        it.setData(Qt.UserRole, sort_value)
    return it


# --------------------------------------------------------------------- view
class GamesView(QWidget):
    continue_requested = Signal(str)          # switch to this game and start reading
    folder_requested = Signal(str)            # open the game-folder dialog
    hook_requested = Signal(str)              # install / remove the hook
    character_requested = Signal(str, str)    # game, character: show in Cast
    delete_requested = Signal(str)            # delete this game (asks first)
    play_requested = Signal(str)              # start the game's .exe
    rename_requested = Signal(str)

    def __init__(self, cfg, log: PlayLog, voice_label: Callable[[str, str], str]):
        super().__init__()
        self.setObjectName("page")
        self.cfg, self.log, self.voice_label = cfg, log, voice_label
        self.current: str | None = None
        self._notes_timer = QTimer(self, singleShot=True, interval=600,
                                   timeout=self._save_notes)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        left = QWidget()
        left.setFixedWidth(270)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 12, 14)
        ll.setSpacing(8)
        self.lbl_totals = label("", "footnote")
        self.lbl_totals.setWordWrap(True)
        ll.addWidget(self.lbl_totals)
        row = QHBoxLayout()
        self.cb_sort = QComboBox()
        for key, text in (("recent", "Last played"), ("most", "Most played"), ("az", "Name")):
            self.cb_sort.addItem(text, key)
        self.cb_sort.currentIndexChanged.connect(lambda _i: self.refresh())
        row.addWidget(self.cb_sort, 1)
        self.cb_hidden = QCheckBox("Hidden")
        self.cb_hidden.setToolTip("Also show the games you hid")
        self.cb_hidden.toggled.connect(lambda _on: self.refresh())
        row.addWidget(self.cb_hidden)
        ll.addLayout(row)
        self.list = QListWidget()
        self.list.setIconSize(QSize(28, 28))
        self.list.currentItemChanged.connect(self._picked)
        ll.addWidget(self.list, 1)
        lay.addWidget(left)
        sep = QFrame()
        sep.setObjectName("vline")
        lay.addWidget(sep)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self._icons = QFileIconProvider()

    # ---------------------------------------------------------- data
    def game_names(self, include_hidden: bool = False) -> list[str]:
        names = set(self.cfg.settings.games) | set(self.log.games)
        if not include_hidden:
            names = {n for n in names if not self.log.game(n).get("hidden")}
        return list(names)

    def _info(self, g: str) -> dict:
        return self.cfg.settings.games.get(g) or {}

    def _last_played(self, g: str) -> float:
        ss = self.log.sessions(g)
        return max([self._info(g).get("last_played", 0)] + [s.end for s in ss])

    def exe_path(self, g: str) -> str:
        info = self._info(g)
        path, exe = info.get("path", ""), info.get("exe", "")
        if path and exe:
            p = Path(path) / exe
            if p.is_file():
                return str(p)
        return ""

    def _icon(self, g: str):
        exe = self.exe_path(g)
        if exe:
            ic = self._icons.icon(QFileInfo(exe))
            if not ic.isNull():
                return ic
        return theme.icon("gamepad-2", theme.TEXT_2, 28)

    # ---------------------------------------------------------- list
    def refresh(self):
        keep = self.current
        names = self.game_names(self.cb_hidden.isChecked())
        key = self.cb_sort.currentData()
        if key == "az":
            names.sort(key=str.lower)
        elif key == "most":
            names.sort(key=lambda n: -self.log.total_seconds(n))
        else:
            names.sort(key=lambda n: -self._last_played(n))
        today = date.today()
        week = {(today - timedelta(days=k)).isoformat() for k in range(7)}
        per = self.log.per_day()
        self.lbl_totals.setText(
            f"Today {fmt_duration(per.get(today.isoformat(), 0), short=True)},  "
            f"this week {fmt_duration(sum(v for d, v in per.items() if d in week), short=True)}")
        self.list.blockSignals(True)
        self.list.clear()
        for n in names:
            last = self._last_played(n)
            hidden = self.log.game(n).get("hidden")
            it = QListWidgetItem(self._icon(n),
                                 f"{n}{'  (hidden)' if hidden else ''}\n"
                                 f"{fmt_duration(self.log.total_seconds(n), short=True)}  ·  "
                                 f"{_day(last) if last else 'not played yet'}")
            it.setData(Qt.UserRole, n)
            it.setSizeHint(QSize(0, 46))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if not names:
            self.current = None
            self.scroll.setWidget(EmptyState("gamepad-2", "No games yet",
                                             "Games appear here once you start reading one."))
            return
        target = keep if keep in names else names[0]
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == target:
                self.list.setCurrentRow(i)
                break
        self.show_game(target)

    def _picked(self, item, _prev=None):
        if item is not None:
            self.show_game(item.data(Qt.UserRole))

    # ---------------------------------------------------------- page
    def show_game(self, g: str):
        self._save_notes()
        self.current = g
        info = self._info(g)
        rec = self.log.records(g)
        chars = {n: r for n, r in self.cfg.settings.voices.get(g, {}).items() if n != NARRATOR}
        lines = max(int(info.get("lines", 0)), rec["lines"])

        body = QWidget()
        body.setObjectName("page")
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 16, 22, 22)
        v.setSpacing(12)

        # header
        v.addWidget(label(g, "pageTitle"))
        engine = ENGINE_LABEL.get(info.get("engine", ""), "Engine not checked yet")
        src = SOURCE_LABEL.get(info.get("text_source", "auto"), "Automatic")
        folder = info.get("path") or "Game folder not set"
        v.addWidget(label(f"{engine}. Text from: {src}.\n{folder}", "footnote", wrap=True))

        # buttons
        b = QHBoxLayout()
        b.setSpacing(6)
        b.addWidget(button("Play", "play", "primary", tip="Start the game",
                           slot=lambda: self.play_requested.emit(g)))
        b.addWidget(button("Continue reading", "book-open",
                           tip="Switch to this game and start reading",
                           slot=lambda: self.continue_requested.emit(g)))
        b.addWidget(button("Game folder…", "folder-open",
                           slot=lambda: self.folder_requested.emit(g)))
        if info.get("engine") == "renpy" and info.get("path"):
            on = renpy_hook.installed(info["path"])
            b.addWidget(button("Remove game hook" if on else "Install game hook",
                               "x" if on else "zap",
                               tip="The hook sends each line straight from the game "
                                   "(exact text, no OCR). Restart the game after changing it.",
                               slot=lambda: self.hook_requested.emit(g)))
        if info.get("path"):
            b.addWidget(button("", "external-link", "ghost", tip="Open the game folder",
                               slot=lambda: QDesktopServices.openUrl(
                                   QUrl.fromLocalFile(info["path"]))))
        b.addWidget(button("", "file-down", "ghost", tip="Export history to CSV",
                           slot=lambda: self._export(g)))
        hidden = self.log.game(g).get("hidden")
        b.addWidget(button("", "eye", "ghost",
                           tip="Show in the list again" if hidden else "Hide from the list",
                           slot=lambda: self._toggle_hidden(g)))
        b.addWidget(button("", "square-pen", "ghost", tip="Rename this game",
                           slot=lambda: self.rename_requested.emit(g)))
        b.addStretch(1)
        b.addWidget(button("Delete game…", "trash-2", "danger",
                           tip="Remove this game, its characters, voices and stats from "
                               "VN Reader (the game itself isn't touched)",
                           slot=lambda: self.delete_requested.emit(g)))
        v.addLayout(b)

        # four numbers
        total_script = int(self.log.game(g).get("script_lines") or 0)
        seen = self.log.seen_count(g)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        tiles.addWidget(_tile(fmt_duration(rec["total"], short=True), "play time"))
        tiles.addWidget(_tile(str(rec["sessions"]), "sessions"))
        tiles.addWidget(_tile(f"{lines:,}", "lines read"))
        tiles.addWidget(_tile(f"{100 * seen / total_script:.0f}%" if total_script else "–",
                              f"story ({seen:,} / {total_script:,} lines)" if total_script
                              else "story progress (needs the script)"))
        v.addLayout(tiles)

        # chart
        v.addWidget(section("Play time, last 14 days"))
        bars = DayBars()
        bars.set_days(self.log.last_days(g, 14))
        v.addWidget(bars)

        # milestones
        top = max((int(r.get("lines", 0)) for r in chars.values()), default=0)
        badges = milestones(lines, rec["total"] / 3600, top, rec["streak"])
        if badges:
            v.addWidget(section("Milestones"))
            row = QHBoxLayout()
            row.setSpacing(6)
            for x in badges:
                pill = label(x, "pill")
                pill.setProperty("tone", "accent")
                row.addWidget(pill)
            row.addStretch(1)
            v.addLayout(row)

        # characters
        v.addWidget(section(f"Characters ({len(chars)})"))
        ct = _table(["Name", "Lines", "Voice", "First seen", "Last seen"])
        order = sorted(chars, key=lambda n: -int(chars[n].get("lines", 0)))
        for i, n in enumerate(order):
            r = chars[n]
            ct.insertRow(i)
            name = _item(("★ " if i < 3 and int(r.get("lines", 0)) else "") + n)
            if r.get("name_colour"):
                name.setForeground(QColor(*r["name_colour"][:3]))
            name.setData(Qt.UserRole, n)
            ct.setItem(i, 0, name)
            ct.setItem(i, 1, _item(f"{int(r.get('lines', 0)):,}"))
            ct.setItem(i, 2, _item(self.voice_label(g, n)))
            ct.setItem(i, 3, _item(_day(r.get("first_seen", 0))))
            ct.setItem(i, 4, _item(_day(r.get("last_seen", 0))))
        ct.cellDoubleClicked.connect(
            lambda row, _c: self.character_requested.emit(g, ct.item(row, 0).data(Qt.UserRole)))
        ct.setToolTip("Double-click a character to open it in Cast")
        _fit(ct)
        v.addWidget(ct)

        # sessions
        ss = sorted(self.log.sessions(g), key=lambda s: -s.start)
        v.addWidget(section(f"Sessions ({len(ss)})"))
        st = _table(["Started", "Length", "Lines", "Words", "Voice delay", "Text from"])
        for i, s in enumerate(ss):
            st.insertRow(i)
            st.setItem(i, 0, _item(time.strftime("%d %b %Y  %H:%M", time.localtime(s.start))))
            st.setItem(i, 1, _item(fmt_duration(s.seconds, short=True)))
            st.setItem(i, 2, _item(s.lines))
            st.setItem(i, 3, _item(f"{s.words:,}"))
            st.setItem(i, 4, _item(f"{s.avg_delay:.2f} s" if s.avg_delay is not None else "–"))
            st.setItem(i, 5, _item(_mix(s.sources)))
        _fit(st, 10)
        v.addWidget(st)

        # records
        v.addWidget(section("Records"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        best = rec["best_day"]
        voice_h = rec["words"] / WORDS_PER_MIN / 60
        rows = [
            ("Longest session", fmt_duration(rec["longest"], short=True)),
            ("Average session", fmt_duration(rec["average"], short=True)),
            ("Most played day", f"{datetime.fromisoformat(best[0]).strftime('%d %b %Y')} "
                                f"({fmt_duration(best[1], short=True)})" if best else "–"),
            ("First played", _day(rec["first"])),
            ("Days played", f"{rec['days_played']}  (best streak {rec['streak']})"),
            ("Words heard", f"{rec['words']:,}  (≈ {voice_h:.1f} h of voice)"),
            ("Fastest voice", f"{rec['fastest_delay']:.2f} s"
             if rec["fastest_delay"] is not None else "–"),
            ("Average voice delay", f"{rec['avg_delay']:.2f} s"
             if rec["avg_delay"] is not None else "–"),
        ]
        for i, (k, val) in enumerate(rows):
            grid.addWidget(label(k, "muted"), i // 2, (i % 2) * 2)
            grid.addWidget(label(val), i // 2, (i % 2) * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)

        # notes
        v.addWidget(section("Notes"))
        self.notes = QPlainTextEdit(self.log.game(g).get("notes", ""))
        self.notes.setPlaceholderText("Your notes for this game, e.g. which route you're on…")
        self.notes.setFixedHeight(90)
        self.notes.textChanged.connect(self._notes_timer.start)
        v.addWidget(self.notes)
        v.addStretch(1)
        self.scroll.setWidget(body)

    # ---------------------------------------------------------- actions
    def _save_notes(self):
        if self.current and getattr(self, "notes", None) is not None:
            try:
                text = self.notes.toPlainText()
            except RuntimeError:                 # page already replaced
                return
            if text != self.log.game(self.current).get("notes", ""):
                self.log.set_notes(self.current, text)
                self.log.save()

    def _toggle_hidden(self, g: str):
        self.log.set_hidden(g, not self.log.game(g).get("hidden"))
        self.log.save()
        self.refresh()

    def _export(self, g: str, path: str | None = None) -> str | None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export history",
                                                  f"{g} history.csv", "CSV (*.csv)")
        if not path:
            return None
        chars = self.cfg.settings.voices.get(g, {})
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Sessions"])
            w.writerow(["started", "ended", "minutes", "lines", "words", "new characters",
                        "avg voice delay (s)", "text from"])
            for s in sorted(self.log.sessions(g), key=lambda s: s.start):
                w.writerow([datetime.fromtimestamp(s.start).isoformat(" ", "minutes"),
                            datetime.fromtimestamp(s.end).isoformat(" ", "minutes") if s.end
                            else "", round(s.seconds / 60, 1), s.lines, s.words, s.new_chars,
                            round(s.avg_delay, 3) if s.avg_delay is not None else "",
                            _mix(s.sources)])
            w.writerow([])
            w.writerow(["Characters"])
            w.writerow(["name", "lines", "voice", "first seen", "last seen"])
            for n, r in sorted(chars.items(), key=lambda kv: -int(kv[1].get("lines", 0))):
                if n == NARRATOR:
                    continue
                w.writerow([n, int(r.get("lines", 0)), self.voice_label(g, n),
                            _day(r.get("first_seen", 0)), _day(r.get("last_seen", 0))])
        return path


def _mix(sources: dict) -> str:
    total = sum(sources.values())
    if not total:
        return "–"
    names = {"hook": "game", "script": "script", "ocr": "OCR"}
    parts = sorted(sources.items(), key=lambda kv: -kv[1])
    return " · ".join(f"{100 * v / total:.0f}% {names.get(k, k)}" for k, v in parts if v)
