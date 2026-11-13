"""Stage directions and emotion for dialogue.

`parse()` takes a line as the game shows it, e.g.

    *Seductively* And this might end up being very fun.

removes what must never be read aloud (*stage directions*, hearts, a trailing "~"), works out
the mood the line should be performed in, and returns the text to speak. Moods map to a
voice *variant* (an emotional clone of the same voice, see app/voice_bank.py), a speed
change, punctuation shaping, and VoiceStudio expression tags ([laughter], [sigh], …).

`add_tags()` additionally tags sounds written into the dialogue ("Ahaha", "Eh?!").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_I = re.IGNORECASE

# ------------------------------------------------------------------ moods
# Variants: emotional clones every voice can have (see voice_bank.MOOD_SCRIPTS).
VARIANTS = ("seductive", "aroused", "whisper", "happy", "sad", "angry")


@dataclass(frozen=True)
class Mood:
    name: str
    variant: str          # one of VARIANTS, or "" = the normal voice
    speed: float
    triggers: str         # regex alternatives, matched at the start of a word
    tag: str = ""         # expression tag placed where the direction stood
    shape: str = ""       # "trail" (. -> …), "breathy" (, -> …), "exclaim" (. -> !)


# Priority order: a direction that matches several moods takes the first one.
MOODS: list[Mood] = [
    Mood("climax", "aroused", 1.0,
         r"climax|orgasm|cum(?:s|ming)?\b|screams? in pleasure|arch(?:es|ing)? (?:her |his )?back"
         r"|trembl\w* violently", shape="exclaim"),
    Mood("aroused", "aroused", 0.85,
         r"moan|panting|pants? (?:heavily|softly|hard|loudly)|breathless|breath(?:es|ing) heavily"
         r"|heavy breath|gasps? in pleasure|whimper|shiver|trembl|squirm|writh|groan|arous|lust"
         r"|hungri|needily|horn(?:y|ily)|flushed|hot and bothered|in pleasure",
         shape="breathy"),
    Mood("seductive", "seductive", 0.92,
         r"seduct|sultr|sensual|allur|flirt|teas|purr|coy|wink|smirk"
         r"|bit(?:es|ing)? (?:her |his |my |your )?lip|lick(?:s|ing)? (?:her |his |my )?lips"
         r"|traces? (?:a |her |his )?finger|leans? (?:in )?clos|sways? (?:her )?hips|sexy|sexily"
         r"|inviting|suggestiv|naught|mischiev|bedroom eyes|husk",
         shape="trail"),
    Mood("begging", "aroused", 0.92, r"begs?\b|begging|plead|whin(?:e|es|ing)\b|desperate|needy",
         shape="trail"),
    Mood("dominant", "angry", 0.95,
         r"command|stern|firmly|demand|orders?\b|authoritativ|dominant|grabs?\b|possessiv"),
    Mood("submissive", "", 0.95, r"obedient|meek|submissiv"),
    Mood("tender", "seductive", 0.95,
         r"loving|tender|warmly|affection|gently|sweetly|caress|cuddl|nuzzl|hugs?\b|hugging"
         r"|embrac|strok"),
    Mood("kiss", "seductive", 0.95, r"kiss|chu\b|smooch|pecks?\b"),
    # The whisper voice is a real whisper: only for lines that say so.
    Mood("whisper", "whisper", 0.9,
         r"whisper|murmur|under (?:her |his |my )?breath|into (?:your|his|her) ear|hushed"),
    Mood("shy", "", 0.95,
         r"blush|shy|shily|nervous|stammer|stutter|embarrass|fidget|timid|fluster"
         r"|avoids? (?:eye|your gaze)|hesita|bashful|sheepish|gulp", shape="trail"),
    Mood("scared", "", 1.05,
         r"scared|afraid|fear|panic|terrif|shaking|frighten|horrif", shape="trail"),
    Mood("playful", "happy", 1.05,
         r"playful|sticks? (?:her |his )?tongue|pokes?\b|giggly|cheek(?:y|ily)|bubbly|wiggl"
         r"|winks? playfully"),
    Mood("laugh", "happy", 1.0, r"laugh|giggl|chuckl|snicker|cackl", tag="[laughter]"),
    Mood("happy", "happy", 1.05,
         r"smil|grin|cheer|happ|excit|beam|squeal|delight|ecstatic|joyful|gleeful|elated"),
    Mood("drunk", "seductive", 0.88, r"drunk|tipsy|slur|hiccup"),
    Mood("sleepy", "", 0.88, r"sleep|yawn|drows|grogg", tag="[sigh]"),
    Mood("sad", "sad", 0.9,
         r"sad|sobs?\b|sobbing|cr(?:y|ies|ying)\b|tear|sniff|frown|sorrow|heartbroken"
         r"|choked? up|depress|miserabl|gloom|melanchol", shape="trail"),
    Mood("jealous", "sad", 0.97, r"jealous|sulk|envious"),
    Mood("soft", "", 0.94, r"quiet|soft|faint|barely", shape="trail"),
    Mood("cold", "", 0.97,
         r"cold|mock|smug|sarcas|dismissiv|scoff|rolls? (?:her |his )?eyes|icily|bored"),
    Mood("angry", "angry", 1.06,
         r"angr|shout|yell|snap|growl|glar|furious|scream|hiss|slam|rage|enrag|irritat|annoy"
         r"|seeth|fum(?:es|ing)\b", shape="exclaim"),
    Mood("disgust", "angry", 1.0, r"disgust|grimac|gags?\b|ew+\b|revolt",
         tag="[dissatisfaction-hnn]"),
    Mood("pout", "", 1.0, r"hmph|pout|huff|tsk", tag="[dissatisfaction-hnn]"),
    Mood("confused", "", 1.0, r"confus|puzzl|tilts? (?:her |his )?head|blinks?\b|bewilder",
         tag="[question-ah]"),
    Mood("surprised", "", 1.05, r"gasp|surpris|shock|startl|eyes widen|jumps?\b|stunned",
         tag="[surprise-ah]"),
    Mood("sigh", "", 0.95, r"sigh|exhal|reliev", tag="[sigh]"),
]
# Moods inferred from the dialogue itself (no direction).
_INFERRED = {
    "excited": Mood("excited", "happy", 1.05, ""),
    "thought": Mood("thought", "", 0.95, ""),
}
NEUTRAL = Mood("", "", 1.0, "")
BY_NAME = {m.name: m for m in MOODS} | _INFERRED

_MOOD_RX = [(m, re.compile(r"\b(?:" + m.triggers + ")", _I)) for m in MOODS]

# Actions that are *sounds*: performed instead of dropped (when vocalize is on). Matched
# at the start of the direction, after an optional subject / adverbs ("she moans softly").
_LEAD = r"^(?:(?:she|he|i|they|you|we|then|and|\w+ly)\s+)*(?:"
_VOCAL: list[tuple[re.Pattern, str, str]] = [
    (re.compile(_LEAD + r"climax|orgasm|cum(?:s|ming)?\b)", _I), "Ahh… ahhn!", "climax"),
    (re.compile(_LEAD + r"moan\w*\s+(?:loudly|louder|out loud))", _I), "Ahhn!", "aroused"),
    (re.compile(_LEAD + r"moan)", _I), "Mmmh…", "aroused"),
    (re.compile(_LEAD + r"pant(?:s|ing)?\b|breath(?:es|ing) heavily)", _I),
     "Haah… haah…", "aroused"),
    (re.compile(_LEAD + r"whimper)", _I), "Nnh…", "aroused"),
    (re.compile(_LEAD + r"gasps? in pleasure)", _I), "Ah…!", "aroused"),
    (re.compile(_LEAD + r"purr)", _I), "Mmmm…", "seductive"),
    (re.compile(_LEAD + r"lick(?:s|ing)? (?:her |his |my )?lips)", _I), "Mmm…", "seductive"),
    (re.compile(_LEAD + r"kiss|chu\b|smooch)", _I), "Mwah.", "kiss"),
    (re.compile(_LEAD + r"squeal)", _I), "Kyaa!", "happy"),
    (re.compile(_LEAD + r"hiccup)", _I), "Hic…", "drunk"),
    (re.compile(_LEAD + r"gulp)", _I), "Gulp…", "shy"),
]

_LOUD = re.compile(r"\b(?:loud|desperate|frantic)", _I)
_SOFT = re.compile(r"\b(?:soft|quiet|barely|faint)", _I)

# ------------------------------------------------------------- dialogue cues
_STARS = re.compile(r"[*＊∗✱⁎]")
_PAIR = re.compile(r"\*\s*([^*\n]{1,60}?)\s*\*")
_PAREN = re.compile(r"\(\s*([^()]{1,40}?)\s*\)")
_HEARTS = re.compile(r"[♥♡❤❣💕💖💗💓💞💘]")
_NOTES = re.compile(r"[♪♫☆★]")
_TILDE = re.compile(r"[~～〜]+")
_MOAN_WORDS = re.compile(r"\b(?:a+hn+|nn+g+h*|hn+g+h*|hn{2,}|m+h+|m{2,}h|ha{2,}h*|u+hn+|ngh+)\b",
                         _I)
# These are moans only when there are several ("Ahh… ohh… ahh!"); one "Ahh!" is surprise.
_MAYBE_MOANS = re.compile(r"\b(?:a+h{2,}|o+h{2,}|oh god)\b", _I)


def _moaning(text: str) -> bool:
    return bool(_MOAN_WORDS.search(text)) or len(_MAYBE_MOANS.findall(text)) >= 2
_STUTTER = re.compile(r"(?<![\w-])([a-z]{1,2})-\1", _I)
_CAPS = re.compile(r"\b[A-Z]{3,}\b")
_ANGRY_WORDS = re.compile(r"\b(?:hate|damn|idiot|stupid|shut up|how dare|get out|stop it|"
                          r"bastard|jerk|pervert|leave me alone)\b", _I)
_SENT_END = re.compile(r"[.!?…\"”」』)]$")
_WORD = re.compile(r"\w", re.UNICODE)
_TAG = re.compile(r"\[[a-z]+(?:-[a-z]+)?\]")
_PH = "\x00"


@dataclass
class Directed:
    spoken: str                  # text to synthesize ("" = nothing to say)
    mood: str = ""               # mood name, "" = neutral
    variant: str = ""            # voice variant (VARIANTS) or ""
    speed: float = 1.0           # multiplier for the user's speed
    directions: tuple[str, ...] = ()


def _mood_of(direction: str) -> Mood | None:
    for m, rx in _MOOD_RX:
        if rx.search(direction):
            return m
    return None


def _vocal_of(direction: str) -> tuple[str, str] | None:
    d = direction.strip()
    for rx, sound, mood in _VOCAL:
        if rx.search(d):
            return sound, mood
    return None


def _is_direction(content: str) -> bool:
    return bool(_mood_of(content) or _vocal_of(content))


def _extract(text: str) -> tuple[str, list[str]]:
    """Replace stage directions by placeholders. Returns (text, directions)."""
    dirs: list[str] = []

    def keep(content: str) -> str:
        dirs.append(content.strip())
        return f" {_PH}{len(dirs) - 1}{_PH} "

    text = _STARS.sub("*", text)

    def pair(m: re.Match) -> str:
        content = m.group(1)
        before = text[:m.start()].rstrip()
        # "I *really* like you": emphasis inside a sentence, not a direction.
        mid_sentence = before and before[-1].isalnum() and m.end() < len(text.rstrip()) \
            and len(content.split()) == 1 and not _is_direction(content)
        if mid_sentence:
            return content
        return keep(content)

    text = _PAIR.sub(pair, text)

    # OCR lost one of the two asterisks: "Seductively* And…" / "…fun. *winks"
    if text.count("*") == 1:
        i = text.index("*")
        before, after = text[:i].strip(), text[i + 1:].strip()
        bw, aw = before.split(), after.split()
        if before and 1 <= len(bw) <= 4 and not re.search(r"[.!?…,\"“]", before) \
                and _PH not in before:
            text = keep(before) + text[i + 1:]
        elif after and 1 <= len(aw) <= 4 and (not before or _SENT_END.search(before)
                                               or before.endswith(_PH)):
            text = text[:i] + keep(after)
    text = text.replace("*", "")

    # (sighs) / (giggles): only when the words are a known action, so "(I think)" stays.
    stripped = text.strip()
    whole = re.fullmatch(r"\(\s*(.+?)\s*\)", stripped)
    if not (whole and not _is_direction(whole.group(1))):
        text = _PAREN.sub(lambda m: keep(m.group(1)) if len(m.group(1).split()) <= 5
                          and _is_direction(m.group(1)) else m.group(0), text)
    return text, dirs


def _shape(text: str, shape: str) -> str:
    text = text.rstrip()
    if not text:
        return text
    if shape in ("trail", "breathy"):
        if shape == "breathy":
            text = re.sub(r",\s+", "… ", text)
        text = re.sub(r"\.$", "…", text)
        if text[-1].isalnum():
            text += "…"
    elif shape == "exclaim":
        text = re.sub(r"[.…]$", "!", text)
        if text[-1].isalnum():
            text += "!"
    return text


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?…])", r"\1", text)
    text = re.sub(r"^[\s,;:.]+", "", text)            # ", and then…" after a removed direction
    text = re.sub(r"([.!?…])[,;]", r"\1", text)
    return text.strip()


def _capitalize(text: str) -> str:
    m = re.search(r"[A-Za-z]", text)
    if m and text[:m.start()].strip(" \"“'") == "":
        return text[:m.start()] + text[m.start()].upper() + text[m.start() + 1:]
    return text


STRENGTHS = ("subtle", "normal", "strong")


def parse(text: str, *, act: bool = True, vocalize: bool = True,
          tags: set[str] | list[str] | None = None, strength: str = "normal") -> Directed:
    """Stage directions out, mood in.

    act: perform the mood (shaping, sounds, tags); off = only remove what isn't spoken.
    vocalize: put a sound in place of actions like *moans* / *kisses*.
    tags: expression tags the TTS engine supports (None = none).
    strength: "subtle" (no emotional voices, half the speed change), "normal", "strong".
    """
    if not text:
        return Directed("")
    tags = set(tags or ())
    raw, dirs = _extract(text)

    mood: Mood | None = None
    chosen = ""
    already_sound = _moaning(re.sub(_PH + r"\d+" + _PH, " ", raw))
    fills: list[str] = []
    for d in dirs:
        m = _mood_of(d)
        vocal = _vocal_of(d) if act and vocalize else None
        if vocal and not m:
            m = BY_NAME.get(vocal[1])
        fill = ""
        if act:
            if vocal and not (already_sound and vocal[1] in ("aroused", "climax")):
                fill = vocal[0]
            elif m and m.tag and m.tag in tags:
                fill = m.tag
        fills.append(fill)
        if m and mood is None:
            mood, chosen = m, d

    def put(match: re.Match) -> str:
        return f" {fills[int(match.group(1))]} "
    spoken = re.sub(_PH + r"(\d+)" + _PH, put, raw)

    # Cues written into the dialogue itself.
    hearts = bool(_HEARTS.search(spoken))
    tilde = bool(re.search(r"\w[~～〜]|[~～〜]\s*$", spoken))
    spoken = _NOTES.sub("", _HEARTS.sub("", spoken))
    spoken = _TILDE.sub("", spoken)

    thought = re.fullmatch(r"\s*\(\s*(.+?)\s*\)\s*", spoken)
    if thought:                                       # Ren'Py inner monologue
        spoken = thought.group(1)

    caps = _CAPS.findall(spoken)
    shouting = len(caps) >= 2
    if shouting:                                      # don't spell "WHAT" out
        spoken = _capitalize(_CAPS.sub(lambda m: m.group(0).lower(), spoken))

    if mood is None:
        moans = _moaning(spoken)
        if thought:
            mood = _INFERRED["thought"]
        elif moans:
            mood = BY_NAME["aroused"]
        elif hearts:
            mood = BY_NAME["seductive"]
        elif tilde:
            mood = BY_NAME["playful"]
        elif shouting or "!!" in spoken:
            angry = shouting and (re.search(r"\?!|!\?", spoken) or _ANGRY_WORDS.search(spoken))
            mood = BY_NAME["angry"] if angry or _ANGRY_WORDS.search(spoken) \
                else _INFERRED["excited"]
        elif _STUTTER.search(spoken):
            mood = BY_NAME["scared"] if "!" in spoken else BY_NAME["shy"]
        elif spoken.count("…") + spoken.count("...") >= 3:
            mood = BY_NAME["shy"]

    spoken = _tidy(spoken)
    if not _WORD.search(_TAG.sub("", spoken)):
        spoken = ""
    mood = mood or NEUTRAL
    if not act:                         # only strip: same voice, same speed
        return Directed(spoken, mood.name, "", 1.0, tuple(dirs))
    out = Directed(spoken, mood.name, mood.variant, mood.speed, tuple(dirs))
    if not spoken:
        return out

    shape = mood.shape
    if chosen and _LOUD.search(chosen):
        out.speed *= 1.05
        shape = "exclaim"
    elif chosen and _SOFT.search(chosen):
        out.speed *= 0.97                       # softer, never a whisper by itself
    k = {"subtle": 0.5, "strong": 1.5}.get(strength, 1.0)
    out.speed = round(min(1.3, max(0.7, 1 + (out.speed - 1) * k)), 3)
    if strength == "subtle":
        out.variant = ""
    out.spoken = _shape(out.spoken, shape)
    if dirs:                            # "*Nervously* uh, hi" -> "Uh, hi"
        out.spoken = _capitalize(out.spoken)
    return out


# --------------------------------------------------- sounds inside the dialogue
# (pattern, tag, replace_text)  -- replace_text=True drops the matched text
_RULES: list[tuple[re.Pattern, str, bool]] = [
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


def add_tags(text: str, supported: set[str] | list[str] | None) -> str:
    if not text or not supported:
        return text
    supported = set(supported)
    out = text
    for pat, tag, replace in _RULES:
        if tag not in supported:
            continue

        def sub(m, tag=tag, replace=replace):
            # Don't tag twice (e.g. "Ahaha" right after a [laughter] from *laughs*).
            before = out_ref[0][max(0, m.start() - 14):m.start()]
            if tag in before:
                return m.group(0) if not replace else ""
            return tag if replace else f"{tag} {m.group(0)}"

        out_ref = [out]
        out = pat.sub(sub, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def strip_tags(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _TAG.sub("", text)).strip()
