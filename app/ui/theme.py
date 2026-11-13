"""VN Reader design system: monochrome, precise, desktop-first.

Black surfaces, 1px #262626 borders, Geist Sans / Geist Mono, Lucide icons. Colour is
only used where it carries meaning (name vs dialogue outlines, gender dots, status).
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from functools import lru_cache

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
                               QApplication, QComboBox, QScrollBar, QStyleFactory, QWidget)

# ---------------------------------------------------------------- tokens
BG = "#000000"
BG_2 = "#0A0A0A"
PANEL = "#111111"
ELEVATED = "#171717"
HOVER = "#1F1F1F"
BORDER = "#262626"
BORDER_HOVER = "#3A3A3A"
TEXT = "#EDEDED"
TEXT_2 = "#888888"
TEXT_3 = "#5C5C5C"
WHITE = "#FFFFFF"

# Functional colours (small, muted): only where colour means something.
NAME = "#E58FB6"          # speaker name (outlines, area overlays)
DIALOGUE = "#7EA6E0"      # dialogue lines
FEMALE = NAME
MALE = DIALOGUE
UNKNOWN = "#A1A1A1"
OK = "#4ADE80"
WARN = "#F5A524"
DANGER = "#F04438"

# Back-compat names used around the UI.
SURFACE = PANEL
SURFACE_2 = ELEVATED
MUTED = TEXT_2
ACCENT = TEXT
ACCENT_HOVER = WHITE
ACCENT_2 = TEXT
BADGE = BG

SANS = "Geist"
MONO = "Geist Mono"
RADIUS = 8
RADIUS_LG = 12


def avatar_color(name: str) -> str:
    """Monochrome avatars: a few greys so faces still differ a little."""
    greys = ["#2A2A2A", "#303030", "#262626", "#343434", "#2C2C2C"]
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16)
    return greys[h % len(greys)]


def gender_color(g: str) -> str:
    return {"female": FEMALE, "male": MALE}.get(g, UNKNOWN)


def gender_icon(g: str) -> str:
    return {"female": "♀", "male": "♂"}.get(g, "?")


# ---------------------------------------------------------------- assets
def asset_dir(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


_fonts_loaded = False


def load_fonts() -> bool:
    """Register Geist Sans + Geist Mono (bundled, OFL). Falls back to Segoe UI /
    Cascadia Mono if they can't be loaded."""
    global _fonts_loaded, SANS, MONO
    if _fonts_loaded:
        return SANS == "Geist"
    _fonts_loaded = True
    fams = set()
    d = asset_dir("fonts")
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.lower().endswith(".ttf"):
            i = QFontDatabase.addApplicationFont(os.path.join(d, f))
            if i >= 0:
                fams.update(QFontDatabase.applicationFontFamilies(i))
    if "Geist" not in fams:
        SANS = "Segoe UI Variable Text"
    if "Geist Mono" not in fams:
        MONO = "Cascadia Mono"
    return "Geist" in fams


def mono(size: float = 9.5, weight=QFont.Normal) -> QFont:
    f = QFont(MONO)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setStyleHint(QFont.Monospace)
    return f


def sans(size: float = 10, weight=QFont.Normal) -> QFont:
    f = QFont(SANS)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


@lru_cache(maxsize=512)
def _svg(name: str) -> str:
    path = asset_dir("icons", f"{name}.svg")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def has_icon(name: str) -> bool:
    return bool(_svg(name))


