from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import presets as presets_mod
from .. import text_fixes, voices, winapi
from ..audio.player import Player
from ..config import NARRATOR, Config
from ..hotkeys import HotkeyManager
from ..name_split import split_name
from ..sources import ocr_filter, preprocess
from ..sources.capture import Capturer, CaptureSpec, frac_to_rect, rect_to_frac
from ..sources.ocr_source import OcrOptions, OcrSource, WindowsOcr
from ..speech_queue import Line, SpeechQueue
from ..text_clean import clean, clean_name
from ..tts.sapi_fallback import SapiEngine
from ..tts.voicestudio import VoiceStudioEngine
from ..voice_bank import VoiceBank
from . import theme
from .characters_view import CUSTOM, Avatar, CharactersView
from .dashboard import (AreaPreview, CastList, LevelMeter, ResponsiveRow, character_menu,
                        stats_text)
from .fixups_view import FixupsView
from .history_view import HistoryView
from .presets_view import PresetsView
from .region_picker import RegionPicker
from .settings_view import QUALITY_STEPS, SettingsView


class Bridge(QObject):
    """Thread -> UI signals."""
    lines = Signal(list, list)       # OCR lines, their boxes
    preview = Signal(object, list)   # captured image, line boxes
    status = Signal(str)
    tags = Signal(list)              # VoiceStudio expression tags
    line_done = Signal()             # a line finished playing
    voices = Signal(list, list)      # VoiceStudio profiles, SAPI voices
    health = Signal(bool)
    voice_ready = Signal(str)        # voice key


def _physical_to_logical(region: list[int]) -> QRect:
    l, t, w, h = region
    for s in QGuiApplication.screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        pl, pt = g.left(), g.top()
        if pl <= l < pl + g.width() * dpr and pt <= t < pt + g.height() * dpr:
            return QRect(int(pl + (l - pl) / dpr), int(pt + (t - pt) / dpr),
                         int(w / dpr), int(h / dpr))
    return QRect(l, t, w, h)


class AreaFlash(QWidget):
    """Briefly outlines the reading area on screen so you can see where it is."""

    def __init__(self, region: list[int], color: QColor):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.color = color
        self.setGeometry(_physical_to_logical(region).adjusted(-3, -3, 3, 3))
        QTimer.singleShot(1500, self.close)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(QPen(self.color, 3))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))


def card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    if title:
        t = QLabel(title.upper())
        t.setObjectName("cardTitle")
        lay.addWidget(t)
    return f, lay


