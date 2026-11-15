"""Speaker names from the game hook: wrong names are read from the screen (OCR, last
fallback), broken old records are cleaned up, and the Play button starts the game."""
import os
import threading

from app.config import NARRATOR
from app.text_clean import looks_wrong
from tests.test_v9_ui import app, win  # noqa: F401  (pytest fixtures)


def test_looks_wrong():
    bad = ["<function pov_view at 0x0000000069f66ac0>", "<bound method A.f of <A>>",
           "[persistent.player_name]", "{b}Mila{/b}", "None", "obj 0x1234abcd",
           "This is a whole sentence that someone said here", "#$%@!", "%s"]
    good = ["You", "???", "Narrator", "Zalton (thinking)", "Younger Zalton", "Seo-yeon",
            "Bro #1", "Dr. Askarova", "Mila", "Auraé girl #1", "Mila's thoughts", "", "Me"]
    assert [b for b in bad if not looks_wrong(b)] == []
    assert [g for g in good if looks_wrong(g)] == []


def _reading(win, monkeypatch):
    said = []
    hints = []
    monkeypatch.setattr(win, "_speak",
                        lambda n, t, h="": (said.append((n, t)), hints.append(h)))
    win._test_hints = hints
    win.ocr._running = True
    win.cfg.game_info(win.game)["text_source"] = "auto"
    return said


def test_good_name_is_used(win, monkeypatch):
    said = _reading(win, monkeypatch)
    try:
        win._on_hook_line("Zalton", "Hi.", "game/a.rpy:1", {"how": "screen"})
        assert said == [("Zalton", "Hi.")]
    finally:
        win.ocr._running = False


def test_name_tag_goes_to_the_base_character(win, monkeypatch):
    """"Zalton (thinking)" / "Zalton (whispering)" are Zalton, in that mood."""
    said = _reading(win, monkeypatch)
    try:
        win._on_hook_line("Zalton (thinking)", "Hmm.", "game/a.rpy:1", {"how": "screen"})
        win._on_hook_line("Zalton (whispering)", "Shh.", "game/a.rpy:2", {"how": "screen"})
        win._on_hook_line("Bro (Mark)", "Yo.", "game/a.rpy:3", {"how": "screen"})
        assert said == [("Zalton", "Hmm."), ("Zalton", "Shh."), ("Bro (Mark)", "Yo.")]
        assert win._test_hints == ["thought", "whisper", ""]
        chars = win.cfg.characters(win.game)
        assert "Zalton (thinking)" not in chars and "Zalton (whispering)" not in chars
    finally:
        win.ocr._running = False


def test_existing_tagged_character_is_kept(win, monkeypatch):
    """A "X (thinking)" record the user already has keeps its own voice."""
    said = _reading(win, monkeypatch)
    win.cfg.character(win.game, "Ivy (thinking)")
    try:
        win._on_hook_line("Ivy (thinking)", "Hmm.", "game/a.rpy:1", {"how": "screen"})
        assert said == [("Ivy (thinking)", "Hmm.")]
    finally:
        win.ocr._running = False


def test_wrong_name_is_read_from_the_screen(win, app, monkeypatch):
    said = _reading(win, monkeypatch)
    win.cfg.character(win.game, "Zalton")
    win.ocr.spec.name = [0, 0, 100, 30]
    done = threading.Event()

    class FakeOcr:
        def read_name(self, img, opts):
            return "Zalt0n"                           # OCR slip: matched to Zalton

    class FakeCap:
        def grab_pair(self, spec):
            return object(), object()

        def close(self):
            pass

    monkeypatch.setattr("app.ui.main_window.Capturer", FakeCap)
    monkeypatch.setattr("app.ui.main_window.make_ocr", lambda opts: FakeOcr())
    orig = win._on_hook_named
    monkeypatch.setattr(win, "_on_hook_named",
                        lambda *a: (orig(*a), done.set()))
    win.bridge.hook_named.disconnect()
    win.bridge.hook_named.connect(win._on_hook_named)
    try:
        win._on_hook_line("<function pov_view at 0x0000000069f66ac0>", "Keep that in mind.",
                          "game/a.rpy:2", {"odd": False})
        for _ in range(100):
            app.processEvents()
            if done.wait(0.02):
                break
        app.processEvents()
        assert said == [("Zalton", "Keep that in mind.")]
        assert not any(looks_wrong(n) for n in win.cfg.characters(win.game))
    finally:
        win.ocr._running = False
        win.ocr.spec.name = None


def test_wrong_name_without_name_area_goes_to_narrator(win, monkeypatch):
    said = _reading(win, monkeypatch)
    win.ocr.spec.name = None
    try:
        win._on_hook_line("", "Keep that in mind.", "game/a.rpy:3", {"odd": True})
        assert said == [(NARRATOR, "Keep that in mind.")]
    finally:
        win.ocr._running = False


def test_broken_old_record_becomes_the_real_character(win, monkeypatch):
    said = _reading(win, monkeypatch)
    junk = "<function pov_view at 0x0000000069f66ac0>"
    rec = win.cfg.character(win.game, junk)
    rec["lines"], voice = 5, rec.get("preset")
    win.cfg.character(win.game, "Emilia")["aliases"] = [junk]
    try:
        win._on_hook_line("Emilia", "Hello.", "game/a.rpy:4", {})          # not dynamic
        assert junk in win.cfg.characters(win.game)                        # left alone
        win._on_hook_line("Zalton", "Hi.", "game/a.rpy:5", {"dyn": True})  # the MC
        chars = win.cfg.characters(win.game)
        assert junk not in chars and chars["Zalton"]["preset"] == voice
        assert chars["Zalton"]["lines"] >= 5
        assert junk not in chars["Emilia"].get("aliases", [])
        assert said[-1] == ("Zalton", "Hi.")
    finally:
        win.ocr._running = False


def test_play_starts_the_exe(win, monkeypatch, tmp_path):
    exe = tmp_path / "Game.exe"
    exe.write_bytes(b"MZ")
    started = []
    monkeypatch.setattr("app.ui.main_window.subprocess.Popen",
                        lambda args, **kw: started.append((args, kw.get("cwd"))))
    win.cfg.game_info(win.game).update(path=str(tmp_path), exe="Game.exe")
    win._launch_game()
    assert started == [([str(exe)], str(tmp_path))]


def test_play_asks_for_the_exe_when_unknown(win, monkeypatch, tmp_path):
    exe = tmp_path / "Other.exe"
    exe.write_bytes(b"MZ")
    started = []
    monkeypatch.setattr("app.ui.main_window.subprocess.Popen",
                        lambda args, **kw: started.append(args))
    monkeypatch.setattr("app.ui.main_window.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(exe), ""))
    win.cfg.game_info(win.game).pop("path", None)
    win.cfg.game_info(win.game).pop("exe", None)
    win._launch_game()
    info = win.cfg.game_info(win.game)
    assert info["exe"] == "Other.exe" and os.path.normcase(info["path"]) == \
        os.path.normcase(str(tmp_path))
    assert started == [[os.path.normpath(str(exe))]]
