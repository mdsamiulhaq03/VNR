"""Turn raw VN text into something a TTS engine reads nicely."""
from __future__ import annotations

import re

_TAG = re.compile(r"\{[^{}]*\}")          # Ren'Py {i}..{/i}, {w}, {color=..}
_VAR = re.compile(r"\[[^\[\]]*\]")        # leftover [variable] substitutions
_ELLIPSIS = re.compile(r"(?:\.{2,}|…+)")
_DASHES = re.compile(r"[-–—]{2,}")
_SPACE = re.compile(r"\s+")
_SPEAKABLE = re.compile(r"\w", re.UNICODE)
# Hearts are kept: they mark a flirty line and app/emotion.py removes them before speaking.
_HEARTS = re.compile(r"[♪♫☆★]")
_LONG_RUN = re.compile(r"([A-Za-z])\1{3,}")


def clean(text: str) -> str:
    if not text:
        return ""
    text = text.replace("{{", "{").replace("[[", "[")
    text = _TAG.sub("", text)
    text = _VAR.sub("", text)
    text = _ELLIPSIS.sub("…", text)
    text = _DASHES.sub("—", text)
    text = text.replace("　", " ")
    text = _HEARTS.sub("", text)                    # ♪ ☆ aren't spoken
    text = _LONG_RUN.sub(r"\1\1\1", text)           # "Ahhhhhhhh" -> "Ahhh" (voice-friendly)
    text = re.sub(r"([!?])\1{2,}", r"\1\1", text)   # "!!!!" -> "!!"
    text = _SPACE.sub(" ", text).strip()
    # Lines like "..." or "!?" carry no words: nothing to speak.
    if not _SPEAKABLE.search(text):
        return ""
    return text


_SPLIT = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+|(?<=[。！？])[」』”）]*")
_CJK_END = re.compile(r"[。！？」』”）]$")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def split_sentences(text: str, min_len: int = 12) -> list[str]:
    """Split a line into sentences so the first one can start playing sooner.

    Tiny pieces ("Mr.", "Oh!") are merged with the next one to avoid choppy speech.
    """
    parts = [p.strip() for p in _SPLIT.split(text) if p and p.strip()]
    if _CJK.search(text):
        min_len = max(4, min_len // 2)     # CJK packs more into each character
    out: list[str] = []
    buf = ""
    for p in parts:
        if buf:
            buf = buf + ("" if _CJK_END.search(buf) else " ") + p
        else:
            buf = p
        if len(buf) >= min_len:
            out.append(buf)
            buf = ""
    if buf:
        if out and len(buf) < min_len:
            out[-1] = out[-1] + ("" if _CJK_END.search(out[-1]) else " ") + buf
        else:
            out.append(buf)
    return out or ([text] if text else [])


def clean_name(name: str) -> str:
    return _SPACE.sub(" ", _TAG.sub("", name or "")).strip()
