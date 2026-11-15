"""Game script reading: Ren'Py archives, script parsing, matching, the hook."""
import json
import pickle
import socket
import textwrap
import time
import zlib
from pathlib import Path

import pytest

from app.game_scripts import detect, renpy_hook
from app.game_scripts.renpy_script import Matcher, fill_vars, load_folder, parse
from app.game_scripts.rpa import ArchiveError, RpaArchive
from app.sources.hook_source import HookSource

SCRIPT = '''\
define Mila = Character(__("Mila"), color = "#26d55c", outlines=[(2,"#000000",0,0)])
define MC = Character("[MCName]", color="#cc2c00")
define Aida = Character("{size=+10}Aida{/size}")
label start:
    scene bg room with dissolve
    play music "audio/theme.ogg"
    "You and [Mila] both get dressed." with dissolve
    Mila happy "Kharzovya is different though. What's the [StudioName] policy?"
    MC "I guess to just let you guys decide for yourselves."
    Aida "Yes."
    Mila "Yes."
    menu:
        "Agree":
            Mila "Ahh, she's one of those girls.."
    python:
        x = "not dialogue"
    Mila "He said \\"hi\\" to me {i}twice{/i}."
'''


def _write_rpa(path: Path, files: dict[str, bytes], key: int = 0x42424242):
    """A real RPA-3.0 archive, like Ren'Py writes them."""
    body, index = b"", {}
    header_len = 34
    for name, data in files.items():
        off = header_len + len(body)
        index[name] = [(off ^ key, len(data) ^ key, b"")]
        body += data
    idx = zlib.compress(pickle.dumps(index, protocol=2))
    offset = header_len + len(body)
    header = f"RPA-3.0 {offset:016x} {key:08x}\n".encode()
    assert len(header) == header_len
    path.write_bytes(header + body + idx)


# ------------------------------------------------------------------ archives
def test_rpa_reads_files(tmp_path):
    p = tmp_path / "scripts.rpa"
    _write_rpa(p, {"script.rpy": SCRIPT.encode(), "x.png": b"\x89PNG"})
    a = RpaArchive(p)
    assert sorted(a.names()) == ["script.rpy", "x.png"]
    assert a.read("script.rpy").decode() == SCRIPT


def test_rpa_refuses_code_in_index(tmp_path):
    class Evil:
        def __reduce__(self):
            return (print, ("pwned",))
    p = tmp_path / "evil.rpa"
    idx = zlib.compress(pickle.dumps({"a": Evil()}, protocol=2))
    p.write_bytes(f"RPA-3.0 {34:016x} {0:08x}\n".encode() + idx)
    with pytest.raises(ArchiveError):
        RpaArchive(p)


# ------------------------------------------------------------------- parsing
def test_parse_lines_and_characters():
    sc = parse({"script.rpy": SCRIPT, "tl/french/script.rpy": 'Mila "Bonjour."'})
    texts = [(ln.tag, ln.text) for ln in sc.lines]
    assert ("", "You and [Mila] both get dressed.") in texts
    assert ("Mila", "Kharzovya is different though. What's the [StudioName] policy?") in texts
    assert ("Mila", 'He said "hi" to me twice.') in texts          # escapes + tags
    assert all("Bonjour" not in t for _, t in texts)                  # tl/ skipped
    assert all("not dialogue" not in t and "theme" not in t for _, t in texts)
    assert all(t != "Agree" for _, t in texts)                        # menu choice
    assert sc.chars["Mila"].name == "Mila" and sc.chars["Mila"].colour == "#26d55c"
    assert sc.chars["MC"].name == ""                                  # set by the player
    assert sc.chars["Aida"].name == "Aida"


def test_load_folder_from_archive(tmp_path):
    (tmp_path / "game").mkdir()
    _write_rpa(tmp_path / "game" / "scripts.rpa", {"script.rpy": SCRIPT.encode()})
    sc = load_folder(tmp_path)
    assert sc and len(sc.lines) >= 6
    assert detect.detect(tmp_path) == "renpy"
    assert detect.detect(tmp_path / "Game.exe") == "renpy"