@lru_cache(maxsize=1024)
def pixmap(name: str, color: str = TEXT, size: int = 16, stroke: float = 2.0) -> QPixmap:
    """A Lucide icon, recoloured, as a crisp (DPI-aware) pixmap."""
    from PySide6.QtSvg import QSvgRenderer
    svg = _svg(name).replace("currentColor", color).replace(
        'stroke-width="2"', f'stroke-width="{stroke}"')
    dpr = 2.0
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        dpr = max(1.0, app.primaryScreen().devicePixelRatio()) * 1.5
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    if svg:
        r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, pm.width(), pm.height()))
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = TEXT, size: int = 16, active: str | None = None,
         disabled: str = TEXT_3) -> QIcon:
    """QIcon with normal / active (hover) / disabled colours."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal)
    ic.addPixmap(pixmap(name, active or color, size), QIcon.Active)
    ic.addPixmap(pixmap(name, disabled, size), QIcon.Disabled)
    return ic


# Small images QSS needs (switch knobs, chevrons): written once to a temp folder.
def _qss_images() -> dict[str, str]:
    d = os.path.join(tempfile.gettempdir(), "vnreader_theme_v9")
    os.makedirs(d, exist_ok=True)
    files = {
        "sw_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                  f'<rect x="0.5" y="0.5" width="33" height="19" rx="9.5" fill="{ELEVATED}" '
                  f'stroke="{BORDER_HOVER}"/><circle cx="10" cy="10" r="6.5" fill="{TEXT_2}"/></svg>',
        "sw_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                 f'<rect x="0.5" y="0.5" width="33" height="19" rx="9.5" fill="{TEXT}" '
                 f'stroke="{TEXT}"/><circle cx="24" cy="10" r="6.5" fill="{BG}"/></svg>',
        "sw_off_dis": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                      f'<rect x="0.5" y="0.5" width="33" height="19" rx="9.5" fill="{PANEL}" '
                      f'stroke="{BORDER}"/><circle cx="10" cy="10" r="6.5" fill="{TEXT_3}"/></svg>',
        "sw_on_dis": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                     f'<rect x="0.5" y="0.5" width="33" height="19" rx="9.5" fill="{TEXT_3}" '
                     f'stroke="{TEXT_3}"/><circle cx="24" cy="10" r="6.5" fill="{PANEL}"/></svg>',
        "radio_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                     f'<circle cx="8" cy="8" r="7" fill="{ELEVATED}" stroke="{BORDER_HOVER}"/></svg>',
        "radio_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                    f'<circle cx="8" cy="8" r="7" fill="{ELEVATED}" stroke="{TEXT}"/>'
                    f'<circle cx="8" cy="8" r="3.5" fill="{TEXT}"/></svg>',
    }
    out = {}
    for k, svg in files.items():
        p = os.path.join(d, k + ".svg")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(svg)
        out[k] = p.replace("\\", "/")
    chev = _svg("chevron-down").replace("currentColor", TEXT_2)
    p = os.path.join(d, "chevron.svg")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(chev or files["radio_off"])
    out["chevron"] = p.replace("\\", "/")
    return out


def build_qss() -> str:
    im = _qss_images()
    return f"""
* {{ font-family: "{SANS}"; font-size: 10pt; color: {TEXT}; outline: none; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page, QWidget#workspace, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#inspector {{ background: {BG_2}; border-left: 1px solid {BORDER}; }}
QWidget#inspector QScrollArea, QWidget#inspector QScrollArea > QWidget > QWidget {{ background: {BG_2}; }}
QWidget#sidebar {{ background: {BG_2}; border-right: 1px solid {BORDER}; }}
QWidget#toolbar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QWidget#transport {{ background: {BG}; border-top: 1px solid {BORDER}; }}
QWidget#transparent {{ background: transparent; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
QFrame#hline {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vline {{ background: {BORDER}; max-width: 1px; min-width: 1px; border: none; }}

QToolTip {{ background: {ELEVATED}; color: {TEXT}; border: 1px solid {BORDER};
            padding: 5px 8px; border-radius: 6px; font-size: 9pt; }}

/* ---- text ---- */
QLabel#pageTitle {{ font-size: 12pt; font-weight: 600; color: {TEXT}; }}
QLabel#section {{ font-family: "{MONO}"; font-size: 8pt; color: {TEXT_2};
                  letter-spacing: 0.6px; }}
