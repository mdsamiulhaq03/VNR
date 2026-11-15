"""Games tab, game hook lines, script fix-ups and the play timer in the main window."""
import time
from datetime import date

from app import winapi
from app.config import NARRATOR
from app.game_scripts.renpy_script import Matcher, parse
from app.playtime import PlayClock
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)

SCRIPT = '''\
define Mila = Character("Mila", color="#26d55c")
define Aida = Character("Aida", color="#e677da")
label start:
    Mila "Kharzovya is different though. What's the [StudioName] policy?"
    Aida "We can work around Aida's preference with camera angles."
    "The room falls quiet for a moment while everyone thinks."
'''


def _said(win):
    return [(n, t) for n, t in getattr(win, "_test_said", [])]


def _capture_speech(win, monkeypatch):
    win._test_said = []
    monkeypatch.setattr(win, "_speak", lambda n, t, h="": win._test_said.append((n, t)))


def test_hook_lines_are_spoken_only_while_reading(win, app, monkeypatch):
    _capture_speech(win, monkeypatch)
    win._on_hook_line("Mila", "Hello there.", "game/day1.rpy:10")
    assert _said(win) == []                          # not reading: ignored
    win.ocr._running = True
    try:
        win._on_hook_line("Mila", "Hello there.", "game/day1.rpy:10")
        win._on_hook_line("", "It is quiet.", "game/day1.rpy:11")
        assert _said(win) == [("Mila", "Hello there."), (NARRATOR, "It is quiet.")]
        assert win.lbl_text.text() == "It is quiet."
        assert win.playlog.seen_count(win.game) == 2
        assert "day1.rpy:10" in win.playlog.game(win.game)["seen"]
        win._set_text_source(win.game, "ocr")        # hook ignored in OCR-only mode
        win._on_hook_line("Mila", "Ignored.", "game/day1.rpy:12")
        assert len(_said(win)) == 2
    finally:
        win.ocr._running = False


def test_hook_live_rests_ocr(win):
    win.cfg.game_info(win.game)["text_source"] = "auto"
    win._on_hook_live(True)
    assert win.ocr.paused and win.st_src.text.text() == "Text: game (live)"
    win._on_hook_live(False)
    assert not win.ocr.paused and win.st_src.text.text() != "Text: game (live)"


def test_script_fixes_ocr_text_and_speaker(win, app, monkeypatch):
    _capture_speech(win, monkeypatch)
    win._matcher, win._matcher_game = Matcher(parse({"s.rpy": SCRIPT})), win.game
    win.cfg.character(win.game, "Mila")
    win._seed_script_colours()
    assert win.cfg.characters(win.game)["Mila"]["name_colour"] == [38, 213, 92]
    name, text = win._handle_reading(
        ["Kharzovya is diferent thouqh. Whats the Studio Hearts po1icy?"], None,
        {"name": "", "styled": False})
    assert text == "Kharzovya is different though. What's the Studio Hearts policy?"
    assert name == "Mila"
    assert win.playlog.seen_count(win.game) == 1


def test_timer_counts_only_with_game_in_front(win, monkeypatch):
    win.cfg.settings.target_window = {"exe": "Game.exe", "title": "Game"}
    fg = {"kind": "Game.exe"}
    monkeypatch.setattr(winapi, "foreground_kind", lambda: fg["kind"])
    win.ocr._running = True
    try:
        win._start_session()
        t = time.time()
        win.clock.tick(t, True)
        win.clock._last = t - 3                       # 3 s of play
        win._check_away()
        played = win.clock.session.seconds
        assert 2.9 <= played <= 3.5
        fg["kind"] = "Code.exe"
        win._check_away()
        win.clock._last = time.time() - 3
        win._check_away()
        assert win.clock.session.seconds == played    # away: not counted
        assert "total" in win.lbl_timer.text()
    finally:
        win.ocr._running = False
        win._end_session()


def test_timer_stops_while_the_voice_is_paused(win, monkeypatch):
    win.cfg.settings.target_window = {"exe": "Game.exe", "title": "Game"}
    monkeypatch.setattr(winapi, "foreground_kind", lambda: "Game.exe")
    win.ocr._running = True
    try:
        win._start_session()
        t = time.time()
        win.clock.tick(t, True)
        win.clock._last = t - 3                       # 3 s of play
        win._check_away()
        played = win.clock.session.seconds
        assert 2.9 <= played <= 3.5
        win._toggle_pause()                           # Pause button / F11
        assert "paused" in win.lbl_timer.text()
        played = win.clock.session.seconds           # (time up to the press counts)
        win.clock._last = time.time() - 5
        win._check_away()
        assert win.clock.session.seconds == played    # paused: not counted
        win._toggle_pause()                           # resume
        win._check_away()
        win.clock._last = time.time() - 2
        win._check_away()
        assert win.clock.session.seconds > played + 1.5
        assert "paused" not in win.lbl_timer.text()
    finally:
        if win.player.paused:
            win._toggle_pause()
        win.ocr._running = False
        win._end_session()
    assert win.playlog.total_seconds(win.game) >= played


def test_games_view_shows_a_game(win, app, tmp_path):
    g = win.game
    win.cfg.character(g, "Mila")["lines"] = 1200
    win.cfg.character(g, "Aida")["lines"] = 40
    win.cfg.game_info(g).update(engine="renpy", path=str(tmp_path), text_source="auto")
    c = PlayClock(win.playlog, g, now=time.time() - 4000)
    c.session.seconds = 3700
    c.session.days = {date.today().isoformat(): 3700}
    for _ in range(5):
        c.line("one two three", "hook")
    c.flush()
    win.playlog.set_total_lines(g, 100)
    win.playlog.mark_seen(g, "a:1")
    win.tabs.setCurrentWidget(win.games_view)
    app.processEvents()
    v = win.games_view
    assert v.current == g and v.list.count() >= 1
    texts = [w.text() for w in v.scroll.widget().findChildren(type(win.lbl_text))]
    assert any("1 h 01 m" in t for t in texts)                 # play time tile
    assert any(t == "1%" for t in texts)                       # story progress
    assert any("1,000 lines with one character" in t for t in texts)
    out = v._export(g, str(tmp_path / "h.csv"))
    csv_text = open(out, encoding="utf-8-sig").read()
    assert "Sessions" in csv_text and "Mila,1200" in csv_text
    v._toggle_hidden(g)
    assert all(v.list.item(i).data(0x0100) != g for i in range(v.list.count()))
    v.cb_hidden.setChecked(True)
    assert any(v.list.item(i).data(0x0100) == g for i in range(v.list.count()))
    win.playlog.set_hidden(g, False)


def test_games_view_notes_saved(win, app):
    win.tabs.setCurrentWidget(win.games_view)
    app.processEvents()
    v = win.games_view
    if v.current is None:
        return
    v.notes.setPlainText("Day 8, Aida route")
    v._save_notes()
    assert win.playlog.game(v.current)["notes"] == "Day 8, Aida route"
