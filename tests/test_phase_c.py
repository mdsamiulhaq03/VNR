"""Setup guide, mini-player, shortcuts sheet, now-playing card."""
from PySide6.QtWidgets import QApplication

from app.config import NARRATOR
from app.ui.dashboard import match_sentence, sentence_html
from app.ui.shortcuts import rows
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


def test_setup_guide_steps_and_finish(win, app, monkeypatch):
    monkeypatch.setattr("app.winapi.list_windows", lambda: [])
    win._open_setup_guide()
    g = win._guide
    try:
        assert g.isVisible() and g.stack.currentIndex() == 0
        assert g.done_steps() == [False, False, bool(getattr(win, "_vs_ok", False)), False]
        win.cfg.settings.target_window = {"exe": "Game.exe", "title": "Game"}
        g.refresh()
        assert g.done_steps()[0] and "Game" in g.st_game.text()
        g._next()
        assert g.stack.currentIndex() == 1
        monkeypatch.setattr(win, "_preview", lambda n: None)
        g.go(3)
        g._test()
        assert g.done_steps()[3] and g.b_next.text() == "Start reading"
        g._finish()
        assert win.cfg.settings.setup_done and not g.isVisible()
    finally:
        g.close()
        win.cfg.settings.target_window = None


def test_mini_player_replaces_the_bubble(win, app):
    from app.ui.chat_head import ChatHead
    from app.ui.mini_player import MiniPlayer
    assert isinstance(win.chat_head, ChatHead)
    win._set_float_style("mini")
    assert isinstance(win.chat_head, MiniPlayer) and win.cfg.settings.float_style == "mini"
    win.cfg.character(win.game, "Mila")
    win._show_and_speak("Mila", "Hello there. How are you?")
    app.processEvents()
    assert "Hello there" in win.chat_head.text.text()
    win._update_chat_head()                                 # same interface as the bubble
    win._set_float_style("bubble")
    assert isinstance(win.chat_head, ChatHead)


def test_shortcut_rows_show_current_keys():
    labels = {"toggle": "Start / stop reading", "pause": "Pause / resume"}
    assert rows({"toggle": "F9"}, labels) == [("Start / stop reading", "F9"),
                                               ("Pause / resume", "Not set")]


def test_shortcuts_sheet_opens(win, app, monkeypatch):
    from app.ui import shortcuts
    shown = []
    monkeypatch.setattr(shortcuts.ShortcutsDialog, "exec", lambda self: shown.append(self))
    win._show_shortcuts()
    assert shown and "Keyboard shortcuts" in shown[0].windowTitle()


def test_sentence_highlight_helpers():
    parts = ["Hey Orion, sorry.", "I didn't notice you came in.", "Want some pizza?"]
    assert match_sentence("Mila. Hey Orion, sorry.", parts) == 0
    assert match_sentence("I didn't notice you came in.", parts) == 1
    assert match_sentence("Completely different words.", parts) == -1
    html = sentence_html(parts, 1)
    assert html.count("<span") == 3 and "notice" in html


def test_now_playing_card_shows_mood_and_follows_the_player(win, app, monkeypatch):
    win.cfg.character(win.game, "Zalton")
    a, b = "I didn't think you would come back here.", "Why are you still standing there?"
    win._show_and_speak("Zalton", f"{a} {b}", True, "whisper")
    assert win.mood_pill.isVisible() and "whisper" in win.mood_pill.text()
    assert len(win._shown_parts) == 2
    uid = win._speaking_uid = 42
    win.queue.sentences[uid] = [a, b]
    monkeypatch.setattr(win.player, "current", lambda: ((uid, 1, 2), 0.5))
    monkeypatch.setattr(win.player, "is_playing", lambda: True)
    win._update_now_playing()
    assert abs(win.progress.value() - 0.75) < 0.01
    assert win._shown_sentence == 1
    monkeypatch.setattr(win.player, "current", lambda: (None, 0.0))
    monkeypatch.setattr(win.player, "is_playing", lambda: False)
    win._update_now_playing()
    assert win.progress.value() == 1.0 and win._shown_sentence == -1
