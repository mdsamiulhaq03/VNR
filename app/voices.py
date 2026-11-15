"""Character voices: gender guessing, presets and VoiceStudio voice design.

A character record (stored in settings.voices[game][name]):
    {
      "gender": "female" | "male" | "unknown",
      "gender_auto": True,            # False once the user picked it
      "preset": "f-sunny",            # preset id, or "" for a custom design
      "accent": "",                   # character accent override ("" = preset's / global)
      "age_override": "",             # character age override ("" = the voice's own age)
      "age", "pitch", "style", "vibe", "seed" # the custom design (used when preset == "")
      "voice_override": "",           # "vs:<profile id>" or "sapi:<voice>" chosen by the user
    }
The *effective design* merges preset, character and global accent settings; its
instruct string + seed identify one voice (see app/voice_bank.py).
"""
from __future__ import annotations

import random
import re

from .config import NARRATOR

GENDERS = ["female", "male", "unknown"]
AGES = ["child", "teenager", "young adult", "middle-aged", "elderly"]
# The ages that can be picked (adult content: every voice has seductive variants).
ADULT_AGES = ["young adult", "middle-aged", "elderly"]
PITCHES = ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"]
ACCENTS = ["american accent", "british accent", "australian accent", "canadian accent",
           "indian accent", "japanese accent", "korean accent", "chinese accent",
           "russian accent", "portuguese accent"]
STYLES = ["", "whisper"]

# Variety cycles for custom designs (used once a gender's presets run out).
_PITCH_CYCLE = {
    "female": ["high pitch", "moderate pitch", "very high pitch", "high pitch", "low pitch",
               "moderate pitch"],
    "male": ["low pitch", "moderate pitch", "very low pitch", "low pitch", "high pitch",
             "moderate pitch"],
    "unknown": ["moderate pitch", "high pitch", "low pitch"],
}
_FEMALE_VIBES = ["sultry", "flirty", "sweet"]
_ACCENT_CYCLE = ["american accent", "british accent", "american accent", "australian accent",
                 "canadian accent", "british accent"]

# Common Japanese given names (romanized), checked before the English name list.
_JP_FEMALE = set("""
ai aiko akane akari akemi aki akiko amane ami anzu aoi asuka asuna ayaka ayame ayano ayu ayumi
chiaki chie chihiro chika chinatsu chiyo chizuru emi emiko eri erika fumiko fuyumi hana hanako
haruka haruna haruhi hikari hinata hiromi hitomi honoka hotaru ichika iroha izumi kaede kana kanae
kanon kaori kasumi kazumi keiko kiyomi kokoro kotone kumiko kurumi kyoko mai maki mami
mana manami mari mariko marina masami mayu mayumi megumi mei michiko midori mika miki miku mina
minami mio misaki misato mitsuki miyu miyuki mizuki moe momo momoko nagisa nana nanami naoko
natsuki natsumi nene noriko nozomi rei reiko rena rika rin rina rio risa ritsuko riko ruka rumi
sachiko saki sakura sana sara saya sayaka sayori setsuna shiho shiori shizuka suzu suzune tomoe
tomoko tsumugi umi wakana yoko yoshiko yui yuika yuka yukari yuki yukiko yume yumi yuna
yuri yuriko yuu yuzu monika miyako kirika kotori madoka homura mikoto ruri shinobu
""".split())
_JP_MALE = set("""
akira atsushi daichi daisuke eiji goro hajime haruki haruto hayato hideo hikaru
hiroki hiroshi hiroto ichiro isamu jiro jun junichi kaito kazuki kazuo keisuke ken kenji kenta
kenichi kosuke koji kota kyo makoto masaki masaru masato minoru naoki naoto noboru osamu riku
ren ryo ryota ryuu ryuji saburo satoshi shin shinji shota shun sora sosuke subaru takashi takeshi
takumi taro tatsuya tetsuya tomoya toru tsubasa yamato yoshi yoshio yuto yuuto yusuke yuuki
yuji kirito shiro kaoru haruhiko itsuki
""".split())
_F_SUFFIX = ("ko", "mi", "ka", "na", "yo", "ri", "ne", "e", "ha")
_M_SUFFIX = ("ro", "ta", "suke", "shi", "to", "ya", "ki", "ji", "ru", "go", "o", "ma")

