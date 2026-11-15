"""VN Reader design system: Apple-inspired, with a Light and a Dark theme.

Tokens are module attributes (theme.TEXT, theme.PANEL, ...). set_mode() rewrites them for
the other theme and re-styles the running app: the stylesheet and palette are rebuilt, and
icons made with icon() re-colour themselves because they remember their colour *role*
("TEXT_2"), not the hex value. Widgets that paint themselves read theme.X in paintEvent.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from functools import lru_cache

from PySide6.QtCore import QByteArray, QEvent, QObject, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QIconEngine, QPainter,
                           QPalette, QPixmap)
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
                               QApplication, QComboBox, QScrollBar, QStyleFactory, QWidget)

# ---------------------------------------------------------------- palettes
# The two themes are tuned separately (not inverted): light separates surfaces with
# white cards, hairlines and soft shadows; dark separates them by elevation, no shadows.
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "BG": "#F5F5F7", "BG_2": "#FBFBFD", "PANEL": "#FFFFFF", "ELEVATED": "#F2F2F7",
        "HOVER": "#E8E8ED", "BORDER": "#D2D2D7", "BORDER_HOVER": "#BDBDC2",
        "TEXT": "#1D1D1F", "TEXT_2": "#86868B", "TEXT_3": "#AEAEB2",
        "ACCENT": "#007AFF", "ACCENT_HOVER": "#0A6FE6", "ACCENT_PRESSED": "#0062CC",
        "ACCENT_SOFT": "rgba(0, 122, 255, 0.12)", "ON_ACCENT": "#FFFFFF",
        "OK": "#34C759", "WARN": "#FF9500", "DANGER": "#FF3B30",
        "DANGER_SOFT": "rgba(255, 59, 48, 0.10)",
        "FEMALE": "#FF2D55", "MALE": "#32ADE6", "UNKNOWN": "#8E8E93",
        "NAME": "#FF2D55", "DIALOGUE": "#007AFF",
        "SIDEBAR": "#EEEEF1", "GLASS": "rgba(255, 255, 255, 0.88)",
        "SHADOW": "rgba(0, 0, 0, 0.14)", "SCRIM": "rgba(0, 0, 0, 0.18)",
        "SELECTION": "#CCE4FF",
    },
    "dark": {
        "BG": "#000000", "BG_2": "#0D0D0E", "PANEL": "#1C1C1E", "ELEVATED": "#2C2C2E",
        "HOVER": "#3A3A3C", "BORDER": "#38383A", "BORDER_HOVER": "#48484A",
        "TEXT": "#F5F5F7", "TEXT_2": "#98989D", "TEXT_3": "#636366",
        "ACCENT": "#0A84FF", "ACCENT_HOVER": "#409CFF", "ACCENT_PRESSED": "#0071E3",
        "ACCENT_SOFT": "rgba(10, 132, 255, 0.22)", "ON_ACCENT": "#FFFFFF",
        "OK": "#30D158", "WARN": "#FF9F0A", "DANGER": "#FF453A",
        "DANGER_SOFT": "rgba(255, 69, 58, 0.16)",
        "FEMALE": "#FF375F", "MALE": "#64D2FF", "UNKNOWN": "#98989D",
        "NAME": "#FF375F", "DIALOGUE": "#0A84FF",
        "SIDEBAR": "#161618", "GLASS": "rgba(44, 44, 46, 0.90)",
        "SHADOW": "rgba(0, 0, 0, 0.55)", "SCRIM": "rgba(0, 0, 0, 0.45)",
        "SELECTION": "#0A3D73",
    },
}
# Roles an icon colour can stand for (first match wins when a hex value is shared).
_ICON_ROLES = ("TEXT", "TEXT_2", "TEXT_3", "ON_ACCENT", "ACCENT", "DANGER", "OK", "WARN",
               "FEMALE", "MALE", "UNKNOWN", "NAME", "DIALOGUE", "BORDER_HOVER", "BORDER",
               "BG", "PANEL", "ELEVATED")

MODES = ("system", "light", "dark")
PREF = "system"            # what the user picked
MODE = "dark"              # the theme in use ("light" / "dark")

# Tokens (filled by _set_tokens; dark until apply() runs).
BG = BG_2 = PANEL = ELEVATED = HOVER = BORDER = BORDER_HOVER = ""
TEXT = TEXT_2 = TEXT_3 = ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ACCENT_SOFT = ON_ACCENT = ""
OK = WARN = DANGER = DANGER_SOFT = FEMALE = MALE = UNKNOWN = NAME = DIALOGUE = ""
SIDEBAR = GLASS = SHADOW = SCRIM = SELECTION = ""
WHITE = "#FFFFFF"
# Back-compat names used around the UI.
SURFACE = SURFACE_2 = MUTED = ACCENT_2 = BADGE = ""

SANS = "Inter"
MONO = "Cascadia Mono"
RADIUS = 8          # controls
RADIUS_LG = 12      # cards
RADIUS_SHEET = 14   # dialogs
RADIUS_POP = 10     # menus, popovers


def _set_tokens(mode: str) -> None:
    global MODE, SURFACE, SURFACE_2, MUTED, ACCENT_2, BADGE
    MODE = mode if mode in PALETTES else "dark"
    g = globals()
    for k, v in PALETTES[MODE].items():
        g[k] = v
    SURFACE, SURFACE_2, MUTED, ACCENT_2, BADGE = PANEL, ELEVATED, TEXT_2, ACCENT, BG


_set_tokens("dark")


def is_dark() -> bool:
    return MODE == "dark"


def role_of(color: str) -> str:
    """The token name a colour value belongs to in the current theme ('' if none)."""
    c = (color or "").strip().lower()
    if not c:
        return ""
    if c.upper() in PALETTES[MODE]:
        return c.upper()                    # already a role name ("TEXT_2")
    for r in _ICON_ROLES:
        if PALETTES[MODE][r].lower() == c:
            return r
    return ""


def resolve(color_or_role: str) -> str:
    """A role name ("TEXT_2") or a colour value -> a colour value for the current theme."""
    if color_or_role and color_or_role.upper() in PALETTES[MODE]:
        return PALETTES[MODE][color_or_role.upper()]
    return color_or_role


def avatar_color(name: str) -> str:
    """Quiet avatar fills so faces still differ a little."""
    tones = (["#E5E5EA", "#EBEBF0", "#E0E0E6", "#E8E6EE", "#E3E7EC"] if MODE == "light" else
             ["#2C2C2E", "#323234", "#2A2A2D", "#302E34", "#2B2F33"])
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16)
    return tones[h % len(tones)]


def gender_color(g: str) -> str:
    return {"female": FEMALE, "male": MALE}.get(g, UNKNOWN)


def gender_icon(g: str) -> str:
    return {"female": "♀", "male": "♂"}.get(g, "?")


def char_colour(rec: dict | None) -> str:
    """A character's colour: their name colour from the game when known, else their
    gender colour."""
    rec = rec or {}
    rgb = rec.get("name_colour")
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        try:
            r, g, b = (max(0, min(255, int(v))) for v in rgb[:3])
            return f"#{r:02X}{g:02X}{b:02X}"
        except (TypeError, ValueError):
            pass
    return gender_color(rec.get("gender", "unknown"))


# ---------------------------------------------------------------- motion
_reduced: bool | None = None


def reduced_motion() -> bool:
    """Windows "Show animations in Windows" is off."""
    global _reduced
    if _reduced is None:
        _reduced = False
        try:
            import ctypes
            v = ctypes.c_bool(True)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0):
                _reduced = not v.value
        except Exception:
            pass
    return _reduced


def motion(ms: int) -> int:
    """Animation length: 0 when the user turned animations off."""
    return 0 if reduced_motion() else ms


# ---------------------------------------------------------------- assets
def asset_dir(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


_fonts_loaded = False


def load_fonts() -> bool:
    """SF Pro when it's installed, else the bundled Inter (OFL), else Segoe UI."""
    global _fonts_loaded, SANS, MONO
    if _fonts_loaded:
        return SANS in ("Inter", "SF Pro Text")
    _fonts_loaded = True
    fams = set()
    d = asset_dir("fonts")
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.lower().startswith("inter") and f.lower().endswith(".ttf"):
            i = QFontDatabase.addApplicationFont(os.path.join(d, f))
            if i >= 0:
                fams.update(QFontDatabase.applicationFontFamilies(i))
    installed = set(QFontDatabase.families())
    if "SF Pro Text" in installed:
        SANS = "SF Pro Text"
    elif "Inter" in fams or "Inter" in installed:
        SANS = "Inter"
    else:
        SANS = "Segoe UI Variable Text"
    MONO = "Cascadia Mono" if "Cascadia Mono" in installed else "Consolas"
    return SANS in ("Inter", "SF Pro Text")


