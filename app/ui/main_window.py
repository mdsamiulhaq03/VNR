from __future__ import annotations

import copy
import os
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import presets as presets_mod
from .. import emotion, latency, name_colour, text_fixes, voices, winapi
from ..game_scripts import detect as game_detect
from ..game_scripts import renpy_hook
from ..game_scripts.renpy_script import Matcher, load_folder
from ..playtime import PlayClock, PlayLog, fmt_duration
from ..sources.hook_source import HookSource
from ..audio.player import Player
from ..config import NARRATOR, Config
from ..hotkeys import HotkeyManager
from ..name_split import split_name
from ..sources import ocr_filter, preprocess
from ..sources.capture import Capturer, CaptureSpec, frac_to_rect, rect_to_frac
from ..sources.ocr_source import OcrOptions, OcrSource, WindowsOcr, make_ocr
from ..speech_queue import Line, SpeechQueue
from ..text_clean import clean, clean_name, looks_wrong, split_sentences
from ..tts.sapi_fallback import SapiEngine
from ..tts.voicestudio import VoiceStudioEngine
from ..vs_launcher import AutoStarter
from ..voice_bank import VoiceBank
from . import theme
from .characters_view import CUSTOM, CharactersView
from .components import info as info_dialog
from .components import (Avatar, ColorDot, Dialog, IconLabel, SegmentedControl, button,
                         confirm, hline, icon_button, label, prompt, section, set_tone)
from .dashboard import (AreaPreview, CastList, LevelMeter, ProgressEdge, StatsLabel, Toast,
                        Transcript, character_menu, fade_in, match_sentence, sentence_html,
                        stats_chips)
from .shell import FadeStack, Page, Sidebar, StatusSegment, TabsAdapter
from .fixups_view import FixupsView
from .history_view import HistoryView
from .presets_view import PresetsView
from .region_picker import RegionPicker
from .screenshot_view import ScreenshotView
from .settings_view import QUALITY_STEPS, QUICK_STEPS, SettingsView
from .settings_view import SECTIONS as _SETTINGS_SECTIONS
from .games_view import GamesView
from .voice_picker import VoicePicker
from .chat_head import ChatHead
from .tray import Tray


class Bridge(QObject):
    """Thread -> UI signals."""
    lines = Signal(list, list, object)       # OCR lines, their boxes, name-area read
    candidate = Signal(list, list, object)   # same, first (unconfirmed) read: prefetch
    preview = Signal(object, list, object)   # captured image, line boxes, name image
    status = Signal(str)
    tags = Signal(list)              # VoiceStudio expression tags
    line_done = Signal()             # a line finished playing
    voices = Signal(list, list)      # VoiceStudio profiles, SAPI voices
    health = Signal(bool)
    voice_ready = Signal(str)        # voice key
    shot = Signal(object, object, str)   # screenshot image, ShotResult, error
    detected = Signal(object, object, str, str)   # name rect, text rect, name read, error
    moods_ready = Signal(str)                     # character whose mood voices are ready
    latency = Signal(float)                       # seconds from text on screen to voice
    hook_line = Signal(str, str, str, object)     # speaker, text, line id, extra (game hook)
    hook_named = Signal(str, str, str, object)    # same, the name read from the screen (OCR)
    hook_live = Signal(bool)                      # the game hook started / stopped talking
    script_ready = Signal(str, object)            # game, its Matcher (or None)


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


