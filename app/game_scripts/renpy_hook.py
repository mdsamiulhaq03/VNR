"""The VN Reader hook for Ren'Py games: one small script file in the game's game/
folder. While the game runs, it sends every line of dialogue (exact text and speaker)
to VN Reader over the local network (127.0.0.1 only), so no OCR is needed.

Removing it (or deleting the file) puts the game back exactly as it was. The game must
be restarted once after installing or removing it.
"""
from __future__ import annotations

from pathlib import Path

from .detect import game_root

PORT = 47811
VERSION = 2
FILE = "zz_vnreader_hook.rpy"
MARKER = "# VN Reader hook"

# Works on Ren'Py 7 (Python 2) and 8 (Python 3): no f-strings. Never raises inside the
# game: every call is wrapped.
TEMPLATE = '''\
# VN Reader hook v{version}: sends each line of dialogue to VN Reader on this PC
# (127.0.0.1:{port}). Safe to delete; VN Reader's "Remove game hook" does that.
init 999 python:
    import socket as _vnr_socket
    import json as _vnr_json
    import time as _vnr_time

    try:
        _vnr_sock = _vnr_socket.socket(_vnr_socket.AF_INET, _vnr_socket.SOCK_DGRAM)
    except Exception:
        _vnr_sock = None

    def _vnr_send(d):
        try:
            if _vnr_sock is not None:
                _vnr_sock.sendto(_vnr_json.dumps(d).encode("utf-8"), ("127.0.0.1", {port}))
        except Exception:
            pass

    def _vnr_clean(s):
        try:
            return renpy.filter_text_tags(s, allow=[])
        except Exception:
            return s

    try:
        _vnr_strs = (str, unicode)
    except NameError:
        _vnr_strs = (str,)

    _vnr_line = {{"id": None, "what": "", "sent": True}}

    def _vnr_name_of(c):
        """The speaker's name as the player sees it: the name box of the game's say
        screen first, else Ren'Py's own name for the character."""
        try:
            scr = getattr(c, "screen", None) or "say"
            who = renpy.get_screen_variable("who", screen=scr)
            if isinstance(who, _vnr_strs) and who.strip():
                return who, "screen"
        except Exception:
            pass
        try:
            if c is not None:
                who = str(c)               # Ren'Py resolves dynamic names here
                pre = getattr(c, "who_prefix", None) or ""
                suf = getattr(c, "who_suffix", None) or ""
                if isinstance(pre, _vnr_strs) and isinstance(suf, _vnr_strs):
                    who = pre + who + suf
                if isinstance(who, _vnr_strs):
                    return who, "renpy"
        except Exception:
            pass
        return None, "none"

    def _vnr_flush():
        line = _vnr_line
        if line["sent"] or line["id"] is None:
            return
        line["sent"] = True
        try:
            c = getattr(renpy.last_say(), "who", None)
        except Exception:
            c = None
        who, how = (None, "none") if c is None else _vnr_name_of(c)
        odd = c is not None and who is None
        if who:
            try:
                if "[" in who:
                    who = renpy.substitute(who)
            except Exception:
                pass
            who = _vnr_clean(who).strip()
        try:
            dyn = bool(getattr(c, "dynamic", False))
        except Exception:
            dyn = False
        _vnr_send({{"t": "say", "who": who or "", "what": line["what"], "id": line["id"],
                   "how": how, "odd": odd, "dyn": dyn}})

    def _vnr_say(event, interact=True, **kw):
        if not interact:
            return
        try:
            if event == "begin":
                _vnr_flush()               # a line whose show_done never came
                what = kw.get("what")
                if what is None:
                    what = renpy.substitute(globals().get("_last_say_what") or "")
                try:
                    loc = "%s:%s" % renpy.get_filename_line()
                except Exception:
                    loc = ""
                _vnr_line["id"], _vnr_line["what"] = loc, _vnr_clean(what)
                _vnr_line["sent"] = False
            elif event in ("show_done", "end"):
                _vnr_flush()               # the name box is up now
        except Exception:
            pass

    def _vnr_beat(_last=[0.0]):
        try:
            now = _vnr_time.time()
            if now - _last[0] >= 1.0:
                _last[0] = now
                _vnr_send({{"t": "hb", "v": {version}, "game": config.name}})
        except Exception:
            pass

    config.all_character_callbacks.append(_vnr_say)
    config.periodic_callbacks.append(_vnr_beat)
'''


def hook_text() -> str:
    return TEMPLATE.format(version=VERSION, port=PORT)


def _game_dir(path: str | Path) -> Path:
    root = game_root(path)
    return root / "game" if (root / "game").is_dir() else root


def installed_version(path: str | Path) -> int:
    """Version of the hook in the game's folder (0 = none)."""
    try:
        f = _game_dir(path) / FILE
        if not f.is_file():
            return 0
        head = (f.read_text("utf-8", "replace").splitlines() or [""])[0]
    except OSError:
        return 0
    if not head.startswith(MARKER):
        return 0
    try:
        return int(head[len(MARKER):].split(":")[0].strip().lstrip("v"))
    except ValueError:
        return 1


def needs_update(path: str | Path) -> bool:
    """An older hook is installed (newer VN Reader): rewrite it."""
    v = installed_version(path)
    return 0 < v < VERSION


def installed(path: str | Path) -> bool:
    try:
        f = _game_dir(path) / FILE
        return f.is_file() and f.read_text("utf-8", "replace").startswith(MARKER)
    except OSError:
        return False


def install(path: str | Path) -> Path:
    """Write the hook into the game's game/ folder. Returns the file written."""
    d = _game_dir(path)
    if not d.is_dir():
        raise FileNotFoundError(f"No game folder at {d}")
    f = d / FILE
    f.write_text(hook_text(), "utf-8")
    return f


def uninstall(path: str | Path) -> bool:
    """Remove the hook (and the compiled copy the game made of it)."""
    d = _game_dir(path)
    removed = False
    for name in (FILE, FILE + "c", FILE[:-4] + ".rpyc"):
        f = d / name
        if f.exists():
            try:
                if name == FILE and not f.read_text("utf-8", "replace").startswith(MARKER):
                    continue                    # not ours: never delete a game's file
                f.unlink()
                removed = True
            except OSError:
                pass
    return removed
