"""Add VoiceStudio expression tags ([laughter], [sigh], …) to dialogue.

Stage directions like *laughs* / (sigh) are replaced by the tag; spoken sounds like
"Ahaha" or "Eh?" keep their text and get the tag in front, so the voice performs them.
Only tags the active VoiceStudio engine reports are used.
"""
from __future__ import annotations

import re

_I = re.IGNORECASE

# (pattern, tag, replace_text)  -- replace_text=True drops the matched stage direction
_RULES: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"[*(\[]\s*(?:laughs?|laughing|giggles?|chuckles?)\s*[*)\]]", _I),
     "[laughter]", True),
    (re.compile(r"[*(\[]\s*sighs?\s*[*)\]]", _I), "[sigh]", True),
    (re.compile(r"\b(?:a?ha(?:ha)+|e?he(?:he)+|fufu(?:fu)*|ufufu|kuku(?:ku)*)\b[!.…~]*", _I),
     "[laughter]", False),
    # Only an actual "sigh" word; panting "Haa… haa…" is left for the voice to perform.
    (re.compile(r"(?:^|(?<=[\s\"“「]))sigh(?=[.…,!\s]|$)", _I), "[sigh]", False),
    # These tags *are* the sound: replace the word, or it's said twice ("Ah, ah!").
    (re.compile(r"\beh\s*\?!?|\beh!\?", _I), "[question-ei]", True),
    (re.compile(r"\b(?:huh|ah)\s*\?", _I), "[question-ah]", True),
    (re.compile(r"\boh\s*\?", _I), "[question-oh]", True),
    # Short surprise only ("Ah!", "Oh!"): long moans ("Ahhhh!", "Ohhh!") stay as text.
    (re.compile(r"\bahh?!", _I), "[surprise-ah]", True),
    (re.compile(r"\b(?:ohh?|whoa)!", _I), "[surprise-oh]", True),
    (re.compile(r"\bwah?!", _I), "[surprise-wa]", True),
    (re.compile(r"\b(?:hmph|hnn+)\b[.!]?", _I), "[dissatisfaction-hnn]", True),
]

_TAG = re.compile(r"\[[a-z]+(?:-[a-z]+)?\]")


def add_tags(text: str, supported: set[str] | list[str] | None) -> str:
    if not text or not supported:
        return text
    supported = set(supported)
    out = text
    for pat, tag, replace in _RULES:
        if tag not in supported:
            continue

        def sub(m, tag=tag, replace=replace):
            # Don't tag twice (e.g. "Ahaha" after a *laughs* that already became a tag).
            before = out_ref[0][max(0, m.start() - 14):m.start()]
            if tag in before:
                return m.group(0) if not replace else ""
            return tag if replace else f"{tag} {m.group(0)}"

        out_ref = [out]
        out = pat.sub(sub, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _TAG.sub("", text)).strip()
