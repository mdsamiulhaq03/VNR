"""Settings tab: OCR cleanup, text colour, hotkeys, voices server."""
from __future__ import annotations

from typing import Callable

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

from .. import voices
from ..config import DEFAULT_HOTKEYS
from ..sources import preprocess

MODES = [("learn", "Auto + learned text colour (recommended)"), ("auto", "Auto"),
         ("color", "Text colour only (pick it below)"), ("off", "Off")]
QUALITY = [("fast", "Fast: starts talking quickest"), ("balanced", "Balanced (recommended)"),
           ("best", "Best: slowest")]
QUALITY_STEPS = {"fast": 8, "balanced": 16, "best": None}
NAME_MODES = [("auto", "Auto-detect (recommended)"), ("always", "Always: top line is the name"),
              ("never", "Never: no names in this game")]
HOTKEY_LABELS = {"toggle": "Start / stop reading", "replay": "Replay last line",
                 "pause": "Pause / resume", "stop": "Stop speaking",
                 "advance": "Auto-advance on / off",
                 "areas": "Pick name + dialogue areas"}
ADVANCE_KEYS = [("enter", "Press Enter"), ("space", "Press Space"),
                ("click", "Click the dialogue box"), ("ctrl", "Press Ctrl")]


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    q = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(q.copy())