QLabel#muted, QLabel#hint {{ color: {TEXT_2}; }}
QLabel#faint {{ color: {TEXT_3}; }}
QLabel#mono {{ font-family: "{MONO}"; font-size: 9pt; color: {TEXT_2}; }}
QLabel#speaker {{ font-size: 12pt; font-weight: 600; }}
QLabel#dialogue {{ font-size: 16pt; color: {WHITE}; }}
QLabel#transcript {{ color: {TEXT_2}; }}
QLabel#kbd {{ font-family: "{MONO}"; font-size: 8pt; color: {TEXT_2};
              background: {ELEVATED}; border: 1px solid {BORDER}; border-radius: 4px;
              padding: 1px 5px; }}
QLabel#badge {{ font-family: "{MONO}"; font-size: 8pt; color: {TEXT_2};
                background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
                padding: 2px 8px; }}
QLabel#toast {{ background: {ELEVATED}; border: 1px solid {BORDER_HOVER}; border-radius: 10px;
                padding: 10px 14px; color: {TEXT}; }}
QLabel#checklist {{ font-family: "{MONO}"; font-size: 8.5pt; color: {TEXT_2}; }}

/* ---- buttons ---- */
QPushButton, QToolButton {{ background: {ELEVATED}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px; padding: 6px 12px; min-height: 18px; }}
QPushButton:hover, QToolButton:hover {{ background: {HOVER}; border-color: {BORDER_HOVER}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {PANEL}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_3}; background: {PANEL};
    border-color: {BORDER}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {TEXT_2}; }}
QPushButton:checked, QToolButton:checked {{ background: {HOVER}; border-color: {TEXT_2}; }}
QPushButton#primary {{ background: {TEXT}; color: {BG}; border: 1px solid {TEXT};
    font-weight: 600; }}
QPushButton#primary:hover {{ background: {WHITE}; border-color: {WHITE}; }}
QPushButton#primary:pressed {{ background: #CFCFCF; }}
QPushButton#danger {{ background: transparent; color: {DANGER}; border: 1px solid #4A1D1A; }}
QPushButton#danger:hover {{ background: #1F0D0B; border-color: {DANGER}; }}
QPushButton#ghost, QToolButton#ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {ELEVATED}; border-color: {BORDER}; }}
QPushButton#ghost:checked, QToolButton#ghost:checked {{ background: {ELEVATED}; border-color: {BORDER}; }}
QPushButton#icon, QToolButton#icon {{ background: transparent; border: 1px solid transparent;
    padding: 5px; min-width: 18px; border-radius: 6px; }}
QPushButton#icon:hover, QToolButton#icon:hover {{ background: {ELEVATED}; border-color: {BORDER}; }}
QPushButton#icon:checked, QToolButton#icon:checked {{ background: {ELEVATED}; border-color: {BORDER_HOVER}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 6px; padding: 4px 10px;
    color: {TEXT_2}; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {HOVER}; color: {TEXT}; }}
QWidget#segmented {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 6px; text-align: left;
    padding: 7px 10px; color: {TEXT_2}; font-weight: 500; }}
QPushButton#nav:hover {{ background: {PANEL}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {ELEVATED}; color: {TEXT}; }}
QPushButton#dropzone {{ background: {BG_2}; border: 1px dashed {BORDER_HOVER};
    border-radius: {RADIUS_LG}px; color: {TEXT_2}; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QKeySequenceEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {BG_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    padding: 5px 9px; selection-background-color: {TEXT}; selection-color: {BG}; min-height: 18px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {BORDER_HOVER}; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QKeySequenceEdit:focus, QSpinBox:focus {{
    border-color: {TEXT_2}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_3}; background: {PANEL}; }}