def test_load_folder_without_scripts(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "script.rpyc").write_bytes(b"compiled")
    assert load_folder(tmp_path) is None
    assert detect.detect(tmp_path) == "renpy"           # the hook still works
    assert detect.detect(tmp_path / "nothing") == "unknown"


# ------------------------------------------------------------------ matching
def test_match_fixes_ocr_and_fills_variables():
    m = Matcher(parse({"s.rpy": SCRIPT}))
    r = m.match("Kharzovya is diferent thouqh. Whats the Studio Hearts po1icy?")
    assert r is not None
    assert r.text == "Kharzovya is different though. What's the Studio Hearts policy?"
    assert r.speaker == "Mila" and r.speaker_sure
    assert m.match("Completely unrelated words about bananas and trains.") is None


def test_short_line_speaker_not_sure():
    m = Matcher(parse({"s.rpy": SCRIPT}))
    r = m.match("Yes.")
    assert r is not None and not r.speaker_sure


def test_fill_vars():
    assert fill_vars("What's the [S] policy?", "Whats the Studio Hearts po1icy?") == \
        "What's the Studio Hearts policy?"
    assert fill_vars("[A] and [B] left.", "Mila and Aida left.") == "Mila and Aida left."
    assert fill_vars("No variables.", "N0 variab1es.") == "No variables."


# ---------------------------------------------------------------------- hook
def test_hook_install_and_remove(tmp_path):
    (tmp_path / "game").mkdir()
    keep = tmp_path / "game" / "script.rpy"
    keep.write_text("label start:\n    return\n")
    assert not renpy_hook.installed(tmp_path)
    f = renpy_hook.install(tmp_path / "Game.exe")
    assert f.parent == tmp_path / "game" and renpy_hook.installed(tmp_path)
    (tmp_path / "game" / (renpy_hook.FILE + "c")).write_bytes(b"compiled by the game")
    assert renpy_hook.uninstall(tmp_path)
    assert not renpy_hook.installed(tmp_path)
    assert list((tmp_path / "game").iterdir()) == [keep]


def test_uninstall_never_deletes_a_foreign_file(tmp_path):
    (tmp_path / "game").mkdir()
    f = tmp_path / "game" / renpy_hook.FILE
    f.write_text("# the game's own file\n")
    renpy_hook.uninstall(tmp_path)
    assert f.exists()


def test_hook_python_compiles():
    """The hook's python block must be valid Python (Ren'Py runs it as is)."""
    text = renpy_hook.hook_text()
    body = text.split("init 999 python:\n", 1)[1]
    compile(textwrap.dedent(body), "hook", "exec")


def _run_hook(who_obj, screen_who=None, events=("begin", "show_done"),
              what="Hi {i}there{/i}."):
    """Run the hook's code with a fake Ren'Py; returns the packets VN Reader gets."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(0.5)
    port = rx.getsockname()[1]
    body = textwrap.dedent(renpy_hook.hook_text().split("init 999 python:\n", 1)[1])
    body = body.replace(str(renpy_hook.PORT), str(port))

    class FakeRenpy:
        @staticmethod
        def filter_text_tags(s, allow=None):
            return s.replace("{i}", "").replace("{/i}", "")

        @staticmethod
        def substitute(s):
            return s.replace("[n]", "Mila")

        @staticmethod
        def last_say():
            return type("L", (), {"who": who_obj})()

        @staticmethod
        def get_filename_line():
            return ("game/day8.rpy", 1258)

        @staticmethod
        def get_screen_variable(name, screen=None):
            if screen_who is None:
                raise KeyError(name)
            return screen_who

    cfg = type("C", (), {"all_character_callbacks": [], "periodic_callbacks": [],
                         "name": "Studio Hearts"})()
    exec(body, {"renpy": FakeRenpy, "config": cfg})
    for ev in events:
        cfg.all_character_callbacks[0](ev, interact=True, what=what)
    cfg.periodic_callbacks[0]()
    out = []
    try:
        while True:
            out.append(json.loads(rx.recvfrom(65536)[0]))
    except socket.timeout:
        pass
    rx.close()
    return out


class _Char:
    """A Ren'Py character as the hook sees it: str() is Ren'Py's own name for it."""
    def __init__(self, shown, dynamic=False, prefix=None, suffix=None, screen="say"):
        self._shown, self.dynamic, self.screen = shown, dynamic, screen
        self.who_prefix, self.who_suffix = prefix, suffix
        self.name = (lambda: shown) if dynamic else shown

    def __str__(self):
        if isinstance(self._shown, Exception):
            raise self._shown
        return self._shown


