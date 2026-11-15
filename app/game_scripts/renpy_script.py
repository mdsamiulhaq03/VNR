"""A Ren'Py game's dialogue, read from its .rpy scripts (loose or inside .rpa archives),
and a matcher that finds the script line behind an OCR read.

The script gives the exact text (no OCR mistakes) and the speaker. [variables] such as
[StudioName] are filled in at run time by the game, so they are wildcards here: their
value is taken from the OCR text when possible.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .rpa import ArchiveError, RpaArchive


@dataclass
class ScriptLine:
    tag: str            # character variable, "" = narration
    text: str           # as written, with [variables], text tags removed
    file: str
    line: int

    @property
    def id(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class CharDef:
    tag: str
    name: str           # display name ("" = unknown / dynamic)
    colour: str = ""    # "#rrggbb" name colour from the script, if any


@dataclass
class Script:
    lines: list[ScriptLine] = field(default_factory=list)
    chars: dict[str, CharDef] = field(default_factory=dict)

    def speaker(self, tag: str) -> str:
        """Display name for a tag ("" for narration or a name the game sets later)."""
        c = self.chars.get(tag)
        return c.name if c else ""

    def next_line(self, line_id: str) -> ScriptLine | None:
        """The dialogue line written after this one in the same file (the likely next
        line; menus and jumps can lead elsewhere)."""
        idx = getattr(self, "_pos", None)
        if idx is None:
            idx = self._pos = {ln.id: i for i, ln in enumerate(self.lines)}
        i = idx.get(line_id)
        if i is None or i + 1 >= len(self.lines):
            return None
        nxt = self.lines[i + 1]
        return nxt if nxt.file == self.lines[i].file else None


# ---------------------------------------------------------------- parsing
_CHAR_DEF = re.compile(r"^\s*(?:define\s+|default\s+|\$\s*)?(\w+)\s*=\s*Character\((.*)$")
_FIRST_ARG = re.compile(r"""^\s*(?:_{1,2}\(\s*)?(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|None)""")
_COLOUR = re.compile(r"""\bcolor\s*=\s*["'](#[0-9a-fA-F]{3,8})["']""")
_SAY = re.compile(r'''^(?:(\w+)(?:\s+[\w-]+)*\s+)?"((?:[^"\\]|\\.)*)"(.*)$''')
_SAY_TAIL = re.compile(r"^\s*((with|id)\s+[\w.()]+\s*)*(\(.*\))?\s*$")
_TEXT_TAG = re.compile(r"\{[^{}]*\}")
_VAR = re.compile(r"\[[^\[\]]+\]")


def strip_tags(s: str) -> str:
    s = s.replace("{{", "\0").replace("[[", "\1")
    s = _TEXT_TAG.sub("", s)
    return s.replace("\0", "{").replace("\1", "[")


def _unescape(s: str) -> str:
    return (s.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ")
            .replace("\\\\", "\\").replace("%%", "%"))


def parse(sources: dict[str, str]) -> Script:
    """sources: {file name: .rpy text}. Translation files (tl/) are skipped."""
    out = Script()
    texts = {n: t for n, t in sources.items() if not n.replace("\\", "/").startswith("tl/")
             and "/tl/" not in n.replace("\\", "/")}
    for t in texts.values():                         # characters first
        for raw in t.splitlines():
            m = _CHAR_DEF.match(raw)
            if not m:
                continue
            tag, rest = m.group(1), m.group(2)
            a = _FIRST_ARG.match(rest)
            name = ""
            if a and (a.group(1) is not None or a.group(2) is not None):
                name = strip_tags(_unescape(a.group(1) if a.group(1) is not None else a.group(2)))
                if _VAR.search(name):                # e.g. "[MCName]": set by the player
                    name = ""
            c = _COLOUR.search(rest)
            out.chars[tag] = CharDef(tag, name.strip(), c.group(1) if c else "")
    for fname, t in texts.items():
        in_python = 0                                # indentation of a python block
        for no, raw in enumerate(t.splitlines(), 1):
            s = raw.strip()
            ind = len(raw) - len(raw.lstrip())
            if in_python and s and ind <= in_python - 1:
                in_python = 0
            if s.endswith(":") and re.match(r"(init\s+[-\d\s]*)?python\b|.*\bpython\s*:$", s):
                in_python = ind + 1
                continue
            if in_python or not s or s.startswith("#") or s.endswith(":"):
                continue
            m = _SAY.match(s)
            if not m:
                continue
            tag, body, tail = m.group(1) or "", m.group(2), m.group(3)
            if tag and tag not in out.chars and tag != "extend":
                continue                             # `add "x"`, `play music "y"`, …
            if not _SAY_TAIL.match(tail):
                continue
            text = " ".join(strip_tags(_unescape(body)).split())
            if not text or not re.search(r"\w", text):
                continue
            out.lines.append(ScriptLine("" if tag == "extend" else tag, text, fname, no))
    return out


def load_folder(folder: str | Path) -> Script | None:
    """The dialogue of the Ren'Py game in `folder` (the one holding game/), or None
    when it has no readable scripts (e.g. only compiled .rpyc files)."""
    game = Path(folder) / "game"
    if not game.is_dir():
        game = Path(folder)
    sources: dict[str, str] = {}
    for p in game.rglob("*.rpy"):
        rel = p.relative_to(game).as_posix()
        try:
            sources[rel] = p.read_text("utf-8", "replace")
        except OSError:
            pass
    for arc in sorted(game.glob("*.rpa")):
        try:
            a = RpaArchive(arc)
        except (ArchiveError, OSError):
            continue
        for n in a.names():
            if n.endswith(".rpy") and n not in sources:
                try:
                    sources[n] = a.read(n).decode("utf-8", "replace")
                except OSError:
                    pass
    if not sources:
        return None
    sc = parse(sources)
    return sc if sc.lines else None


# ---------------------------------------------------------------- matching
def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", s.lower().replace("’", "'")))


@dataclass
class Match:
    line: ScriptLine
    speaker: str        # display name from the script ("" = unknown)
    text: str           # final text: the script's, with [variables] filled from OCR
    score: float
    speaker_sure: bool = True   # False: the same words are said by others too ("Yes.")


class Matcher:
    """Finds the script line behind an OCR read (fast enough for every line)."""
    MIN_SCORE = 0.8

    def __init__(self, script: Script):
        self.script = script
        self._norm = [_norm(_VAR.sub(" ", ln.text)) for ln in script.lines]
        self._index: dict[str, list[int]] = defaultdict(list)
        for i, n in enumerate(self._norm):
            for k in self._keys(n):
                self._index[k].append(i)
        # Words in a large share of all lines ("the", "you") only cost time.
        common = max(200, len(self._norm) // 25)
        self._index = {k: v for k, v in self._index.items() if len(v) <= common}
        self._last = -1                              # index of the last matched line
        self._said_by: dict[str, set[str]] = defaultdict(set)
        for n, ln in zip(self._norm, script.lines):
            self._said_by[n].add(ln.tag)

    @staticmethod
    def _keys(n: str) -> set[str]:
        """Words that can find a line even when the OCR got other words wrong."""
        return {w for w in n.split() if len(w) >= 3} or set(n.split())

    def match(self, text: str, name: str | None = None) -> Match | None:
        n = _norm(text)
        if not n:
            return None
        votes: dict[int, int] = defaultdict(int)
        for k in self._keys(n):
            for i in self._index.get(k, ()):
                votes[i] += 1
        if not votes:
            return None
        cands = sorted(votes, key=lambda i: -votes[i])[:40]
        best, best_score = None, 0.0
        for i in cands:
            r = difflib.SequenceMatcher(None, n, self._norm[i], autojunk=False).ratio()
            if 0 <= i - self._last <= 30:            # the story usually moves forward
                r += 0.03
            ln = self.script.lines[i]
            if name and self.script.speaker(ln.tag).lower() == name.lower():
                r += 0.02
            if r > best_score:
                best, best_score = i, r
        if best is None or best_score < self.MIN_SCORE:
            return None
        self._last = best
        ln = self.script.lines[best]
        sure = len(self._said_by[self._norm[best]]) == 1 and len(self._norm[best].split()) >= 3
        return Match(ln, self.script.speaker(ln.tag), fill_vars(ln.text, text), best_score, sure)


def fill_vars(script_text: str, ocr_text: str) -> str:
    """The script text with each [variable] replaced by what the OCR saw in its place
    (found by aligning the two texts, so OCR slips elsewhere don't matter)."""
    if not _VAR.search(script_text):
        return script_text
    marked = _VAR.sub("", script_text)
    ocr = ocr_text.strip()
    sm = difflib.SequenceMatcher(None, marked, ocr, autojunk=False)
    vals = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        for _ in range(marked.count("", i1, i2)):
            vals.append(ocr[j1:j2].strip() if op != "equal" and marked.count("", i1, i2) == 1
                        else "")
    if any(not v for v in vals):
        return ocr_text            # couldn't place a variable: the OCR text is safer
    it = iter(vals)
    return _VAR.sub(lambda _m: next(it), script_text)