QLineEdit[placeholderText] {{ color: {TEXT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({im['chevron']}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px; padding: 4px; selection-background-color: {ELEVATED};
    selection-color: {TEXT}; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 2px 6px; border-radius: 5px; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 34px; height: 20px; image: url({im['sw_off']}); }}
QCheckBox::indicator:checked {{ image: url({im['sw_on']}); }}
QCheckBox::indicator:disabled {{ image: url({im['sw_off_dis']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({im['sw_on_dis']}); }}
QCheckBox:disabled {{ color: {TEXT_3}; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; image: url({im['radio_off']}); }}
QRadioButton::indicator:checked {{ image: url({im['radio_on']}); }}

QSlider::groove:horizontal {{ height: 2px; background: {BORDER_HOVER}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {TEXT}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -5px 0;
    border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {WHITE}; }}

/* ---- lists & tables ---- */
QListWidget, QTableWidget, QTreeWidget {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px; gridline-color: {BORDER}; alternate-background-color: {BG_2}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:hover, QTableWidget::item:hover {{ background: {PANEL}; }}
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {ELEVATED}; color: {TEXT}; }}
QListWidget#chips::item {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 3px 8px; font-family: "{MONO}"; font-size: 9pt; }}
QListWidget#chips::item:selected {{ border-color: {TEXT_2}; }}
QHeaderView::section {{ background: {BG}; color: {TEXT_2}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 7px 8px; font-family: "{MONO}";
    font-size: 8pt; }}
QTableCornerButton::section {{ background: {BG}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    height: 0; width: 0; background: none; }}

/* ---- menus ---- */
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ELEVATED}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 6px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; }}

/* ---- chrome ---- */
QStatusBar {{ background: {BG_2}; color: {TEXT_2}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ font-family: "{MONO}"; font-size: 8.5pt; color: {TEXT_2}; padding: 0 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; padding: 6px 12px; border: none;
    border-bottom: 1px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 1px solid {TEXT}; }}
QGroupBox {{ background: transparent; border: none; margin-top: 18px; padding: 8px 0 0 0;
    font-family: "{MONO}"; font-size: 8pt; color: {TEXT_2}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0; color: {TEXT_2}; }}
QMessageBox {{ background: {BG}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
QProgressBar {{ background: {ELEVATED}; border: none; border-radius: 2px; height: 4px; }}
QProgressBar::chunk {{ background: {TEXT}; border-radius: 2px; }}
"""


QSS = ""   # filled by apply()


class NoWheelChanges(QObject):
    """The mouse wheel never changes dropdowns, sliders or number boxes (too easy to do
    by accident while scrolling a page): it scrolls the page they sit on instead.
    Also gives every new top-level window the black native title bar."""

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Wheel and isinstance(
                obj, (QComboBox, QAbstractSpinBox, QAbstractSlider)) \
                and not isinstance(obj, QScrollBar):
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QAbstractScrollArea):
                w = w.parentWidget()
            if w is not None:
                QApplication.sendEvent(w.verticalScrollBar(), event)
            return True                      # never let the control itself change
        if t == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and not obj.property("_vnr_dark"):
            obj.setProperty("_vnr_dark", True)
            try:
                from .. import winapi
                winapi.dark_title_bar(int(obj.winId()))
            except Exception:
                pass
        return False


def apply(app: QApplication) -> None:
    global QSS
    load_fonts()
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.Base, BG_2),
                      (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT),
                      (QPalette.WindowText, TEXT), (QPalette.Button, ELEVATED),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, TEXT),
                      (QPalette.HighlightedText, BG), (QPalette.PlaceholderText, TEXT_3),
                      (QPalette.ToolTipBase, ELEVATED), (QPalette.ToolTipText, TEXT),
                      (QPalette.Link, TEXT), (QPalette.Mid, BORDER), (QPalette.Dark, BG)]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    f = QFont(SANS, 10)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    QSS = build_qss()
    app.setStyleSheet(QSS)
    app._no_wheel = NoWheelChanges(app)
    app.installEventFilter(app._no_wheel)
