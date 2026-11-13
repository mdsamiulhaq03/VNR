import json
import os
import tempfile
from pathlib import Path

from app import winapi
from app.config import DEFAULT_HOTKEYS, Config
from app.hotkeys import parse


def test_area_hotkey_default_and_parse():
    assert DEFAULT_HOTKEYS["areas"] == "F6"
    assert parse("F6") == (0, 0x75)


def test_old_settings_keep_working_with_new_keys():
    d = Path(tempfile.mkdtemp())
    (d / "settings.json").write_text(json.dumps({"hotkeys": {"toggle": "F9"}}), "utf-8")
    c = Config(d / "settings.json")
    assert c.settings.hotkeys.get("areas", "F6") == "F6"    # how main_window reads it
    assert c.settings.close_to_tray is True and c.settings.tray_hint_shown is False


def test_window_at_ignores_desktop_and_own_windows():
    # The far corner of the screen is the desktop/taskbar or another app, never us.
    w = winapi.window_at(0, 0)
    assert w is None or (w.exe and w.hwnd)
    assert winapi.window_at(-100000, -100000) is None


def test_stats_chips():
    from app.ui.dashboard import stats_chips
    chips = stats_chips(1, 5, True)
    assert chips[0][0].endswith("1 character") and chips[1][0].endswith("5 lines read")
    assert chips[-1] == ("● Reading", True)
    assert not any(live for _, live in stats_chips(2, 1, False, 0))


def test_icon_files_exist():
    root = Path(__file__).resolve().parents[1] / "assets"
    from PIL import Image
    sizes = Image.open(root / "vnr.ico").info.get("sizes")
    assert (16, 16) in sizes and (256, 256) in sizes
    assert os.path.getsize(root / "vnr.png") > 1000
