"""Light / Dark / System themes: tokens, live switching, icons that follow the theme."""
import json
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from app.ui import theme
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


@pytest.fixture(autouse=True)
def back_to_dark(app):
    yield
    theme.set_mode("dark", animate=False)


def _ink(pm) -> QColor:
    """The most opaque pixel of an icon."""
    img = pm.toImage()
    best, col = -1, QColor()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > best:
                best, col = c.alpha(), c
    return col


def _close(a: QColor, hex_: str, tol: int = 40) -> bool:
    b = QColor(hex_)
    return all(abs(x - y) <= tol for x, y in ((a.red(), b.red()), (a.green(), b.green()),
                                              (a.blue(), b.blue())))


def test_tokens_and_stylesheet_follow_the_theme(app):
    theme.set_mode("light", animate=False)
    assert theme.MODE == "light" and theme.BG == "#F5F5F7" and theme.TEXT == "#1D1D1F"
    assert theme.ACCENT == "#007AFF" and "#F5F5F7" in app.styleSheet()
    theme.set_mode("dark", animate=False)
    assert theme.MODE == "dark" and theme.PANEL == "#1C1C1E" and theme.ACCENT == "#0A84FF"
    assert "#1C1C1E" in app.styleSheet()


def test_icons_recolour_after_a_switch(app):
    theme.set_mode("light", animate=False)
    ic = theme.icon("play", theme.TEXT_2, 16)           # a hex value: remembered as its role
    assert _close(_ink(ic.pixmap(QSize(16, 16))), theme.PALETTES["light"]["TEXT_2"])
    theme.set_mode("dark", animate=False)
    assert _close(_ink(ic.pixmap(QSize(16, 16))), theme.PALETTES["dark"]["TEXT_2"])


def test_system_mode_follows_windows(app, monkeypatch):
    monkeypatch.setattr(theme, "system_mode", lambda: "light")
    assert theme.set_mode("system", animate=False) == "light"
    monkeypatch.setattr(theme, "system_mode", lambda: "dark")
    assert theme.set_mode("system", animate=False) == "dark"


def test_reduced_motion_turns_animations_off(monkeypatch):
    monkeypatch.setattr(theme, "_reduced", True)
    assert theme.motion(200) == 0
    monkeypatch.setattr(theme, "_reduced", False)
    assert theme.motion(200) == 200


def test_theme_choice_is_saved(win, app):
    win._set_theme("light")
    assert theme.MODE == "light" and win.cfg.settings.theme == "light"
    saved = json.loads(Path(win.cfg.path).read_text("utf-8"))
    assert saved["theme"] == "light"
    assert win.seg_theme.value() == "light" and win.settings_view.seg_theme.value() == "light"
    win._set_theme("dark")


def test_char_colour_uses_the_game_colour_first():
    assert theme.char_colour({"name_colour": [255, 128, 0], "gender": "male"}) == "#FF8000"
    assert theme.char_colour({"gender": "female"}) == theme.FEMALE
    assert theme.char_colour({}) == theme.UNKNOWN


def test_no_hard_coded_colours_in_inline_styles():
    """Colours live in the theme's stylesheet, so they change with it."""
    ui = Path(theme.__file__).parent
    bad = []
    for f in ui.glob("*.py"):
        if f.name == "theme.py":
            continue
        for no, line in enumerate(f.read_text("utf-8").splitlines(), 1):
            if "setStyleSheet(" in line and (re.search(r"#[0-9A-Fa-f]{6}", line) or
                                             "theme." in line):
                bad.append(f"{f.name}:{no}")
    assert bad == []