def mono(size: float = 9.5, weight=QFont.Normal) -> QFont:
    """Numbers and key caps."""
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


def _dpr() -> float:
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        return max(1.0, app.primaryScreen().devicePixelRatio()) * 1.5
    return 2.0


@lru_cache(maxsize=2048)
def _render(name: str, color: str, size: int, stroke: float) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    svg = _svg(name).replace("currentColor", color).replace(
        'stroke-width="2"', f'stroke-width="{stroke}"')
    dpr = _dpr()
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


def pixmap(name: str, color: str = "TEXT", size: int = 16, stroke: float = 1.75) -> QPixmap:
    """A Lucide icon as a crisp pixmap (a fixed image: use icon() where it should follow
    theme changes)."""
    return _render(name, resolve(color), size, stroke)


class _RoleIcon(QIconEngine):
    """A Lucide icon that picks its colours when painted, so it follows the theme."""

    def __init__(self, name: str, normal: str, active: str, disabled: str, size: int):
        super().__init__()
        self.name, self.size = name, size
        # Remember roles where possible; literal colours stay as they are.
        self.cols = {m: (role_of(c) or c) for m, c in
                     ((QIcon.Normal, normal), (QIcon.Active, active),
                      (QIcon.Selected, active), (QIcon.Disabled, disabled))}

    def _pm(self, mode) -> QPixmap:
        return _render(self.name, resolve(self.cols.get(mode, self.cols[QIcon.Normal])),
                       self.size, 1.75)

    def paint(self, painter: QPainter, rect: QRect, mode, state):
        painter.drawPixmap(rect, self._pm(mode))

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        pm = self._pm(mode)
        if size.width() and pm.width() != int(size.width() * pm.devicePixelRatio()):
            return pm.scaled(size * pm.devicePixelRatio(), Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
        return pm

    def actualSize(self, size: QSize, mode, state) -> QSize:
        return size

    def clone(self):
        c = _RoleIcon(self.name, "", "", "", self.size)
        c.cols = dict(self.cols)
        return c


def icon(name: str, color: str = "TEXT", size: int = 16, active: str | None = None,
         disabled: str = "TEXT_3") -> QIcon:
    """A theme-following icon with normal / hover / disabled colours."""
    return QIcon(_RoleIcon(name, color, active or color, disabled, size))


# Small images the stylesheet needs (switch knobs, chevrons): one set per theme.
def _qss_images() -> dict[str, str]:
    d = os.path.join(tempfile.gettempdir(), f"vnreader_theme_v10_{MODE}")
    os.makedirs(d, exist_ok=True)
    knob = "#FFFFFF"
    off_track = "#E9E9EB" if MODE == "light" else "#39393D"
    files = {
        "sw_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="38" height="22">'
                  f'<rect x="0" y="0" width="38" height="22" rx="11" fill="{off_track}"/>'
                  f'<circle cx="11" cy="11" r="9" fill="{knob}"/></svg>',
        "sw_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="38" height="22">'
                 f'<rect x="0" y="0" width="38" height="22" rx="11" fill="{ACCENT}"/>'
                 f'<circle cx="27" cy="11" r="9" fill="{knob}"/></svg>',
        "sw_off_dis": f'<svg xmlns="http://www.w3.org/2000/svg" width="38" height="22" '
                      f'opacity="0.45"><rect x="0" y="0" width="38" height="22" rx="11" '
                      f'fill="{off_track}"/><circle cx="11" cy="11" r="9" fill="{knob}"/></svg>',
        "sw_on_dis": f'<svg xmlns="http://www.w3.org/2000/svg" width="38" height="22" '
                     f'opacity="0.45"><rect x="0" y="0" width="38" height="22" rx="11" '
                     f'fill="{ACCENT}"/><circle cx="27" cy="11" r="9" fill="{knob}"/></svg>',
        "radio_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                     f'<circle cx="8" cy="8" r="7.25" fill="{PANEL}" stroke="{BORDER_HOVER}" '
                     f'stroke-width="1.5"/></svg>',
        "radio_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                    f'<circle cx="8" cy="8" r="8" fill="{ACCENT}"/>'
                    f'<circle cx="8" cy="8" r="3" fill="#FFFFFF"/></svg>',
        "check": f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
                 f'viewBox="0 0 24 24" fill="none" stroke="{ACCENT}" stroke-width="2.5" '
                 f'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    }
    out = {}
    for k, svg in files.items():
        p = os.path.join(d, k + ".svg")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(svg)
        out[k] = p.replace("\\", "/")
    chev = _svg("chevron-down").replace("currentColor", TEXT_2).replace(
        'stroke-width="2"', 'stroke-width="2.25"')
    p = os.path.join(d, "chevron.svg")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(chev or files["radio_off"])
    out["chevron"] = p.replace("\\", "/")
    return out


def build_qss() -> str:
    im = _qss_images()
    light = MODE == "light"
    card_border = BORDER if light else "transparent"
    return f"""
* {{ font-family: "{SANS}"; font-size: 10pt; color: {TEXT}; outline: none; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page, QWidget#workspace, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#inspector {{ background: {BG}; border-left: 1px solid {BORDER}; }}
QWidget#inspector QScrollArea, QWidget#inspector QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QWidget#toolbar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QWidget#transport {{ background: {BG}; border-top: 1px solid {BORDER}; }}
QWidget#transparent {{ background: transparent; }}
QFrame#panel, QFrame#card {{ background: {PANEL}; border: 1px solid {card_border};
    border-radius: {RADIUS_LG}px; }}
QFrame#group {{ background: {PANEL}; border: 1px solid {card_border}; border-radius: {RADIUS_LG}px; }}
QFrame#hline {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vline {{ background: {BORDER}; max-width: 1px; min-width: 1px; border: none; }}
QFrame#row, QFrame#castRow {{ background: transparent; border-radius: {RADIUS}px; }}
QFrame#row:hover, QFrame#castRow:hover {{ background: {HOVER}; }}
QFrame#row[selected="true"] {{ background: {ACCENT_SOFT}; }}

QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
            padding: 6px 9px; border-radius: 8px; font-size: 9pt; }}

/* ---- text ---- */
QLabel#pageTitle {{ font-size: 15pt; font-weight: 600; color: {TEXT}; letter-spacing: -0.2px; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; color: {TEXT}; letter-spacing: -0.2px; }}
QLabel#section {{ font-size: 10.5pt; font-weight: 600; color: {TEXT}; }}
QLabel#muted, QLabel#hint {{ color: {TEXT_2}; }}
QLabel#faint {{ color: {TEXT_3}; }}
QLabel#footnote {{ font-size: 8.5pt; color: {TEXT_2}; }}
QLabel#mono {{ font-size: 9pt; color: {TEXT_2}; }}
QLabel#number {{ font-size: 9pt; color: {TEXT_2}; }}
QLabel#brand {{ font-size: 10.5pt; font-weight: 600; color: {TEXT}; }}
QLabel#speaker {{ font-size: 13pt; font-weight: 600; letter-spacing: -0.2px; }}
QLabel#dialogue {{ font-size: 15pt; color: {TEXT}; }}
QLabel#transcript {{ color: {TEXT_2}; }}
QLabel#kbd {{ font-family: "{MONO}"; font-size: 8.5pt; color: {TEXT};
              background: {ELEVATED}; border: 1px solid {BORDER}; border-bottom-width: 2px;
              border-radius: 5px; padding: 1px 6px; }}
QLabel#badge, QLabel#pill {{ font-size: 8.5pt; font-weight: 500; color: {TEXT_2};
                background: {ELEVATED}; border: none; border-radius: 9px; padding: 2px 9px;
                max-height: 18px; }}
QLabel#pill[tone="accent"] {{ color: {ACCENT}; background: {ACCENT_SOFT}; }}
QLabel#pill[tone="ok"] {{ color: {OK}; }}
QLabel#pill[tone="warn"] {{ color: {WARN}; }}
QLabel#pill[tone="danger"] {{ color: {DANGER}; background: {DANGER_SOFT}; }}
QLabel#toast {{ background: {GLASS}; border: 1px solid {BORDER}; border-radius: 18px;
                padding: 9px 16px; color: {TEXT}; }}
QLabel#checklist {{ font-size: 9pt; color: {TEXT_2}; }}
QLabel#errorText {{ font-size: 8.5pt; color: {DANGER}; }}
QFrame#banner {{ background: {ELEVATED}; border: none; border-radius: {RADIUS}px; }}
QFrame#banner[tone="danger"] {{ background: {DANGER_SOFT}; }}
QFrame#banner[tone="warn"] {{ background: {ELEVATED}; }}

/* ---- buttons ---- */
QPushButton, QToolButton {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px; padding: 5px 14px; min-height: 18px; font-weight: 500; }}
QPushButton:hover, QToolButton:hover {{ background: {ELEVATED}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {HOVER}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_3}; background: {PANEL};
    border-color: {BORDER}; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT};
    color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; border: 1px solid {ACCENT};
    font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{ background: {ELEVATED}; color: {TEXT_3}; border-color: {ELEVATED}; }}
QPushButton#primary:focus {{ border: 1px solid {ACCENT_PRESSED}; }}
QPushButton#danger {{ background: transparent; color: {DANGER}; border: 1px solid transparent; }}
QPushButton#danger:hover {{ background: {DANGER_SOFT}; }}
QPushButton#danger:focus {{ border: 1px solid {DANGER}; }}
QPushButton#ghost, QToolButton#ghost {{ background: transparent; border: 1px solid transparent;
    color: {ACCENT}; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {ACCENT_SOFT}; }}
QPushButton#ghost:checked, QToolButton#ghost:checked {{ background: {ACCENT_SOFT}; }}
QPushButton#ghost:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#icon, QToolButton#icon {{ background: transparent; border: 1px solid transparent;
    padding: 4px; min-width: 18px; border-radius: {RADIUS}px; }}
QPushButton#icon:hover, QToolButton#icon:hover {{ background: {HOVER}; }}
QPushButton#icon:pressed, QToolButton#icon:pressed {{ background: {BORDER}; }}
QPushButton#icon:checked, QToolButton#icon:checked {{ background: {ACCENT_SOFT}; }}
QPushButton#icon:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 6px; padding: 3px 12px;
    color: {TEXT}; font-weight: 500; }}
QPushButton#seg:hover {{ background: {HOVER}; }}
QPushButton#seg:checked {{ background: {PANEL if light else "#636366"}; color: {TEXT};
    border: 1px solid {BORDER if light else "#636366"}; }}
QWidget#segmented {{ background: {"#E3E3E8" if light else ELEVATED}; border: none;
    border-radius: 8px; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 7px; text-align: left;
    padding: 6px 10px; color: {TEXT}; font-weight: 500; }}
QPushButton#nav:hover {{ background: {"rgba(0,0,0,0.05)" if light else "rgba(255,255,255,0.06)"}; }}
QPushButton#nav:checked {{ background: {"rgba(0,0,0,0.08)" if light else "rgba(255,255,255,0.10)"};
    color: {TEXT}; font-weight: 600; }}
QPushButton#nav:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#dropzone {{ background: {PANEL}; border: 1.5px dashed {BORDER_HOVER};
    border-radius: {RADIUS_LG}px; color: {TEXT_2}; }}
QPushButton#dropzone:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#link {{ background: transparent; border: none; color: {ACCENT}; padding: 0;
    font-weight: 500; text-align: left; }}
QPushButton#link:hover {{ text-decoration: underline; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QKeySequenceEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    padding: 5px 10px; selection-background-color: {SELECTION}; selection-color: {TEXT};
    min-height: 18px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QTextEdit:hover,
QPlainTextEdit:hover {{ border-color: {BORDER_HOVER}; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QKeySequenceEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit[invalid="true"], QComboBox[invalid="true"] {{ border: 1px solid {DANGER}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {TEXT_3};
    background: {ELEVATED}; border-color: {ELEVATED}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({im['chevron']}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: {RADIUS_POP}px; padding: 5px; selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT}; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 2px 8px; border-radius: 6px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ border: none; width: 16px; background: transparent; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 38px; height: 22px; image: url({im['sw_off']}); }}
QCheckBox::indicator:checked {{ image: url({im['sw_on']}); }}
QCheckBox::indicator:disabled {{ image: url({im['sw_off_dis']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({im['sw_on_dis']}); }}
QCheckBox:disabled {{ color: {TEXT_3}; }}
QCheckBox:focus {{ color: {ACCENT}; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; image: url({im['radio_off']}); }}
QRadioButton::indicator:checked {{ image: url({im['radio_on']}); }}

QSlider::groove:horizontal {{ height: 4px; background: {HOVER if light else ELEVATED};
    border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 18px; height: 18px; margin: -7px 0;
    border-radius: 9px; border: 1px solid {BORDER_HOVER if light else "#8E8E93"}; }}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT}; }}

/* ---- lists & tables ---- */
QListWidget, QListView, QTableWidget, QTreeWidget {{ background: {PANEL};
    border: 1px solid {card_border}; border-radius: {RADIUS_LG}px; padding: 4px;
    gridline-color: {BORDER}; alternate-background-color: {ELEVATED}; }}
QListWidget::item, QListView::item {{ padding: 7px 10px; border-radius: 7px; }}
QListWidget::item:hover, QTableWidget::item:hover, QListView::item:hover {{ background: {HOVER}; }}
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected,
QListView::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget#chips {{ background: transparent; border: none; padding: 0; }}
QListWidget#chips::item {{ background: {ELEVATED}; border: none; border-radius: 9px;
    padding: 3px 10px; font-size: 9pt; }}
QListWidget#chips::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QHeaderView::section {{ background: {PANEL}; color: {TEXT_2}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 7px 10px; font-size: 9pt; font-weight: 600; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HOVER if light else "#5A5A5E"};
    border-radius: 3px; min-height: 30px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_3 if light else "#7C7C80"}; margin: 0 1px;
    border-radius: 4px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HOVER if light else "#5A5A5E"};
    border-radius: 3px; min-width: 30px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    height: 0; width: 0; background: none; }}

/* ---- menus ---- */
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: {RADIUS_POP}px;
    padding: 5px; }}
QMenu::item {{ padding: 5px 28px 5px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
QMenu::indicator:checked {{ image: url({im['check']}); }}

/* ---- chrome ---- */
QStatusBar {{ background: {BG}; color: {TEXT_2}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ font-size: 8.5pt; color: {TEXT_2}; padding: 0 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; padding: 6px 12px; border: none;
    border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QGroupBox {{ background: transparent; border: none; margin-top: 22px; padding: 8px 0 0 0;
    font-size: 10.5pt; font-weight: 600; color: {TEXT}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0; color: {TEXT}; }}
QGroupBox#settingsGroup {{ background: {PANEL}; border: 1px solid {card_border};
    border-radius: {RADIUS_LG}px; margin-top: 0; padding: 0; font-size: 10pt;
    font-weight: 400; }}
QGroupBox#settingsGroup QLabel {{ background: transparent; }}
QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
QProgressBar {{ background: {HOVER if light else ELEVATED}; border: none; border-radius: 2px;
    max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
/* ---- setup guide ---- */
QLabel#stepNum {{ background: {ELEVATED}; color: {TEXT_2}; border-radius: 12px;
    font-size: 9pt; font-weight: 600; }}
QLabel#stepNum[state="current"] {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QLabel#stepNum[state="done"] {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

/* ---- now playing ---- */
QFrame#nowCard {{ background: {PANEL}; border: 1px solid {card_border};
    border-radius: {RADIUS_LG}px; }}
QFrame#nowCard QLabel#dialogue {{ font-size: 15pt; }}

/* ---- named pieces (formerly inline styles) ---- */
QLabel#strong {{ font-weight: 600; }}
QLabel#strong[tone="ok"] {{ color: {OK}; }}
QLabel#headline {{ font-size: 12pt; font-weight: 600; letter-spacing: -0.1px; }}
QLabel#stat {{ font-size: 17pt; font-weight: 600; color: {TEXT}; letter-spacing: -0.3px; }}
QLabel#body {{ color: {TEXT}; }}
QLabel#areaDot[area="name"] {{ color: {NAME}; }}
QLabel#areaDot[area="dialogue"] {{ color: {DIALOGUE}; }}
QLabel#areaPreview {{ background: {"#E9E9EE" if light else "#0A0A0B"}; border: 1px solid {BORDER};
    border-radius: {RADIUS_LG}px; color: {TEXT_2}; }}
QLabel#dropArea {{ border: 1.5px dashed {BORDER_HOVER}; border-radius: {RADIUS_LG}px;
    background: {PANEL}; color: {TEXT_2}; }}
QLabel#dropArea[hover="true"] {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; color: {ACCENT}; }}
QLabel#swatch {{ border: 1px solid {BORDER_HOVER}; border-radius: 6px; }}
QLabel#swatch[empty="true"] {{ border: 1px dashed {BORDER_HOVER}; background: transparent; }}
QWidget#navPane {{ background: {BG}; border: none; border-right: 1px solid {BORDER}; }}
QWidget#listPane {{ background: {BG}; border: none; }}
QWidget#navPane QScrollArea, QWidget#navPane QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QListWidget#plainList, QListWidget#transcriptList {{ background: transparent; border: none;
    padding: 0; }}
QListWidget#transcriptList {{ padding: 0 18px; }}
QListWidget#transcriptList::item {{ padding: 7px 10px; border-radius: 8px; color: {TEXT_2}; }}
QListWidget#transcriptList::item:hover {{ background: {HOVER}; color: {TEXT}; }}
QListWidget#transcriptList::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QScrollArea#clear, QScrollArea#clear > QWidget > QWidget {{ background: transparent; border: none; }}
QFrame#toastFrame {{ background: {GLASS}; border: 1px solid {BORDER if light else "#48484A"};
    border-radius: 14px; }}
QFrame#chatPill {{ background: {GLASS}; border: 1px solid {BORDER if light else "#48484A"};
    border-radius: 16px; }}
QLineEdit#titleEdit {{ font-size: 13pt; font-weight: 600; background: transparent;
    border: 1px solid transparent; border-radius: 6px; padding: 2px 4px; }}
QLineEdit#titleEdit:hover {{ background: {ELEVATED}; }}
QLineEdit#titleEdit:focus {{ background: {PANEL}; border: 1px solid {ACCENT}; }}
QDialog#sheet {{ background: transparent; }}
QFrame#sheetCard {{ background: {PANEL}; border: 1px solid {BORDER if light else "#3A3A3C"};
    border-radius: {RADIUS_SHEET}px; }}
"""


QSS = ""   # filled by apply()


# ---------------------------------------------------------------- applying
class _Notifier(QObject):
    changed = Signal(str)            # the new theme ("light" / "dark")


notifier: _Notifier | None = None


def _palette() -> QPalette:
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.Base, PANEL),
                      (QPalette.AlternateBase, ELEVATED), (QPalette.Text, TEXT),
                      (QPalette.WindowText, TEXT), (QPalette.Button, PANEL),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
                      (QPalette.HighlightedText, ON_ACCENT), (QPalette.PlaceholderText, TEXT_3),
                      (QPalette.ToolTipBase, PANEL), (QPalette.ToolTipText, TEXT),
                      (QPalette.Link, ACCENT), (QPalette.Mid, BORDER), (QPalette.Dark, BG),
                      (QPalette.Light, PANEL), (QPalette.Midlight, ELEVATED),
                      (QPalette.Shadow, "#000000")]:
        pal.setColor(role, QColor(col))
    return pal