def _says(packets):
    return [p for p in packets if p["t"] == "say"]


def test_hook_takes_the_name_from_the_name_box():
    (p,) = _says(_run_hook(_Char("ignored"), screen_who="[n] (thinking)"))
    assert (p["who"], p["what"], p["id"], p["how"]) ==         ("Mila (thinking)", "Hi there.", "game/day8.rpy:1258", "screen")
    assert not p["odd"]


def test_hook_falls_back_to_renpys_own_name():
    # e.g. Desert Stalker: Character(pov_view, dynamic=True, who_suffix=" (thinking)")
    (p,) = _says(_run_hook(_Char("Zalton", dynamic=True, suffix=" (thinking)")))
    assert (p["who"], p["how"], p["dyn"], p["odd"]) == ("Zalton (thinking)", "renpy", True,
                                                        False)
    (p,) = _says(_run_hook(_Char("Zalton", prefix="Younger ")))
    assert p["who"] == "Younger Zalton" and not p["dyn"]


def test_hook_marks_a_name_it_cant_work_out():
    (p,) = _says(_run_hook(_Char(TypeError("not a name"))))
    assert p["who"] == "" and p["odd"] and p["how"] == "none"


def test_hook_sends_each_line_once():
    packets = _run_hook(_Char("Mila"), events=("begin", "show_done", "show_done", "end"))
    assert len(_says(packets)) == 1                       # {w} pauses: still one line
    late = _run_hook(_Char("Mila"), events=("begin", "end"))
    assert len(_says(late)) == 1                          # no show_done: sent at the end
    hb = [p for p in packets if p["t"] == "hb"]
    assert hb and hb[0]["v"] == renpy_hook.VERSION and hb[0]["game"] == "Studio Hearts"


def test_hook_version_and_update(tmp_path):
    (tmp_path / "game").mkdir()
    assert renpy_hook.installed_version(tmp_path) == 0 and not renpy_hook.needs_update(tmp_path)
    old = renpy_hook.hook_text().replace(f"hook v{renpy_hook.VERSION}:", "hook v1:")
    (tmp_path / "game" / renpy_hook.FILE).write_text(old)
    assert renpy_hook.installed_version(tmp_path) == 1 and renpy_hook.needs_update(tmp_path)
    renpy_hook.install(tmp_path)
    assert renpy_hook.installed_version(tmp_path) == renpy_hook.VERSION
    assert not renpy_hook.needs_update(tmp_path)


def test_hook_source_dedupes_and_goes_offline(monkeypatch):
    import app.sources.hook_source as hs
    got, live = [], []
    src = HookSource(lambda *a: got.append(a), on_live=live.append, port=0)
    say = json.dumps({"t": "say", "who": "Mila", "what": "Hello  there.", "id": "a:1"}).encode()
    src.feed(say)
    src.feed(say)                              # {w} pause: same statement again
    src.feed(json.dumps({"t": "say", "who": "", "what": "Narration.", "id": "a:2"}).encode())
    src.feed(b"not json")
    assert [g[:3] for g in got] == [("Mila", "Hello there.", "a:1"), ("", "Narration.", "a:2")]
    assert live == [True]
    later = time.monotonic() + 10
    monkeypatch.setattr(hs.time, "monotonic", lambda: later)
    src._check_live()
    assert live == [True, False] and not src.live


def test_hook_source_udp_roundtrip():
    got = []
    src = HookSource(lambda *a: got.append(a), port=0)
    # port 0 is not usable for a client to find: bind to a free port explicitly
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    src.port = s.getsockname()[1]
    s.close()
    assert src.start()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(json.dumps({"t": "say", "who": "Aida", "what": "Hi.", "id": "x:1"}).encode(),
              ("127.0.0.1", src.port))
    end = time.time() + 2
    while not got and time.time() < end:
        time.sleep(0.02)
    src.stop()
    assert [g[:3] for g in got] == [("Aida", "Hi.", "x:1")]
