"""Clean up OCR output: drop menu buttons and junk lines, fix common misreads.

Input lines are (text, height_px) as returned by WindowsOcr.lines_prepared.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

_TOKEN = re.compile(r"\S+")
_WORD = re.compile(r"[A-Za-z0-9|'’]+")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")
_LATIN = re.compile(r"[A-Za-z]")
# Interjections that legitimately have no vowels.
_NO_VOWEL_OK = {"hm", "hmm", "hmmm", "hmph", "shh", "sh", "tsk", "psst", "pfft", "brr", "grr",
                "zzz", "mm", "mmm", "nn", "ngh", "tch", "ck", "pst", "hrm", "hrmm", "ssh"}
_OK_SINGLE = {"a", "i", "o"}
# Vowel-less sounds that are real dialogue: moans, hums, shushing ("Nnnn…", "Hnnng!", "Mmm").
_NO_VOWEL_SOUND = re.compile(r"(?:h*m+h*|h*n+g*h*|n+g+h*|s+h+|t+s+k+|p+f+t+|g+r+|b+r+|z+|"
                             r"t+c+h+|p+s+t+|h+r+m+|m+h+m+|k+h+)")
# Elongated sounds ("Haaah", "Ahhhn", "Ohhh", "Hyaaan") are never typos.
_ELONGATED = re.compile(r"(.)\1{1,}")
_SOUND_LETTERS = set("ahnmoiuyekfw")

# Words OCR spell-fix must know: anime/VN vocabulary and adult words, so they're never
# "corrected" (Senpai -> Sendai) or treated as garbled.
VN_WORDS = """
senpai sempai sensei kouhai kohai onii oniichan onee oneechan oneesan niisan neesan nii nee
chan kun san sama dono tan chin baka bakka kawaii desu nya nyan nyaa ara arara mou ehehe
uwah uwaa hyaa hyaaa hyaan kyaa kyaaa yamete itai sugoi oppai ecchi hentai etchi tsundere
yandere waifu oji ojisan obasan ojousama goshujinsama master mistress senpai's sensei's
cum cums cumming cummed creampie clit clitoris boobs tits titties nipples nipple pussy cock
dick dicks cocks balls horny slut sluts whore bitch fuck fucks fucking fucked fucker shit
damn moan moans moaning moaned orgasm orgasms panties bra lewd naughty pervert perv
blowjob handjob thighs butt ass asses anal oral strap lube dildo vibrator ahegao pantsu
haah haaah hah haa nngh ngh nnn mmm mhm hnng hnngh ahh ahhh ahhhn ohh ohhh uhh unn ufufu
fufu fufufu kukuku tehe hehe ahaha ehehe
""".split()
# VN interjections / laughs that must never be "corrected" (Hehe -> Here).
_INTERJECTION = re.compile(
    r"(?:a|e|u)?(?:ha|he|hi|fu|ku|ni|ya|wa|ki)(?:ha|he|hi|fu|ku|ni|ya|wa|ki)+h?|"
    r"(?:hmph|hmm+|huh|ugh+|eh+|ah+|oh+|uh+|um+|wah+|kya+|uwa+h?|ehe+|ara|mou|nya+n?|"
    r"geh|tch|pfft|psst|shh+|ooh+|aww+|yay|whoa|hnn+|mhm|nngh|ngh|ehehe|ahaha|fufu|ufufu|"
    r"kukuku|tehe|teehee|yikes|eek|wha|hah|heh|meh|bah|ahem|oof|ow+|gah|blah)")
# (wrong, right) OCR confusions, tried only if the result is a real word.
_CONFUSIONS = [("rn", "m"), ("vv", "w"), ("cl", "d"), ("0", "o"), ("1", "l"), ("|", "l"),
               ("5", "s"), ("ii", "u"), ("li", "h"), ("tb", "th")]


@dataclass
class FilterResult:
    lines: list[str] = field(default_factory=list)
    boxes: list[tuple] = field(default_factory=list)    # (height, top, left, width) per line
    dropped: list[tuple[str, str]] = field(default_factory=list)       # (line, reason)
    corrections: list[tuple[str, str]] = field(default_factory=list)   # (wrong, right)


def _norm(tok: str) -> str:
    return re.sub(r"[^0-9a-z]", "", tok.lower())


def ignore_set(words: list[str]) -> set[str]:
    out = set()
    for w in words:
        out.add(_norm(w))
        for part in w.split():
            out.add(_norm(part))
    out.discard("")
    return out


def junk_reason(line: str, lang: str = "en") -> str | None:
    s = line.strip()
    letters = sum(ch.isalpha() for ch in s)
    nonspace = sum(not ch.isspace() for ch in s)
    if letters < 2 and not (letters == 1 and s.strip("?!.… ").lower() in ("i", "a", "o")):
        return "no letters"
    if nonspace and letters / nonspace < 0.5:
        return "mostly symbols"
    if lang.split("-")[0].lower() in ("en", "") and _LATIN.search(s):
        words = [_norm(t) for t in _TOKEN.findall(s)]
        words = [w for w in words if w and not w.isdigit()]
        if words and all(not _VOWEL.search(w) and w not in _NO_VOWEL_OK
                         and not _NO_VOWEL_SOUND.fullmatch(w) for w in words):
            return "no vowels (garbled)"
        if len(words) >= 3:
            singles = sum(1 for w in words if len(w) == 1 and w not in _OK_SINGLE)
            if singles / len(words) > 0.5:
                return "scattered single characters"
    return None


def _strip_menu_edges(line: str, ignore: set[str]) -> str:
    toks = line.split()
    # A run of >=2 menu words glued to an end of a dialogue line (e.g. a quick menu row).
    i = 0
    while i < len(toks) and _norm(toks[i]) in ignore:
        i += 1
    j = len(toks)
    while j > i and _norm(toks[j - 1]) in ignore:
        j -= 1
    if i >= 2:
        toks = toks[i:]
        j -= i
        i = 0
    if len(toks) - j >= 2:
        toks = toks[:j]
    return " ".join(toks)


def filter_lines(lines: list[tuple[str, float]], ignore_words: list[str],
                 lang: str = "en") -> FilterResult:
    res = FilterResult()
    if not lines:
        return res
    ignore = ignore_set(ignore_words)
    heights = [item[1] for item in lines if item[1] > 0]
    med = statistics.median(heights) if heights else 0
    for item in lines:
        text, h = item[0], item[1]
        box = tuple(item[1:5]) if len(item) >= 5 else (h, 0.0, 0.0, 0.0)
        t = text.strip()
        if not t:
            continue
        toks = [_norm(x) for x in t.split()]
        toks = [x for x in toks if x]
        if toks and sum(x in ignore for x in toks) / len(toks) >= 0.6:
            res.dropped.append((t, "menu buttons"))
            continue
        if med and len(lines) >= 2 and h and h < 0.55 * med:
            res.dropped.append((t, "tiny text (not dialogue)"))
            continue
        why = junk_reason(t, lang)
        if why:
            res.dropped.append((t, why))
            continue
        stripped = _strip_menu_edges(t, ignore)
        if stripped != t:
            res.dropped.append((t[len(stripped):] if t.startswith(stripped) else t,
                                "menu buttons at the edge"))
        if stripped:
            res.lines.append(stripped)
            res.boxes.append(box)
    return res


# ------------------------------------------------------------ spelling
_spell = None


def _checker():
    global _spell
    if _spell is None:
        from spellchecker import SpellChecker
        _spell = SpellChecker(distance=1)
        _spell.word_frequency.load_words(VN_WORDS)
    return _spell


def _match_case(src: str, word: str) -> str:
    if src.isupper() and len(src) > 1:
        return word.upper()
    if src[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def _fix_word(w: str, known_lower: set[str], sentence_start: bool) -> str | None:
    """A corrected word, or None to leave it alone."""
    sp = _checker()
    low = w.lower().replace("’", "'")
    core = low.strip("'")
    if not core or core in known_lower or sp.known([core]) or _INTERJECTION.fullmatch(core):
        return None
    if _NO_VOWEL_SOUND.fullmatch(core):
        return None
    # Drawn-out sounds (haaah, ahhhn, hyaaan, nnnnh) are never typos.
    if _ELONGATED.search(core) and set(core) <= _SOUND_LETTERS:
        return None
    if "-" in w:                               # stutters: "D-don't", "N-no"
        return None
    if w in ("|", "l") or (w == "1" and sentence_start):
        return "I"
    if any(ch.isdigit() for ch in core) and core.isdigit():
        return None
    # 1) single OCR confusions that produce a real word
    for wrong, right in _CONFUSIONS:
        if wrong not in core:
            continue
        cands = [core.replace(wrong, right)]
        i = core.find(wrong)
        while i >= 0:                      # also each occurrence on its own
            cands.append(core[:i] + right + core[i + len(wrong):])
            i = core.find(wrong, i + 1)
        for cand in cands:
            if sp.known([cand]):
                return _match_case(w, cand)
    # 2) distance-1 spelling fix for ordinary words (not names: capitalized mid-sentence)
    if len(core) >= 4 and core.isalpha() and (w[:1].islower() or sentence_start):
        cand = sp.correction(core)
        if cand and cand != core and sp.known([cand]):
            return _match_case(w, cand)
    return None


def correct(text: str, known_names: list[str]) -> tuple[str, list[tuple[str, str]]]:
    known_lower = {n.lower() for name in known_names for n in name.split()}
    fixes: list[tuple[str, str]] = []
    out = []
    pos = 0
    sentence_start = True
    for m in _WORD.finditer(text):
        out.append(text[pos:m.start()])
        w = m.group(0)
        between = text[pos:m.start()]
        if re.search(r"[.!?…]\s*$", between):
            sentence_start = True
        new = _fix_word(w, known_lower, sentence_start)
        if new and new != w:
            fixes.append((w, new))
            w = new
        out.append(w)
        pos = m.end()
        sentence_start = False
    out.append(text[pos:])
    return "".join(out), fixes


def word_score(text: str) -> float:
    """Share of words that are real English words (higher = cleaner OCR)."""
    words = [w.lower().strip("'") for w in _WORD.findall(text) if len(w) >= 2]
    words = [w for w in words if w.isalpha()]
    if not words:
        return 0.0
    return len(_checker().known(words)) / len(words) if words else 0.0


def text_quality(text: str) -> float:
    """How much good text a read contains: real words count, garbled words cost.
    Unlike word_score this prefers the read that kept *more* lines (e.g. the name on a
    coloured name box that the cleaned-up image lost)."""
    words = [w.lower().strip("'") for w in _WORD.findall(text) if len(w) >= 2]
    words = [w for w in words if w.isalpha()]
    if not words:
        return 0.0
    known = len(_checker().known(words))
    capitalized_names = sum(1 for w in _WORD.findall(text) if w[:1].isupper() and len(w) > 2)
    unknown = len(words) - known
    # Unknown capitalized words are often names, so they cost less.
    return known - 0.6 * max(0, unknown - capitalized_names) - 0.2 * min(unknown,
                                                                        capitalized_names)
