"""Which engine a game folder uses, and what VN Reader can do with it."""
from __future__ import annotations

import re
from pathlib import Path


def game_root(path: str | Path) -> Path:
    """The game's folder, whether given the folder or the game's .exe."""
    p = Path(path)
    return p.parent if p.suffix.lower() == ".exe" else p


def detect(path: str | Path) -> str:
    """"renpy" or "unknown" (unknown engines are read with OCR)."""
    root = game_root(path)
    game = root / "game"
    if (root / "renpy").is_dir():
        return "renpy"
    if game.is_dir() and any(next(game.glob(p), None) for p in ("*.rpa", "*.rpy", "*.rpyc")):
        return "renpy"
    return "unknown"


ENGINE_LABEL = {"renpy": "Ren'Py", "unknown": "Unknown engine"}


_NOT_GAME = ("unins", "crash", "setup", "redist", "python", "pythonw", "zsync", "notification",
             "helper", "launcher_update")


def find_exes(path: str | Path) -> list[str]:
    """The game's .exe files in its folder (installers, crash reporters and the like are
    left out), main-looking ones first."""
    root = game_root(path)
    out = []
    try:
        for p in sorted(root.glob("*.exe")):
            low = p.stem.lower()
            if any(w in low for w in _NOT_GAME) or low.endswith("-32"):
                continue
            out.append(p.name)
    except OSError:
        return []
    return sorted(out, key=lambda n: (len(n), n.lower()))


_TITLE = re.compile(r"""^\s*define\s+config\.name\s*=\s*_{0,2}\(?\s*["']([^"']+)["']""",
                    re.M)


def game_title(path: str | Path) -> str:
    """A Ren'Py game's own title (config.name in options.rpy), or ""."""
    root = game_root(path)
    game = root / "game"
    texts = []
    try:
        loose = game / "options.rpy"
        if loose.is_file():
            texts.append(loose.read_text("utf-8", "replace"))
        else:
            from .rpa import ArchiveError, RpaArchive
            for arc in sorted(game.glob("*.rpa")):
                try:
                    a = RpaArchive(arc)
                except (ArchiveError, OSError):
                    continue
                for n in a.names():
                    if n.rsplit("/", 1)[-1] == "options.rpy":
                        texts.append(a.read(n).decode("utf-8", "replace"))
                if texts:
                    break
    except OSError:
        return ""
    for t in texts:
        m = _TITLE.search(t)
        if m:
            return m.group(1).strip()
    return ""
