"""Voice picker, roles, cast grouping, quality steps, game setup from a folder, rename
and delete from the Games page."""
import time

from app import presets
from app.config import NARRATOR, Config
from app.game_scripts import detect
from app.playtime import PlayClock
from app.ui import components
from app.ui.settings_view import QUALITY_STEPS, QUICK_STEPS
from app.ui.voice_picker import VoicePicker, suggestions
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


# ------------------------------------------------------------------ quality
def test_quality_steps_no_more_crackly_first_sentence(win):
    # Best (the default) never goes below 16 steps; Instant / Balanced are the user's
    # own choice of speed over quality (about 1 s / 1.5 s).
    assert QUALITY_STEPS["best"] is None and QUICK_STEPS == 16
    assert QUALITY_STEPS["fast"] < QUALITY_STEPS["balanced"] < QUICK_STEPS
    s = win.cfg.settings
    s.quick_start, s.vs_quality = True, "best"
    assert win._quick_steps() == 16
    for q in ("fast", "balanced"):              # already faster: no quick start
        s.vs_quality = q
        assert win._quick_steps() is None
    s.quick_start = False
    assert win._quick_steps() is None
    assert Config.__init__ and __import__("app.config").config.Settings().vs_quality == "best"


# ------------------------------------------------------------------ picker
def test_suggestions_follow_role_then_gender():
    ps = presets.defaults()
    used = {"f-zoe": ["Mila"]}
    sug = suggestions(ps, "female", "tomboy", used)
    assert [presets.category_of(p) for p in sug] == ["energetic"] * 5
    assert sug[-1]["id"] == "f-zoe"                         # used ones last
    plain = suggestions(ps, "male", "", {})
    assert all(p["gender"] == "male" for p in plain)
    narr = suggestions(ps, "female", "narration", {})
    assert all(presets.category_of(p) == "narrator" for p in narr)


def test_voice_picker_lists_groups_and_chooses(win, app):
    rec = {"gender": "female", "preset": "f-scarlett", "role": "boss"}
    dlg = VoicePicker(win, "Aida", rec, win.cfg.settings.presets, {"f-scarlett": ["Aida"]},
                      win.chars.sources, lambda pid: None)
    tiles = dlg.tiles()
    n_female = sum(p["gender"] == "female" for p in win.cfg.settings.presets)
    assert len(tiles) == 5 + n_female    # 5 suggested + every female voice (filter: female)
    assert presets.CATEGORY["mature"]["best_for"][0] in "boss"
    first5 = [t.pid for t in tiles[:5]]
    assert all(presets.category_of(presets.by_id(win.cfg.settings.presets)[p]) == "mature"
               for p in first5)
    dlg.gender.set_value("all", emit=True)
    assert len(dlg.tiles()) == 5 + 60
    dlg.search.setText("russian boss")
    assert {t.pid for t in dlg.tiles()} == {"f-katya", "m-viktor"}   # female + male boss
    dlg.gender.set_value("female", emit=True)
    assert {t.pid for t in dlg.tiles()} == {"f-katya"}
    dlg._use("preset:f-katya")
    assert dlg.choice == "preset:f-katya"


def test_role_switches_to_a_fitting_voice(win, app, monkeypatch):
    monkeypatch.setattr(win, "_preview", lambda name: None)
    win.cfg.character(win.game, "Aida").update(gender="female", preset="f-sakura")
    win._char_role("Aida", "boss")
    rec = win.cfg.character(win.game, "Aida")
    assert rec["role"] == "boss"
    assert presets.category_of(presets.by_id(win.cfg.settings.presets)[rec["preset"]]) == \
        "mature"
    win._char_reroll("Aida")                        # ✨ stays in the role's group
    rec = win.cfg.character(win.game, "Aida")
    assert presets.category_of(presets.by_id(win.cfg.settings.presets)[rec["preset"]]) == \
        "mature"
    card = win.chars.cards["Aida"]
    assert card.lbl_voice.text() == presets.by_id(win.cfg.settings.presets)[rec["preset"]]["name"]
    assert "Mature" in card.lbl_kind.text()


def test_cast_grouped_by_voice_type(win, app):
    win.cfg.character(win.game, "Mila").update(gender="female", preset="f-sakura")
    win.cfg.character(win.game, "Jake").update(gender="male", preset="m-jake")
    win._reload_characters()
    headers = [win.chars.md.list.item(i).text() for i in range(win.chars.md.list.count())
               if win.chars.md.list.item(i).data(0x0100) is None]
    # Group headings are sentence case (the redesign dropped all-caps labels).
    assert "Sweet" in headers and "Rough & energetic" in headers and headers[0] == "Narrator"
    win.chars.cb_group.setCurrentIndex(win.chars.cb_group.findData("name"))
    assert win.cfg.settings.cast_group == "name"
    assert all(win.chars.md.list.item(i).data(0x0100) is not None
               for i in range(win.chars.md.list.count()))