def _hex(c: str) -> int:
    return int(c.lstrip("#")[:6], 16)


def style_title_bar(widget: QWidget) -> None:
    """The native title bar in the theme's colours (Windows 11; dark / light on 10)."""
    try:
        from .. import winapi
        winapi.title_bar(int(widget.winId()), dark=MODE == "dark", caption=_hex(BG),
                         text=_hex(TEXT), border=_hex(BORDER))
    except Exception:
        pass


def system_mode() -> str:
    """What Windows uses for apps right now ("light" / "dark")."""
    app = QApplication.instance()
    try:
        cs = app.styleHints().colorScheme() if app else None
        if cs == Qt.ColorScheme.Light:
            return "light"
        if cs == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    try:                                            # fallback: the registry
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except Exception:
        return "dark"


def resolve_mode(pref: str) -> str:
    return system_mode() if pref not in ("light", "dark") else pref


class NoWheelChanges(QObject):
    """The mouse wheel never changes dropdowns, sliders or number boxes (too easy to do
    by accident while scrolling a page): it scrolls the page they sit on instead.
    Also gives every new top-level window the theme's native title bar."""

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
                and obj.property("_vnr_bar") != MODE:
            obj.setProperty("_vnr_bar", MODE)
            style_title_bar(obj)
        return False