_detector = None


def _gg(name: str) -> str:
    global _detector
    try:
        if _detector is None:
            import gender_guesser.detector as gd
            _detector = gd.Detector(case_sensitive=False)
        g = _detector.get_gender(name)
    except Exception:
        return "unknown"
    if g in ("female", "mostly_female"):
        return "female"
    if g in ("male", "mostly_male"):
        return "male"
    return "unknown"


_MALE_TITLES = {"mr", "mister", "sir", "lord", "king", "prince", "father", "dad", "daddy",
                "papa", "uncle", "brother", "bro", "grandpa", "grandfather", "old man", "boy",
                "master", "duke", "count", "baron", "emperor", "monk", "priest", "headmaster",
                "oniisan", "oniichan", "onii", "niisan", "ojisan", "ojiisan", "otousan", "senpai-kun"}
_FEMALE_TITLES = {"ms", "mrs", "miss", "madam", "madame", "lady", "queen", "princess", "mother",
                  "mom", "mommy", "mama", "aunt", "sister", "sis", "grandma", "grandmother",
                  "girl", "mistress", "duchess", "countess", "empress", "nun", "maid", "witch",
                  "oneesan", "oneechan", "onee", "neesan", "obasan", "obaasan", "okaasan",
                  "ojousama", "headmistress"}


def guess_gender(name: str) -> str:
    """Best guess from a character name: 'female', 'male' or 'unknown'."""
    if not name or name == NARRATOR:
        return "unknown"
    low = name.strip().lower()
    words = [re.sub(r"[^a-z]", "", w) for w in re.split(r"[\s_.]+", low)]
    words = [w for w in words if w]
    # Titles and family words ("Mr. Tanaka", "Old Man", "Onee-chan", "Mother").
    joined = re.sub(r"[^a-z ]", "", low.replace("-", ""))
    for t in _MALE_TITLES:
        if t in words or joined.startswith(t + " ") or joined == t:
            return "male"
    for t in _FEMALE_TITLES:
        if t in words or joined.startswith(t + " ") or joined == t:
            return "female"
    if re.search(r"-(?:kun)\b", low):
        return "male"
    if re.search(r"-(?:chan|tan)\b", low):
        return "female"
    first = re.split(r"[\s\-_.]+", name.strip())[0]
    key = re.sub(r"[^a-z]", "", first.lower())
    if not key:
        return "unknown"
    if key in _JP_FEMALE:
        return "female"
    if key in _JP_MALE:
        return "male"
    g = _gg(first)
    if g != "unknown":
        return g
    if len(key) >= 3:
        if key.endswith(_F_SUFFIX):
            return "female"
        if key.endswith(_M_SUFFIX):
            return "male"
    return "unknown"


# ------------------------------------------------------------- designs
def effective(rec: dict, presets: dict[str, dict], accent_override: str = "",
              name: str = "") -> dict:
    """The final voice design for a character: preset + character accent + global accent."""
    p = presets.get(rec.get("preset") or "")
    base = p if p else rec
    d = {
        "gender": base.get("gender") or rec.get("gender") or "unknown",
        "age": base.get("age") or "young adult",
        "pitch": base.get("pitch") or "moderate pitch",
        "accent": base.get("accent") or "",
        "style": base.get("style") or "",
        "vibe": base.get("vibe") or "",
        "seed": int(base.get("seed") or 42),
        "label": p["name"] if p else (name or "custom"),
    }
    if rec.get("accent") and p:            # per-character accent on top of a preset
        d["accent"] = rec["accent"]
    if rec.get("age_override"):            # per-character age, preset or custom
        d["age"] = rec["age_override"]
    if rec.get("pitch_override"):          # e.g. "Younger Zalton": a step higher
        d["pitch"] = rec["pitch_override"]
    if accent_override:                    # global accent for every voice
        d["accent"] = accent_override
    if d["accent"] in ("", "Auto"):
        d["accent"] = ""
    return d


