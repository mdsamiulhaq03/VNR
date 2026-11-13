"""Split the speaker name off the top line of the reading area.

Rule: if there's no clear name on the top line, the whole text belongs to the
narrator. When unsure, we say "no name" so narration is never cut.
"""
from __future__ import annotations

import re
import statistics

from . import text_fixes

_SENTENCE_PUNCT = re.compile(r"[.,!?…。、！？\"“”「」『』~♪]")
_LOOSE_PUNCT = re.compile(r"[,!…。、！\"“”「」『』~♪]")       # with a layout hint, "." / "?" ok
_INLINE = re.compile(r"^\s*([^\s:：「『\"“][^:：「『\"“]{0,23}?)\s*(?:[:：]\s*|(?=[「『]))(.+)$")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’.\-]*")


def _title_words(s: str) -> bool:
    """Latin names are Capitalized words ("Mr. Tanaka", "Old Man"); "The next day" isn't."""
    words = _LATIN_WORD.findall(s)
    small = {"of", "the", "de", "von", "van", "no", "da", "le", "la"}
    return all(w[0].isupper() or w.lower() in small for w in words) if words else True


def looks_like_name(line: str, loose: bool = False) -> bool:
    s = line.strip()
    max_len, max_words = (30, 4) if loose else (24, 3)
    if not s or len(s) > max_len or len(s.split()) > max_words:
        return False
    if (_LOOSE_PUNCT if loose else _SENTENCE_PUNCT).search(s):
        return False
    if loose and s.strip("?") == "":           # "???" = unknown speaker
        return True
    if not s[0].isalpha():
        return False
    return _title_words(s)


def separated_top(boxes: list[tuple] | None) -> bool | None:
    """Does the top line stand apart (a name box)? None if we can't tell.

    boxes: (height, top, left, width) per line, top-to-bottom.
    """
    if not boxes or len(boxes) < 2 or not any(b[0] for b in boxes):
        return None
    h = statistics.median(b[0] for b in boxes) or 1.0
    gaps = [boxes[i + 1][1] - (boxes[i][1] + boxes[i][0]) for i in range(len(boxes) - 1)]
    first, rest = gaps[0], gaps[1:]
    typical = statistics.median(rest) if rest else 0.35 * h
    big_gap = first > max(1.4 * typical, typical + 0.3 * h)
    lefts = [b[2] for b in boxes[1:]]
    x_off = abs(boxes[0][2] - statistics.median(lefts)) > 1.2 * h if lefts else False
    return bool(big_gap or x_off)


def _caps_name(first: str, rest: list[str]) -> bool:
    """A short ALL-CAPS top line over normal-case dialogue ("LUNA", "PRESENTER")."""
    letters = [c for c in first if c.isalpha()]
    if len(letters) < 2 or len(first.split()) > 3 or not all(c.isupper() for c in letters):
        return False
    body = "".join(rest)
    return any(c.islower() for c in body)


def split_name(lines: list[str], known: list[str], mode: str = "auto",
               boxes: list[tuple] | None = None,
               styled: bool | None = None) -> tuple[str, str]:
    """Returns (speaker, dialogue). speaker is '' for narration.

    styled: the top line has another colour/size than the rest (see ocr_source)."""
    pairs = [(ln.strip(), boxes[i] if boxes and i < len(boxes) else None)
             for i, ln in enumerate(lines) if ln and ln.strip()]
    lines = [p[0] for p in pairs]
    boxes = [p[1] for p in pairs] if boxes and all(p[1] for p in pairs) else None
    if not lines:
        return "", ""
    first, rest = lines[0], lines[1:]

    if mode == "never":
        return "", " ".join(lines)

    if mode == "always":
        if rest:
            return first, " ".join(rest)
        m = _INLINE.match(first)
        return (m.group(1).strip(), m.group(2).strip()) if m else ("", first)

    # auto
    if rest:
        # A known character's name counts even with punctuation ("Mr. Tanaka").
        if len(first) <= 30 and known:
            hit = text_fixes.match_speaker(first, known)
            if hit in known:
                return hit, " ".join(rest)
        apart = separated_top(boxes)
        if (apart is True or styled is True) and looks_like_name(first, loose=True):
            return first, " ".join(rest)
        if _caps_name(first, rest) and looks_like_name(first, loose=True):
            return first, " ".join(rest)
        if apart is None and looks_like_name(first):     # no layout info: text rules only
            return first, " ".join(rest)
        # apart is False: the top line flows with the text -> narration
    m = _INLINE.match(first)
    if m and looks_like_name(m.group(1)):
        return m.group(1).strip(), " ".join([m.group(2).strip()] + rest)
    return "", " ".join(lines)