def _restyle(app: QApplication) -> None:
    global QSS
    app.setPalette(_palette())
    QSS = build_qss()
    app.setStyleSheet(QSS)


def apply(app: QApplication, pref: str = "system") -> None:
    """Set up fonts, the theme and the stylesheet (before any window is shown, so the
    first frame is already in the right theme)."""
    global PREF, notifier
    load_fonts()
    app.setStyle(QStyleFactory.create("Fusion"))
    PREF = pref if pref in MODES else "system"
    _set_tokens(resolve_mode(PREF))
    f = QFont(SANS, 10)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    _restyle(app)
    if notifier is None:
        notifier = _Notifier()
    if getattr(app, "_no_wheel", None) is None:
        app._no_wheel = NoWheelChanges(app)
        app.installEventFilter(app._no_wheel)
    if not getattr(app, "_vnr_scheme_hooked", False):
        app._vnr_scheme_hooked = True
        try:
            app.styleHints().colorSchemeChanged.connect(
                lambda _cs: PREF == "system" and set_mode("system"))
        except Exception:
            pass


def set_mode(pref: str, animate: bool = True) -> str:
    """Switch theme while the app runs (System / Light / Dark). Returns the theme now in
    use. The change cross-fades from a snapshot so nothing flashes."""
    global PREF
    app = QApplication.instance()
    PREF = pref if pref in MODES else "system"
    new = resolve_mode(PREF)
    if app is None:
        _set_tokens(new)
        return MODE
    if new == MODE and QSS:
        return MODE
    covers = []
    if animate and motion(220):
        for w in app.topLevelWidgets():
            if w.isVisible() and w.isWindow() and w.width() > 200 and not w.isMinimized():
                covers.append(_Cover(w))
    _set_tokens(new)
    _restyle(app)
    for w in app.topLevelWidgets():
        if w.isWindow():
            w.setProperty("_vnr_bar", MODE)
            if w.isVisible():
                style_title_bar(w)
            w.update()
    if notifier is not None:
        notifier.changed.emit(MODE)
    for c in covers:
        c.fade()
    return MODE


class _Cover(QWidget):
    """A snapshot of a window laid over it, faded out after the theme changed."""

    def __init__(self, win: QWidget):
        super().__init__(win)
        self._pm = win.grab()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(win.rect())
        self._op = 1.0
        self.show()
        self.raise_()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(self._op)
        p.drawPixmap(0, 0, self._pm)

    def fade(self):
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        a = QVariantAnimation(self)
        a.setDuration(motion(220))
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.valueChanged.connect(self._set)
        a.finished.connect(self.deleteLater)
        a.start()

    def _set(self, v):
        self._op = float(v)
        self.update()