def iso_lang(tag: str) -> str:
    return (tag or "en").split("-")[0].lower()


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.cfg = config
        s = config.settings
        self.setWindowTitle("VN Reader")
        self.resize(820, 880)
        self.setMinimumWidth(560)
        self.game = s.ocr_profile
        self._flashes: list[QWidget] = []
        self._hwnd = 0
        self._vs_profiles: list = []
        self._sapi_voices: list = []
        self._cleaned_legacy = False

        self.bridge = Bridge()
        self.bridge.lines.connect(self._on_lines)
        self.bridge.preview.connect(lambda img, boxes: self.preview.set_frame(img, boxes))
        self.bridge.tags.connect(lambda t: setattr(self.queue, "expression_tags", set(t)))
        self.bridge.status.connect(self._set_status)
        self.bridge.voices.connect(self._on_voices)
        self.bridge.health.connect(self._on_health)
        self.bridge.voice_ready.connect(lambda _k: self._refresh_ready())

        self.player = Player()
        self.player.volume = 0.0 if s.muted else s.volume
        self.vs = VoiceStudioEngine(s.voicestudio_url, s.voicestudio_api_key)
        self.vs.num_step = QUALITY_STEPS.get(s.vs_quality, 16)
        self.sapi = SapiEngine()
        self.bank = VoiceBank(self.cfg, self.vs, on_status=self.bridge.status.emit,
                              on_ready=self.bridge.voice_ready.emit)
        self.queue = SpeechQueue(self.vs, self.sapi, self.player,
                                 speed_getter=lambda: self.cfg.settings.speed,
                                 on_status=self.bridge.status.emit, bank=self.bank)
        self.queue.expressive = lambda: self.cfg.settings.expressive
        self.queue.on_done = lambda _line: self.bridge.line_done.emit()
        self.bridge.line_done.connect(self._schedule_advance)
        self._advance_token = 0
        self.ocr = OcrSource(self.bridge.lines.emit, on_status=self.bridge.status.emit,
                             on_preview=self.bridge.preview.emit)

        self._build_ui()
        self._refresh_windows()
        self._refresh_recent()
        self._apply_capture()
        self._apply_ocr_options()
        self._refresh_voices()

        self.hotkeys = HotkeyManager(int(self.winId()))
        self._apply_hotkeys()

        self._health_timer = QTimer(self, interval=5000, timeout=self._check_health)
        self._health_timer.start()
        self._check_health()
        self._speak_timer = QTimer(self, interval=200, timeout=self._update_speaking)
        self._speak_timer.start()

    # ================================================================== UI
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("page")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 6)

        head = QHBoxLayout()
        title = QLabel("VN Reader")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch(1)
        self.pill_vs = QLabel("VoiceStudio …")
        self.pill_vs.setObjectName("pill")
        head.addWidget(self.pill_vs)
        outer.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_read_tab(), "Read")

        self.chars = CharactersView()
        self.chars.changed.connect(self._char_changed)
        self.chars.voice_chosen.connect(self._char_voice)
        self.chars.gender_changed.connect(self._char_gender)
        self.chars.reroll.connect(self._char_reroll)
        self.chars.preview.connect(self._preview)
        self.chars.reassign_all.connect(self._reassign_all)
        self.chars.refresh.connect(self._refresh_voices)
        self.chars.menu.connect(self._char_menu)
        self.tabs.addTab(self.chars, "Characters")

        self.presets_view = PresetsView()
        self.presets_view.changed.connect(self._preset_changed)
        self.presets_view.reroll.connect(self._preset_reroll)
        self.presets_view.preview.connect(self._preset_preview)
        self.presets_view.duplicate.connect(self._preset_duplicate)
        self.presets_view.delete.connect(self._preset_delete)
        self.presets_view.add.connect(self._preset_add)
        self.presets_view.reset.connect(self._preset_reset)
        self.tabs.addTab(self.presets_view, "Voices")

        self.fixups = FixupsView(self.cfg, lambda: self.game,
                                 lambda: self.lbl_text.selectedText())
        self.tabs.addTab(self.fixups, "Fix-ups")

        self.history = HistoryView()
        self.history.replay_requested.connect(self._speak)
        self.tabs.addTab(self.history, "History")

        self.settings_view = SettingsView(self.cfg, self._grab_text_now, self._run_test_ocr,
                                          self._set_status)
        self.settings_view.ocr_options_changed.connect(self._apply_ocr_options)
        self.settings_view.hotkeys_changed.connect(self._apply_hotkeys)
        self.settings_view.voicestudio_changed.connect(self._vs_changed)
        self.settings_view.accent_changed.connect(self._accent_changed)
        self.settings_view.names_changed.connect(
            lambda on: (self.cb_names.blockSignals(True), self.cb_names.setChecked(on),
                        self.cb_names.blockSignals(False)))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        self.settings_view.setObjectName("page")
        sc.setWidget(self.settings_view)
        self.tabs.addTab(sc, "Settings")

        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self._load_game(self.game)
        self._reload_presets()

    def _build_read_tab(self) -> QWidget:
        s = self.cfg.settings
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("page")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(2, 6, 6, 2)
        lay.setSpacing(12)
        sc.setWidget(body)
        outer.addWidget(sc, 1)

        # --- Hero header: game title, recent games, stats
        hero = QFrame()
        hero.setObjectName("hero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        self.lbl_game = QLabel(self.game)
        self.lbl_game.setObjectName("heroTitle")
        self.lbl_game.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.lbl_game, 1)
        self.cb_recent = QComboBox()
        self.cb_recent.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_recent.setMinimumContentsLength(16)
        self.cb_recent.setToolTip("Switch game: restores its window, reading area and "
                                  "characters")
        self.cb_recent.activated.connect(self._recent_chosen)
        top.addWidget(self.cb_recent)
        b_delgame = QPushButton("🗑")
        b_delgame.setObjectName("icon")
        b_delgame.setToolTip("Delete this game from history (characters, voices, fix-ups)")
        b_delgame.clicked.connect(self._delete_game)
        top.addWidget(b_delgame)
        self.btn_setup = QPushButton("⚙ Setup")
        self.btn_setup.setCheckable(True)
        self.btn_setup.setToolTip("Game window, profile, language and reading area")
        self.btn_setup.toggled.connect(lambda on: self.setup_card.setVisible(on))
        top.addWidget(self.btn_setup)
        hl.addLayout(top)
        stats_row = QHBoxLayout()
        self.lbl_stats = QLabel()
        self.lbl_stats.setStyleSheet("color:#c9cbe0;")
        stats_row.addWidget(self.lbl_stats, 1)
        self.cb_auto = QCheckBox("Auto-advance")
        self.cb_auto.setToolTip("When a line has been read, go to the next one automatically "
                                "(F7 to switch on/off). Key and delay: Settings.")
        self.cb_auto.setChecked(s.auto_advance)
        self.cb_auto.toggled.connect(self._auto_toggled)
        stats_row.addWidget(self.cb_auto)
        self.cb_names = QCheckBox("Read names aloud")
        self.cb_names.setToolTip("Off: only the dialogue is spoken, never the speaker's name")
        self.cb_names.setChecked(s.read_names)
        self.cb_names.toggled.connect(self._read_names_toggled)
        stats_row.addWidget(self.cb_names)
        hl.addLayout(stats_row)
        lay.addWidget(hero)

        # --- Setup (collapsible)
        self.setup_card, cl = card("Setup")
        wr = QHBoxLayout()
        lbl = QLabel("Window")
        lbl.setFixedWidth(64)
        wr.addWidget(lbl)
        self.cb_window = QComboBox()
        self.cb_window.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_window.setMinimumContentsLength(20)
        self.cb_window.setToolTip("Pick the game window so the area follows it when it "
                                  "moves or resizes")
        self.cb_window.activated.connect(self._window_chosen)
        b_wref = QPushButton("⟳")
        b_wref.setObjectName("icon")
        b_wref.setToolTip("Refresh the window list")
        b_wref.clicked.connect(self._refresh_windows)
        wr.addWidget(self.cb_window, 1)
        wr.addWidget(b_wref)
        cl.addLayout(wr)
        pr = QHBoxLayout()
        lbl = QLabel("Profile")
        lbl.setFixedWidth(64)
        pr.addWidget(lbl)
        self.ed_profile = QLineEdit(s.ocr_profile)
        self.ed_profile.setToolTip("Characters, voices and fix-ups are saved under this name")
        self.ed_profile.editingFinished.connect(self._profile_changed)
        pr.addWidget(self.ed_profile, 1)
        pr.addWidget(QLabel("Language"))
        self.ed_lang = QLineEdit(s.ocr_language)
        self.ed_lang.setToolTip("OCR language tag, e.g. en-US, ja, zh-Hans")
        self.ed_lang.setFixedWidth(72)
        self.ed_lang.editingFinished.connect(self._lang_changed)
        pr.addWidget(self.ed_lang)
        cl.addLayout(pr)
        self.cb_bg = QCheckBox("Keep reading when the game is covered by other windows")
        self.cb_bg.setChecked(s.background_capture)
        self.cb_bg.toggled.connect(self._bg_toggled)
        cl.addWidget(self.cb_bg)
        r1 = QHBoxLayout()
        b_draw = QPushButton("✏  Draw reading area")
        b_draw.setToolTip("One box around the dialogue box, including the name line on top")
        b_draw.clicked.connect(self._pick_region)
        b_show = QPushButton("👁  Show")
        b_show.setToolTip("Outline the reading area on screen")
        b_show.clicked.connect(self._flash_region)
        r1.addWidget(b_draw, 1)
        r1.addWidget(b_show)
        cl.addLayout(r1)
        self.lbl_region = QLabel()
        self.lbl_region.setObjectName("muted")
        cl.addWidget(self.lbl_region)
        self.setup_card.setVisible(False)
        lay.addWidget(self.setup_card)

        # --- Now speaking | Live area
        row = ResponsiveRow(640)
        c, cl = card("Now speaking")
        who = QHBoxLayout()
        self.av = Avatar(NARRATOR, 44)
        who.addWidget(self.av)
        wl = QVBoxLayout()
        wl.setSpacing(0)
        self.lbl_speaker = QLabel("—")
        self.lbl_speaker.setObjectName("speaker")
        self.lbl_voice = QLabel("")
        self.lbl_voice.setObjectName("muted")
        self.lbl_voice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wl.addWidget(self.lbl_speaker)
        wl.addWidget(self.lbl_voice)
        who.addLayout(wl, 1)
        self.meter = LevelMeter()
        who.addWidget(self.meter)
        cl.addLayout(who)
        self.lbl_text = QLabel("Pick your game, draw the reading area, then press "
                               "Start reading.")
        self.lbl_text.setObjectName("dialogue")
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setMinimumHeight(70)
        self.lbl_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.lbl_text, 1)
        tr = QHBoxLayout()
        self.btn_pause = QPushButton("⏸")
        self.btn_pause.setObjectName("icon")
        self.btn_pause.setToolTip("Pause / resume")
        self.btn_pause.clicked.connect(self._toggle_pause)
        b_stop = QPushButton("⏹")
        b_stop.setObjectName("icon")
        b_stop.setToolTip("Stop speaking")
        b_stop.clicked.connect(self.queue.stop)
        b_replay = QPushButton("↺")
        b_replay.setObjectName("icon")
        b_replay.setToolTip("Replay last line")
        b_replay.clicked.connect(self.queue.replay)
        self.btn_mute = QPushButton("🔇" if s.muted else "🔊")
        self.btn_mute.setObjectName("icon")
        self.btn_mute.setToolTip("Mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setChecked(s.muted)
        self.btn_mute.toggled.connect(self._mute)
        for w in (self.btn_pause, b_stop, b_replay, self.btn_mute):
            tr.addWidget(w)
        self.sl_vol = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=int(s.volume * 100))
        self.sl_vol.setToolTip("Volume")
        self.sl_vol.valueChanged.connect(self._volume)
        tr.addWidget(self.sl_vol, 1)
        cl.addLayout(tr)
        sr = QHBoxLayout()
        self.lbl_speed = QLabel(f"Speed {s.speed:.2f}×")
        self.lbl_speed.setFixedWidth(84)
        sr.addWidget(self.lbl_speed)
        self.sl_speed = QSlider(Qt.Horizontal, minimum=50, maximum=200, value=int(s.speed * 100))
        self.sl_speed.valueChanged.connect(self._speed)
        sr.addWidget(self.sl_speed, 1)
        cl.addLayout(sr)
        row.add(c, 3)

        c, cl = card("Live area")
        self.preview = AreaPreview()
        cl.addWidget(self.preview, 1)
        leg = QLabel(f"<span style='color:{theme.FEMALE}'>■</span> name   "
                     f"<span style='color:{theme.ACCENT}'>■</span> dialogue")
        leg.setObjectName("muted")
        cl.addWidget(leg)
        row.add(c, 2)
        lay.addWidget(row)

        # --- Cast
        c, cl = card("Cast")
        self.cast = CastList()
        self.cast.setMinimumHeight(200)
        self.cast.preview.connect(self._preview)
        self.cast.menu.connect(self._char_menu)
        cl.addWidget(self.cast, 1)
        lay.addWidget(c, 1)

        # --- Start / stop (always visible, outside the scroll area)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(2, 8, 6, 4)
        self.btn_ocr = QPushButton()
        self.btn_ocr.clicked.connect(self._toggle_ocr)
        bottom.addWidget(self.btn_ocr, 1)
        outer.addLayout(bottom)
        self.lbl_keys = QLabel()
        self.lbl_keys.setObjectName("muted")
        self.lbl_keys.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.lbl_keys)
        self._update_ocr_button()
        return page

    # ======================================================== recent games
    def _refresh_recent(self):
        self.cb_recent.blockSignals(True)
        self.cb_recent.clear()
        games = self.cfg.recent_games()
        if not games:
            self.cb_recent.addItem("No games yet: pick the game window below", None)
        for name, info in games:
            n = len([k for k in self.cfg.settings.voices.get(name, {}) if k != NARRATOR])
            exe = info.get("exe", "")
            when = time.strftime("%d %b", time.localtime(info["last_played"])) \
                if info.get("last_played") else ""
            label = f"🕘  {name}  ·  {n} character{'s' if n != 1 else ''}"
            if when:
                label += f"  ·  {when}"
            self.cb_recent.addItem(label, name)
            self.cb_recent.setItemData(self.cb_recent.count() - 1,
                                       exe or "no window remembered", Qt.ToolTipRole)
        i = self.cb_recent.findData(self.game)
        self.cb_recent.setCurrentIndex(max(0, i))
        self.cb_recent.blockSignals(False)

    def _recent_chosen(self, _idx: int):
        name = self.cb_recent.currentData()
        if not name:
            return
        info = self.cfg.settings.games.get(name, {})
        self.ed_profile.setText(name)
        self.cfg.settings.ocr_profile = name
        if info.get("exe"):
            self.cfg.settings.target_window = {"exe": info["exe"],
                                               "title": info.get("title", "")}
        self.cfg.save()
        self._load_game(name)
        self._refresh_windows()
        self._apply_capture()
        running = bool(self._resolve_hwnd()) if info.get("exe") else True
        n = len([k for k in self.cfg.characters(name) if k != NARRATOR])
        self._set_status(f"Loaded {name}: {n} characters" +
                         ("" if running else " · start the game, then press Start reading"))

    # ========================================================= game window
    def _refresh_windows(self):
        s = self.cfg.settings
        self.cb_window.clear()
        self.cb_window.addItem("🖥  Whole screen (fixed position)", None)
        wins = sorted(winapi.list_windows(), key=lambda w: w.title.lower())
        sel = 0
        for w in wins:
            self.cb_window.addItem("🎮  " + w.label, w)
            t = s.target_window
            if t and w.exe.lower() == (t.get("exe") or "").lower():
                if not sel or w.title == t.get("title"):
                    sel = self.cb_window.count() - 1
        if s.target_window and not sel:
            t = s.target_window
            self.cb_window.addItem(f"🎮  {t.get('title')}  [{t.get('exe')}]  (not running)",
                                   winapi.WindowInfo(0, t.get("title", ""), t.get("exe", "")))
            sel = self.cb_window.count() - 1
        self.cb_window.setCurrentIndex(sel)
        w = self.cb_window.currentData()
        self._hwnd = w.hwnd if w else 0

    def _window_chosen(self, _idx: int):
        w: winapi.WindowInfo | None = self.cb_window.currentData()
        s = self.cfg.settings
        if w is None:
            s.target_window = None
            self._hwnd = 0
        else:
            s.target_window = {"title": w.title, "exe": w.exe}
            self._hwnd = w.hwnd
            # Re-use the profile this exe was saved under before, else its exe name.
            known = [g for g, i in s.games.items() if i.get("exe", "").lower() == w.exe.lower()]
            self.ed_profile.setText(known[0] if known else
                                    (os.path.splitext(w.exe)[0] or w.title))
            self._profile_changed()
            self.cfg.touch_game(self.game, w.exe, w.title)
            self._refresh_recent()
        self.cfg.save()
        self._apply_capture()

    def _resolve_hwnd(self) -> int:
        t = self.cfg.settings.target_window
        if not t:
            return 0
        if self._hwnd and winapi.is_window(self._hwnd):
            return self._hwnd
        w = winapi.find_window(t.get("exe", ""), t.get("title", ""))
        self._hwnd = w.hwnd if w else 0
        return self._hwnd

    def _bg_toggled(self, on: bool):
        self.cfg.settings.background_capture = on
        self.cfg.save()
        self._apply_capture()

    def _spec(self) -> CaptureSpec:
        s = self.cfg.settings
        t = s.target_window
        if t:
            regs = self.cfg.window_regions(t.get("exe", ""))
            return CaptureSpec("window", regs.get("text"), t.get("exe", ""), t.get("title", ""),
                               self._hwnd, s.background_capture)
        return CaptureSpec("screen", s.ocr_text_region)

    def _apply_capture(self):
        self.ocr.spec = self._spec()
        spec = self.ocr.spec
        where = "follows the game window" if spec.mode == "window" else "fixed on screen"
        self.lbl_region.setText(("✓ Area set · " + where) if spec.text
                                else "No area yet: click Draw reading area")

    # ================================================================ area
    def _pick_region(self):
        if self.cfg.settings.target_window and not self._resolve_hwnd():
            self._set_status("The game window isn't open: start the game or pick it again")
            return
        self.hide()
        self._picker = RegionPicker("Drag one box around the dialogue box, including the "
                                    "name line. Esc to cancel.")

        def done(rect):
            s = self.cfg.settings
            if s.target_window:
                hwnd = self._resolve_hwnd()
                client = winapi.client_rect_screen(hwnd) if hwnd else None
                if not client or client[2] <= 0:
                    self._set_status("Lost the game window: try again")
                else:
                    self.cfg.window_regions(s.target_window["exe"])["text"] = \
                        rect_to_frac(rect, client)
            else:
                s.ocr_text_region = rect
            self.cfg.save()
            self._apply_capture()
            self.show()
            self._flash_region()

        self._picker.picked.connect(done)
        self._picker.cancelled.connect(self.show)
        QTimer.singleShot(200, self._picker.show)

    def _screen_rect(self):
        spec = self.ocr.spec
        if not spec.text:
            return None
        if spec.mode == "screen":
            return spec.text
        hwnd = self._resolve_hwnd()
        return frac_to_rect(spec.text, winapi.client_rect_screen(hwnd)) if hwnd else None

    def _flash_region(self):
        rect = self._screen_rect()
        if not rect:
            self._set_status("No area to show (or the game window isn't open)")
            return
        self._flashes = [AreaFlash(rect, QColor(theme.ACCENT))]
        self._flashes[0].show()

    # ================================================================= OCR
    def _update_ocr_button(self):
        running = self.ocr.running
        self.btn_ocr.setText("■   Stop reading" if running else "▶   Start reading")
        self.btn_ocr.setObjectName("danger" if running else "primary")
        self.btn_ocr.style().unpolish(self.btn_ocr)
        self.btn_ocr.style().polish(self.btn_ocr)

    def _toggle_ocr(self):
        if self.ocr.running:
            self.ocr.stop()
            self._set_status("Stopped")
        else:
            self._apply_capture()
            self.ocr.start()
            if self.ocr.running:
                t = self.cfg.settings.target_window or {}
                self.cfg.touch_game(self.game, t.get("exe", ""), t.get("title", ""))
                self._refresh_recent()
        self._update_ocr_button()
        self._update_stats()

    def _apply_ocr_options(self):
        s = self.cfg.settings
        self.ocr.opts = OcrOptions(s.ocr_language, s.ocr_mode, s.text_color, s.color_tolerance,
                                   list(s.ignore_words))

    def _grab_text_now(self):
        """Capture the reading area right now (hides this window if it might be in the way)."""
        spec = self._spec()
        if not spec.text:
            return None
        hide = not (spec.mode == "window" and spec.background)
        if hide:
            self.hide()
            QApplication.processEvents()
            time.sleep(0.3)
        cap = Capturer()
        try:
            img = cap.grab(spec)
        except Exception as e:
            self._set_status(str(e))
            img = None
        finally:
            cap.close()
            if hide:
                self.show()
        return img

    def _run_test_ocr(self, img):
        s = self.cfg.settings
        self._apply_ocr_options()
        reading = WindowsOcr(s.ocr_language).read_dialogue(img, self.ocr.opts)
        processed = preprocess.process(img, s.ocr_mode, s.text_color, s.color_tolerance)
        speaker, text, fixes = self._interpret(reading.lines, reading.result.boxes)
        out = [f"Name: {speaker if speaker != NARRATOR else '(none, so this counts as narration)'}",
               f"Text: {text or '(nothing to read)'}", "",
               f"Read from the {reading.used} image. Raw OCR lines:"]
        out += [f"   {r[0]}" for r in reading.raw] or ["   (none)"]
        if reading.result.dropped:
            out += ["", "Skipped:"] + [f"   {t!r}: {why}" for t, why in reading.result.dropped]
        if fixes:
            out += ["", "Fixed:"] + [f"   {a} → {b}" for a, b in fixes]
        return processed, "\n".join(out)

    def _profile_changed(self):
        name = self.ed_profile.text().strip() or "My game"
        self.cfg.settings.ocr_profile = name
        self.cfg.save()
        self._load_game(name)

    def _lang_changed(self):
        self.cfg.settings.ocr_language = self.ed_lang.text().strip() or "en-US"
        self.cfg.save()
        self._apply_ocr_options()

    # =============================================================== lines
    def _load_game(self, game: str):
        self.game = game
        self.cfg.character(game, NARRATOR)
        self._reload_characters()
        if hasattr(self, "fixups"):
            self.fixups.reload()
        if hasattr(self, "cb_recent"):
            i = self.cb_recent.findData(game)
            if i >= 0:
                self.cb_recent.setCurrentIndex(i)
        if hasattr(self, "lbl_game"):
            self.lbl_game.setText(game)
            self._update_stats()
            # A new game without a reading area: open the setup straight away.
            if not self._spec().text:
                self.btn_setup.setChecked(True)

    def _designs(self) -> dict[str, dict]:
        return {n: self.cfg.design(self.game, n) for n in self.cfg.characters(self.game)}

    def _voice_label(self, name: str) -> str:
        rec = self.cfg.character(self.game, name)
        ov = rec.get("voice_override", "")
        if ov.startswith("sapi:"):
            return "Windows voice"
        if ov.startswith("vs:"):
            return "my VoiceStudio voice"
        d = self.cfg.design(self.game, name)
        return d.get("label") if rec.get("preset") else "custom voice"

    def _reload_characters(self):
        chars = self.cfg.characters(self.game)
        self.chars.load(chars, self._designs())
        if hasattr(self, "cast"):
            self.cast.set_characters(chars, {n: self._voice_label(n) for n in chars})

    def _update_stats(self):
        chars = [k for k in self.cfg.characters(self.game) if k != NARRATOR]
        info = self.cfg.settings.games.get(self.game, {})
        self.lbl_stats.setText(stats_text(len(chars), int(info.get("lines", 0)),
                                          self.ocr.running, info.get("last_played", 0)))

    def _interpret(self, lines: list, boxes: list | None = None) -> tuple[str, str, list]:
        """OCR lines -> (speaker, dialogue, corrections). No name -> narrator."""
        s = self.cfg.settings
        rules = self.cfg.all_fixups(self.game)
        pairs = [(text_fixes.apply(clean(ln), rules),
                  boxes[i] if boxes and i < len(boxes) else None) for i, ln in enumerate(lines)]
        pairs = [p for p in pairs if p[0]]
        if not pairs:
            return NARRATOR, "", []
        fixed = [p[0] for p in pairs]
        bxs = [p[1] for p in pairs] if boxes and all(p[1] for p in pairs) else None
        known = [k for k in self.cfg.characters(self.game) if k != NARRATOR]
        speaker, text = split_name(fixed, known, s.name_mode, bxs)
        text = clean(text)
        fixes = []
        if text and s.ocr_autocorrect and iso_lang(s.ocr_language) == "en":
            names = known + [r.get("replace", "") for r in rules] + ([speaker] if speaker else [])
            text, fixes = ocr_filter.correct(text, names)
        speaker = clean_name(speaker)
        if speaker:
            speaker = text_fixes.match_speaker(speaker, known)
        return speaker or NARRATOR, text, fixes

    def _on_lines(self, lines: list, boxes: list | None = None):
        self._advance_token += 1             # a new line cancels any pending advance
        name, text, _fixes = self._interpret(lines, boxes)
        if not text:
            return
        new = name not in self.cfg.characters(self.game)
        rec = self.cfg.character(self.game, name)
        design = self.cfg.design(self.game, name)
        self.cfg.count_line(self.game, name)
        self.chars.ensure(name, rec, design)
        if new:
            self.bank.prewarm(design)
            self._refresh_recent()
        self._reload_cast()
        self._update_stats()
        self.preview.set_name_on_top(name != NARRATOR)
        self._show_line(name, text, rec, design)
        self.history.add(name, text)
        if name == NARRATOR and not self.cfg.settings.read_narrator:
            self._schedule_advance()         # not spoken: still move on
            return
        self._speak(name, text)

    # ======================================================== auto-advance
    def _auto_toggled(self, on: bool):
        self.cfg.settings.auto_advance = on
        self.cfg.save()
        self._set_status("Auto-advance on: the story moves on by itself after each line"
                         if on else "Auto-advance off")

    def _toggle_auto(self):
        self.cb_auto.setChecked(not self.cb_auto.isChecked())

    def _schedule_advance(self):
        s = self.cfg.settings
        if not (s.auto_advance and self.ocr.running):
            return
        token = self._advance_token
        QTimer.singleShot(int(max(0.0, s.advance_delay) * 1000),
                          lambda: token == self._advance_token and self._advance())

    def _advance(self):
        s = self.cfg.settings
        if not (s.auto_advance and self.ocr.running) or self.player.paused:
            return
        hwnd = self._resolve_hwnd()
        if not hwnd:                         # whole-screen mode: use the active window
            fg = int(winapi.user32.GetForegroundWindow() or 0)
            hwnd = fg if fg and fg != int(self.winId()) else 0
        if not hwnd:
            self._set_status("Auto-advance: pick the game window in Setup so I know where "
                             "to press")
            return
        click = None
        if s.advance_key == "click":
            # Click the middle of the reading area (the dialogue box), in client pixels.
            rect = self._screen_rect()
            cl = winapi.client_rect_screen(hwnd)
            if rect:
                click = (rect[0] + rect[2] // 2 - cl[0], rect[1] + rect[3] // 2 - cl[1])
            else:
                click = (cl[2] // 2, int(cl[3] * 0.85))
        winapi.send_advance(hwnd, s.advance_key, click)

    def _reload_cast(self):
        chars = self.cfg.characters(self.game)
        self.cast.set_characters(chars, {n: self._voice_label(n) for n in chars})

    def _show_line(self, name: str, text: str, rec: dict, design: dict):
        self.av.set_name(name)
        g = rec.get("gender", "unknown")
        icon = theme.gender_icon(g) if name != NARRATOR else ""
        self.lbl_speaker.setText(("Narrator" if name == NARRATOR else name) +
                                 (f"  <span style='color:{theme.gender_color(g)}'>{icon}</span>"
                                  if icon and icon != "?" else ""))
        ov = rec.get("voice_override")
        if ov:
            self.lbl_voice.setText(ov.split(":", 1)[-1])
        else:
            label = design.get("label") if rec.get("preset") else "custom voice"
            self.lbl_voice.setText(f"{label} · {voices.instruct(design)}")
        self.lbl_text.setText(text)

    def _speak(self, name: str, text: str):
        rec = self.cfg.character(self.game, name)
        if self.cfg.settings.read_names and name != NARRATOR:
            text = f"{name}. {text}"
        self.queue.say(Line(self.game, name, text, dict(rec),
                            iso_lang(self.cfg.settings.ocr_language),
                            self.cfg.design(self.game, name)))

    def _preview(self, name: str):
        who = "the narrator" if name == NARRATOR else name
        line = f"Hi, I'm {who}. This is what my voice sounds like."
        self._show_line(name, line, self.cfg.character(self.game, name),
                        self.cfg.design(self.game, name))
        self.queue.say(Line(self.game, name, line, dict(self.cfg.character(self.game, name)),
                            iso_lang(self.cfg.settings.ocr_language),
                            self.cfg.design(self.game, name)))

    def _update_speaking(self):
        on = self.player.is_playing() and not self.player.paused
        self.meter.push(self.player.level if on else 0.0)

    def _read_names_toggled(self, on: bool):
        self.cfg.settings.read_names = on
        self.cfg.save()
        if hasattr(self, "settings_view"):
            self.settings_view.cb_names.blockSignals(True)
            self.settings_view.cb_names.setChecked(on)
            self.settings_view.cb_names.blockSignals(False)

    # ================================================== delete / merge
    def _delete_game(self):
        g = self.game
        n = len([k for k in self.cfg.characters(g) if k != NARRATOR])
        if QMessageBox.question(
                self, "Delete game",
                f"Delete “{g}” from your history?\n\nThis removes its {n} character"
                f"{'s' if n != 1 else ''}, their voice choices, fix-ups and reading area.\n"
                "Your shared voice presets are kept.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        if self.ocr.running:
            self._toggle_ocr()
        self.cfg.delete_game(g)
        self.bank.prune(self.cfg.used_voice_keys())
        self.ed_profile.setText(self.cfg.settings.ocr_profile)
        self._load_game(self.cfg.settings.ocr_profile)
        self._refresh_recent()
        self._refresh_windows()
        self._apply_capture()
        self._set_status(f"Deleted {g}")

    def _char_menu(self, name: str, pos):
        others = list(self.cfg.characters(self.game))
        character_menu(self, name, others, on_edit=self._edit_character,
                       on_merge=self._merge_character,
                       on_delete=self._delete_character).exec(pos)

    def _edit_character(self, name: str):
        self.tabs.setCurrentWidget(self.chars)
        card = self.chars.cards.get(name)
        if card:
            self.chars.scroll.ensureWidgetVisible(card)

    def _delete_character(self, name: str):
        if QMessageBox.question(
                self, "Delete character",
                f"Delete “{name}” from {self.game}?\n\nIf they speak again they'll come back "
                "as a new character with a fresh voice.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.cfg.delete_character(self.game, name)
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self._update_stats()
        self._refresh_recent()
        self._set_status(f"Deleted {name}")

    def _merge_character(self, src: str, dst: str):
        if QMessageBox.question(
                self, "Merge characters",
                f"Merge “{src}” into “{dst}”?\n\n“{src}” is removed, its lines count for "
                f"{dst}, and from now on “{src}” is read as {dst} (added to Fix-ups).",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes) != QMessageBox.Yes:
            return
        self.cfg.merge_character(self.game, src, dst)
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self.fixups.reload()
        self._update_stats()
        self._refresh_recent()
        self._set_status(f"Merged {src} into {dst}")

    # ========================================================== characters
    def _others(self, name: str) -> dict:
        return {k: v for k, v in self.cfg.characters(self.game).items() if k != name}

    def _char_refresh(self, name: str, prewarm: bool = True):
        rec = self.cfg.character(self.game, name)
        design = self.cfg.design(self.game, name)
        self.chars.ensure(name, rec, design)
        self._reload_cast()
        if prewarm and not rec.get("voice_override"):
            self.bank.prewarm(design)

    def _char_changed(self, name: str, changes: dict):
        self.cfg.update_character(self.game, name, **changes)
        self._char_refresh(name)

    def _char_voice(self, name: str, value: str):
        if value.startswith("preset:"):
            self.cfg.update_character(self.game, name, preset=value[7:], voice_override="")
        elif value == CUSTOM:
            self.cfg.update_character(self.game, name, preset="", voice_override="")
        elif value:
            self.cfg.update_character(self.game, name, voice_override=value)
        self._char_refresh(name)
        self._preview(name)

    def _char_gender(self, name: str, gender: str):
        others = self._others(name)
        pid = voices.pick_preset(gender, others, self.cfg.settings.presets)
        changes = {"gender": gender, "gender_auto": False, "preset": pid, "voice_override": ""}
        if not pid:
            changes.update(voices.custom_design(name, gender, others))
        self.cfg.update_character(self.game, name, **changes)
        self._char_refresh(name)
        self._preview(name)

    def _char_reroll(self, name: str):
        rec = self.cfg.character(self.game, name)
        others = self._others(name)
        g = rec.get("gender", "unknown")
        pool = [p for p in self.cfg.settings.presets
                if g == "unknown" or p.get("gender") == g]
        used = {o.get("preset") for o in others.values()}
        cur = rec.get("preset", "")
        ids = [p["id"] for p in pool]
        nxt = ""
        if cur in ids:                      # next unused preset after the current one
            k = ids.index(cur)
            for p in ids[k + 1:] + ids[:k]:
                if p not in used:
                    nxt = p
                    break
        elif ids:
            nxt = next((p for p in ids if p not in used), "")
        if nxt:
            self.cfg.update_character(self.game, name, preset=nxt, voice_override="")
        else:                               # no free preset: new custom voice
            self.cfg.update_character(self.game, name, preset="", voice_override="",
                                      **voices.custom_design(name, g, others))
        self._char_refresh(name)
        self._preview(name)

    def _reassign_all(self):
        chars = self.cfg.characters(self.game)
        for name in list(chars):
            if name == NARRATOR:
                continue
            chars[name]["preset"] = None        # free all presets first
        for name in list(chars):
            if name == NARRATOR:
                continue
            g = chars[name].get("gender", "unknown")
            pid = voices.pick_preset(g, {k: v for k, v in chars.items() if k != name},
                                     self.cfg.settings.presets)
            chars[name].update(preset=pid, voice_override="")
            if not pid:
                chars[name].update(voices.custom_design(name, g, chars))
        self.cfg.save()
        self._reload_characters()
        for n in chars:
            self.bank.prewarm(self.cfg.design(self.game, n))
        self._set_status("Every character got a preset of their gender")

    def _accent_changed(self):
        self._reload_characters()
        self._reload_presets()
        for n in self.cfg.characters(self.game):
            self.bank.prewarm(self.cfg.design(self.game, n))

    # ============================================================= presets
    def _preset_design(self, pid: str) -> dict:
        return voices.effective({"preset": pid},
                                presets_mod.by_id(self.cfg.settings.presets),
                                self.cfg.settings.accent_override)

    def _ready_presets(self) -> set[str]:
        return {p["id"] for p in self.cfg.settings.presets
                if self.bank.cached(self._preset_design(p["id"]))}

    def _reload_presets(self):
        self.presets_view.load(self.cfg.settings.presets, self._ready_presets())
        self.chars.set_sources(self.cfg.settings.presets, self._vs_profiles, self._sapi_voices)

    def _refresh_ready(self):
        ready = self._ready_presets()
        for p in self.cfg.settings.presets:
            self.presets_view.update(p, p["id"] in ready)

    def _find_preset(self, pid: str) -> dict | None:
        return presets_mod.by_id(self.cfg.settings.presets).get(pid)

    def _preset_changed(self, pid: str, changes: dict):
        p = self._find_preset(pid)
        if not p:
            return
        p.update(changes)
        self.cfg.save()
        self.presets_view.update(p, bool(self.bank.cached(self._preset_design(pid))))
        if "name" in changes or "gender" in changes:
            self.chars.set_sources(self.cfg.settings.presets, self._vs_profiles,
                                   self._sapi_voices)
        self._reload_characters()
        if "name" not in changes:
            self.bank.prewarm(self._preset_design(pid))

    def _preset_reroll(self, pid: str):
        p = self._find_preset(pid)
        if not p:
            return
        self.bank.forget(self._preset_design(pid))
        p["seed"] = voices.fresh_seed({x.get("seed") for x in self.cfg.settings.presets})
        self.cfg.save()
        self._refresh_ready()
        self._reload_characters()
        self._preset_preview(pid)

    def _preset_preview(self, pid: str):
        p = self._find_preset(pid)
        if not p:
            return
        line = f"Hi, I'm {p['name']}. {p.get('note') or 'This is my voice'}."
        design = self._preset_design(pid)
        self.lbl_speaker.setText(p["name"])
        self.lbl_voice.setText(voices.instruct(design))
        self.lbl_text.setText(line)
        self.queue.say(Line(self.game, p["name"], line, {}, "en", design))

    def _preset_duplicate(self, pid: str):
        p = self._find_preset(pid)
        if p:
            np_ = presets_mod.new_preset(base=p)
            np_["seed"] = voices.fresh_seed({x.get("seed") for x in self.cfg.settings.presets})
            self.cfg.settings.presets.append(np_)
            self.cfg.save()
            self._reload_presets()

    def _preset_add(self, gender: str):
        np_ = presets_mod.new_preset(gender)
        np_["seed"] = voices.fresh_seed({x.get("seed") for x in self.cfg.settings.presets})
        self.cfg.settings.presets.append(np_)
        self.cfg.save()
        self._reload_presets()
        self._set_status("New preset added at the bottom of the list")

    def _preset_delete(self, pid: str):
        s = self.cfg.settings
        s.presets = [p for p in s.presets if p["id"] != pid]
        for game, chars in s.voices.items():
            for name, rec in chars.items():
                if rec.get("preset") == pid:
                    others = {k: v for k, v in chars.items() if k != name}
                    rec["preset"] = voices.pick_preset(rec.get("gender", "unknown"), others,
                                                       s.presets)
        self.cfg.save()
        self._reload_presets()
        self._reload_characters()

    def _preset_reset(self):
        s = self.cfg.settings
        s.presets = presets_mod.defaults()
        ids = {p["id"] for p in s.presets}
        for chars in s.voices.values():
            for name, rec in chars.items():
                if rec.get("preset") and rec["preset"] not in ids:
                    others = {k: v for k, v in chars.items() if k != name}
                    rec["preset"] = voices.pick_preset(rec.get("gender", "unknown"), others,
                                                       s.presets)
        self.cfg.save()
        self._reload_presets()
        self._reload_characters()
        self._set_status("Presets restored to the built-in 3 male + 8 female voices")

    # ============================================================ controls
    def _toggle_pause(self):
        self.player.paused = not self.player.paused
        self.btn_pause.setText("▶" if self.player.paused else "⏸")

    def _mute(self, on: bool):
        self.cfg.settings.muted = on
        self.btn_mute.setText("🔇" if on else "🔊")
        self.player.volume = 0.0 if on else self.cfg.settings.volume
        self.cfg.save()

    def _volume(self, v: int):
        self.cfg.settings.volume = v / 100
        if not self.cfg.settings.muted:
            self.player.volume = v / 100
        self.cfg.save()

    def _speed(self, v: int):
        self.cfg.settings.speed = v / 100
        self.lbl_speed.setText(f"Speed {v / 100:.2f}×")
        self.cfg.save()

    def _apply_hotkeys(self):
        hk = self.cfg.settings.hotkeys
        actions = {
            "toggle": (hk.get("toggle", ""), self._toggle_ocr),
            "replay": (hk.get("replay", ""), self.queue.replay),
            "pause": (hk.get("pause", ""), self._toggle_pause),
            "stop": (hk.get("stop", ""), self.queue.stop),
            "advance": (hk.get("advance", "F7"), self._toggle_auto),
        }
        hk = {a: seq for a, (seq, _fn) in actions.items()}
        errors = self.hotkeys.set(actions)
        parts = [f"{hk[a]} {label}" for a, label in
                 (("toggle", "start/stop"), ("replay", "replay"), ("pause", "pause"),
                  ("stop", "stop"), ("advance", "auto-advance")) if hk.get(a)]
        self.lbl_keys.setText("⌨  " + "  ·  ".join(parts) if parts else "")
        if errors:
            self._set_status("; ".join(errors))

    # ============================================================== voices
    def _vs_changed(self):
        s = self.cfg.settings
        self.vs.base_url = s.voicestudio_url.rstrip("/")
        self.vs.api_key = s.voicestudio_api_key
        self.vs.num_step = QUALITY_STEPS.get(s.vs_quality, 16)
        self.vs._ok_until = 0
        self._refresh_voices()
        self._check_health()

    def _refresh_voices(self):
        def work():
            profiles = []
            if self.vs.available():
                if not self._cleaned_legacy:
                    self._cleaned_legacy = True
                    n = self.bank.cleanup_legacy()
                    if n:
                        self.bridge.status.emit(f"Removed {n} old drifting voice(s) from "
                                                "VoiceStudio")
                profiles = self.vs.profiles()
                self.bridge.tags.emit(sorted(self.vs.expression_tags()))
            self.bridge.voices.emit(profiles, self.sapi.voices())
        threading.Thread(target=work, daemon=True).start()

    def _on_voices(self, profiles: list, sapi: list):
        self._vs_profiles, self._sapi_voices = profiles, sapi
        self.chars.set_sources(self.cfg.settings.presets, profiles, sapi)
        self._reload_cast()

    def _check_health(self):
        threading.Thread(target=lambda: self.bridge.health.emit(self.vs.available()),
                         daemon=True).start()

    def _on_health(self, ok: bool):
        was = self.pill_vs.objectName() == "pillOk"
        self.pill_vs.setText("● VoiceStudio connected" if ok else "● VoiceStudio offline")
        self.pill_vs.setObjectName("pillOk" if ok else "pillWarn")
        self.pill_vs.setToolTip("" if ok else "Using Windows voices until VoiceStudio is running")
        self.pill_vs.style().unpolish(self.pill_vs)
        self.pill_vs.style().polish(self.pill_vs)
        if ok and not was:
            self._refresh_voices()

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg, 8000)
        # The OCR thread may stop itself (e.g. OCR unavailable): keep the button honest.
        self._update_ocr_button()

    def closeEvent(self, e):
        self.ocr.stop()
        self.hotkeys.clear()
        self.queue.shutdown()
        self.player.close()
        self.cfg.save()
        super().closeEvent(e)