class ColorPickDialog(QDialog):
    """Shows the captured text area; click on a letter to choose the text colour."""

    def __init__(self, img: Image.Image, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Click on the text colour")
        self.img = img.convert("RGB")
        self.color: list[int] | None = None
        scale = max(1, min(3, 900 // max(1, self.img.width)))
        self.scale = scale
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Click directly on a letter of the dialogue text."))
        self.lbl = QLabel()
        self.lbl.setPixmap(pil_to_pixmap(self.img.resize((self.img.width * scale,
                                                          self.img.height * scale),
                                                         Image.NEAREST)))
        self.lbl.setCursor(Qt.CrossCursor)
        self.lbl.mousePressEvent = self._click
        sc = QScrollArea()
        sc.setWidget(self.lbl)
        sc.setWidgetResizable(False)
        lay.addWidget(sc)

    def _click(self, e):
        p = e.position().toPoint()
        x = min(self.img.width - 1, max(0, p.x() // self.scale))
        y = min(self.img.height - 1, max(0, p.y() // self.scale))
        # Average a 3x3 patch so anti-aliased edges don't skew the colour.
        px = [self.img.getpixel((min(self.img.width - 1, max(0, x + dx)),
                                 min(self.img.height - 1, max(0, y + dy))))
              for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        center = self.img.getpixel((x, y))
        close = [c for c in px if sum(abs(a - b) for a, b in zip(c, center)) < 90] or [center]
        self.color = [int(sum(c[i] for c in close) / len(close)) for i in range(3)]
        self.accept()


class OcrTestDialog(QDialog):
    def __init__(self, raw: Image.Image, processed: Image.Image, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR test")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("What the app captured:"))
        a = QLabel()
        a.setPixmap(pil_to_pixmap(raw).scaledToWidth(min(800, raw.width * 2),
                                                     Qt.SmoothTransformation))
        lay.addWidget(a)
        lay.addWidget(QLabel("After cleanup (what OCR sees):"))
        b = QLabel()
        b.setPixmap(pil_to_pixmap(processed).scaledToWidth(min(800, processed.width),
                                                           Qt.SmoothTransformation))
        lay.addWidget(b)
        lay.addWidget(QLabel("Recognized text:"))
        t = QTextEdit(text or "(nothing recognized)")
        t.setReadOnly(True)
        t.setMinimumHeight(220)
        t.setMinimumWidth(560)
        lay.addWidget(t)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close)


class _Sections:
    """Collects the settings groups; SettingsView lays them out as sections."""

    def __init__(self):
        self.groups = []

    def addWidget(self, w):
        self.groups.append(w)

    def addStretch(self, *_a):
        pass


SECTIONS = [  # (nav title, icon, description), in the order the groups are built
    ("General", "app-window", "Window and system tray behaviour."),
    ("Text recognition", "scan-text", "How the game text is read from the screen."),
    ("Hotkeys", "keyboard", "Global shortcuts. They work while the game has focus."),
    ("Voice", "mic", "VoiceStudio connection, voice quality and what gets spoken."),
    ("Auto-advance", "play", "Hands-free reading: move the game on after each line."),
    ("Text clean-up", "eraser", "Menu words to ignore and automatic OCR fixes."),
]


class SettingsView(QWidget):
    ocr_options_changed = Signal()
    hotkeys_changed = Signal()
    voicestudio_changed = Signal()
    accent_changed = Signal()
    names_changed = Signal(bool)
    tray_changed = Signal()

    def __init__(self, cfg, grab_text: Callable[[], Image.Image | None],
                 run_ocr: Callable[[Image.Image], tuple[Image.Image, str]],
                 on_status: Callable[[str], None]):
        super().__init__()
        self.cfg = cfg
        self.grab_text = grab_text
        self.run_ocr = run_ocr
        self.on_status = on_status
        s = cfg.settings
        outer = _Sections()

        # Window & tray
        wg = QGroupBox("Window && tray")
        wf = QVBoxLayout(wg)
        self.cb_show_tray = QCheckBox("Show VN Reader in the system tray (next to the clock)")
        self.cb_show_tray.setToolTip("A tray icon with Start/Stop, Pause, Auto-advance, Pick "
                                     "areas and Quit.")
        self.cb_show_tray.setChecked(s.show_tray)
        self.cb_show_tray.toggled.connect(self._show_tray_toggled)
        wf.addWidget(self.cb_show_tray)
        self.cb_tray = QCheckBox("Keep running in the tray when the window is closed or "
                                 "minimized")
        self.cb_tray.setToolTip("Reading and hotkeys keep working. Quit from the tray icon's "
                                "menu. Off: closing the window quits VN Reader.")
        self.cb_tray.setChecked(s.close_to_tray)
        self.cb_tray.setEnabled(s.show_tray)
        self.cb_tray.toggled.connect(self._tray_toggled)
        wf.addWidget(self.cb_tray)
        outer.addWidget(wg)

        # OCR cleanup
        g = QGroupBox("Text recognition")
        f = QFormLayout(g)
        self.cb_engine = QComboBox()
        self.cb_engine.addItem("PaddleOCR (best for game fonts, English)", "paddle")
        self.cb_engine.addItem("Windows OCR (built in, every language)", "windows")
        self.cb_engine.setToolTip(
            "PaddleOCR reads outlined, rounded and coloured game text much better. "
            "For other languages than English, Windows OCR is used automatically.")
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData(s.ocr_engine)))
        self.cb_engine.currentIndexChanged.connect(self._engine_changed)
        f.addRow("OCR engine:", self.cb_engine)
        self.cb_gpu = QCheckBox("Use the graphics card for OCR (DirectML)")
        self.cb_gpu.setToolTip(
            "Measured on an RTX 3060 Ti: about 1.7x faster for whole screenshots, but a bit "
            "slower for small dialogue boxes. The Screenshot tab always uses the graphics "
            "card when it can. PaddleOCR only.")
        self.cb_gpu.setChecked(s.ocr_gpu)
        self.cb_gpu.toggled.connect(self._gpu_changed)
        f.addRow("", self.cb_gpu)
        self.cb_mode = QComboBox()
        for key, label in MODES:
            self.cb_mode.addItem(label, key)
        self.cb_mode.setCurrentIndex(max(0, [m[0] for m in MODES].index(s.ocr_mode)
                                         if s.ocr_mode in [m[0] for m in MODES] else 0))
        self.cb_mode.currentIndexChanged.connect(self._mode_changed)
        f.addRow("Image cleanup:", self.cb_mode)

        crow = QHBoxLayout()
        self.swatch = QLabel()
        self.swatch.setFixedSize(28, 20)
        b_pick = QPushButton("Pick text colour…")
        b_pick.clicked.connect(self._pick_color)
        crow.addWidget(self.swatch)
        self.lbl_learned = QLabel("")
        self.lbl_learned.setObjectName("muted")
        crow.addWidget(self.lbl_learned)
        crow.addWidget(b_pick)
        crow.addStretch(1)
        f.addRow("Text colour:", crow)

        trow = QHBoxLayout()
        self.sl_tol = QSlider(Qt.Horizontal, minimum=10, maximum=160, value=s.color_tolerance)
        self.lbl_tol = QLabel(str(s.color_tolerance))
        self.sl_tol.valueChanged.connect(self._tol_changed)
        trow.addWidget(self.sl_tol)
        trow.addWidget(self.lbl_tol)
        f.addRow("Colour tolerance:", trow)

        self.cb_name = QComboBox()
        for key, label in NAME_MODES:
            self.cb_name.addItem(label, key)
        self.cb_name.setCurrentIndex(max(0, self.cb_name.findData(s.name_mode)))
        self.cb_name.currentIndexChanged.connect(self._name_mode)
        f.addRow("Name on top line:", self.cb_name)

        b_test = QPushButton("Test OCR on the reading area")
        b_test.clicked.connect(self._test_ocr)
        f.addRow("", b_test)
        outer.addWidget(g)

        # Hotkeys
        hk = QGroupBox("Hotkeys (work while you're in the game)")
        hf = QFormLayout(hk)
        self.hk_edits: dict[str, QKeySequenceEdit] = {}
        for action, label in HOTKEY_LABELS.items():
            row = QHBoxLayout()
            ed = QKeySequenceEdit(s.hotkeys.get(action, DEFAULT_HOTKEYS[action]))
            ed.setMaximumSequenceLength(1)
            ed.editingFinished.connect(self._save_hotkeys)
            clr = QPushButton("Clear")
            clr.setFixedWidth(68)
            clr.clicked.connect(lambda _=False, e=ed: (e.clear(), self._save_hotkeys()))
            row.addWidget(ed)
            row.addWidget(clr)
            self.hk_edits[action] = ed
            hf.addRow(label + ":", row)
        b_reset = QPushButton("Reset to defaults")
        b_reset.clicked.connect(self._reset_hotkeys)
        hf.addRow("", b_reset)
        outer.addWidget(hk)

        # Voices server / misc
        vg = QGroupBox("Voices")
        vf = QFormLayout(vg)
        self.ed_url = QLineEdit(s.voicestudio_url)
        self.ed_url.editingFinished.connect(self._vs_changed)
        vf.addRow("VoiceStudio address:", self.ed_url)
        self.ed_key = QLineEdit(s.voicestudio_api_key)
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("only needed for a remote VoiceStudio")
        self.ed_key.editingFinished.connect(self._vs_changed)
        vf.addRow("API key:", self.ed_key)
        self.cb_narr = QCheckBox("Read lines with no speaker name (narration)")
        self.cb_narr.setChecked(s.read_narrator)
        self.cb_narr.toggled.connect(self._narr)
        vf.addRow("", self.cb_narr)
        self.cb_names = QCheckBox("Speak character names (off: only the dialogue is spoken)")
        self.cb_names.setChecked(s.read_names)
        self.cb_names.toggled.connect(self._names)
        vf.addRow("", self.cb_names)
        self.cb_expr = QCheckBox("Act out emotions: *stage directions*, hearts, laughs, sighs")
        self.cb_expr.setToolTip("'*Seductively* Hello' is spoken as a seductive 'Hello'. "
                                "Stage directions are never read aloud, even with this off.")
        self.cb_expr.setChecked(s.expressive)
        self.cb_expr.toggled.connect(self._expr)
        vf.addRow("", self.cb_expr)
        self.cb_moodv = QCheckBox("Emotional voice variants (seductive, aroused, whisper, "
                                  "happy, sad, angry)")
        self.cb_moodv.setToolTip("Each voice gets a version in that mood. The first time a "
                                 "mood is needed it's made in the background (a few seconds)")
        self.cb_moodv.setChecked(s.emotion_voices)
        self.cb_moodv.toggled.connect(lambda on: self._flag("emotion_voices", on))
        vf.addRow("", self.cb_moodv)
        self.cb_vocal = QCheckBox("Voice action sounds (*moans*, *kisses*, *pants*, …)")
        self.cb_vocal.setToolTip("An action that is a sound is performed ('Mmmh…', 'Mwah.') "
                                 "instead of skipped")
        self.cb_vocal.setChecked(s.vocalize)
        self.cb_vocal.toggled.connect(lambda on: self._flag("vocalize", on))
        vf.addRow("", self.cb_vocal)
        for cb in (self.cb_moodv, self.cb_vocal):
            cb.setEnabled(s.expressive)
        self.cb_quality = QComboBox()
        for key, label in QUALITY:
            self.cb_quality.addItem(label, key)
        self.cb_quality.setCurrentIndex(max(0, self.cb_quality.findData(s.vs_quality)))
        self.cb_quality.currentIndexChanged.connect(self._quality)
        vf.addRow("Voice quality:", self.cb_quality)
        self.cb_quick = QCheckBox("Quick start: first sentence on fast quality "
                                  "(the voice starts ~1 s sooner)")
        self.cb_quick.setToolTip("The rest of the line uses the quality above, made while the "
                                 "first sentence plays. Same voice, slightly rougher first "
                                 "sentence.")
        self.cb_quick.setChecked(s.quick_start)
        self.cb_quick.toggled.connect(self._quick)
        vf.addRow("", self.cb_quick)
        self.cb_save = QCheckBox("Save each character's voice in my VoiceStudio library "
                                 "(keeps voices identical every line)")
        self.cb_save.setChecked(s.save_vs_profiles)
        self.cb_save.setToolTip("Recommended. Without it, VoiceStudio re-invents the voice for "
                                "every sentence, so it can change after a full stop.")
        self.cb_save.toggled.connect(self._save_profiles)
        vf.addRow("", self.cb_save)
        self.cb_accent_all = QComboBox()
        self.cb_accent_all.addItem("Keep each voice's own accent", "")
        for a in voices.ACCENTS:
            self.cb_accent_all.addItem(a.replace(" accent", "").capitalize(), a)
        self.cb_accent_all.setCurrentIndex(max(0, self.cb_accent_all.findData(s.accent_override)))
        self.cb_accent_all.currentIndexChanged.connect(self._accent_all)
        vf.addRow("Accent for all voices:", self.cb_accent_all)
        outer.addWidget(vg)

        # Auto-advance
        ag = QGroupBox("Auto-advance (hands-free reading)")
        af = QFormLayout(ag)
        self.cb_adv_key = QComboBox()
        for k, label in ADVANCE_KEYS:
            self.cb_adv_key.addItem(label, k)
        self.cb_adv_key.setCurrentIndex(max(0, self.cb_adv_key.findData(s.advance_key)))
        self.cb_adv_key.setToolTip("How to go to the next line. Enter works in most games "
                                   "(Ren'Py too); use Click if your game ignores keys.")
        self.cb_adv_key.currentIndexChanged.connect(self._adv_key)
        af.addRow("Next line by:", self.cb_adv_key)
        drow = QHBoxLayout()
        self.sl_delay = QSlider(Qt.Horizontal, minimum=0, maximum=40,
                                value=int(round(s.advance_delay * 10)))
        self.lbl_delay = QLabel(f"{s.advance_delay:.1f} s")
        self.lbl_delay.setFixedWidth(44)
        self.sl_delay.valueChanged.connect(self._adv_delay)
        drow.addWidget(self.sl_delay)
        drow.addWidget(self.lbl_delay)
        af.addRow("Pause after each line:", drow)
        note = QLabel("Turn it on with the Auto-advance switch on the Read tab or F7. "
                      "It stops by itself at choice menus (the text stops changing).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        af.addRow(note)
        outer.addWidget(ag)

        # Text clean-up
        cg = QGroupBox("Text clean-up")
        cf = QFormLayout(cg)
        self.ed_ignore = QTextEdit(", ".join(s.ignore_words))
        self.ed_ignore.setMaximumHeight(70)
        self.ed_ignore.setToolTip("Lines made of these words are treated as menu buttons and "
                                  "skipped. Separate with commas.")
        self.ed_ignore.textChanged.connect(self._ignore_changed)
        cf.addRow("Ignore menu words:", self.ed_ignore)
        self.cb_autocorrect = QCheckBox("Fix common OCR mistakes (English): rn→m, | →I, typos")
        self.cb_autocorrect.setChecked(s.ocr_autocorrect)
        self.cb_autocorrect.toggled.connect(self._autocorrect)
        cf.addRow("", self.cb_autocorrect)
        outer.addWidget(cg)
        self._build_sections(outer.groups)
        self._update_swatch()

    # ---- OCR ----
    def _update_swatch(self):
        c = self.cfg.settings.text_color
        learned = getattr(self, "_learned", None)
        if self.cfg.settings.ocr_mode == "learn" and learned:
            c = learned
        if c:
            self.swatch.setStyleSheet(f"background: {QColor(*c).name()}; border:1px solid gray;")
            src = "learned from this game's lines" if c is learned else "picked by you"
            self.swatch.setToolTip(f"RGB {list(c)} ({src})")
        else:
            self.swatch.setStyleSheet("border:1px dashed gray;")
            self.swatch.setToolTip("not set yet: with 'Auto + learned text colour' it's "
                                   "learned after a few lines")
        if hasattr(self, "lbl_learned"):
            self.lbl_learned.setText("(learned)" if c is not None and c is learned else "")

    def show_learned_colour(self, rgb):
        self._learned = rgb
        self._update_swatch()

    def _gpu_changed(self, on: bool):
        self.cfg.settings.ocr_gpu = on
        self.cfg.save()
        self.ocr_options_changed.emit()

    def _mode_changed(self):
        mode = self.cb_mode.currentData()
        self.cfg.settings.ocr_mode = mode
        self.cfg.save()
        if mode == "color" and not self.cfg.settings.text_color:
            self.on_status("Pick the text colour so this mode can work")
        self.ocr_options_changed.emit()
        self._update_swatch()

    def _tol_changed(self, v: int):
        self.lbl_tol.setText(str(v))
        self.cfg.settings.color_tolerance = v
        self.cfg.save()
        self.ocr_options_changed.emit()

    def _pick_color(self):
        img = self.grab_text()
        if img is None:
            self.on_status("Draw the text area first (and make sure the game is visible)")
            return
        d = ColorPickDialog(img, self)
        if d.exec() and d.color:
            self.cfg.settings.text_color = d.color
            self.cfg.settings.ocr_mode = "color"
            self.cb_mode.setCurrentIndex(self.cb_mode.findData("color"))
            self.cfg.save()
            self._update_swatch()
            self.ocr_options_changed.emit()

    def _test_ocr(self):
        img = self.grab_text()
        if img is None:
            self.on_status("Draw the text area first (and make sure the game is visible)")
            return
        try:
            processed, text = self.run_ocr(img)
        except Exception as e:
            self.on_status(f"OCR failed: {e}")
            return
        OcrTestDialog(img, processed, text, self).exec()

    # ---- hotkeys ----
    def _save_hotkeys(self):
        self.cfg.settings.hotkeys = {a: e.keySequence().toString() for a, e in
                                     self.hk_edits.items()}
        self.cfg.save()
        self.hotkeys_changed.emit()

    def _reset_hotkeys(self):
        for a, e in self.hk_edits.items():
            e.setKeySequence(DEFAULT_HOTKEYS[a])
        self._save_hotkeys()

    # ---- misc ----
    def _vs_changed(self):
        self.cfg.settings.voicestudio_url = self.ed_url.text().strip() or "http://127.0.0.1:3900"
        self.cfg.settings.voicestudio_api_key = self.ed_key.text().strip()
        self.cfg.save()
        self.voicestudio_changed.emit()

    def _narr(self, on: bool):
        self.cfg.settings.read_narrator = on
        self.cfg.save()

    def _adv_key(self):
        self.cfg.settings.advance_key = self.cb_adv_key.currentData()
        self.cfg.save()

    def _adv_delay(self, v: int):
        self.cfg.settings.advance_delay = v / 10
        self.lbl_delay.setText(f"{v / 10:.1f} s")
        self.cfg.save()

    def _names(self, on: bool):
        self.cfg.settings.read_names = on
        self.cfg.save()
        self.names_changed.emit(on)

    def _expr(self, on: bool):
        self.cfg.settings.expressive = on
        self.cfg.save()
        for cb in (self.cb_moodv, self.cb_vocal):
            cb.setEnabled(on)

    def _flag(self, name: str, on: bool):
        setattr(self.cfg.settings, name, on)
        self.cfg.save()

    def _accent_all(self):
        self.cfg.settings.accent_override = self.cb_accent_all.currentData() or ""
        self.cfg.save()
        self.accent_changed.emit()

    def _ignore_changed(self):
        words = [w.strip() for w in self.ed_ignore.toPlainText().replace("\n", ",").split(",")]
        self.cfg.settings.ignore_words = [w for w in words if w]
        self.cfg.save()
        self.ocr_options_changed.emit()

    def _autocorrect(self, on: bool):
        self.cfg.settings.ocr_autocorrect = on
        self.cfg.save()

    def _quality(self):
        self.cfg.settings.vs_quality = self.cb_quality.currentData()
        self.cfg.save()
        self.voicestudio_changed.emit()

    def _build_sections(self, groups):
        from .components import label, section
        from . import theme
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        nav_box = QWidget()
        nav_box.setObjectName("inspector")
        nav_box.setAttribute(Qt.WA_StyledBackground, True)
        nav_box.setStyleSheet(f"QWidget#inspector {{ border-left: none; border-right: 1px solid "
                              f"{theme.BORDER}; }}")
        nav_box.setFixedWidth(220)
        nl = QVBoxLayout(nav_box)
        nl.setContentsMargins(10, 14, 10, 10)
        nl.setSpacing(2)
        self.stack = QStackedWidget()
        self._nav: list[QPushButton] = []
        items = list(zip(SECTIONS, groups)) + [(("About", "info", ""), None)]
        for i, ((title, icon, desc), grp) in enumerate(items):
            b = QPushButton(title)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(theme.icon(icon, theme.TEXT_2, 15, active=theme.TEXT))
            b.clicked.connect(lambda _=False, i=i: self.show_section(i))
            nl.addWidget(b)
            self._nav.append(b)
            page = QWidget()
            page.setObjectName("page")
            pl = QVBoxLayout(page)
            pl.setContentsMargins(32, 26, 32, 26)
            pl.setSpacing(6)
            pl.addWidget(label(title, "pageTitle"))
            if desc:
                pl.addWidget(label(desc, "muted", wrap=True))
            pl.addSpacing(10)
            if grp is not None:
                grp.setTitle("")
                grp.setMaximumWidth(760)
                grp.setStyleSheet("QGroupBox { margin-top: 0; padding: 0; }")
                pl.addWidget(grp)
            else:
                self._about(pl)
            pl.addStretch(1)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        nl.addStretch(1)
        root.addWidget(nav_box)
        root.addWidget(self.stack, 1)
        self.show_section(0)

    def show_section(self, i: int):
        self.stack.setCurrentIndex(i)
        for k, b in enumerate(self._nav):
            b.setChecked(k == i)

    def _about(self, lay):
        from .components import label, section
        try:
            from importlib.metadata import version
            qt = version("PySide6")
        except Exception:
            qt = ""
        lay.addWidget(label("VN Reader reads visual novel dialogue off your screen and speaks "
                            "every character with their own voice.", "muted", wrap=True))
        lay.addSpacing(12)
        lay.addWidget(section("Open source"))
        for name, lic in (("Geist Sans & Geist Mono, Vercel", "SIL Open Font License 1.1"),
                          ("Lucide icons", "ISC License"),
                          ("PaddleOCR models via RapidOCR", "Apache License 2.0"),
                          ("ONNX Runtime (DirectML)", "MIT License"),
                          (f"Qt for Python {qt}".strip(), "LGPL v3"),
                          ("VoiceStudio (separate app)", "its own license")):
            row = QHBoxLayout()
            row.addWidget(label(name))
            row.addStretch(1)
            row.addWidget(label(lic, "mono"))
            lay.addLayout(row)

    def _tray_toggled(self, on: bool):
        self.cfg.settings.close_to_tray = on
        self.cfg.save()
        self.tray_changed.emit()

    def _show_tray_toggled(self, on: bool):
        self.cfg.settings.show_tray = on
        self.cfg.save()
        self.cb_tray.setEnabled(on)
        self.tray_changed.emit()

    def _engine_changed(self):
        self.cfg.settings.ocr_engine = self.cb_engine.currentData()
        self.cfg.save()
        self.ocr_options_changed.emit()

    def _quick(self, on: bool):
        self.cfg.settings.quick_start = on
        self.cfg.save()

    def _save_profiles(self, on: bool):
        self.cfg.settings.save_vs_profiles = on
        self.cfg.save()

    def _name_mode(self):
        self.cfg.settings.name_mode = self.cb_name.currentData()
        self.cfg.save()
