from __future__ import annotations

import copy
import os
import threading
import time

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import presets as presets_mod
from .. import emotion, text_fixes, voices, winapi
from ..audio.player import Player
from ..config import NARRATOR, Config
from ..hotkeys import HotkeyManager
from ..name_split import split_name
from ..sources import ocr_filter, preprocess
from ..sources.capture import Capturer, CaptureSpec, frac_to_rect, rect_to_frac
from ..sources.ocr_source import OcrOptions, OcrSource, WindowsOcr, make_ocr
from ..speech_queue import Line, SpeechQueue
from ..text_clean import clean, clean_name
from ..tts.sapi_fallback import SapiEngine
from ..tts.voicestudio import VoiceStudioEngine
from ..voice_bank import VoiceBank
from . import theme
from .characters_view import CUSTOM, CharactersView
from .components import (Avatar, Dialog, button, confirm, hline, icon_button, label,
                         prompt, section)
from .dashboard import (AreaPreview, CastList, LevelMeter, StatsLabel, Toast, Transcript,
                        character_menu, fade_in, stats_chips)
from .shell import FadeStack, Page, Sidebar, StatusSegment, TabsAdapter
from .fixups_view import FixupsView
from .history_view import HistoryView
from .presets_view import PresetsView
from .region_picker import RegionPicker
from .screenshot_view import ScreenshotView
from .settings_view import QUALITY_STEPS, SettingsView
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
        self.vs = VoiceStudioEngine(s.voicestudio_url, s.voicestudio_api_key)
        self.vs.num_step = QUALITY_STEPS.get(s.vs_quality, 16)
        self.sapi = SapiEngine()
        self.bank = VoiceBank(self.cfg, self.vs, on_status=self.bridge.status.emit,
                              on_ready=self.bridge.voice_ready.emit)
        self.queue = SpeechQueue(self.vs, self.sapi, self.player,
                                 speed_getter=lambda: self.cfg.settings.speed,
                                 on_status=self.bridge.status.emit, bank=self.bank)
        self.queue.expressive = lambda: self.cfg.settings.expressive
        self.queue.emotion_voices = lambda: self.cfg.settings.emotion_voices
        self.queue.vocalize = lambda: self.cfg.settings.vocalize
        self.queue.quick_steps = self._quick_steps
        self.queue.on_done = lambda _line: self.bridge.line_done.emit()
        self.bridge.line_done.connect(self._schedule_advance)
        self._advance_token = 0
        self.ocr = OcrSource(self.bridge.lines.emit, on_status=self.bridge.status.emit,
                             on_preview=self.bridge.preview.emit,
                             on_candidate=self.bridge.candidate.emit)

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
        self.hotkeys = HotkeyManager(int(self.winId()))
        self._apply_hotkeys()

        self._health_timer = QTimer(self, interval=5000, timeout=self._check_health)
        self._health_timer.start()
        self._check_health()
        self._speak_timer = QTimer(self, interval=200, timeout=self._update_speaking)
        self._speak_timer.start()

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
               ("replace", "Fix-ups", "Ctrl+5"), ("history", "History", "Ctrl+6")]
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
        b_gmenu = icon_button("ellipsis", "Game actions", size=15)
        b_gmenu.clicked.connect(lambda: self._game_menu(b_gmenu))
        gr.addWidget(b_gmenu)
        gl.addLayout(gr)
        self.sidebar.bottom.addWidget(self.game_box)
        self.sidebar.bottom.addWidget(hline())
        self.sidebar.bottom.addSpacing(4)
        self.sidebar.add("settings", "Settings", "Ctrl+,", bottom=True)
        self.sidebar.compact_hide = [self.game_box]

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

        self.chars = CharactersView()
        self.chars.changed.connect(self._char_changed)
        self.chars.voice_chosen.connect(self._char_voice)
        self.chars.gender_changed.connect(self._char_gender)
        self.chars.reroll.connect(self._char_reroll)
        self.chars.preview.connect(self._preview)
        self.chars.reassign_all.connect(self._reassign_all)
        self.chars.refresh.connect(self._refresh_voices)
        self.chars.menu.connect(self._char_menu)
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
        self.settings_view.names_changed.connect(
            lambda on: (self.cb_names.blockSignals(True), self.cb_names.setChecked(on),
                        self.cb_names.blockSignals(False)))
        settings_page = Page("Settings")
        settings_page.set_body(self.settings_view)

        pages = [self.read_page, shots_page, chars_page, voices_page, fix_page, hist_page,
                 settings_page]
        for p in pages:
            self.stack.addWidget(p)
        self.tabs = TabsAdapter(self.stack, self.sidebar, pages, {})
        self.tabs._widgets = {}
        for content, i in ((self.read_body, 0), (self.shots, 1), (self.chars, 2),
                           (self.presets_view, 3), (self.fixups, 4), (self.history, 5),
                           (self.settings_view, 6)):
            self.tabs.register(content, i)
        self.sidebar.page_requested.connect(self.tabs.setCurrentIndex)
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(
            lambda _i: self.tabs.currentWidget() is self.shots and self._warm_shot_engine())

        # Keyboard: pages, sidebar, inspector, start/stop, setup.
        for i, key in enumerate(["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6",
                                 "Ctrl+,"]):
            QShortcut(QKeySequence(key), self, activated=lambda i=i: self.tabs.setCurrentIndex(i))
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.sidebar.toggle)
        QShortcut(QKeySequence("Ctrl+I"), self, activated=self._toggle_inspector)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._toggle_ocr)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self._open_setup)
        self.sidebar.collapse_toggled.connect(self._sidebar_toggled)
        self.sidebar.set_compact(bool(s.sidebar_collapsed))

        self.setCentralWidget(root)
        self._build_status_bar()
        self.lbl_keys = self.st_keys
        self.toast = Toast(root)
        self._build_setup_dialog()
        self._load_game(self.game)
        self._reload_presets()

    def _build_status_bar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)
        self.st_state = StatusSegment(dot=True)
        self.st_game = StatusSegment("gamepad-2")
        self.st_ocr = StatusSegment("scan-text")
        self.st_vs = StatusSegment(dot=True)
        self.st_msg = QLabel("")
        self.st_keys = QLabel("")
        for w in (self.st_state, self.st_game, self.st_ocr, self.st_vs):
            sb.addWidget(w)
        sb.addWidget(self.st_msg, 1)
        sb.addPermanentWidget(self.st_keys)
        self.st_vs.set("VoiceStudio …", theme.TEXT_3)
        self.pill_vs = self.st_vs          # (older code paths)
        self._vs_ok = False

    def _sidebar_toggled(self, compact: bool):
        self.cfg.settings.sidebar_collapsed = compact
        self.cfg.save()

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
            self.toast.move(self.centralWidget().width() - self.toast.width() - 16,
                            self.centralWidget().height() - self.toast.height() - 40)

    def _game_menu(self, anchor):
        m = QMenu(self)
        m.addAction(theme.icon("settings", theme.TEXT_2), "Game setup…   Ctrl+Shift+S",
                    self._open_setup)
        m.addAction(theme.icon("scan-text", theme.TEXT_2), "Auto-detect name + dialogue areas",
                    self._auto_detect_areas)
        m.addAction(theme.icon("crosshair", theme.TEXT_2), "Pick name + dialogue areas   F6",
                    self._pick_areas_sequence)
        m.addSeparator()
        m.addAction(theme.icon("trash-2", theme.DANGER), "Delete this game…", self._delete_game)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ------------------------------------------------------------ Read page
    def _build_read_page(self) -> QWidget:
        s = self.cfg.settings
        page = Page(self.game)
        self.lbl_game = page.header.title
        self.lbl_game.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_game.setMinimumWidth(90)
        self.lbl_stats = StatsLabel()
        page.header.actions.insertWidget(1, self.lbl_stats)
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
        now = QWidget()
        now.setObjectName("workspace")
        nl = QVBoxLayout(now)
        nl.setContentsMargins(28, 24, 28, 14)
        nl.setSpacing(10)
        nl.addWidget(section("Now speaking"))
        who = QHBoxLayout()
        who.setSpacing(12)
        self.av = Avatar(NARRATOR, 36)
        who.addWidget(self.av)
        wv = QVBoxLayout()
        wv.setSpacing(1)
        self.lbl_speaker = QLabel("—")
        self.lbl_speaker.setObjectName("speaker")
        self.lbl_voice = QLabel("")
        self.lbl_voice.setObjectName("mono")
        self.lbl_voice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wv.addWidget(self.lbl_speaker)
        wv.addWidget(self.lbl_voice)
        who.addLayout(wv, 1)
        self.meter = LevelMeter()
        who.addWidget(self.meter)
        nl.addLayout(who)
        self.lbl_text = QLabel("Pick your game, press F6 in the game to mark the name and "
                               "dialogue, then start reading.")
        self.lbl_text.setObjectName("dialogue")
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setMinimumHeight(64)
        self.lbl_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nl.addWidget(self.lbl_text)
        wl.addWidget(now)
        wl.addWidget(hline())
        tr_head = QHBoxLayout()
        tr_head.setContentsMargins(28, 12, 28, 4)
        tr_head.addWidget(section("Transcript"))
        tr_head.addStretch(1)
        tr_head.addWidget(label("click a line to hear it again", "faint"))
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
        self.lbl_speed.setObjectName("mono")
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
        lh.addWidget(label(f"<span style='color:{theme.NAME}'>■</span> name  "
                           f"<span style='color:{theme.DIALOGUE}'>■</span> dialogue", "mono"))
        il.addLayout(lh)
        self.preview = AreaPreview()
        self.preview.setMinimumHeight(110)
        il.addWidget(self.preview, 2)

        il.addWidget(section("Areas"))
        self.lbl_name_area = QLabel()
        self.lbl_name_area.setObjectName("mono")
        self.lbl_region = QLabel()
        self.lbl_region.setObjectName("mono")
        for lbl, which, tip in ((self.lbl_name_area, "name", "name"),
                                (self.lbl_region, "text", "dialogue")):
            r = QHBoxLayout()
            r.setSpacing(4)
            dot = QLabel("■")
            dot.setStyleSheet(f"color:{theme.NAME if which == 'name' else theme.DIALOGUE};")
            r.addWidget(dot)
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
        d = Dialog(self, "Game setup", "Which window to read, what this game's profile is "
                   "called, and where the name and dialogue appear.", "settings")
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
        form.addRow(label("Window", "muted"), wr)
        pr = QHBoxLayout()
        pr.setSpacing(10)
        self.ed_profile = QLineEdit(s.ocr_profile)
        self.ed_profile.setToolTip("Characters, voices and fix-ups are saved under this name")
        self.ed_profile.editingFinished.connect(self._profile_changed)
        pr.addWidget(self.ed_profile, 1)
        pr.addWidget(label("Language", "muted"))
        self.ed_lang = QLineEdit(s.ocr_language)
        self.ed_lang.setToolTip("OCR language tag, e.g. en-US, ja, zh-Hans")
        self.ed_lang.setFixedWidth(84)
        self.ed_lang.editingFinished.connect(self._lang_changed)
        pr.addWidget(self.ed_lang)
        form.addRow(label("Profile", "muted"), pr)
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
                self.ed_profile.setText(known[0] if known else
                                        (os.path.splitext(w.exe)[0] or w.title))
                self._profile_changed()
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
        self.lbl_region.setText(("Dialogue · " + ("window" if spec.mode == "window" else "screen"))
                                if spec.text else "Dialogue · not set")
        self.lbl_region.setToolTip(f"Dialogue area: {where}" if spec.text else
                                   "No dialogue area yet: press F6 in the game")
        if hasattr(self, "btn_clear_name"):
            self.btn_clear_name.setEnabled(bool(spec.name))
            self.lbl_name_area.setText("Name · set" if spec.name else "Name · top line")
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
            f"<span style='color:{theme.TEXT_3}'>—</span>"
        win = bool(self.cfg.settings.target_window)
        key = self.cfg.settings.hotkeys.get("areas", "F6") or "the crosshair button"
        rows = [(vs, "VoiceStudio", "" if vs else "offline: Windows voices are used"),
                (win, "Game window", "" if win else "optional: whole screen"),
                (bool(spec.text), "Dialogue area", "" if spec.text else f"press {key} in the game"),
                (bool(spec.name), "Name area", "" if spec.name else "optional")]
        html = "<br>".join(f"{ok(b)}&nbsp; {t}" + (f" <span style='color:{theme.TEXT_3}'>· {h}</span>"
                                                     if h else "") for b, t, h in rows)
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
                                        theme.TEXT if running else theme.BG, 15))
        if hasattr(self, "st_state"):
            self.st_state.set("Reading" if running else "Stopped",
                              theme.OK if running else theme.TEXT_3, pulse=running)
        if hasattr(self, "tray"):
            self.tray.update_state()

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
        """Steps for each line's first sentence: fast, so the voice starts sooner."""
        s = self.cfg.settings
        if not s.quick_start or s.vs_quality == "fast":
            return None
        return QUALITY_STEPS["fast"]

    def _on_candidate(self, lines: list, boxes: list | None = None, info=None):
        """First read of a new line (still being confirmed): start making its voice."""
        try:
            name, text, _ = self._interpret(lines, boxes, info)
        except Exception:
            return
        if not text or name not in self.cfg.characters(self.game):
            return                       # never create characters from an unconfirmed read
        if name == NARRATOR and not self.cfg.settings.read_narrator:
            return
        self.queue.prefetch(self._make_line(name, text))

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
        self._learn_from(name, text, info)
        self._show_and_speak(name, text, auto_advance)
        return name, text

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

    def _show_and_speak(self, name: str, text: str, auto_advance: bool = True):
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
        self.transcript.add(name, text)
        self._advance_ok = auto_advance      # screenshots never move the game on
        if name == NARRATOR and not self.cfg.settings.read_narrator:
            self._schedule_advance()         # not spoken: still move on
            return
        self._speak(name, text)

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

    def _show_line(self, name: str, text: str, rec: dict, design: dict):
        g = rec.get("gender", "unknown")
        self.av.set_name(name, ring="" if name == NARRATOR else theme.gender_color(g))
        fade_in(self.lbl_text)
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
        if self.cfg.settings.expressive:
            mood = emotion.parse(text).mood
            if mood:
                self.lbl_voice.setText(f"{self.lbl_voice.text()} · {mood}")
        self.lbl_text.setText(text)

    def _make_line(self, name: str, text: str) -> Line:
        rec = self.cfg.character(self.game, name)
        if self.cfg.settings.read_names and name != NARRATOR:
            text = f"{name}. {text}"
        return Line(self.game, name, text, dict(rec), iso_lang(self.cfg.settings.ocr_language),
                    self.cfg.design(self.game, name))

    def _speak(self, name: str, text: str):
        self.queue.say(self._make_line(name, text))

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
        if not confirm(self, "Delete game",
                       f"Delete “{g}” from your history? This removes its {n} character"
                       f"{'s' if n != 1 else ''}, their voice choices, fix-ups and reading "
                       "areas. Your shared voice presets are kept.", "Delete game",
                       destructive=True):
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
        self._set_status("Presets restored to the built-in 15 female + 4 male voices")

    # ============================================================ controls
    def _toggle_pause(self):
        self.player.paused = not self.player.paused
        self.btn_pause.setIcon(theme.icon("play" if self.player.paused else "pause",
                                          theme.TEXT_2, 16, active=theme.TEXT))

    def _mute(self, on: bool):
        self.cfg.settings.muted = on
        self.btn_mute.setIcon(theme.icon("volume-x" if on else "volume-2", theme.TEXT_2, 16,
                                         active=theme.TEXT))
        self.player.volume = 0.0 if on else self.cfg.settings.volume
        self.cfg.save()

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
            "stop": (hk.get("stop", ""), self.queue.stop),
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
        threading.Thread(target=lambda: self.bridge.health.emit(self.vs.available()),
                         daemon=True).start()

    def _on_health(self, ok: bool):
        was = getattr(self, "_vs_ok", False)
        self._vs_ok = ok
        self.st_vs.set("VoiceStudio" if ok else "VoiceStudio offline",
                       theme.OK if ok else theme.WARN,
                       tip="Connected" if ok else "Using Windows voices until VoiceStudio runs")
        if ok and not was:
            self._refresh_voices()
        self._update_checklist()

    def _set_status(self, msg: str):
        self._status_msg(msg)
        # The OCR thread may stop itself (e.g. OCR unavailable): keep the button honest.
        self._update_ocr_button()

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
        self.ocr.stop()
        self.hotkeys.clear()
        self.queue.shutdown()
        self.player.close()
        self.cfg.save()
        super().closeEvent(e)
        QApplication.instance().quit()      # (the tray keeps Qt from quitting by itself)
