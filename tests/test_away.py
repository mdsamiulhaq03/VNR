"""Pause voice + reading while another program is in front of the game."""
from app import winapi
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


def test_is_away():
    assert winapi.is_away("Code.exe", "StudioHearts.exe")
    assert winapi.is_away("shell", "StudioHearts.exe")          # desktop / taskbar
    assert not winapi.is_away("studiohearts.exe", "StudioHearts.exe")
    assert not winapi.is_away("own", "StudioHearts.exe")        # VN Reader itself
    assert not winapi.is_away("", "StudioHearts.exe")
    assert not winapi.is_away("Code.exe", "")                   # no game window chosen


def _setup(win, monkeypatch, fg):
    win.cfg.settings.target_window = {"exe": "StudioHearts.exe", "title": "Studio Hearts"}
    win.cfg.settings.pause_when_away = True
    win.ocr._running = True                  # "reading", without a real capture thread
    monkeypatch.setattr(winapi, "foreground_kind", lambda: fg["kind"])


def test_pauses_and_resumes(win, monkeypatch):
    fg = {"kind": "StudioHearts.exe"}
    _setup(win, monkeypatch, fg)
    try:
        win._check_away()
        assert not win.player.paused and not win.ocr.paused
        fg["kind"] = "Code.exe"
        win._check_away()
        assert win.player.paused and win.ocr.paused
        fg["kind"] = "own"                   # VN Reader in front counts as the game
        win._check_away()
        assert not win.player.paused and not win.ocr.paused
        fg["kind"] = "Code.exe"
        win._check_away()
        fg["kind"] = "StudioHearts.exe"
        win._check_away()
        assert not win.player.paused and not win.ocr.paused
    finally:
        win.ocr._running = False


def test_manual_pause_stays(win, monkeypatch):
    fg = {"kind": "StudioHearts.exe"}
    _setup(win, monkeypatch, fg)
    try:
        win._toggle_pause()                  # the user paused (F11)
        fg["kind"] = "Code.exe"
        win._check_away()
        fg["kind"] = "StudioHearts.exe"
        win._check_away()
        assert win.player.paused             # still paused: it was the user's choice
        assert not win.ocr.paused
    finally:
        win.ocr._running = False


def test_setting_off(win, monkeypatch):
    fg = {"kind": "Code.exe"}
    _setup(win, monkeypatch, fg)
    win.cfg.settings.pause_when_away = False
    try:
        win._check_away()
        assert not win.player.paused and not win.ocr.paused
    finally:
        win.ocr._running = False