class CountdownBadge(QWidget):
    """"Auto-detect in 3…" at the top of the screen. Clicks go through to the game."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.text = ""
        g = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(g.left() + g.width() // 2 - 260, g.top() + 24, 520, 64)

    def set_text(self, text: str):
        self.text = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 15, 18, 225))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        p.setPen(QColor(theme.TEXT))
        f = p.font()
        f.setPointSizeF(13)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self.text)


def asset_path(name: str) -> str:
    """A file in assets/ (inside the exe when frozen)."""
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


def iso_lang(tag: str) -> str:
    return (tag or "en").split("-")[0].lower()


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.cfg = config
        s = config.settings
        self.setWindowTitle("VN Reader")
        self.resize(1180, 760)
        self.setMinimumSize(900, 600)
        self.game = s.ocr_profile
        self._flashes: list[QWidget] = []
        self._hwnd = 0
        self._vs_profiles: list = []
        self._sapi_voices: list = []
        self._cleaned_legacy = False

        self.bridge = Bridge()
        self.bridge.lines.connect(self._on_lines)
        self.bridge.candidate.connect(self._on_candidate)
        self.bridge.preview.connect(lambda img, boxes, name_img:
                                    self.preview.set_frame(img, boxes, name_img))
        self.bridge.tags.connect(lambda t: setattr(self.queue, "expression_tags", set(t)))
        self.bridge.status.connect(self._set_status)
        self.bridge.voices.connect(self._on_voices)
        self.bridge.health.connect(self._on_health)
        self.bridge.voice_ready.connect(lambda _k: self._refresh_ready())
        self.bridge.detected.connect(self._on_detected)

        self.player = Player()
        self.player.volume = 0.0 if s.muted else s.volume
        self.player.on_start = latency.audio_started      # timing log (latency.log)
        latency.on_result = lambda total, _text: self.bridge.latency.emit(total)
        self.bridge.latency.connect(self._on_latency)
        self.vs = VoiceStudioEngine(s.voicestudio_url, s.voicestudio_api_key)
        self.vs.num_step = QUALITY_STEPS.get(s.vs_quality)
        self.vs_starter = AutoStarter()
        self._vs_autostart_armed = True        # start VoiceStudio if it's off at launch
        self.sapi = SapiEngine()
        self.bank = VoiceBank(self.cfg, self.vs, on_status=self.bridge.status.emit,
                              on_ready=self.bridge.voice_ready.emit)
        self.queue = SpeechQueue(self.vs, self.sapi, self.player,
                                 speed_getter=lambda: self.cfg.settings.speed,
                                 on_status=self.bridge.status.emit, bank=self.bank)
        self.queue.expressive = lambda: self.cfg.settings.expressive
        self.queue.emotion_voices = lambda: self.cfg.settings.emotion_voices
        self.queue.vocalize = lambda: self.cfg.settings.vocalize
        self.queue.strength = lambda: self.cfg.settings.emotion_strength
        self.queue.quick_steps = self._quick_steps
        self.queue.on_done = lambda _line: self.bridge.line_done.emit()
        self.bridge.line_done.connect(self._schedule_advance)
        self._mood_demo: list = []                    # Cast → Moods: lines still to play
        self.bridge.line_done.connect(self._next_mood)
        self.bridge.moods_ready.connect(self._start_mood_demo)
        self._advance_token = 0
        self.ocr = OcrSource(self.bridge.lines.emit, on_status=self.bridge.status.emit,
                             on_preview=self.bridge.preview.emit,
                             on_candidate=self.bridge.candidate.emit)

        # Play time + per-game history (Games tab), kept next to settings.json.
        self.playlog = PlayLog(Path(self.cfg.path).parent / "playlog.json")
        self.clock: PlayClock | None = None
        self._line_source = "ocr"          # where the line being handled came from
        self._line_fixed = False           # ...and whether the script corrected the OCR
        # Text straight from the game: the Ren'Py hook, or the script files + OCR.
        self._matcher: Matcher | None = None
        self._matcher_game = ""
        self._hook_live = False
        self.hook = HookSource(self.bridge.hook_line.emit, self.bridge.hook_live.emit,
                               port=int(os.environ.get("VNR_HOOK_PORT") or renpy_hook.PORT))
        self.bridge.hook_line.connect(self._on_hook_line)
        self.bridge.hook_named.connect(self._on_hook_named)
        self.bridge.hook_live.connect(self._on_hook_live)
        self.bridge.script_ready.connect(self._on_script_ready)
        self.hook.start()

        self._build_ui()
        self._refresh_windows()
        self._refresh_recent()
        self._apply_capture()
        self._apply_ocr_options()
        self._refresh_voices()

        self._quitting = False
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = Tray(self, asset_path("vnr.png"))
            QApplication.instance().setQuitOnLastWindowClosed(False)
            self._apply_tray()
        self.settings_view.tray_changed.connect(self._apply_tray)
        self._build_chat_head()
        self.settings_view.chat_head_changed.connect(self._apply_chat_head)
        self.hotkeys = HotkeyManager(int(self.winId()))
        self._apply_hotkeys()

        self._health_timer = QTimer(self, interval=5000, timeout=self._check_health)
        self._health_timer.start()
        self._check_health()
        self._speak_timer = QTimer(self, interval=200, timeout=self._update_speaking)
        self._speak_timer.start()
        self._auto_paused = False        # paused by us because the game isn't in front
        self._was_paused = False         # ...and whether the voice was paused already
        self._away_timer = QTimer(self, interval=250, timeout=self._check_away)
        self._away_timer.start()

    # ================================================================== UI
    def _build_ui(self):
        s = self.cfg.settings
        root = QWidget()
        root.setObjectName("page")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- sidebar
        self.sidebar = Sidebar(asset_path("vnr.png"))
        outer.addWidget(self.sidebar)
        self.stack = FadeStack()
        outer.addWidget(self.stack, 1)

        nav = [("book-open", "Read", "Ctrl+1"), ("image", "Screenshot", "Ctrl+2"),
               ("users", "Cast", "Ctrl+3"), ("audio-lines", "Voices", "Ctrl+4"),
               ("replace", "Fix-ups", "Ctrl+5"), ("history", "History", "Ctrl+6"),
               ("gamepad-2", "Games", "Ctrl+7")]
        for ic, text, sc in nav:
            self.sidebar.add(ic, text, sc)

        # Game switcher at the bottom of the sidebar.
        self.game_box = QWidget()
        self.game_box.setObjectName("transparent")
        gl = QVBoxLayout(self.game_box)
        gl.setContentsMargins(2, 6, 2, 6)
        gl.setSpacing(4)
        gl.addWidget(section("Game"))
        gr = QHBoxLayout()
        gr.setSpacing(4)
        self.cb_recent = QComboBox()
        self.cb_recent.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_recent.setMinimumContentsLength(8)
        self.cb_recent.setToolTip("Switch game: restores its window, reading areas and "
                                  "characters")
        self.cb_recent.activated.connect(self._recent_chosen)
        gr.addWidget(self.cb_recent, 1)
        gr.addWidget(icon_button("play", "Play: start the game", lambda: self._launch_game(),
                                 size=15))
        b_gmenu = icon_button("ellipsis", "Game actions", size=15)
        b_gmenu.clicked.connect(lambda: self._game_menu(b_gmenu))
        gr.addWidget(b_gmenu)
        gl.addLayout(gr)
        self.sidebar.bottom.addWidget(self.game_box)
        self.sidebar.bottom.addWidget(hline())
        self.sidebar.bottom.addSpacing(4)
        self.sidebar.add("settings", "Settings", "Ctrl+,", bottom=True)
        # Appearance: Light / Dark / System (an icon that cycles when the sidebar is narrow).
        self.seg_theme = SegmentedControl(
            [("light", ""), ("dark", ""), ("system", "")], s.theme,
            icons={"light": "sun", "dark": "moon", "system": "monitor"})
        for k, tip in (("light", "Light"), ("dark", "Dark"), ("system", "Match Windows")):
            self.seg_theme._btns[k].setToolTip(f"Appearance: {tip}")
        self.seg_theme.changed.connect(self._set_theme)
        self.sidebar.bottom.addSpacing(6)
        self.sidebar.bottom.addWidget(self.seg_theme)
        self.b_theme_cycle = icon_button("monitor", "Appearance", self._cycle_theme, size=15)
        self.sidebar.bottom.addWidget(self.b_theme_cycle, 0, Qt.AlignHCenter)
        self.sidebar.compact_hide = [self.game_box, self.seg_theme]
        self.sidebar.compact_show = [self.b_theme_cycle]
        self._update_theme_cycle()

        # ---- pages
        self.read_page = self._build_read_page()

        self.shots = ScreenshotView(lambda: self.game)
        self.shots.read_request.connect(self._read_screenshot)
        self.shots.replay.connect(self._speak)
        self.bridge.shot.connect(self._on_shot)
        self.bridge.line_done.connect(self._shot_spoken)
        shots_page = Page("Screenshot")
        self.shots.install_toolbar(shots_page.header)
        shots_page.set_body(self.shots)

        self.chars = CharactersView(s.cast_group)
        self.chars.set_sources(s.presets, [], [])
        self.chars.changed.connect(self._char_changed)
        self.chars.voice_chosen.connect(self._char_voice)
        self.chars.pick_voice.connect(self._pick_voice)
        self.chars.role_changed.connect(self._char_role)
        self.chars.group_changed.connect(self._cast_group_changed)
        self.chars.gender_changed.connect(self._char_gender)
        self.chars.reroll.connect(self._char_reroll)
        self.chars.preview.connect(self._preview)
        self.chars.preview_moods.connect(self._preview_moods)
        self.chars.reassign_all.connect(self._reassign_all)
        self.chars.refresh.connect(self._refresh_voices)
        self.chars.menu.connect(self._char_menu)
        self.chars.merge_tagged.connect(self._merge_tagged)
        chars_page = Page("Cast")
        self.chars.install_toolbar(chars_page.header)
        chars_page.set_body(self.chars)

        self.presets_view = PresetsView()
        self.presets_view.changed.connect(self._preset_changed)
        self.presets_view.reroll.connect(self._preset_reroll)
        self.presets_view.preview.connect(self._preset_preview)
        self.presets_view.duplicate.connect(self._preset_duplicate)
        self.presets_view.delete.connect(self._preset_delete)
        self.presets_view.add.connect(self._preset_add)
        self.presets_view.reset.connect(self._preset_reset)
        voices_page = Page("Voices")
        self.presets_view.install_toolbar(voices_page.header)
        voices_page.set_body(self.presets_view)

        self.fixups = FixupsView(self.cfg, lambda: self.game,
                                 lambda: self.lbl_text.selectedText())
        fix_page = Page("Fix-ups")
        self.fixups.install_toolbar(fix_page.header)
        fix_page.set_body(self.fixups)

        self.history = HistoryView()
        self.history.replay_requested.connect(self._speak)
        hist_page = Page("History")
        self.history.install_toolbar(hist_page.header)
        hist_page.set_body(self.history)

        self.settings_view = SettingsView(self.cfg, self._grab_text_now, self._run_test_ocr,
                                          self._set_status)
        self.settings_view.ocr_options_changed.connect(self._apply_ocr_options)
        self.settings_view.hotkeys_changed.connect(self._apply_hotkeys)
        self.settings_view.voicestudio_changed.connect(self._vs_changed)
        self.settings_view.accent_changed.connect(self._accent_changed)
        self.settings_view.theme_changed.connect(self._set_theme)
        self.settings_view.float_style_changed.connect(self._set_float_style)
        self.settings_view.names_changed.connect(
            lambda on: (self.cb_names.blockSignals(True), self.cb_names.setChecked(on),
                        self.cb_names.blockSignals(False)))
        settings_page = Page("Settings")
        settings_page.set_body(self.settings_view)

        self.games_view = GamesView(self.cfg, self.playlog,
                                    lambda g, n: self._voice_label(n, g))
        self.games_view.continue_requested.connect(self._continue_game)
        self.games_view.folder_requested.connect(self._game_folder_dialog)
        self.games_view.hook_requested.connect(self._toggle_hook)
        self.games_view.character_requested.connect(self._show_cast_character)
        self.games_view.delete_requested.connect(self._delete_game)
        self.games_view.play_requested.connect(self._launch_game)
        self.games_view.rename_requested.connect(self._rename_game)
        games_page = Page("Games")
        games_page.set_body(self.games_view)

        pages = [self.read_page, shots_page, chars_page, voices_page, fix_page, hist_page,
                 games_page, settings_page]
        for p in pages:
            self.stack.addWidget(p)
        self.tabs = TabsAdapter(self.stack, self.sidebar, pages, {})
        self.tabs._widgets = {}
        for content, i in ((self.read_body, 0), (self.shots, 1), (self.chars, 2),
                           (self.presets_view, 3), (self.fixups, 4), (self.history, 5),
                           (self.games_view, 6), (self.settings_view, 7)):
            self.tabs.register(content, i)
        self.sidebar.page_requested.connect(self.tabs.setCurrentIndex)
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(
            lambda _i: self.tabs.currentWidget() is self.shots and self._warm_shot_engine())
        self.tabs.currentChanged.connect(
            lambda i: i == 6 and self.games_view.refresh())

        # Keyboard: pages, sidebar, inspector, start/stop, setup.
        for i, key in enumerate(["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6",
                                 "Ctrl+7", "Ctrl+,"]):
            QShortcut(QKeySequence(key), self, activated=lambda i=i: self.tabs.setCurrentIndex(i))
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.sidebar.toggle)
        QShortcut(QKeySequence("Ctrl+I"), self, activated=self._toggle_inspector)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._toggle_ocr)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self._open_setup)
        QShortcut(QKeySequence("F1"), self, activated=self._show_shortcuts)
        self.sidebar.collapse_toggled.connect(self._sidebar_toggled)
        self.sidebar.set_compact(bool(s.sidebar_collapsed))

        self.setCentralWidget(root)
        root.setFocusPolicy(Qt.ClickFocus)       # nothing shows a focus ring until Tab
        if theme.notifier is not None:
            theme.notifier.changed.connect(self._on_theme_changed)
        self._build_status_bar()
        self.lbl_keys = self.st_keys
        self.st_keys.setCursor(Qt.PointingHandCursor)
        self.st_keys.setToolTip("All keyboard shortcuts (F1)")
        self.st_keys.mousePressEvent = lambda e: self._show_shortcuts()
        self.toast = Toast(root)
        self._build_setup_dialog()
        self._load_game(self.game)
        self._reload_presets()
        # First launch (nothing set up yet): the setup guide.
        if (not s.setup_done and not s.target_window and not self.ocr.spec.text
                and not os.environ.get("VNR_NO_SETUP_GUIDE")):
            QTimer.singleShot(700, self._open_setup_guide)

    def _open_setup_guide(self):
        from .setup_wizard import SetupWizard
        if getattr(self, "_guide", None) is None:
            self._guide = SetupWizard(self)
        self._guide.go(0)
        self._guide.show()
        self._guide.raise_()
        self._guide.activateWindow()

    def _build_status_bar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)
        self.st_state = StatusSegment(dot=True)
        self.st_game = StatusSegment("gamepad-2")
        self.st_ocr = StatusSegment("scan-text")
        self.st_src = StatusSegment("book-open")
        self.st_vs = StatusSegment(dot=True)
        self.st_msg = QLabel("")
        self.st_keys = QLabel("")
        for w in (self.st_state, self.st_game, self.st_ocr, self.st_src, self.st_vs):
            sb.addWidget(w)
        sb.addWidget(self.st_msg, 1)
        sb.addPermanentWidget(self.st_keys)
        self.st_vs.set("VoiceStudio…", "TEXT_3")
        self.st_vs.set_clickable(True)
        self.st_vs.clicked.connect(self._vs_menu)
        self.pill_vs = self.st_vs          # (older code paths)
        self._vs_ok = False

    def _sidebar_toggled(self, compact: bool):
        self.cfg.settings.sidebar_collapsed = compact
        self.cfg.save()

    # ---- appearance ----
    def _set_theme(self, pref: str):
        """Light / Dark / System: saved, applied at once (cross-fade, no restart)."""
        if pref not in theme.MODES:
            return
        self.cfg.settings.theme = pref
        self.cfg.save()
        theme.set_mode(pref)
        if self.seg_theme.value() != pref:
            self.seg_theme.set_value(pref)
        if hasattr(self, "settings_view") and hasattr(self.settings_view, "seg_theme"):
            self.settings_view.seg_theme.set_value(pref)
        self._update_theme_cycle()

    def _on_theme_changed(self, _mode: str):
        """Things built from colour values (rich text) are rebuilt in the new theme."""
        self._update_checklist()
        self._update_timer()
        self._update_theme_cycle()
        if hasattr(self, "_last_shown"):
            self._show_line(*self._last_shown)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self.centralWidget() and self.centralWidget().setFocus())

    def _cycle_theme(self):
        order = ["light", "dark", "system"]
        cur = self.cfg.settings.theme if self.cfg.settings.theme in order else "system"
        self._set_theme(order[(order.index(cur) + 1) % 3])

    def _update_theme_cycle(self):
        pref = self.cfg.settings.theme
        ic = {"light": "sun", "dark": "moon"}.get(pref, "monitor")
        self.b_theme_cycle.setIcon(theme.icon(ic, "TEXT_2", 15, active="TEXT"))
        self.b_theme_cycle.setToolTip({"light": "Appearance: Light", "dark": "Appearance: Dark"}
                                      .get(pref, "Appearance: match Windows"))

    def resizeEvent(self, e):
        """Narrow windows: collapse the sidebar to icons and drop secondary text, so the
        toolbar and transport never clip. The user's own sidebar choice is kept."""
        super().resizeEvent(e)
        if not hasattr(self, "sidebar"):
            return
        w = self.width()
        narrow = w < 1060
        want = bool(self.cfg.settings.sidebar_collapsed) or narrow
        if self.sidebar.compact != want:
            self.sidebar.set_compact(want)
        if hasattr(self, "lbl_stats"):
            self.lbl_stats.setVisible(w >= 1180)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.move(self.toast.target())

    def _game_menu(self, anchor):
        m = QMenu(self)
        m.addAction(theme.icon("play", "TEXT_2", active="ON_ACCENT"), "Play game", self._launch_game)
        m.addSeparator()
        m.addAction(theme.icon("settings", "TEXT_2", active="ON_ACCENT"), "Game setup…   Ctrl+Shift+S",
                    self._open_setup)
        m.addAction(theme.icon("scan-text", "TEXT_2", active="ON_ACCENT"), "Auto-detect name + dialogue areas",
                    self._auto_detect_areas)
        m.addAction(theme.icon("crosshair", "TEXT_2", active="ON_ACCENT"), "Pick name + dialogue areas   F6",
                    self._pick_areas_sequence)
        m.addSeparator()
        m.addAction(theme.icon("folder-open", "TEXT_2", active="ON_ACCENT"), "Game folder (read its script)…",
                    self._choose_game_folder)
        m.addAction(theme.icon("square-pen", "TEXT_2", active="ON_ACCENT"), "Rename game…", self._rename_game)
        info = self.cfg.game_info(self.game)
        if info.get("engine") == "renpy" and info.get("path"):
            on = renpy_hook.installed(info["path"])
            m.addAction(theme.icon("x" if on else "zap", "TEXT_2", active="ON_ACCENT"),
                        "Remove game hook" if on else "Install game hook (exact text, no OCR)",
                        lambda: self._toggle_hook(self.game))
        src = m.addMenu(theme.icon("book-open", "TEXT_2", active="ON_ACCENT"), "Text source")
        cur = info.get("text_source", "auto")
        for key, text in (("auto", "Automatic (game hook → script → OCR)"),
                          ("files", "Script files + OCR (ignore the hook)"),
                          ("ocr", "OCR only")):
            a = src.addAction(text, lambda k=key: self._set_text_source(self.game, k))
            a.setCheckable(True)
            a.setChecked(cur == key)
        m.addSeparator()
        m.addAction(theme.icon("trash-2", "DANGER", active="ON_ACCENT"), "Delete this game…", self._delete_game)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ------------------------------------------------------------ Read page
    def _build_read_page(self) -> QWidget:
        s = self.cfg.settings
        page = Page(self.game)
        self.lbl_game = page.header.title
        self.lbl_game.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.lbl_game.setMinimumWidth(60)
        self.lbl_stats = StatsLabel()
        page.header.actions.insertWidget(1, self.lbl_stats)
        self.lbl_timer = label("", "number")
        page.header.actions.insertWidget(2, self.lbl_timer)
        self.btn_ocr = QPushButton()
        self.btn_ocr.setCursor(Qt.PointingHandCursor)
        self.btn_ocr.setMinimumWidth(150)
        self.btn_ocr.clicked.connect(self._toggle_ocr)
        self.cb_auto = QCheckBox("Auto-advance")
        self.cb_auto.setToolTip("When a line has been read, go to the next one automatically "
                                "(F7). Key and delay: Settings → Auto-advance.")
        self.cb_auto.setChecked(s.auto_advance)
        self.cb_auto.toggled.connect(self._auto_toggled)
        self.cb_names = QCheckBox("Speak names")
        self.cb_names.setToolTip("On: “Luna.” is said before Luna's line. "
                                 "Off: only the dialogue is spoken.")
        self.cb_names.setChecked(s.read_names)
        self.cb_names.toggled.connect(self._read_names_toggled)
        self.btn_areas = icon_button("crosshair", "Pick name + dialogue areas from the game (F6)",
                                     self._pick_areas_sequence)
        self.btn_detect = icon_button("scan-text", "Auto-detect the name + dialogue areas: "
                                      "this window minimizes, then the game is read after "
                                      "3 seconds (have a line of dialogue on screen)",
                                      self._auto_detect_areas)
        self.btn_setup = icon_button("settings", "Game setup: window, profile, language, "
                                     "areas   Ctrl+Shift+S", self._open_setup)
        self.btn_inspector = icon_button("panel-right", "Show / hide the inspector   Ctrl+I",
                                         self._toggle_inspector, checkable=True)
        self.btn_inspector.setChecked(s.inspector_open)
        page.header.add(self.cb_auto, self.cb_names, None, self.btn_detect, self.btn_areas,
                        self.btn_setup,
                        self.btn_inspector, None, self.btn_ocr)

        # ---- body: workspace | inspector
        self.read_split = QSplitter(Qt.Horizontal)
        self.read_split.setChildrenCollapsible(False)
        self.read_split.setHandleWidth(1)
        self.read_body = self.read_split
        page.set_body(self.read_split)

        ws = QWidget()
        ws.setObjectName("workspace")
        wl = QVBoxLayout(ws)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        # Now playing: who speaks, how (mood), the line with the sentence being spoken
        # highlighted, and a progress line along the card's bottom edge.
        now = QWidget()
        now.setObjectName("workspace")
        nl = QVBoxLayout(now)
        nl.setContentsMargins(24, 20, 24, 12)
        nl.setSpacing(0)
        card = QFrame()
        card.setObjectName("nowCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 0)
        cl.setSpacing(12)
        who = QHBoxLayout()
        who.setSpacing(14)
        self.av = Avatar(NARRATOR, 48)
        who.addWidget(self.av)
        wv = QVBoxLayout()
        wv.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_speaker = QLabel("Nobody yet")
        self.lbl_speaker.setObjectName("speaker")
        top.addWidget(self.lbl_speaker)
        self.mood_pill = label("", "pill")
        self.mood_pill.setProperty("tone", "accent")
        self.mood_pill.hide()
        top.addWidget(self.mood_pill)
        top.addStretch(1)
        wv.addLayout(top)
        self.lbl_voice = QLabel("")
        self.lbl_voice.setObjectName("footnote")
        self.lbl_voice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wv.addWidget(self.lbl_voice)
        who.addLayout(wv, 1)
        self.meter = LevelMeter()
        who.addWidget(self.meter, 0, Qt.AlignVCenter)
        cl.addLayout(who)
        self.lbl_text = QLabel("Pick your game, press F6 in the game to mark the name and "
                               "dialogue, then start reading.")
        self.lbl_text.setObjectName("dialogue")
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setMinimumHeight(64)
        self.lbl_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.lbl_text)
        cl.addSpacing(6)
        self.progress = ProgressEdge()
        cl.addWidget(self.progress)
        nl.addWidget(card)
        wl.addWidget(now)
        tr_head = QHBoxLayout()
        tr_head.setContentsMargins(28, 12, 28, 4)
        tr_head.addWidget(section("Transcript"))
        tr_head.addStretch(1)
        tr_head.addWidget(label("Click a line to hear it again", "footnote"))
        wl.addLayout(tr_head)
        self.transcript = Transcript()
        self.transcript.replay.connect(self._speak)
        wl.addWidget(self.transcript, 1)

        # Transport bar
        tb = QWidget()
        tb.setObjectName("transport")
        tl = QHBoxLayout(tb)
        tl.setContentsMargins(20, 8, 20, 8)
        tl.setSpacing(4)
        self.btn_pause = icon_button("pause", "Pause / resume   F11", self._toggle_pause)
        b_stop = icon_button("square", "Stop speaking   F8", self.queue.stop)
        b_replay = icon_button("rotate-ccw", "Replay last line   F10", self.queue.replay)
        self.btn_mute = icon_button("volume-x" if s.muted else "volume-2", "Mute",
                                    checkable=True)
        self.btn_mute.setChecked(s.muted)
        self.btn_mute.toggled.connect(self._mute)
        for w in (self.btn_pause, b_stop, b_replay):
            tl.addWidget(w)
        tl.addSpacing(10)
        tl.addWidget(self.btn_mute)
        self.sl_vol = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=int(s.volume * 100))
        self.sl_vol.setToolTip("Volume")
        self.sl_vol.setMinimumWidth(60)
        self.sl_vol.setMaximumWidth(140)
        self.sl_vol.valueChanged.connect(self._volume)
        tl.addWidget(self.sl_vol)
        tl.addSpacing(18)
        tl.addWidget(label("Speed", "muted"))
        self.sl_speed = QSlider(Qt.Horizontal, minimum=50, maximum=200, value=int(s.speed * 100))
        self.sl_speed.setMinimumWidth(60)
        self.sl_speed.setMaximumWidth(140)
        self.sl_speed.valueChanged.connect(self._speed)
        tl.addWidget(self.sl_speed)
        self.lbl_speed = QLabel(f"{s.speed:.2f}×")
        self.lbl_speed.setObjectName("number")
        self.lbl_speed.setFixedWidth(44)
        tl.addWidget(self.lbl_speed)
        tl.addStretch(1)
        wl.addWidget(tb)
        self.read_split.addWidget(ws)

        # ---- inspector
        insp = QWidget()
        insp.setObjectName("inspector")
        insp.setAttribute(Qt.WA_StyledBackground, True)
        il = QVBoxLayout(insp)
        il.setContentsMargins(16, 16, 16, 12)
        il.setSpacing(10)
        lh = QHBoxLayout()
        lh.addWidget(section("Live area"))
        lh.addStretch(1)
        for role, text in (("NAME", "Name"), ("DIALOGUE", "Dialogue")):
            lh.addWidget(ColorDot(role, 8))
            lh.addWidget(label(text, "footnote"))
            lh.addSpacing(6)
        il.addLayout(lh)
        self.preview = AreaPreview()
        self.preview.setMinimumHeight(110)
        il.addWidget(self.preview, 2)

        il.addWidget(section("Areas"))
        self.lbl_name_area = QLabel()
        self.lbl_name_area.setObjectName("footnote")
        self.lbl_region = QLabel()
        self.lbl_region.setObjectName("footnote")
        for lbl, which, tip in ((self.lbl_name_area, "name", "name"),
                                (self.lbl_region, "text", "dialogue")):
            r = QHBoxLayout()
            r.setSpacing(4)
            r.addWidget(ColorDot("NAME" if which == "name" else "DIALOGUE", 8))
            r.addSpacing(4)
            r.addWidget(lbl, 1)
            r.addWidget(icon_button("pencil", f"Draw the {tip} area",
                                    lambda _=False, w=which: self._pick_region(w), size=14))
            if which == "name":
                self.btn_clear_name = icon_button("x", "Remove the name area (the name is "
                                                  "then taken from the dialogue's top line)",
                                                  self._clear_name_area, size=14)
                r.addWidget(self.btn_clear_name)
            else:
                r.addWidget(icon_button("eye", "Show both areas on the screen",
                                        self._flash_region, size=14))
            il.addLayout(r)
        self.lbl_check = QLabel()
        self.lbl_check.setObjectName("checklist")
        self.lbl_check.setWordWrap(True)
        il.addWidget(self.lbl_check)
        il.addWidget(button("Setup guide", "sparkles", "ghost",
                            tip="Step by step: game window, text areas, VoiceStudio, a test "
                                "line", slot=self._open_setup_guide), 0, Qt.AlignLeft)
        il.addWidget(hline())
        il.addWidget(section("Cast"))
        self.cast = CastList()
        self.cast.preview.connect(self._preview)
        self.cast.menu.connect(self._char_menu)
        il.addWidget(self.cast, 3)
        self.inspector = insp
        self.read_split.addWidget(insp)
        self.read_split.setStretchFactor(0, 1)
        self.read_split.setStretchFactor(1, 0)
        insp.setMinimumWidth(260)
        self.read_split.setSizes([700, int(s.inspector_width or 320)])
        self.read_split.splitterMoved.connect(self._inspector_moved)
        insp.setVisible(bool(s.inspector_open))

        return page

    def _toggle_inspector(self):
        on = not self.inspector.isVisible()
        self.inspector.setVisible(on)
        self.btn_inspector.setChecked(on)
        self.cfg.settings.inspector_open = on
        self.cfg.save()

    def _inspector_moved(self, *_):
        if self.inspector.isVisible():
            self.cfg.settings.inspector_width = self.inspector.width()
            self.cfg.save()

    # ------------------------------------------------------------ game setup
    def _build_setup_dialog(self):
        s = self.cfg.settings
        d = Dialog(self, "Game setup", "Pick the game's window (or its folder): VN Reader "
                   "picks the game by itself. Then mark where the name and dialogue appear.",
                   "settings")
        d.setMinimumWidth(560)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        wr = QHBoxLayout()
        self.cb_window = QComboBox()
        self.cb_window.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_window.setMinimumContentsLength(24)
        self.cb_window.setToolTip("Pick the game window so the areas follow it when it moves "
                                  "or resizes")
        self.cb_window.activated.connect(self._window_chosen)
        wr.addWidget(self.cb_window, 1)
        wr.addWidget(icon_button("refresh-cw", "Refresh the window list",
                                 self._refresh_windows, size=15))
        wr.addWidget(button("Game folder…", "folder-open",
                            tip="Pick the folder with the game's .exe: VN Reader picks the "
                                "game and, for Ren'Py games, reads its script",
                            slot=self._choose_game_folder))
        form.addRow(label("Game window", "muted"), wr)
        gr = QHBoxLayout()
        gr.setSpacing(10)
        self.lbl_setup_game = label("", "strong")
        self.lbl_setup_game.setToolTip("Characters, voices, fix-ups and stats are kept per "
                                       "game. It's chosen from the window or folder.")
        gr.addWidget(self.lbl_setup_game, 1)
        gr.addWidget(button("Rename…", "square-pen", "ghost", tip="Rename this game",
                            slot=self._rename_game))
        gr.addWidget(label("Language", "muted"))
        self.ed_lang = QLineEdit(s.ocr_language)
        self.ed_lang.setToolTip("OCR language tag, e.g. en-US, ja, zh-Hans")
        self.ed_lang.setFixedWidth(84)
        self.ed_lang.editingFinished.connect(self._lang_changed)
        gr.addWidget(self.ed_lang)
        form.addRow(label("Game", "muted"), gr)
        self.cb_bg = QCheckBox("Keep reading when other windows cover the game")
        self.cb_bg.setChecked(s.background_capture)
        self.cb_bg.toggled.connect(self._bg_toggled)
        form.addRow("", self.cb_bg)
        d.content.addLayout(form)
        d.content.addSpacing(6)
        d.content.addWidget(section("Status"))
        self.lbl_setup_areas = QLabel()
        self.lbl_setup_areas.setObjectName("checklist")
        self.lbl_setup_areas.setWordWrap(True)
        d.content.addWidget(self.lbl_setup_areas)
        ar = QHBoxLayout()
        ar.addWidget(button("Auto-detect", "scan-text", "primary",
                            "Minimizes this window, waits 3 s, then finds the name and the "
                            "dialogue in the game by itself",
                            lambda: (d.accept(), self._auto_detect_areas())))
        ar.addWidget(button("Pick both from the game", "crosshair", "",
                            "Drag the NAME box, then the DIALOGUE box (F6 in the game)",
                            lambda: (d.accept(), self._pick_areas_sequence())))
        ar.addWidget(button("Name area", "pencil", tip="Draw only the name area",
                            slot=lambda: (d.accept(), self._pick_region("name"))))
        ar.addWidget(button("Dialogue area", "pencil", tip="Draw only the dialogue area",
                            slot=lambda: (d.accept(), self._pick_region("text"))))
        ar.addWidget(button("Show", "eye", tip="Outline both areas on the screen",
                            slot=self._flash_region))
        ar.addStretch(1)
        d.content.addLayout(ar)
        d.add_button("Done", "primary")
        self.setup_dlg = d
        self.setup_card = d                 # (older code paths)

    def _open_setup(self):
        self._refresh_windows()
        self._update_checklist()
        self._update_setup_game()
        self.setup_dlg.show()
        self.setup_dlg.raise_()
        self.setup_dlg.activateWindow()


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
        self._set_target_window(self.cb_window.currentData())

    def _set_target_window(self, w: "winapi.WindowInfo | None", refresh_list: bool = False):
        """Follow this game window (None = whole screen). Picks the game profile this exe
        was saved under before, else one named after the exe."""
        s = self.cfg.settings
        if w is None:
            s.target_window = None
            self._hwnd = 0
        else:
            same = (s.target_window or {}).get("exe", "").lower() == w.exe.lower()
            s.target_window = {"title": w.title, "exe": w.exe}
            self._hwnd = w.hwnd
            if not same:
                # Re-use the profile this exe was saved under before, else its exe name.
                known = [g for g, i in s.games.items()
                         if i.get("exe", "").lower() == w.exe.lower()]
                self._use_game(known[0] if known else self._title_name(w.title, w.exe))
            self.cfg.touch_game(self.game, w.exe, w.title)
            self._refresh_recent()
        self.cfg.save()
        if refresh_list:
            self._refresh_windows()
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
                               self._hwnd, s.background_capture, regs.get("name"))
        return CaptureSpec("screen", s.ocr_text_region, name=s.ocr_name_region)

    def _apply_capture(self):
        self.ocr.spec = self._spec()
        spec = self.ocr.spec
        where = "follows the game window" if spec.mode == "window" else "fixed on screen"
        self.lbl_region.setText(("Dialogue: " + ("follows the game window"
                                                 if spec.mode == "window" else "on screen"))
                                if spec.text else "Dialogue: not set")
        self.lbl_region.setToolTip(f"Dialogue area: {where}" if spec.text else
                                   "No dialogue area yet: press F6 in the game")
        if hasattr(self, "btn_clear_name"):
            self.btn_clear_name.setEnabled(bool(spec.name))
            self.lbl_name_area.setText("Name: its own area" if spec.name
                                       else "Name: top line of the dialogue")
            self.lbl_name_area.setToolTip(
                "Names are read only from the name area" if spec.name else
                "No name area: the name is taken from the top line of the dialogue area")
        self._update_checklist()

    def _update_checklist(self):
        """One line under Setup: what's ready and what's missing."""
        if not hasattr(self, "lbl_check"):
            return
        spec = self.ocr.spec
        vs = getattr(self, "_vs_ok", False)
        ok = lambda b: f"<span style='color:{theme.OK}'>✓</span>" if b else \
            f"<span style='color:{theme.TEXT_3}'>○</span>"
        win = bool(self.cfg.settings.target_window)
        key = self.cfg.settings.hotkeys.get("areas", "F6") or "the crosshair button"
        rows = [(vs, "VoiceStudio", "" if vs else "offline, Windows voices are used"),
                (win, "Game window", "" if win else "optional, the whole screen is read"),
                (bool(spec.text), "Dialogue area", "" if spec.text else f"press {key} in the game"),
                (bool(spec.name), "Name area", "" if spec.name else "optional")]
        html = "<br>".join(
            f"{ok(b)}&nbsp; <span style='color:{theme.TEXT}'>{t}</span>" +
            (f"<span style='color:{theme.TEXT_2}'>: {h}</span>" if h else "")
            for b, t, h in rows)
        self.lbl_check.setText(html)
        if hasattr(self, "lbl_setup_areas"):
            self.lbl_setup_areas.setText(html)
        if hasattr(self, "st_ocr"):
            from ..sources.ocr_source import _is_english
            s = self.cfg.settings
            paddle = s.ocr_engine == "paddle" and _is_english(s.ocr_language)
            self.st_ocr.set(("PaddleOCR" if paddle else "Windows OCR") +
                            (" · GPU" if paddle and s.ocr_gpu else ""),
                            tip="Text recognition engine (Settings → Text recognition)")
            self.st_game.set(self.game, tip="Current game profile")

    # ================================================================ area
    # ---- F6: both areas, one after the other, straight from the game ----
    def _pick_areas_sequence(self):
        """Step 1: the NAME box (Enter skips it). Step 2: the DIALOGUE box. The window
        under the dialogue box becomes the game window, and both areas are saved
        relative to it. Nothing changes if Esc is pressed at any step."""
        if getattr(self, "_picking", False):
            return
        self._picking = True
        was_visible = self.isVisible() and not self.isMinimized()
        self.hide()
        state: dict = {}

        def finish(ok: bool):
            self._picking = False
            if ok:   # find the game window *before* our own window comes back over it
                self._save_areas(state.get("name"), state["text"])
            if was_visible:                    # from the game (F6): stay out of the way
                self.show()

        def step2():
            keep = [(state["name"], "NAME", theme.NAME)] if state.get("name") else []
            self._picker = RegionPicker(
                "Drag a box around the dialogue text only.",
                title="Now the DIALOGUE area", color=theme.DIALOGUE, step=2, total=2, keep=keep)
            self._picker.picked.connect(lambda r: (state.__setitem__("text", r), finish(True)))
            self._picker.cancelled.connect(lambda: finish(False))
            self._picker.show()

        def got_name(rect):
            state["name"] = rect
            QTimer.singleShot(60, step2)

        def skip_name():
            state["name"] = None
            QTimer.singleShot(60, step2)

        self._picker = RegionPicker(
            "Drag a box around where the speaker's NAME appears (a bit wider than long "
            "names). No separate name box in this game? Press Enter.",
            title="First the NAME area", color=theme.NAME, step=1, total=2, skippable=True)
        self._picker.picked.connect(got_name)
        self._picker.skipped.connect(skip_name)
        self._picker.cancelled.connect(lambda: finish(False))
        QTimer.singleShot(150, self._picker.show)

    # ---- Auto-detect: minimize, count down, read the whole game, find both areas ----
    DETECT_DELAY = 3

    def _auto_detect_areas(self):
        if getattr(self, "_picking", False) or getattr(self, "_detecting", False):
            return
        self._detecting = True
        self._detect_restore = self.isVisible() and not self.isMinimized()
        self._warm_shot_engine()                     # loads while we count down
        if self._detect_restore:
            self.showMinimized()
        self._badge = CountdownBadge()
        self._badge.show()
        self._detect_tick(self.DETECT_DELAY)

    def _detect_tick(self, left: int):
        if left > 0:
            self._badge.set_text(f"Auto-detect in {left}…  click the game, show a line of "
                                 "dialogue")
            QTimer.singleShot(1000, lambda: self._detect_tick(left - 1))
            return
        self._badge.hide()                           # not in the screenshot
        QTimer.singleShot(150, self._detect_capture)

    def _detect_capture(self):
        """The game window if it's in front (else the screen under the mouse) is read on
        a worker thread."""
        fg = winapi.foreground_window()
        frame = winapi.client_rect_screen(fg.hwnd) if fg else None
        if not frame or frame[2] < 200 or frame[3] < 150:
            frame = None
        opts = copy.copy(self.ocr.opts)
        self._badge.set_text("Reading the game…")
        self._badge.show()

        def work():
            import mss
            from ..sources.screenshot_reader import areas_from_result, auto_detect
            try:
                f = frame
                if f is None:                        # no game window: the monitor in use
                    x, y = winapi.cursor_pos()
                    with mss.mss() as m:
                        mons = m.monitors[1:] or m.monitors
                        mon = next((mo for mo in mons if mo["left"] <= x < mo["left"] +
                                    mo["width"] and mo["top"] <= y < mo["top"] +
                                    mo["height"]), mons[0])
                    f = (mon["left"], mon["top"], mon["width"], mon["height"])
                cap = Capturer()
                try:
                    img = cap._grab_screen(list(f))
                finally:
                    cap.close()
                ocr = make_ocr(opts, limit=1920, gpu=True)
                res = auto_detect(img, ocr, opts)
                name, text = areas_from_result(res, img.size)
                if not text:
                    self.bridge.detected.emit(None, None, "", "No dialogue found")
                    return
                off = lambda r: [r[0] + f[0], r[1] + f[1], r[2], r[3]] if r else None
                self.bridge.detected.emit(off(name), off(text), res.info.get("name") or "", "")
            except Exception as e:
                self.bridge.detected.emit(None, None, "", str(e))
        threading.Thread(target=work, name="auto-detect", daemon=True).start()

    def _on_detected(self, name_rect, text_rect, name: str, error: str):
        self._detecting = False
        if getattr(self, "_badge", None):
            self._badge.close()
            self._badge = None
        if error or not text_rect:
            if self._detect_restore:
                self.showNormal()
                self.activateWindow()
            self._toast(f"Auto-detect: {error or 'nothing found'}. Show a line of dialogue "
                        "in the game and try again, or draw the areas with F6.", theme.DANGER)
            return
        self._save_areas(name_rect, text_rect)        # finds the game window + flashes them
        if name:
            self._set_status(f"Auto-detected the name area (“{name}”) and the dialogue area. "
                             "Not quite right? Press F6 to draw them.")
        else:
            self._set_status("Auto-detected the dialogue area (no separate name box: the "
                             "name is read from its top line). Press F6 to adjust.")
        if self._detect_restore:                      # after the outlines have been seen
            QTimer.singleShot(1800, lambda: (self.showNormal(), self.activateWindow()))

    def _save_areas(self, name_rect, text_rect):
        """Save both areas, following the window under the dialogue box if there is one."""
        s = self.cfg.settings
        cx, cy = text_rect[0] + text_rect[2] // 2, text_rect[1] + text_rect[3] // 2
        w = winapi.window_at(cx, cy)
        client = winapi.client_rect_screen(w.hwnd) if w else None
        if w and client and client[2] > 0 and client[3] > 0:
            self._set_target_window(w, refresh_list=True)
            regs = self.cfg.window_regions(w.exe)
            regs["aspect"] = client[2] / client[3]          # for the Screenshot tab
            regs["text"] = rect_to_frac(text_rect, client)
            regs["name"] = rect_to_frac(name_rect, client) if name_rect else None
            where = f"following “{w.title}”"
        else:
            s.ocr_text_region = text_rect
            s.ocr_name_region = name_rect
            if s.target_window:
                self._set_target_window(None, refresh_list=True)
            where = "fixed on screen"
        self.cfg.save()
        self._apply_capture()
        self._flash_region()
        self._toast(("Name + dialogue areas saved" if name_rect else "Dialogue area saved "
                     "(name from its top line)") + f" · {where}")

    def _pick_region(self, which: str = "text"):
        if self.cfg.settings.target_window and not self._resolve_hwnd():
            self._set_status("The game window isn't open: start the game or pick it again")
            return
        self.hide()
        if which == "name":
            title, color = "Draw the NAME area", theme.NAME
            hint = "Drag a box around where the speaker's name appears (a bit wider than " \
                   "long names)."
        elif self._spec().name:
            title, color = "Draw the DIALOGUE area", theme.DIALOGUE
            hint = "Drag a box around the dialogue text only (the name has its own area)."
        else:
            title, color = "Draw the DIALOGUE area", theme.DIALOGUE
            hint = "Drag one box around the dialogue box, including the name line on top."
        keep = []
        other = self._screen_rect("text" if which == "name" else "name")
        if other:
            keep = [(other, "DIALOGUE" if which == "name" else "NAME",
                     theme.DIALOGUE if which == "name" else theme.NAME)]
        self._picker = RegionPicker(hint, title=title, color=color, keep=keep)

        def done(rect):
            s = self.cfg.settings
            if s.target_window:
                hwnd = self._resolve_hwnd()
                client = winapi.client_rect_screen(hwnd) if hwnd else None
                if not client or client[2] <= 0:
                    self._set_status("Lost the game window: try again")
                else:
                    regs = self.cfg.window_regions(s.target_window["exe"])
                    regs[which] = rect_to_frac(rect, client)
                    regs["aspect"] = client[2] / client[3]
            elif which == "name":
                s.ocr_name_region = rect
            else:
                s.ocr_text_region = rect
            self.cfg.save()
            self._apply_capture()
            self.show()
            self._flash_region()
            if which == "name":
                self._set_status("Name area set: speaker names are now read only from there")

        self._picker.picked.connect(done)
        self._picker.cancelled.connect(self.show)
        QTimer.singleShot(200, self._picker.show)

    def _clear_name_area(self):
        s = self.cfg.settings
        if s.target_window:
            self.cfg.window_regions(s.target_window["exe"])["name"] = None
        else:
            s.ocr_name_region = None
        self.cfg.save()
        self._apply_capture()
        self._set_status("Name area removed: the name is taken from the top line again")

    def _screen_rect(self, which: str = "text"):
        spec = self.ocr.spec
        area = spec.name if which == "name" else spec.text
        if not area:
            return None
        if spec.mode == "screen":
            return area
        hwnd = self._resolve_hwnd()
        return frac_to_rect(area, winapi.client_rect_screen(hwnd)) if hwnd else None

    def _flash_region(self):
        rect = self._screen_rect()
        if not rect:
            self._set_status("No area to show (or the game window isn't open)")
            return
        self._flashes = [AreaFlash(rect, QColor(theme.DIALOGUE))]
        name = self._screen_rect("name")
        if name:
            self._flashes.append(AreaFlash(name, QColor(theme.NAME)))
        for f in self._flashes:
            f.show()

    # ================================================================= OCR
    def _update_ocr_button(self):
        if not hasattr(self, "btn_ocr"):
            return
        running = self.ocr.running
        key = self.cfg.settings.hotkeys.get("toggle", "")
        self.btn_ocr.setText(("Stop reading" if running else "Start reading") +
                             (f"   {key}" if key else ""))
        name = "" if running else "primary"
        if self.btn_ocr.objectName() != name:
            self.btn_ocr.setObjectName(name)
            self.btn_ocr.style().unpolish(self.btn_ocr)
            self.btn_ocr.style().polish(self.btn_ocr)
        self.btn_ocr.setIcon(theme.icon("square" if running else "play",
                                        "TEXT" if running else "ON_ACCENT", 15))
        if hasattr(self, "st_state"):
            self.st_state.set("Reading" if running else "Stopped",
                              theme.OK if running else theme.TEXT_3, pulse=running)
        if hasattr(self, "tray"):
            self.tray.update_state()
        self._update_chat_head()

    def _toggle_ocr(self):
        if self.ocr.running:
            self.ocr.stop()
            self._end_session()
            self._set_status("Stopped")
        else:
            if not getattr(self, "_vs_ok", False):    # reading needs voices: start VoiceStudio
                self._vs_autostart_armed = True
                self._check_health()
            self._apply_capture()
            self.ocr.start()
            if self.ocr.running:
                t = self.cfg.settings.target_window or {}
                self.cfg.touch_game(self.game, t.get("exe", ""), t.get("title", ""))
                self._refresh_recent()
                self._start_session()
                self._sync_ocr_pause()
        self._update_ocr_button()
        self._update_stats()

    def _apply_ocr_options(self):
        s = self.cfg.settings
        mode, color, learned = s.ocr_mode, s.text_color, None
        if mode == "learn":
            learned = self.cfg.learned_text_colour(self.game)
            from ..sources.ocr_source import _is_english
            paddle = s.ocr_engine == "paddle" and _is_english(s.ocr_language)
            if learned and not paddle:
                # Windows OCR: clean the image down to the learned dialogue colour.
                mode, color = "color", learned
        self.ocr.opts = OcrOptions(s.ocr_language, mode, color, s.color_tolerance,
                                   list(s.ignore_words), s.ocr_engine, s.ocr_gpu, learned)
        if hasattr(self, "settings_view"):
            self.settings_view.show_learned_colour(learned)

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
        self._test_name_img = None
        try:
            img, self._test_name_img = cap.grab_pair(spec)
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
        ocr = make_ocr(self.ocr.opts)
        reading = ocr.read_dialogue(img, self.ocr.opts)
        name_img = getattr(self, "_test_name_img", None)
        name_read = ocr.read_name(name_img, self.ocr.opts) if name_img is not None else None
        processed = preprocess.process(img, s.ocr_mode, s.text_color, s.color_tolerance)
        speaker, text, fixes = self._interpret(reading.lines, reading.result.boxes, name_read,
                                               reading.result.top_styled)
        src = " (from the name area)" if name_read is not None else ""
        out = [f"Name{src}: "
               f"{speaker if speaker != NARRATOR else '(none, so this counts as narration)'}",
               f"Text: {text or '(nothing to read)'}", "",
               f"Read from the {reading.used} image. Raw OCR lines:"]
        out += [f"   {r[0]}" for r in reading.raw] or ["   (none)"]
        if reading.result.dropped:
            out += ["", "Skipped:"] + [f"   {t!r}: {why}" for t, why in reading.result.dropped]
        if fixes:
            out += ["", "Fixed:"] + [f"   {a} → {b}" for a, b in fixes]
        return processed, "\n".join(out)

    def _use_game(self, name: str):
        """Switch to the game called `name` (created when new)."""
        name = name.strip() or "My game"
        self.cfg.settings.ocr_profile = name
        self.cfg.save()
        self._load_game(name)
        self._refresh_recent()
        self._update_setup_game()

    def _update_setup_game(self):
        if not hasattr(self, "lbl_setup_game"):
            return
        g = self.game
        n = len([k for k in self.cfg.characters(g) if k != NARRATOR])
        lines = int((self.cfg.settings.games.get(g) or {}).get("lines", 0))
        new = not lines and not n
        self.lbl_setup_game.setText(f"{g}  (new)" if new else
                                    f"{g}  ·  {n} character{'s' if n != 1 else ''}  ·  "
                                    f"{lines:,} lines")

    def _rename_game(self, game: str | None = None):
        old = game or self.game
        new, ok = prompt(self, "Rename game", "The game's characters, voices, fix-ups and "
                         "stats keep working under the new name.", old, "Rename")
        if not ok or not new.strip() or new.strip() == old:
            return
        got = self.cfg.rename_game(old, new)
        if not got:
            info_dialog(self, "Name taken", f"There's already a game called “{new.strip()}”.")
            return
        self.playlog.rename(old, got)
        self.playlog.save()
        if old == self.game:
            self.game = got
            self._load_game(got)
        self._refresh_recent()
        self._update_setup_game()
        self._refresh_games_view()
        self._set_status(f"Renamed {old} to {got}")

    @staticmethod
    def _title_name(title: str, exe: str) -> str:
        """A game's name from its window title ("Studio Hearts - v0.5" -> "Studio Hearts"),
        else from its exe."""
        t = (title or "").strip()
        for sep in (" - ", " | ", " – "):
            if sep in t and len(t.split(sep)[0].strip()) >= 3:
                t = t.split(sep)[0].strip()
        return t if 2 <= len(t) <= 60 else (os.path.splitext(exe)[0] or "My game")

    def _choose_game_folder(self):
        """Pick a game's folder: the game is found (or created) from the .exe in it."""
        start = (self.cfg.game_info(self.game).get("path") or "")
        if not start and self.cfg.settings.target_window:
            hwnd = self._resolve_hwnd()
            exe = winapi.exe_path(hwnd) if hwnd else ""
            start = os.path.dirname(exe) if exe else ""
        path = QFileDialog.getExistingDirectory(
            self, "Choose the game's folder (where its .exe is)", start)
        if path:
            self._game_from_folder(os.path.normpath(path))

    def _game_from_folder(self, path: str) -> str:
        """The game for this folder (switched to): a known game with one of its .exe files,
        else a new game named after the game's title or folder. Returns its name."""
        exes = game_detect.find_exes(path)
        low = {e.lower() for e in exes}
        known = [g for g, i in self.cfg.settings.games.items()
                 if (i.get("exe") or "").lower() in low or
                 os.path.normcase(i.get("path") or "") == os.path.normcase(path)]
        if self.game in known:
            game = self.game
        elif known:
            game = known[0]
        else:
            game = (game_detect.game_title(path)
                    or (os.path.splitext(exes[0])[0] if exes else "")
                    or os.path.basename(path) or "My game")
            if game in self.cfg.settings.games:
                game = f"{game} ({os.path.basename(path)})"
        info = self.cfg.game_info(game)
        exe = info.get("exe") or (exes[0] if exes else "")
        if exe and not info.get("exe"):
            info["exe"], info["title"] = exe, info.get("title") or game
        info.update(path=path, engine=game_detect.detect(path))
        info.setdefault("text_source", "auto")
        self.cfg.save()
        if game != self.game:
            if exe:
                self.cfg.settings.target_window = {"exe": exe, "title": info.get("title", "")}
                self._hwnd = 0
            self._use_game(game)
            self._refresh_windows()
            self._apply_capture()
        self._after_folder_set(game)
        return game

    def _after_folder_set(self, game: str):
        info = self.cfg.game_info(game)
        path = info.get("path", "")
        if info.get("engine") == "renpy":
            if not renpy_hook.installed(path) and confirm(
                    self, "Read the text straight from the game?",
                    f"{game} is a Ren'Py game. VN Reader can add one small file to its game "
                    "folder (zz_vnreader_hook.rpy) that sends every line to VN Reader the "
                    "moment it's shown: exact text and speaker, no OCR delay.\n\nYou can "
                    "remove it any time (game menu → Remove game hook). Without it, VN "
                    "Reader still reads the game's script files to fix OCR mistakes.",
                    ok="Install game hook", icon="zap"):
                self._toggle_hook(game)
            if game == self.game:
                self._load_script(game)
        else:
            info_dialog(self, "Script not readable",
                        "VN Reader can only read the script of Ren'Py games so far. This "
                        "game keeps using OCR.")
        self._update_setup_game()
        self._refresh_games_view()

    def _lang_changed(self):
        self.cfg.settings.ocr_language = self.ed_lang.text().strip() or "en-US"
        self.cfg.save()
        self._apply_ocr_options()

    # =============================================================== lines
    def _load_game(self, game: str):
        switched = game != getattr(self, "game", None)
        self.game = game
        self.cfg.character(game, NARRATOR)
        if switched and getattr(self, "clock", None) is not None:
            self._start_session()                # play time belongs to the new game
        if hasattr(self, "playlog"):
            self._load_script(game)
            self._update_timer()
        if hasattr(self, "ocr"):
            self._apply_ocr_options()            # this game's learned text colour
        if hasattr(self, "shots"):
            self.shots.refresh_game()
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
                QTimer.singleShot(400, lambda: self._toast(
                    "New game: press F6 in the game to mark the name and dialogue "
                    "(or open Game setup)"))

    def _designs(self) -> dict[str, dict]:
        return {n: self.cfg.design(self.game, n) for n in self.cfg.characters(self.game)}

    def _voice_label(self, name: str, game: str | None = None) -> str:
        game = game or self.game
        rec = self.cfg.character(game, name)
        ov = rec.get("voice_override", "")
        if ov.startswith("sapi:"):
            return "Windows voice"
        if ov.startswith("vs:"):
            return "my VoiceStudio voice"
        d = self.cfg.design(game, name)
        return d.get("label") if rec.get("preset") else "custom voice"

    def _reload_characters(self):
        chars = self.cfg.characters(self.game)
        self.chars.load(chars, self._designs())
        if hasattr(self, "cast"):
            self.cast.set_characters(chars, {n: self._voice_label(n) for n in chars})

    def _update_stats(self):
        chars = [k for k in self.cfg.characters(self.game) if k != NARRATOR]
        info = self.cfg.settings.games.get(self.game, {})
        self.lbl_stats.set_chips(stats_chips(len(chars), int(info.get("lines", 0)),
                                             self.ocr.running, info.get("last_played", 0)))
        self._update_checklist()

    def _interpret(self, lines: list, boxes: list | None = None,
                   name: str | None = None, styled: bool | None = None
                   ) -> tuple[str, str, list]:
        """OCR lines -> (speaker, dialogue, corrections). No name -> narrator.

        name: the read of the separate name area ('' = nobody), or None when there is
        no name area and the name is split off the top line.
        styled: the top line has another colour/size than the dialogue (a name hint)."""
        if isinstance(name, dict):              # {"name", "styled"} from OcrSource
            name, styled = name.get("name"), name.get("styled")
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
        aliases = self.cfg.alias_map(self.game)          # old names of renamed characters
        if name is not None:          # name area: all of the reading area is dialogue
            speaker = text_fixes.apply(clean(name), rules) if name else ""
            text = " ".join(fixed)
        else:
            speaker, text = split_name(fixed, known + list(aliases), s.name_mode, bxs, styled)
        text = clean(text)
        fixes = []
        if text and iso_lang(s.ocr_language) == "en":
            names = known + [r.get("replace", "") for r in rules] + ([speaker] if speaker else [])
            if s.ocr_autocorrect:
                text, fixes = ocr_filter.correct(text, names, self.cfg.game_words(self.game))
            else:
                text, fixes = ocr_filter.repair_ocr(text, names,
                                                    self.cfg.game_words(self.game))
        speaker = clean_name(speaker)
        if speaker:
            if speaker.lower() in aliases:
                speaker = aliases[speaker.lower()]
            else:
                speaker = text_fixes.match_speaker(speaker, known + list(aliases))
                speaker = aliases.get(speaker.lower(), speaker)
        return speaker or NARRATOR, text, fixes

    def _quick_steps(self):
        """Steps for each line's first sentence: a little fewer, so the voice starts
        sooner (None: same as the rest, when the quality is already that fast)."""
        s = self.cfg.settings
        if not s.quick_start:
            return None
        full = QUALITY_STEPS.get(s.vs_quality)
        if full is not None and full <= QUICK_STEPS:
            return None
        return QUICK_STEPS

    def _on_candidate(self, lines: list, boxes: list | None = None, info=None):
        """First read of a new line (still being confirmed): start making its voice."""
        try:
            name, text, _ = self._interpret(lines, boxes, info)
        except Exception:
            return
        name, hint = self._speaker_tag(name)
        if text:
            name, text, _m = self._script_fix(name, text)
        if not text or name not in self.cfg.characters(self.game):
            return                       # never create characters from an unconfirmed read
        if name == NARRATOR and not self.cfg.settings.read_narrator:
            return
        self.queue.prefetch(self._make_line(name, text, hint))

    def _on_lines(self, lines: list, boxes: list | None = None, info=None):
        self._advance_token += 1             # a new line cancels any pending advance
        self._handle_reading(lines, boxes, info, auto_advance=True)

    def _handle_reading(self, lines: list, boxes: list | None, info,
                        auto_advance: bool = True) -> tuple[str, str]:
        """A confirmed read (live area or Screenshot tab): learn from it, show it, count
        it, put it in History and speak it. Returns (speaker, text)."""
        name, text, _fixes = self._interpret(lines, boxes, info)
        if not text:
            return name, ""
        name, hint = self._speaker_tag(name)
        ocr_text = text
        name, text, m = self._script_fix(name, text)
        name = self._colour_fix(name, info, m)
        self._line_source = "script" if m else "ocr"
        self._line_fixed = bool(m) and text != ocr_text
        if m:
            self.playlog.mark_seen(self.game, m.line.id)
        self._learn_from(name, text, info)
        self._show_and_speak(name, text, auto_advance, hint)
        return name, text

    # ------------------------------------------------- text from the game
    def _text_source(self, game: str | None = None) -> str:
        return self.cfg.game_info(game or self.game).get("text_source", "auto")

    def _script_fix(self, name: str, text: str):
        """Swap an OCR read for the game script's exact line (and its speaker, when
        the OCR couldn't read the name). Returns (name, text, match or None)."""
        if (self._matcher is None or self._matcher_game != self.game
                or self._text_source() == "ocr"):
            return name, text, None
        m = self._matcher.match(text, None if name == NARRATOR else name)
        if m is None:
            return name, text, None
        known = self.cfg.characters(self.game)
        if m.speaker_sure and (name == NARRATOR or name not in known):
            if m.line.tag == "":
                name = NARRATOR
            elif m.speaker:
                name = m.speaker
        return name, m.text, m

    def _colour_fix(self, name: str, info, m) -> str:
        """Name colours: learn each speaker's, and use them to identify a name the OCR
        garbled (only in games whose names really have different colours)."""
        img = info.get("name_img") if isinstance(info, dict) else None
        if img is None:
            return name
        chars = self.cfg.characters(self.game)
        if name != NARRATOR and name not in chars and not (m and m.speaker_sure):
            guess = name_colour.guess(chars, name_colour.measure(img))
            if guess:
                return guess
        elif name in chars and name != NARRATOR:
            name_colour.learn(chars[name], name_colour.measure(img))
        return name

    def _on_hook_line(self, who: str, text: str, line_id: str, extra=None):
        """A line straight from the game (Ren'Py hook): exact text, and the speaker as
        shown in the game's name box. A name that can't be right (the game gave none
        usable, or it looks like program code) is read from the screen instead (OCR,
        the last fallback)."""
        if not self.ocr.running or self._auto_paused or self._text_source() != "auto":
            return
        extra = extra if isinstance(extra, dict) else {}
        name = (who or "").strip()
        if extra.get("odd") or looks_wrong(name):
            self._hook_name_by_ocr(text, line_id, extra)
            return
        self._speak_hook_line(name, text, line_id, extra)

    def _speak_hook_line(self, name: str, text: str, line_id: str, extra: dict):
        name, hint = self._speaker_tag(name)          # "X (thinking)" -> X, thought
        if name and extra.get("dyn") and self.cfg.absorb_broken(self.game, name):
            self._reload_characters()                 # a broken old record became `name`
        self._advance_token += 1
        self._line_source, self._line_fixed = "hook", False
        lid = line_id[5:] if line_id.startswith("game/") else line_id
        self.playlog.mark_seen(self.game, lid)
        self._show_and_speak(name or NARRATOR, text, True, hint)
        self._prefetch_next(lid)

    def _prefetch_next(self, lid: str):
        """Make the script's next line while this one plays, so it's ready when the
        player clicks. Only a guess (menus and jumps go elsewhere), so it's made in the
        background and never creates a character."""
        if self._matcher is None or self._matcher_game != self.game:
            return
        try:
            nxt = self._matcher.script.next_line(lid)
        except Exception:
            return
        if nxt is None or "[" in nxt.text:                 # [variables] filled in later
            return
        name = self._matcher.script.speaker(nxt.tag) if nxt.tag else NARRATOR
        if not name:                                       # a name the game sets later
            return
        if name == NARRATOR and not self.cfg.settings.read_narrator:
            return
        name, hint = self._speaker_tag(name)
        if name not in self.cfg.characters(self.game):
            return
        self.queue.prefetch(self._make_line(name, nxt.text, hint), background=True)

    def _speaker_tag(self, name: str) -> tuple[str, str]:
        """A speaker name with a mood tag ("Zalton (thinking)", "Mika [whispering]") is
        the base character, said in that mood. Names the user already has as a character
        or alias stay as they are. Returns (speaker, mood hint)."""
        if not name or name == NARRATOR:
            return name, ""
        chars = self.cfg.characters(self.game)
        aliases = self.cfg.alias_map(self.game)          # old names of renamed characters
        if name in chars:
            return name, ""
        if name.lower() in aliases:
            return aliases[name.lower()], ""
        base, hint = emotion.split_speaker_tag(name)
        if base == name:
            return name, ""
        fx = emotion.tag_effect(name)                    # "(on the phone)" -> phone sound
        if fx:
            hint = f"{hint}+{fx}"
        if base.lower() in aliases:
            base = aliases[base.lower()]
        elif base not in chars:
            known = [k for k in chars if k != NARRATOR]
            base = text_fixes.match_speaker(base, known + list(aliases))
            base = aliases.get(base.lower(), base)
        return base, hint

    def _hook_name_by_ocr(self, text: str, line_id: str, extra: dict):
        """Read the speaker's name from the name area on screen (background thread)."""
        spec, opts = self.ocr.spec, self.ocr.opts
        if not spec.name:
            if not getattr(self, "_name_area_hint", False):
                self._name_area_hint = True
                self._status_msg("The game didn't give this speaker's name: set a name area "
                                 "(F6) so VN Reader can read it from the screen", 8000)
            self._speak_hook_line("", text, line_id, extra)
            return

        def job():
            name = ""
            try:
                cap = Capturer()
                try:
                    _img, name_img = cap.grab_pair(spec)
                finally:
                    cap.close()
                if name_img is not None:
                    name = make_ocr(opts).read_name(name_img, opts) or ""
            except Exception:
                name = ""
            try:
                self.bridge.hook_named.emit(name, text, line_id, extra)
            except RuntimeError:                      # the window closed meanwhile
                pass
        threading.Thread(target=job, name="hook-name", daemon=True).start()

    def _on_hook_named(self, name: str, text: str, line_id: str, extra):
        name = (name or "").strip()
        if looks_wrong(name):
            name = ""
        if name:
            known = [k for k in self.cfg.characters(self.game) if k != NARRATOR]
            aliases = self.cfg.alias_map(self.game)
            name = text_fixes.match_speaker(name, known + list(aliases)) or name
        self._speak_hook_line(name, text, line_id, extra if isinstance(extra, dict) else {})

    def _on_hook_live(self, live: bool):
        self._hook_live = live
        self._sync_ocr_pause()
        if self.ocr.running and self._text_source() == "auto":
            self._status_msg("Reading straight from the game (game hook)" if live else
                             "The game hook stopped: reading the screen (OCR) again", 5000)

    def _sync_ocr_pause(self):
        """OCR rests while the game isn't in front, or while the game hook delivers."""
        self.ocr.paused = self._auto_paused or (self._hook_live and
                                                self._text_source() == "auto")
        self._update_source_chip()

    def _update_source_chip(self):
        if not hasattr(self, "st_src"):
            return
        src = self._text_source()
        if self._hook_live and src == "auto":
            self.st_src.set("Text: game (live)", theme.OK)
        elif self._matcher is not None and self._matcher_game == self.game and src != "ocr":
            self.st_src.set("Text: script + OCR", theme.TEXT_2)
        else:
            self.st_src.set("Text: OCR", theme.TEXT_3)

    def _load_script(self, game: str):
        """Read the game's script in the background (Ren'Py games with a folder set)."""
        info = self.cfg.game_info(game)
        if game == self.game:
            self._matcher, self._matcher_game = None, ""
            self._update_source_chip()
        if info.get("path") and info.get("engine") == "renpy":
            self._update_hook(game)
        if not info.get("path") or info.get("engine") != "renpy" or \
                info.get("text_source", "auto") == "ocr":
            return
        path = info["path"]

        def job():
            try:
                sc = load_folder(path)
                payload = (Matcher(sc), sc) if sc else None
            except Exception as e:
                self.bridge.status.emit(f"Couldn't read the game's script: {e}")
                payload = None
            try:
                self.bridge.script_ready.emit(game, payload)
            except RuntimeError:                 # the window closed meanwhile
                pass
        threading.Thread(target=job, name="script", daemon=True).start()

    def _on_script_ready(self, game: str, payload):
        if payload is None:
            if game == self.game:
                self._status_msg("This game's script isn't readable (compiled only): the "
                                 "game hook or OCR will be used", 6000)
            return
        matcher, sc = payload
        self.playlog.set_total_lines(game, len(sc.lines))
        self.playlog.save()
        if game != self.game:
            return
        self._matcher, self._matcher_game = matcher, game
        self._seed_script_colours()
        self._update_source_chip()
        self._status_msg(f"Read {len(sc.lines):,} lines of {game}'s script: OCR mistakes and "
                         "names are fixed from it", 6000)

    def _seed_script_colours(self, only: str | None = None):
        """Name colours written in the script (Character(..., color=...))."""
        if self._matcher is None:
            return
        chars = self.cfg.characters(self.game)
        for cd in self._matcher.script.chars.values():
            if cd.name and cd.colour and cd.name in chars and (only is None or cd.name == only):
                rgb = name_colour.hex_to_rgb(cd.colour)
                if rgb:
                    chars[cd.name]["name_colour"] = rgb
                    chars[cd.name]["name_colour_source"] = "script"

    # ------------------------------------------------------ game folder
    def _game_folder_dialog(self, game: str):
        info = self.cfg.game_info(game)
        start = info.get("path", "")
        if not start and game == self.game and self.cfg.settings.target_window:
            hwnd = self._resolve_hwnd()
            exe = winapi.exe_path(hwnd) if hwnd else ""
            start = os.path.dirname(exe) if exe else ""
        path = QFileDialog.getExistingDirectory(
            self, f"{game}: choose the game's folder (where its .exe is)", start)
        if not path:
            return
        path = os.path.normpath(path)
        info.update(path=path, engine=game_detect.detect(path))
        info.setdefault("text_source", "auto")
        if not info.get("exe"):
            exes = game_detect.find_exes(path)
            if exes:
                info["exe"], info["title"] = exes[0], info.get("title") or game
        self.cfg.save()
        self._after_folder_set(game)

    def _update_hook(self, game: str) -> bool:
        """An older game hook in the game's folder is replaced by the current one."""
        path = self.cfg.game_info(game).get("path", "")
        if not path or not renpy_hook.needs_update(path):
            return False
        try:
            renpy_hook.install(path)
        except OSError:
            return False
        t = self.cfg.settings.target_window or {}
        exe = self.cfg.game_info(game).get("exe", "")
        if exe and winapi.find_window(exe, t.get("title", "")):
            self._toast("Game hook updated: restart the game once to use it.")
        return True

    def _launch_game(self, game: str | None = None):
        """Start the game's .exe (asks for it once when VN Reader doesn't know it)."""
        game = game or self.game
        info = self.cfg.game_info(game)
        exe = os.path.join(info.get("path", ""), info.get("exe", "")) \
            if info.get("path") and info.get("exe") else ""
        if not exe or not os.path.isfile(exe):
            exe, _ = QFileDialog.getOpenFileName(
                self, f"{game}: choose the game's .exe", info.get("path", ""),
                "Programs (*.exe)")
            if not exe:
                return
            exe = os.path.normpath(exe)
            folder = os.path.dirname(exe)
            info.update(path=folder, exe=os.path.basename(exe),
                        engine=game_detect.detect(folder))
            info.setdefault("title", game)
            info.setdefault("text_source", "auto")
            self.cfg.save()
        self._update_hook(game)
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags,
                             close_fds=True)
        except OSError as e:
            info_dialog(self, "Couldn't start the game", f"{exe}\n\n{e}")
            return
        self._set_status(f"Starting {game}…")
        self._refresh_games_view()

    def _toggle_hook(self, game: str):
        path = self.cfg.game_info(game).get("path", "")
        if not path:
            self._game_folder_dialog(game)
            return
        try:
            if renpy_hook.installed(path):
                renpy_hook.uninstall(path)
                self._toast("Game hook removed. Restart the game to finish.")
            else:
                renpy_hook.install(path)
                self._toast("Game hook installed. Restart the game once: VN Reader then "
                            "reads every line straight from it.")
        except OSError as e:
            info_dialog(self, "Couldn't change the game folder",
                        f"{e}\n\nIf the game is in Program Files, run VN Reader as "
                        "administrator once, or move the game to another folder.")
        self._refresh_games_view()

    def _set_text_source(self, game: str, source: str):
        self.cfg.game_info(game)["text_source"] = source
        self.cfg.save()
        if game == self.game:
            self._load_script(game)
            self._sync_ocr_pause()

    def _refresh_games_view(self):
        if hasattr(self, "games_view") and self.tabs.currentIndex() == 6:
            self.games_view.refresh()

    def _switch_game(self, game: str):
        if game == self.game:
            return
        i = self.cb_recent.findData(game)
        if i >= 0:
            self.cb_recent.setCurrentIndex(i)
            self._recent_chosen(i)
        else:
            self._load_game(game)

    def _continue_game(self, game: str):
        self._switch_game(game)
        self.tabs.setCurrentIndex(0)
        if not self.ocr.running:
            self._toggle_ocr()

    def _show_cast_character(self, game: str, name: str):
        self._switch_game(game)
        self.tabs.setCurrentIndex(2)
        self.chars.select(name)

    # ------------------------------------------------------- play time
    def _start_session(self):
        self._end_session()
        self.clock = PlayClock(self.playlog, self.game)
        self._update_timer()

    def _end_session(self):
        if self.clock is not None:
            self.clock.flush()
            self.clock = None
        self._update_timer()

    def _update_timer(self, counting: bool | None = None):
        """counting: the play clock is running (reading, game in front, voice not paused);
        None = work it out from the clock."""
        if not hasattr(self, "lbl_timer"):
            return
        total = self.playlog.total_seconds(self.game)
        if self.clock is None:
            self.lbl_timer.setText(f"{fmt_duration(total, short=True)} played"
                                   if total else "")
            self.lbl_timer.setToolTip("")
            return
        if counting is None:
            counting = self.clock.counting
        cur = self.clock.session.seconds
        self.lbl_timer.setText(f"{fmt_duration(cur)}" + ("" if counting else ", paused") +
                               f"  ({fmt_duration(total, short=True)} total)")
        s = self.clock.session
        avg = f"{s.avg_delay:.2f} s" if s.avg_delay is not None else "–"
        self.lbl_timer.setToolTip(
            f"This session: {s.lines} lines, {s.words:,} words, {s.new_chars} new "
            f"characters\nVoice delay: {avg} average" +
            (f", {s.p90_delay:.2f} s for 9 in 10 lines" if s.p90_delay is not None else "") +
            (f"\nOCR mistakes fixed from the script: {s.fixed}" if s.fixed else "") +
            "\nThe timer pauses while the voice is paused or the game isn't in front.")

    def _learn_from(self, name: str, text: str, info):
        """Word memory (made-up words seen in 3 lines are protected) and the dialogue
        text colour (after 5 agreeing lines, used to clean up busy backgrounds)."""
        s = self.cfg.settings
        if iso_lang(s.ocr_language) == "en":
            known = [k for k in self.cfg.characters(self.game)] + [name]
            new = self.cfg.learn_words(self.game, ocr_filter.unknown_words(text, known))
            if new and hasattr(self, "fixups"):
                self.fixups.reload()
        colour = info.get("colour") if isinstance(info, dict) else None
        if colour and self.cfg.record_text_colour(self.game, colour):
            self._apply_ocr_options()
            if s.ocr_mode == "learn":
                self._set_status("Learned this game's text colour: busy backgrounds are "
                                 "now filtered out")

    # ============================================================ screenshots
    def _read_screenshot(self, img):
        """Screenshot tab: find the name + dialogue in a background thread."""
        import copy
        opts = copy.copy(self.ocr.opts)
        s = self.cfg.settings
        areas, aspect = None, None
        t = s.target_window or {}
        exe = t.get("exe") or (s.games.get(self.game) or {}).get("exe", "")
        if exe:
            areas = dict(self.cfg.window_regions(exe))
            aspect = areas.get("aspect")
            hwnd = self._resolve_hwnd() if t else 0
            if hwnd:
                cl = winapi.client_rect_screen(hwnd)
                if cl[2] > 0 and cl[3] > 0:
                    aspect = cl[2] / cl[3]

        def work():
            from ..sources.screenshot_reader import find_dialogue
            try:
                ocr_area = make_ocr(opts)
                ocr_full = make_ocr(opts, limit=1920, gpu=True)   # GPU: faster on big images
                res = find_dialogue(img, ocr_area, ocr_full, opts, areas, aspect)
                self.bridge.shot.emit(img, res, "")
            except Exception as e:
                self.bridge.shot.emit(img, None, str(e))
        threading.Thread(target=work, name="screenshot", daemon=True).start()

    def _warm_shot_engine(self):
        """Start the whole-screenshot OCR engine in the background (~3 s, once), so the
        first dropped screenshot is read straight away."""
        if getattr(self, "_shot_warm", False):
            return
        self._shot_warm = True
        opts = self.ocr.opts
        threading.Thread(target=lambda: make_ocr(opts, limit=1920, gpu=True),
                         name="warm-ocr", daemon=True).start()

    def _on_shot(self, img, result, error: str):
        if error or result is None or not result.found:
            self.shots.show_result(img, result, "", "")
            if error:
                self._set_status(f"Screenshot: {error}")
            self.shots.done()
            return
        name, text = self._handle_reading(result.lines, result.boxes, result.info,
                                          auto_advance=False)
        self.shots.show_result(img, result, name, text)
        spoken = bool(text) and not (name == NARRATOR and not self.cfg.settings.read_narrator)
        if not spoken:
            self.shots.done()
            return
        self._shot_speaking = True
        self._shot_token = getattr(self, "_shot_token", 0) + 1
        token = self._shot_token
        # Safety net: if the line never finishes (stopped, replaced), move on anyway.
        QTimer.singleShot(60000, lambda: token == self._shot_token and self._shot_spoken())

    def _shot_spoken(self):
        if getattr(self, "_shot_speaking", False):
            self._shot_speaking = False
            self.shots.done()

    def _show_and_speak(self, name: str, text: str, auto_advance: bool = True,
                        mood_hint: str = ""):
        self._mood_demo.clear()                       # a game line ends a mood demo
        new = name not in self.cfg.characters(self.game)
        rec = self.cfg.character(self.game, name)
        design = self.cfg.design(self.game, name)
        self._advance_ok = auto_advance      # screenshots never move the game on
        skip = name == NARRATOR and not self.cfg.settings.read_narrator
        if not skip:
            self._speak(name, text, mood_hint)   # first: the voice must not wait for the UI
        self.cfg.count_line(self.game, name)
        if self.clock is not None and auto_advance:
            self.clock.line(text, self._line_source, new, self._line_fixed)
        self._line_source, self._line_fixed = "ocr", False
        self.chars.ensure(name, rec, design)
        if new:
            self._seed_script_colours(name)
            self.bank.prewarm(design)
            self._refresh_recent()
        self._reload_cast()
        self._update_stats()
        self.preview.set_name_on_top(name != NARRATOR)
        self._show_line(name, text, rec, design, mood_hint)
        mood = ""
        if self.cfg.settings.expressive:
            mood = emotion.parse(text, strength=self.cfg.settings.emotion_strength,
                                 hint=mood_hint).mood
        self.history.add(name, text, mood)
        self.transcript.add(name, text)
        if skip:
            self._schedule_advance()         # not spoken: still move on

    # ======================================================== auto-advance
    def _auto_toggled(self, on: bool):
        self.cfg.settings.auto_advance = on
        self.cfg.save()
        self._toast("Auto-advance on: the story moves on by itself after each line"
                    if on else "Auto-advance off")
        self._update_ocr_button()

    def _toggle_auto(self):
        self.cb_auto.setChecked(not self.cb_auto.isChecked())

    def _schedule_advance(self):
        s = self.cfg.settings
        if not (s.auto_advance and self.ocr.running and getattr(self, "_advance_ok", True)):
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

    def _show_line(self, name: str, text: str, rec: dict, design: dict, mood_hint: str = ""):
        self._last_shown = (name, text, rec, design, mood_hint)
        g = rec.get("gender", "unknown")
        ring = "" if name == NARRATOR else theme.char_colour(rec)
        if hasattr(self, "chat_head"):
            self._update_chat_head(speaker="Narrator" if name == NARRATOR else name, ring=ring)
        self.av.set_name(name, ring=ring)
        new_line = getattr(self, "_shown_text", None) != text
        if new_line:
            fade_in(self.lbl_text)
        self.lbl_speaker.setText("Narrator" if name == NARRATOR else name)
        ov = rec.get("voice_override")
        if ov:
            self.lbl_voice.setText(ov.split(":", 1)[-1])
        else:
            label = design.get("label") if rec.get("preset") else "Custom voice"
            self.lbl_voice.setText(f"{label}: {voices.instruct(design)}")
        mood = ""
        if self.cfg.settings.expressive:
            mood = emotion.parse(text, strength=self.cfg.settings.emotion_strength,
                                 hint=mood_hint).mood
        effect = emotion.split_hint(mood_hint)[1] or rec.get("effect", "")
        pill = ", ".join(x for x in (mood, {"phone": "on the phone", "radio": "over the radio"}
                                     .get(effect, "")) if x)
        self.mood_pill.setText(pill)
        self.mood_pill.setVisible(bool(pill))
        # The line, split into the sentences the highlight steps through.
        self._shown_text = text
        self._shown_parts = split_sentences(text) or [text]
        self._shown_sentence = -2
        self.lbl_text.setText(text)
        if new_line:
            self.progress.set_value(0.0)
        self._float_line(None)

    def _float_line(self, html_text: str | None, progress: float | None = None):
        """Mirror the line (and progress) in the mini-player, when that's shown."""
        mp = getattr(self, "chat_head", None)
        if mp is None or not hasattr(mp, "set_line"):
            return
        if html_text is None:
            import html as _h
            html_text = _h.escape(getattr(self, "_shown_text", "") or "")
        # isHidden: the pill's own state (isVisible is False whenever another page is shown)
        mp.set_line(html_text, self.mood_pill.text() if not self.mood_pill.isHidden() else "")
        mp.set_progress(self.progress.value() if progress is None else progress)

    def _update_now_playing(self):
        """Highlight the sentence being spoken and move the progress line (every 200 ms;
        it only reads the player's position)."""
        if not hasattr(self, "progress") or not getattr(self, "_shown_parts", None):
            return
        tag, frac = self.player.current()
        uid = getattr(self, "_speaking_uid", None)
        if not (isinstance(tag, tuple) and len(tag) == 3 and tag[0] == uid):
            if not self.player.is_playing() and self.progress.value() > 0:
                self.progress.set_value(1.0)          # finished: full, all text plain
                if self._shown_sentence != -1:
                    self._shown_sentence = -1
                    self.lbl_text.setText(self._shown_text)
                    self._float_line(None, 1.0)
            return
        _uid, i, n = tag
        self.progress.set_value((i + frac) / max(1, n))
        mp = getattr(self, "chat_head", None)
        if mp is not None and hasattr(mp, "set_progress"):
            mp.set_progress(self.progress.value())
        spoken = self.queue.sentences.get(uid) or []
        j = match_sentence(spoken[i], self._shown_parts) if i < len(spoken) else -1
        if j < 0 or len(self._shown_parts) < 2:
            return                                    # one sentence: nothing to step
        if j != self._shown_sentence:
            self._shown_sentence = j
            html = sentence_html(self._shown_parts, j)
            self.lbl_text.setText(html)
            self._float_line(html)

    def _make_line(self, name: str, text: str, mood_hint: str = "") -> Line:
        rec = self.cfg.character(self.game, name)
        if self.cfg.settings.read_names and name != NARRATOR:
            text = f"{name}. {text}"
        mood, effect = emotion.split_hint(mood_hint)
        self._line_uid = getattr(self, "_line_uid", 0) + 1
        return Line(self.game, name, text, dict(rec), iso_lang(self.cfg.settings.ocr_language),
                    self.cfg.design(self.game, name), mood,
                    effect or rec.get("effect", ""), self._line_uid)

    def _speak(self, name: str, text: str, mood_hint: str = ""):
        line = self._make_line(name, text, mood_hint)
        self._speaking_uid = line.uid             # the now-playing card follows this line
        latency.speak(line.text)
        self.queue.say(line)

    # ---- Cast → Moods: the same line in every mood ----
    MOOD_DEMO = [("", "normal"), ("*Seductively* ", "seductive"), ("*moans softly* ", "aroused"),
                 ("*whispers* ", "whisper"), ("*smiles* ", "happy"), ("*sadly* ", "sad"),
                 ("*angrily* ", "angry")]
    MOOD_SAMPLE = "I've been waiting for you all night."

    def _preview_moods(self, name: str):
        rec = self.cfg.character(self.game, name)
        if rec.get("voice_override"):
            self._set_status("Moods need a preset or custom voice (not your own or a "
                             "Windows voice)")
            return
        self._mood_demo.clear()
        design = self.cfg.design(self.game, name)
        moods = [m for _p, m in self.MOOD_DEMO if m != "normal"]
        use_variants = self.cfg.settings.emotion_voices and \
            self.cfg.settings.emotion_strength != "subtle"

        def work():
            try:
                if use_variants and self.bank.enabled() and self.vs.available():
                    self.bridge.status.emit(f"Preparing {name}'s voice…")
                    self.bank.get_profile(design)
                    for i, m in enumerate(moods, 1):
                        self.bridge.status.emit(f"Preparing {name}'s moods {i}/{len(moods)} "
                                                f"({m})…")
                        self.bank.ensure_variant(design, m)
            finally:
                self.bridge.moods_ready.emit(name)
        threading.Thread(target=work, name="moods", daemon=True).start()

    def _start_mood_demo(self, name: str):
        self._mood_demo[:] = [(name, prefix + self.MOOD_SAMPLE) for prefix, _m in self.MOOD_DEMO]
        self._advance_ok = False                      # never moves the game on
        self._set_status(f"Playing {name} in every mood")
        self._play_next_mood()

    def _next_mood(self):
        if self._mood_demo:
            QTimer.singleShot(450, self._play_next_mood)

    def _play_next_mood(self):
        if not self._mood_demo:
            return
        name, text = self._mood_demo.pop(0)
        rec = self.cfg.character(self.game, name)
        design = self.cfg.design(self.game, name)
        self._show_line(name, text, rec, design)
        self.queue.say(Line(self.game, name, text, dict(rec),
                            iso_lang(self.cfg.settings.ocr_language), design))

    def _preview(self, name: str):
        self._mood_demo.clear()
        who = "the narrator" if name == NARRATOR else name
        line = f"Hi, I'm {who}. This is what my voice sounds like."
        self._show_line(name, line, self.cfg.character(self.game, name),
                        self.cfg.design(self.game, name))
        self.queue.say(Line(self.game, name, line, dict(self.cfg.character(self.game, name)),
                            iso_lang(self.cfg.settings.ocr_language),
                            self.cfg.design(self.game, name)))

    def _update_speaking(self):
        self._update_vs_light()                  # busy / waiting (reads flags only)
        self._update_now_playing()
        on = self.player.is_playing() and not self.player.paused
        self.meter.push(self.player.level if on else 0.0)
        if hasattr(self, "chat_head") and self.chat_head.isVisible():
            self.chat_head.set_level(self.player.level if on else 0.0)

    def _read_names_toggled(self, on: bool):
        self.cfg.settings.read_names = on
        self.cfg.save()
        if hasattr(self, "settings_view"):
            self.settings_view.cb_names.blockSignals(True)
            self.settings_view.cb_names.setChecked(on)
            self.settings_view.cb_names.blockSignals(False)

    # ================================================== delete / merge
    def _delete_game(self, game: str | None = None):
        g = game or self.game
        n = len([k for k in self.cfg.settings.voices.get(g, {}) if k != NARRATOR])
        path = self.cfg.settings.games.get(g, {}).get("path", "")
        hook = bool(path) and renpy_hook.installed(path)
        if not confirm(self, "Delete game",
                       f"Delete “{g}” from VN Reader? This removes its {n} character"
                       f"{'s' if n != 1 else ''}, their voice choices, fix-ups, reading "
                       "areas, play time and notes." +
                       (" The game hook is removed from its folder too." if hook else "") +
                       " Your shared voice presets are kept. The game itself isn't touched.",
                       "Delete game", destructive=True):
            return
        current = g == self.game
        if current and self.ocr.running:
            self._toggle_ocr()
        if hook:
            renpy_hook.uninstall(path)
        self.cfg.delete_game(g)
        self.playlog.remove(g)
        self.playlog.save()
        self.bank.prune(self.cfg.used_voice_keys())
        if current:
            self._load_game(self.cfg.settings.ocr_profile)
            self._refresh_windows()
            self._apply_capture()
        self._refresh_recent()
        self._update_setup_game()
        if hasattr(self, "games_view"):
            self.games_view.current = None
            self._refresh_games_view()
        self._set_status(f"Deleted {g}")

    def _char_menu(self, name: str, pos):
        others = list(self.cfg.characters(self.game))
        character_menu(self, name, others, on_edit=self._edit_character,
                       on_merge=self._merge_character,
                       on_delete=self._delete_character,
                       on_rename=self._rename_character).exec(pos)

    def _rename_character(self, name: str):
        new, ok = prompt(self, "Rename character",
                         f"When the game shows “{name}” again, the line still goes to this "
                         "character. The voice stays the same.", name, "Rename")
        new = (new or "").strip()
        if not ok or not new or new == name:
            return
        if new in self.cfg.characters(self.game) and not confirm(
                self, "Merge characters", f"“{new}” already exists. Merge “{name}” into "
                f"“{new}”?", "Merge", icon="merge"):
            return
        result = self.cfg.rename_character(self.game, name, new)
        if not result:
            return
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self.fixups.reload()
        self._update_stats()
        if self.lbl_speaker.text().startswith(name):
            self.lbl_speaker.setText(self.lbl_speaker.text().replace(name, result, 1))
            self.av.set_name(result)
        self._set_status(f"Renamed {name} → {result}")

    def _edit_character(self, name: str):
        self.tabs.setCurrentWidget(self.chars)
        self.chars.select(name)

    def _delete_character(self, name: str):
        if not confirm(self, "Delete character",
                       f"Delete “{name}” from {self.game}? If they speak again they'll come "
                       "back as a new character with a fresh voice.", "Delete",
                       destructive=True):
            return
        self.cfg.delete_character(self.game, name)
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self._update_stats()
        self._refresh_recent()
        self._set_status(f"Deleted {name}")

    def _merge_character(self, src: str, dst: str):
        if not confirm(self, "Merge characters",
                       f"Merge “{src}” into “{dst}”? “{src}” is removed, its lines count for "
                       f"{dst}, and from now on “{src}” is read as {dst} (added to Fix-ups).",
                       "Merge", icon="merge"):
            return
        self.cfg.merge_character(self.game, src, dst)
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self.fixups.reload()
        self._update_stats()
        self._refresh_recent()
        self._set_status(f"Merged {src} into {dst}")

    def _merge_tagged(self):
        """One click: "Zalton (thinking)", "Zalton (whispering)"... become Zalton."""
        pairs = self.cfg.tagged_pairs(self.game)
        if not pairs:
            return
        listed = "\n".join(f"{a}  →  {b}" for a, b in pairs[:8]) + (
            f"\n…and {len(pairs) - 8} more" if len(pairs) > 8 else "")
        if not confirm(self, "Merge mood duplicates",
                       f"These are the same character in a mood. Merge them? Their lines "
                       f"count for the main character, and later lines use its voice in that "
                       f"mood.\n\n{listed}", "Merge", icon="merge"):
            return
        done = self.cfg.fold_tagged(self.game)
        self.bank.prune(self.cfg.used_voice_keys())
        self._reload_characters()
        self._reload_cast()
        self.fixups.reload()
        self._update_stats()
        self._toast(f"Merged {len(done)} mood duplicate{'s' if len(done) != 1 else ''}")

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

    def _pick_voice(self, name: str):
        """The grouped voice picker for one character."""
        rec = self.cfg.character(self.game, name)
        used: dict[str, list[str]] = {}
        for n, r in self.cfg.characters(self.game).items():
            if r.get("preset") and not r.get("voice_override"):
                used.setdefault(r["preset"], []).append("Narrator" if n == NARRATOR else n)
        dlg = VoicePicker(self, "the narrator" if name == NARRATOR else name, rec,
                          self.cfg.settings.presets, used, self.chars.sources,
                          self._preset_preview)
        if dlg.exec() and dlg.choice:
            self._char_voice(name, dlg.choice)

    def _char_role(self, name: str, role: str):
        """A role was chosen: switch to a voice from the role's group if the current one
        isn't from it (your own and Windows voices are kept)."""
        self.cfg.update_character(self.game, name, role=role)
        rec = self.cfg.character(self.game, name)
        want = presets_mod.ROLE_CATEGORY.get(role)
        cur = presets_mod.by_id(self.cfg.settings.presets).get(rec.get("preset") or "")
        if want and not rec.get("voice_override") and \
                (cur is None or presets_mod.category_of(cur) != want):
            g = rec.get("gender", "unknown")
            if presets_mod.CATEGORY[want]["gender"] in ("female", "male"):
                g = presets_mod.CATEGORY[want]["gender"]
            pid = voices.pick_preset(g, self._others(name), self.cfg.settings.presets, role,
                                     narrator=want == "narrator")
            pick = presets_mod.by_id(self.cfg.settings.presets).get(pid)
            if pick and presets_mod.category_of(pick) == want:
                self.cfg.update_character(self.game, name, preset=pid, voice_override="")
                self._char_refresh(name)
                self._set_status(f"{name} is now voiced by {pick['name']} "
                                 f"({presets_mod.CATEGORY[want]['title']})")
                self._preview(name)
                return
        self._char_refresh(name)

    def _cast_group_changed(self, group: str):
        self.cfg.settings.cast_group = group
        self.cfg.save()

    def _char_reroll(self, name: str):
        rec = self.cfg.character(self.game, name)
        others = self._others(name)
        g = rec.get("gender", "unknown")
        want = presets_mod.ROLE_CATEGORY.get(rec.get("role", ""))
        pool = [p for p in self.cfg.settings.presets
                if (g == "unknown" or p.get("gender") == g)
                and presets_mod.category_of(p) != "narrator"]
        if want:                            # a role: stay in its group
            pool = [p for p in self.cfg.settings.presets
                    if presets_mod.category_of(p) == want] or pool
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
                                     self.cfg.settings.presets, chars[name].get("role", ""))
            chars[name].update(preset=pid, voice_override="")
            if not pid:
                chars[name].update(voices.custom_design(name, g, chars))
        self.cfg.save()
        self._reload_characters()
        for n in chars:
            self.bank.prewarm(self.cfg.design(self.game, n))
        self._set_status("Every character got a fresh voice for their gender and role")

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
        if set(changes) <= {"category", "best_for"}:     # listing only: the voice is the same
            if "category" in changes:
                self._reload_presets()
            return
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
        line = presets_mod.sample_line(p)
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
        self._set_status(f"Presets restored to the {len(s.presets)} built-in voices")

    # ================================================== pause when away
    def _check_away(self):
        """Pause the voice and stop looking at the screen while another program is in
        front of the game; carry on when the game is back."""
        s = self.cfg.settings
        exe = (s.target_window or {}).get("exe", "")
        front = not exe or not winapi.is_away(winapi.foreground_kind(), exe)
        if self.clock is not None:      # play time: reading, game in front, voice not paused
            counting = self.ocr.running and front and not self.player.paused
            self.clock.tick(time.time(), counting)
            self._timer_ticks = getattr(self, "_timer_ticks", 0) + 1
            if self._timer_ticks % 4 == 0:
                self._update_timer(counting)
        away = bool(s.pause_when_away and exe and self.ocr.running and not front)
        if away and not self._auto_paused:
            self._auto_paused, self._was_paused = True, self.player.paused
            self._sync_ocr_pause()
            if not self.player.paused:
                self._toggle_pause()
            self._status_msg("Paused: the game isn't in front (Settings → pause when I "
                             "switch to another program)", 4000)
        elif not away and self._auto_paused:
            self._auto_paused = False
            self._sync_ocr_pause()
            if self.player.paused and not self._was_paused:   # a manual pause stays
                self._toggle_pause()

    # ============================================================ controls
    def _toggle_pause(self):
        if self.clock is not None:          # count play time up to the press, not after
            self.clock.tick(time.time(), self.clock.counting)
        self.player.paused = not self.player.paused
        if self.clock is not None:
            if self.player.paused:
                self.clock.tick(time.time(), False)
            self._update_timer(self.clock.counting and not self.player.paused)
        self.btn_pause.setIcon(theme.icon("play" if self.player.paused else "pause",
                                          "TEXT_2", 16, active="TEXT"))
        if hasattr(self, "tray"):
            self.tray.update_state()
        self._update_chat_head()

    def _stop_voice(self):
        self._mood_demo.clear()
        self.queue.stop()

    def _mute(self, on: bool):
        self.cfg.settings.muted = on
        self.btn_mute.setIcon(theme.icon("volume-x" if on else "volume-2", theme.TEXT_2, 16,
                                         active=theme.TEXT))
        self.player.volume = 0.0 if on else self.cfg.settings.volume
        self.cfg.save()
        self._update_chat_head()

    def _volume(self, v: int):
        self.cfg.settings.volume = v / 100
        if not self.cfg.settings.muted:
            self.player.volume = v / 100
        self.cfg.save()

    def _speed(self, v: int):
        self.cfg.settings.speed = v / 100
        self.lbl_speed.setText(f"{v / 100:.2f}×")
        self.cfg.save()

    def _apply_hotkeys(self):
        hk = self.cfg.settings.hotkeys
        actions = {
            "toggle": (hk.get("toggle", ""), self._toggle_ocr),
            "replay": (hk.get("replay", ""), self.queue.replay),
            "pause": (hk.get("pause", ""), self._toggle_pause),
            "stop": (hk.get("stop", ""), self._stop_voice),
            "advance": (hk.get("advance", "F7"), self._toggle_auto),
            "areas": (hk.get("areas", "F6"), self._pick_areas_sequence),
        }
        hk = {a: seq for a, (seq, _fn) in actions.items()}
        errors = self.hotkeys.set(actions)
        parts = [f"{hk[a]} {label}" for a, label in
                 (("toggle", "start/stop"), ("replay", "replay"), ("pause", "pause"),
                  ("stop", "stop"), ("advance", "auto-advance"), ("areas", "areas"))
                 if hk.get(a)]
        self.lbl_keys.setText("   ".join(parts) if parts else "")
        key = hk.get("areas")
        if hasattr(self, "btn_areas"):
            self.btn_areas.setToolTip("Pick name + dialogue areas from the game" +
                                      (f"   {key}" if key else ""))
        self._update_ocr_button()
        self._update_checklist()
        if hasattr(self, "tray"):
            self.tray.update_state()
        if errors:
            self._set_status("; ".join(errors))

    # ============================================================== voices
    def _vs_changed(self):
        s = self.cfg.settings
        self.vs.base_url = s.voicestudio_url.rstrip("/")
        self.vs.api_key = s.voicestudio_api_key
        self.vs.num_step = QUALITY_STEPS.get(s.vs_quality)
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
                    # Clones no voice uses any more (e.g. the old built-in voice set).
                    n += len(self.bank.prune(self.cfg.used_voice_keys()))
                    if n:
                        self.bridge.status.emit(f"Removed {n} old voice(s) from "
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
        def work():
            ok = self.vs.available()
            try:
                self.bridge.health.emit(ok)
            except RuntimeError:                 # the window closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_health(self, ok: bool):
        was = getattr(self, "_vs_ok", False)
        self._vs_ok = ok
        st = self.vs_starter
        if ok:
            if st.started_at:
                self._toast("VoiceStudio is ready")
            st.ready()
        else:
            s = self.cfg.settings
            if getattr(self, "_vs_autostart_armed", False):
                self._vs_autostart_armed = False
                res = st.maybe_start(s.voicestudio_url, s.vs_exe, s.vs_autostart)
                if res == "started":
                    self._status_msg("Starting VoiceStudio…", 6000)
                elif res == "not-found":
                    self._status_msg("VoiceStudio isn't installed where expected: set its "
                                     "program in Settings → Voice", 8000)
            if st.timed_out():
                st.ready()
                self._toast("VoiceStudio didn't start. Open it yourself, then VN Reader "
                            "connects by itself.", theme.WARN)
        # Check every second while it starts, else every 5 s.
        self._health_timer.setInterval(1000 if st.starting else 5000)
        self._update_vs_light()
        if ok and not was:
            self._refresh_voices()
        self._update_checklist()

    def _start_voicestudio(self):
        """Status light menu: start it now (also when auto-start is off)."""
        s = self.cfg.settings
        self.vs_starter.started_at = 0.0
        res = self.vs_starter.maybe_start(s.voicestudio_url, s.vs_exe, True)
        msg = {"started": "Starting VoiceStudio…",
               "running": "VoiceStudio is open and still loading",
               "not-found": "VoiceStudio isn't installed where expected: set its program in "
                            "Settings → Voice",
               "not-local": "VoiceStudio runs on another computer: start it there",
               "failed": "Couldn't start VoiceStudio"}.get(res, "")
        if msg:
            self._status_msg(msg, 6000)
        self._check_health()

    def _update_vs_light(self):
        """The VoiceStudio status light: ready, making a voice, starting or offline."""
        if not hasattr(self, "st_vs"):
            return
        ok = getattr(self, "_vs_ok", False)
        vs, st = self.vs, self.vs_starter
        if ok and (vs.busy or vs.queued):
            q = vs.queued
            state = ("VoiceStudio: making voice" + (f" ({q} waiting)" if q else ""),
                     "ACCENT", True)
        elif ok:
            state = ("VoiceStudio", "OK", False)
        elif st.starting:
            state = ("Starting VoiceStudio…", "WARN", True)
        else:
            state = ("VoiceStudio offline", "DANGER", False)
        if getattr(self, "_vs_state", None) == state:
            return
        self._vs_state = state
        info = vs.info or {}
        q_label = {"fast": "Instant", "balanced": "Balanced", "best": "Best"}.get(
            self.cfg.settings.vs_quality, "")
        lines = []
        if ok:
            dev = info.get("device", "")
            lines.append("Connected" + (f" on {dev}" if dev else "") +
                         (f", version {info['version']}" if info.get("version") else ""))
        elif st.starting:
            lines.append("VoiceStudio is starting. Windows voices are used until it's ready.")
        else:
            lines.append("Windows voices are used until VoiceStudio runs. Click to start it.")
        if q_label:
            lines.append(f"Speed / quality: {q_label}")
        last = getattr(self, "_last_latency", None)
        if last is not None:
            lines.append(f"Last line started {last:.1f} s after the text appeared")
        if vs.last_error:
            when = time.strftime("%H:%M", time.localtime(vs.last_error_at))
            lines.append(f"Last error ({when}): {vs.last_error[:160]}")
        self.st_vs.set(state[0], state[1], pulse=state[2], tip="\n".join(lines))

    def _vs_menu(self, at):
        m = QMenu(self)
        if not getattr(self, "_vs_ok", False):
            m.addAction(theme.icon("play", "TEXT_2", active="ON_ACCENT"), "Start VoiceStudio",
                        self._start_voicestudio)
        m.addAction(theme.icon("refresh-cw", "TEXT_2", active="ON_ACCENT"), "Check again",
                    lambda: (setattr(self.vs, "_ok_until", 0), self._check_health()))
        m.addAction(theme.icon("gauge" if theme.has_icon("gauge") else "settings", "TEXT_2",
                               active="ON_ACCENT"), "Speed and quality…",
                    self._open_voice_settings)
        if self.vs.last_error:
            m.addSeparator()
            m.addAction(theme.icon("copy", "TEXT_2", active="ON_ACCENT"), "Copy last error",
                        lambda: QApplication.clipboard().setText(self.vs.last_error))
        m.exec(at - QPoint(0, m.sizeHint().height() + 4))

    def _show_shortcuts(self):
        from .settings_view import HOTKEY_LABELS
        from .shortcuts import ShortcutsDialog

        def change():
            self.tabs.setCurrentIndex(7)
            for i, (title, _ic, _d) in enumerate(_SETTINGS_SECTIONS):
                if title == "Hotkeys":
                    self.settings_view.show_section(i)
        ShortcutsDialog(self, self.cfg.settings.hotkeys, HOTKEY_LABELS, change).exec()

    def _open_voice_settings(self):
        self.tabs.setCurrentIndex(7)
        for i, (title, _ic, _d) in enumerate(_SETTINGS_SECTIONS):
            if title == "Voice":
                self.settings_view.show_section(i)

    def _set_status(self, msg: str):
        self._status_msg(msg)
        # The OCR thread may stop itself (e.g. OCR unavailable): keep the button honest.
        self._update_ocr_button()

    def _on_latency(self, total: float):
        """Voice started: show how long after the text appeared (details: latency.log)."""
        self._last_latency = total
        self._vs_state = None                    # refresh the status light's tooltip
        if self.clock is not None:
            self.clock.delay(total)
        msg = f"Voice started {total:.1f} s after the text appeared"
        if total > 2.0 and not getattr(self, "_typing_tip", False):
            self._typing_tip = True
            msg += ". Tip: set the game's text speed to instant for a faster voice"
        self._status_msg(msg, 4000)

    def _status_msg(self, msg: str, ms: int = 8000):
        if not hasattr(self, "st_msg"):
            return
        self.st_msg.setText(msg)
        self.st_msg.setToolTip(msg)
        self._msg_token = getattr(self, "_msg_token", 0) + 1
        t = self._msg_token
        QTimer.singleShot(ms, lambda: t == self._msg_token and self.st_msg.setText(""))

    def _toast(self, msg: str, color: str = ""):
        """A short pop-up message (also in the status bar; a tray balloon when hidden)."""
        self._status_msg(msg)
        if self.isVisible() and not self.isMinimized():
            self.toast.show_message(msg, color=color)
        elif hasattr(self, "tray"):
            self.tray.notify(msg)

    # ================================================================ tray
    def _to_tray(self) -> bool:
        s = self.cfg.settings
        return (hasattr(self, "tray") and s.show_tray and s.close_to_tray
                and not self._quitting)

    def _apply_tray(self):
        """Show or hide the tray icon (Settings → Window & tray)."""
        if not hasattr(self, "tray"):
            return
        if self.cfg.settings.show_tray:
            self.tray.icon.show()
        else:
            self.tray.hide()
            if not self.isVisible():          # never leave the app invisible
                self.tray.show_window()

    # ========================================================= chat head
    def _build_chat_head(self):
        """The floating control bubble (Settings → Window & tray)."""
        actions = {
            "toggle": lambda: self._toggle_ocr(),
            "pause": lambda: self._toggle_pause(),
            "replay": lambda: self.queue.replay(),
            "stop": lambda: self._stop_voice(),
            "auto": lambda: self._toggle_auto(),
            "mute": lambda: self.btn_mute.setChecked(not self.btn_mute.isChecked()),
            "open": lambda: self._show_window(),
            "hide": lambda: self._set_chat_head(False),
            "quit": lambda: self.quit_app(),
        }
        self._float_actions = actions
        old = getattr(self, "chat_head", None)
        key_for = lambda a: self.cfg.settings.hotkeys.get(a, "")       # noqa: E731
        if self.cfg.settings.float_style == "mini":
            from .mini_player import MiniPlayer
            self.chat_head = MiniPlayer(actions, key_for, on_moved=self._mini_moved)
            self.chat_head.place(self.cfg.settings.mini_player_pos)
        else:
            self.chat_head = ChatHead(actions, asset_path("vnr.png"), key_for=key_for,
                                      on_moved=self._chat_head_moved)
            self.chat_head.place(self.cfg.settings.chat_head_pos)
        if old is not None:                      # switched style: replace the old one
            old.set_shown(False)
            old.deleteLater()
        self._update_chat_head()
        self._apply_chat_head()

    def _mini_moved(self, pos: list[int]):
        self.cfg.settings.mini_player_pos = pos
        self.cfg.save()

    def _set_float_style(self, style: str):
        if style not in ("bubble", "mini") or style == self.cfg.settings.float_style:
            return
        self.cfg.settings.float_style = style
        self.cfg.save()
        self._build_chat_head()

    def _update_chat_head(self, speaker: str | None = None, ring: str | None = None):
        if not hasattr(self, "chat_head"):
            return
        s = self.cfg.settings
        self.chat_head.update_state(self.ocr.running, self.player.paused, s.auto_advance,
                                    s.muted, speaker, ring)

    def _apply_chat_head(self):
        if hasattr(self, "chat_head"):
            self.chat_head.set_shown(self.cfg.settings.show_chat_head)
        if hasattr(self, "tray"):
            self.tray.update_state()

    def _set_chat_head(self, on: bool):
        self.cfg.settings.show_chat_head = on
        self.cfg.save()
        self.settings_view.set_chat_head_checked(on)
        self._apply_chat_head()
        if not on:
            self._toast("Bubble hidden. Bring it back from the tray menu or Settings → "
                        "Window & tray")

    def _chat_head_moved(self, pos: list[int]):
        self.cfg.settings.chat_head_pos = pos
        self.cfg.save()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _hide_to_tray(self):
        self.hide()
        if not self.cfg.settings.tray_hint_shown:
            self.cfg.settings.tray_hint_shown = True
            self.cfg.save()
            self.tray.notify("VN Reader is still running in the tray. Your hotkeys keep "
                             "working; right-click the icon to quit.")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and self.isMinimized() and self._to_tray():
            QTimer.singleShot(0, self._hide_to_tray)

    def quit_app(self):
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, e):
        if self._to_tray():
            e.ignore()
            self._hide_to_tray()
            return
        if hasattr(self, "tray"):
            self.tray.hide()
        if hasattr(self, "chat_head"):
            self.chat_head.set_shown(False)
            if hasattr(self.chat_head, "pill"):          # the bubble's control row
                self.chat_head.pill.close()
            self.chat_head.close()
        self.ocr.stop()
        self.hook.stop()
        self._end_session()
        self.playlog.save(force=True)
        self.hotkeys.clear()
        self.queue.shutdown()
        self.player.close()
        self.cfg.save()
        super().closeEvent(e)
        QApplication.instance().quit()      # (the tray keeps Qt from quitting by itself)
