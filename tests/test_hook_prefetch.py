"""With the game hook, the script's next line is made while this one plays."""
from app.config import NARRATOR
from app.game_scripts.renpy_script import Matcher, parse
from tests.test_game_scripts import SCRIPT
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


def test_next_line_follows_the_script():
    sc = parse({"script.rpy": SCRIPT})
    ids = [ln.id for ln in sc.lines]
    nxt = sc.next_line(ids[3])                       # Aida "Yes." -> Mila "Yes."
    assert nxt.text == "Yes." and sc.speaker(nxt.tag) == "Mila"
    assert sc.next_line(ids[-1]) is None
    assert sc.next_line("other.rpy:1") is None


def test_hook_line_prefetches_the_next_known_speaker(win, monkeypatch):
    sc = parse({"script.rpy": SCRIPT})
    win._matcher, win._matcher_game = Matcher(sc), win.game
    calls = []
    monkeypatch.setattr(win.queue, "prefetch",
                        lambda line, background=False: calls.append((line.speaker, line.text,
                                                                     background)))
    monkeypatch.setattr(win, "_speak", lambda n, t, h="": None)
    win.cfg.character(win.game, "Mila")
    win.cfg.settings.read_names = False
    ids = [ln.id for ln in sc.lines]
    win.ocr._running = True
    try:
        win._on_hook_line("Aida", "Yes.", "game/" + ids[3], {"how": "screen"})
        assert ("Mila", "Yes.", True) in calls       # next line, in the background
        calls.clear()
        # Next line has a [variable] -> no guess. Unknown speaker -> no new character.
        win._on_hook_line("Mila", "x", "game/" + ids[0], {"how": "screen"})
        assert calls == []
        assert "MC" not in win.cfg.characters(win.game)
    finally:
        win.ocr._running = False
        win._matcher = None


def test_narration_skipped_when_narrator_is_off(win, monkeypatch):
    sc = parse({"script.rpy": 'label a:\n    m "Hi."\n    "The door opens."\n'
                              'define m = Character("Mila")\n'})
    win._matcher, win._matcher_game = Matcher(sc), win.game
    calls = []
    monkeypatch.setattr(win.queue, "prefetch", lambda line, background=False: calls.append(line))
    monkeypatch.setattr(win, "_speak", lambda n, t, h="": None)
    win.cfg.settings.read_narrator = False
    win.cfg.character(win.game, NARRATOR)
    win.ocr._running = True
    try:
        win._on_hook_line("Mila", "Hi.", "game/" + sc.lines[0].id, {"how": "screen"})
        assert calls == []
    finally:
        win.ocr._running = False
        win._matcher = None
        win.cfg.settings.read_narrator = True