# --------------------------------------------------------------- game setup
def _fake_renpy(tmp_path, title="Studio Hearts", exe="StudioHearts.exe"):
    (tmp_path / "game").mkdir()
    (tmp_path / "renpy").mkdir()
    (tmp_path / exe).write_bytes(b"MZ")
    (tmp_path / "UnityCrashHandler64.exe").write_bytes(b"MZ")
    (tmp_path / "game" / "options.rpy").write_text(
        f'define config.name = _("{title}")\n', "utf-8")
    (tmp_path / "game" / "script.rpy").write_text('define m = Character("Mila")\n'
                                                  'label start:\n    m "Hello there."\n')
    return tmp_path


def test_find_exes_and_title(tmp_path):
    f = _fake_renpy(tmp_path)
    assert detect.find_exes(f) == ["StudioHearts.exe"]
    assert detect.game_title(f) == "Studio Hearts"


def test_folder_picks_the_game(win, app, tmp_path, monkeypatch):
    monkeypatch.setattr(components.Dialog, "exec", lambda self: 0)     # no hook prompt
    f = _fake_renpy(tmp_path)
    g = win._game_from_folder(str(f))
    assert g == "Studio Hearts" and win.game == "Studio Hearts"
    info = win.cfg.game_info(g)
    assert info["exe"] == "StudioHearts.exe" and info["engine"] == "renpy"
    assert info["path"] == str(f)
    win._use_game("Other")
    assert win._game_from_folder(str(f)) == "Studio Hearts"    # same exe: same game again
    assert len([k for k in win.cfg.settings.games if k.startswith("Studio Hearts")]) == 1


def test_window_names_the_game_from_its_title(win):
    assert win._title_name("Studio Hearts - v0.5.4", "StudioHearts.exe") == "Studio Hearts"
    assert win._title_name("", "StudioHearts.exe") == "StudioHearts"


def test_rename_game_moves_everything(win, app, monkeypatch):
    win._use_game("Old Name")
    win.cfg.character("Old Name", "Mila")["lines"] = 3
    win.cfg.game_info("Old Name")["lines"] = 3
    c = PlayClock(win.playlog, "Old Name", now=time.time() - 100)
    c.session.seconds = 60
    c.flush()
    monkeypatch.setattr("app.ui.main_window.prompt", lambda *a, **k: ("New Name", True))
    win._rename_game()
    assert win.game == "New Name" and "Old Name" not in win.cfg.settings.games
    assert win.cfg.characters("New Name")["Mila"]["lines"] == 3
    assert win.playlog.total_seconds("New Name") == 60
    assert win.cfg.rename_game("New Name", "") == ""


def test_delete_another_game_from_games_page(win, app, monkeypatch):
    monkeypatch.setattr("app.ui.main_window.confirm", lambda *a, **k: True)
    keep = win.game
    win.cfg.character("Unwanted", "Bob")["lines"] = 2
    win.cfg.touch_game("Unwanted", "unwanted.exe", "Unwanted")
    c = PlayClock(win.playlog, "Unwanted", now=time.time() - 50)
    c.session.seconds = 30
    c.flush()
    win.tabs.setCurrentWidget(win.games_view)
    app.processEvents()
    win.games_view.delete_requested.emit("Unwanted")
    assert "Unwanted" not in win.cfg.settings.games and "Unwanted" not in win.cfg.settings.voices
    assert "Unwanted" not in win.playlog.games
    assert win.game == keep                                  # the current game stays
    names = [win.games_view.list.item(i).data(0x0100)
             for i in range(win.games_view.list.count())]
    assert "Unwanted" not in names


def test_old_settings_move_to_best_quality_once(tmp_path):
    import json
    (tmp_path / "s.json").write_text(json.dumps({"vs_quality": "balanced",
                                                 "presets_version": presets.PRESETS_VERSION}))
    c = Config(tmp_path / "s.json")
    assert c.settings.vs_quality == "best"
    c.settings.vs_quality = "fast"                  # the user's own choice afterwards stays
    c.save()
    assert Config(tmp_path / "s.json").settings.vs_quality == "fast"
