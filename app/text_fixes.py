"""User fix-up rules (pronunciation / OCR corrections) and speaker-name matching."""
from __future__ import annotations

import difflib
import re


def apply(text: str, rules: list[dict]) -> str:
    for r in rules:
        find = (r.get("find") or "").strip()
        if not find:
            continue
        rep = r.get("replace") or ""
        pat = re.escape(find)
        if r.get("whole_word", True):
            pat = rf"(?<!\w){pat}(?!\w)"
        flags = 0 if r.get("case_sensitive") else re.IGNORECASE
        text = re.sub(pat, lambda _m: rep, text, flags=flags)
    return re.sub(r"\s+", " ", text).strip()


def match_speaker(name: str, known: list[str], cutoff: float = 0.8) -> str:
    """Map an OCR'd name onto an already-known speaker if it's a near miss."""
    if not name or name in known:
        return name
    lower = {k.lower(): k for k in known}
    if name.lower() in lower:
        return lower[name.lower()]
    hit = difflib.get_close_matches(name.lower(), list(lower), n=1, cutoff=cutoff)
    return lower[hit[0]] if hit else name