def instruct(design: dict) -> str:
    """VoiceStudio voice-design tag string."""
    parts = []
    if design.get("gender") in ("female", "male"):
        parts.append(design["gender"])
    for k in ("age", "pitch", "style", "accent"):
        v = design.get(k)
        if v and v != "Auto":
            parts.append(v)
    return ", ".join(parts)


def voice_key(design: dict) -> str:
    """Identifies one voice: same key -> same VoiceStudio clone. The vibe is part of it
    (it changes the reference clip), but only when set, so older keys stay valid."""
    key = f"{instruct(design)}|{design.get('seed')}"
    return f"{key}|{design['vibe']}" if design.get("vibe") else key


def fresh_seed(used: set) -> int:
    while True:
        s = random.randint(1, 99999)
        if s not in used:
            return s


def custom_design(name: str, gender: str, others: dict[str, dict]) -> dict:
    """A custom design for `name` that differs from the other characters' voices."""
    used = {o.get("seed") for o in others.values()}
    if name == NARRATOR:
        return {"gender": gender if gender != "unknown" else "male", "age": "middle-aged",
                "pitch": "low pitch", "accent": "", "style": "", "seed": fresh_seed(used)}
    n = sum(1 for k, o in others.items() if k != NARRATOR and o.get("gender") == gender
            and not o.get("preset"))
    pitches = _PITCH_CYCLE.get(gender, _PITCH_CYCLE["unknown"])
    return {
        "gender": gender,
        "age": "young adult",
        "pitch": pitches[n % len(pitches)],
        "accent": _ACCENT_CYCLE[(n // len(pitches) + n) % len(_ACCENT_CYCLE)],
        "style": "",
        "vibe": _FEMALE_VIBES[n % len(_FEMALE_VIBES)] if gender == "female" else "",
        "seed": fresh_seed(used),
    }


# Order in which new characters get voices: one from each category in turn, so a new
# cast is varied (narrator voices only for the narrator).
AUTO_ORDER = {"female": ["sweet", "flirty", "seductive", "energetic", "cool", "mature", "shy"],
              "male": ["lead", "charming", "rough", "commanding"]}


def pick_preset(gender: str, others: dict[str, dict], presets: list[dict], role: str = "",
                narrator: bool = False) -> str:
    """An unused preset for a new character ('' if they're all taken): from the role's
    category first, else one category after another (round robin)."""
    from .presets import ROLE_CATEGORY, category_of
    used = {o.get("preset") for o in others.values() if o.get("preset")}
    free = [p for p in presets if p["id"] not in used]
    if narrator:
        pool = [p for p in free if category_of(p) == "narrator"]
        return pool[0]["id"] if pool else ""
    free = [p for p in free if category_of(p) != "narrator" and
            (gender == "unknown" or p.get("gender") == gender)]
    if not free:
        return ""
    order = AUTO_ORDER.get(gender) or AUTO_ORDER["female"] + AUTO_ORDER["male"]
    want = ROLE_CATEGORY.get(role)
    if want:
        order = [want] + [c for c in order if c != want]
    rank = {c: i for i, c in enumerate(order)}
    pos: dict[str, int] = {}
    keyed = []
    count: dict[str, int] = {}
    for p in presets:                       # position of each preset in its category
        if p.get("style") == "whisper":     # (whispers don't take a place)
            continue
        c = category_of(p)
        pos[p["id"]] = count.get(c, 0)
        count[c] = count.get(c, 0) + 1
    for p in free:
        c = category_of(p)
        first = 0 if want and c == want else 1           # the role's category: all first
        # Whisper voices whisper every line: only when nothing else is free.
        keyed.append((p.get("style") == "whisper", first, pos.get(p["id"], 99),
                      rank.get(c, len(rank)), p["id"]))
    keyed.sort()
    return keyed[0][-1]


# "Younger Zalton" / "Old Zalton" / "Zalton (young)": the same person at another age.
_YOUNGER = ("young", "younger", "little", "kid", "child", "teen", "teenage", "past")
_OLDER = ("old", "older", "elder", "adult", "grown-up", "future")
_AGE_PREFIX = re.compile(r"^(" + "|".join(_YOUNGER + _OLDER) + r")\s+(.+)$", re.I)
_AGE_TAG = re.compile(r"^(.+?)\s*[(\[]\s*(" + "|".join(_YOUNGER + _OLDER) + r")\s*[)\]]\s*$",
                      re.I)


def age_variant(name: str, others: dict[str, dict]) -> tuple[str, int] | None:
    """(base character, -1 younger / +1 older) when `name` is an existing character at
    another age, else None."""
    m = _AGE_PREFIX.match((name or "").strip())
    word, base = (m.group(1), m.group(2)) if m else (None, None)
    if not m:
        m = _AGE_TAG.match((name or "").strip())
        if m:
            base, word = m.group(1), m.group(2)
    if not base:
        return None
    lower = {k.lower(): k for k in others if k != NARRATOR}
    real = lower.get(base.strip().lower())
    if not real:
        return None
    return real, (-1 if word.lower() in _YOUNGER else 1)


def _shift(items: list[str], value: str, step: int) -> str:
    i = items.index(value) if value in items else 0
    return items[max(0, min(len(items) - 1, i + step))]


def related_voice(base: dict, step: int, presets: list[dict]) -> dict:
    """The base character's voice, a step younger (higher) or older (lower). Ages stay
    adult (never below young adult)."""
    out = {k: base[k] for k in ("gender", "preset", "accent", "vibe", "seed", "age", "pitch",
                                "style", "voice_override", "role") if k in base}
    out["gender_auto"] = base.get("gender_auto", True)
    p = {x["id"]: x for x in presets}.get(base.get("preset") or "")
    src = p if p else base
    age = base.get("age_override") or src.get("age") or "young adult"
    pitch = base.get("pitch_override") or src.get("pitch") or "moderate pitch"
    new_age = _shift(ADULT_AGES, age if age in ADULT_AGES else "young adult", step)
    if new_age != age:
        out["age_override"] = new_age
    elif base.get("age_override"):
        out["age_override"] = base["age_override"]
    out["pitch_override"] = _shift(PITCHES, pitch, -step)       # younger: higher
    return out


def complete_character(name: str, rec: dict, others: dict[str, dict],
                       presets: list[dict]) -> dict:
    """Fill in a new, legacy (v2) or pre-preset (v3) character record."""
    rel = age_variant(name, others) if not rec else None
    if rel is not None:                       # "Younger Zalton": Zalton's voice, younger
        base, step = rel
        out = related_voice(others[base], step, presets)
        out["variant_of"] = base
        out.setdefault("voice_override", "")
        return out
    gender = rec.get("gender") or guess_gender(name)
    out = {"gender_auto": rec.get("gender_auto", True), "voice_override": ""}
    out.update(custom_design(name, gender, others))
    out["preset"] = pick_preset(gender, others, presets, rec.get("role", ""),
                                narrator=name == NARRATOR)
    if rec.get("role"):
        out["role"] = rec["role"]
    if out["preset"]:
        out["accent"] = ""          # "" = use the preset's own accent
    if rec.get("voice_override"):
        out["voice_override"] = rec["voice_override"]
    return out
