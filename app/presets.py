"""Built-in voice presets: 60 adult voices in 12 categories of 5.

A category says what *kind* of voice it is (husky and slow, bright and quick…) and who
it suits best (femme fatale, childhood friend, boss…), so the Cast page can suggest
voices for a character's role. Within a category no two voices share the same age,
pitch, accent and style, and every seed is different, so all 60 sound different.

The *vibe* picks the script the voice's reference clip is recorded with (see
app/voice_bank.py), which sets its everyday delivery. Users can edit, add and delete
presets.
"""
from __future__ import annotations

import copy
import uuid

# Version of the built-in set. Settings from an older version get the whole set replaced
# (see Config._load).
PRESETS_VERSION = 3   # (seeds tuned with tools/check_voices.py)

# key, title, gender, kind of voice, best for, default vibe
CATEGORIES: list[dict] = [
    {"key": "seductive", "title": "Seductive", "gender": "female",
     "kind": "Husky, slow and low", "best_for": ["femme fatale", "temptress", "lover"],
     "vibe": "sultry"},
    {"key": "flirty", "title": "Flirty", "gender": "female",
     "kind": "Playful and teasing", "best_for": ["flirt", "party girl", "gyaru"],
     "vibe": "flirty"},
    {"key": "sweet", "title": "Sweet", "gender": "female",
     "kind": "Warm and gentle", "best_for": ["girlfriend", "heroine", "childhood friend"],
     "vibe": "sweet"},
    {"key": "shy", "title": "Shy & soft", "gender": "female",
     "kind": "Quiet and soft", "best_for": ["shy girl", "bookworm", "gentle type"],
     "vibe": "shy"},
    {"key": "mature", "title": "Mature & dominant", "gender": "female",
     "kind": "Older, firm and confident",
     "best_for": ["boss", "mistress", "teacher", "older woman"], "vibe": "dominant"},
    {"key": "energetic", "title": "Energetic", "gender": "female",
     "kind": "Bright, quick and lively",
     "best_for": ["tomboy", "sporty girl", "cheerful friend"], "vibe": "energetic"},
    {"key": "cool", "title": "Cool & calm", "gender": "female",
     "kind": "Even and composed",
     "best_for": ["AI", "doctor", "receptionist", "cool beauty"], "vibe": "cool"},
    {"key": "lead", "title": "Male lead", "gender": "male",
     "kind": "Friendly and natural", "best_for": ["protagonist", "best friend"],
     "vibe": ""},
    {"key": "charming", "title": "Charming", "gender": "male",
     "kind": "Smooth and low", "best_for": ["seducer", "gentleman", "love interest"],
     "vibe": "charming"},
    {"key": "commanding", "title": "Deep & commanding", "gender": "male",
     "kind": "Older and very deep", "best_for": ["male boss", "villain", "father figure"],
     "vibe": "dominant"},
    {"key": "rough", "title": "Rough & energetic", "gender": "male",
     "kind": "Loud and casual", "best_for": ["bro", "rival", "jock"], "vibe": "rough"},
    {"key": "narrator", "title": "Narrator", "gender": "any",
     "kind": "Calm storyteller", "best_for": ["narration", "inner thoughts"],
     "vibe": "narrator"},
]
CATEGORY = {c["key"]: c for c in CATEGORIES}
# Every role, in category order (the Cast page's Role list).
ROLES: list[str] = [r for c in CATEGORIES for r in c["best_for"]]
ROLE_CATEGORY = {r: c["key"] for c in CATEGORIES for r in c["best_for"]}

YA, MA, OLD = "young adult", "middle-aged", "elderly"
VL, LO, MO, HI, VH = "very low pitch", "low pitch", "moderate pitch", "high pitch", \
    "very high pitch"


def _p(pid, name, cat, gender, age, pitch, accent, seed, note, style="", vibe=None):
    c = CATEGORY[cat]
    return {"id": pid, "name": name, "gender": gender, "age": age, "pitch": pitch,
            "accent": f"{accent} accent", "style": style,
            "vibe": c["vibe"] if vibe is None else vibe, "seed": seed, "note": note,
            "category": cat, "best_for": list(c["best_for"])}


F, M = "female", "male"
DEFAULT_PRESETS: list[dict] = [
    # ---- seductive ----
    _p("f-scarlett", "Scarlett", "seductive", F, YA, LO, "american", 3011, "Husky seductress"),
    _p("f-natasha", "Natasha", "seductive", F, YA, LO, "russian", 3127, "Cool femme fatale"),
    _p("f-jasmine", "Jasmine", "seductive", F, YA, LO, "indian", 12884, "Smooth and sensual"),
    _p("f-raven", "Raven", "seductive", F, YA, VL, "american", 4357, "Dark husky whisper",
       style="whisper", vibe="breathy"),
    _p("f-elena", "Elena", "seductive", F, YA, MO, "portuguese", 5003, "Velvet late-night voice"),
    # ---- flirty ----
    _p("f-valentina", "Valentina", "flirty", F, YA, MO, "portuguese", 3241, "Warm Latina tease"),
    _p("f-chloe", "Chloe", "flirty", F, YA, MO, "british", 7395, "Teasing British flirt"),
    _p("f-ruby", "Ruby", "flirty", F, YA, HI, "american", 5485, "Bubbly and flirty"),
    _p("f-kiara", "Kiara", "flirty", F, YA, MO, "australian", 4133, "Sun-kissed flirt"),
    _p("f-gina", "Gina", "flirty", F, YA, HI, "canadian", 7131, "Giggly party girl"),
    # ---- sweet ----
    _p("f-sakura", "Sakura", "sweet", F, YA, HI, "japanese", 3581, "Sweet anime girlfriend"),
    _p("f-mei", "Mei", "sweet", F, YA, LO, "chinese", 5937, "Soft and alluring"),
    _p("f-seoyeon", "Seo-yeon", "sweet", F, YA, HI, "korean", 4021, "Cute idol type"),
    _p("f-bella", "Bella", "sweet", F, YA, MO, "american", 4241, "Girl next door"),
    _p("f-emily", "Emily", "sweet", F, YA, MO, "british", 5227, "Kind childhood friend"),
    # ---- shy & soft ----
    _p("f-yumi", "Yumi", "shy", F, YA, MO, "japanese", 3697, "Breathy whisper",
       style="whisper", vibe="breathy"),
    _p("f-lily", "Lily", "shy", F, YA, HI, "american", 8360, "Timid and quiet"),
    _p("f-aiko", "Aiko", "shy", F, YA, MO, "japanese", 6450, "Soft-spoken bookworm"),
    _p("f-nora", "Nora", "shy", F, YA, MO, "canadian", 5557, "Gentle and hesitant"),
    _p("f-priya", "Priya", "shy", F, YA, HI, "indian", 10714, "Sweet and bashful"),
    # ---- mature & dominant ----
    _p("f-vivian", "Vivian", "mature", F, MA, LO, "british", 4463, "Elegant mature temptress"),
    _p("f-lena", "Lena", "mature", F, MA, LO, "american", 9612, "Confident and commanding"),
    _p("f-katya", "Katya", "mature", F, MA, VL, "russian", 5779, "Strict mistress"),
    _p("f-diane", "Diane", "mature", F, MA, MO, "australian", 6890, "Warm older woman",
       vibe="sultry"),
    _p("f-helena", "Helena", "mature", F, OLD, LO, "british", 6996, "Stern headmistress"),
    # ---- energetic ----
    _p("f-zoe", "Zoe", "energetic", F, YA, HI, "american", 6091, "Peppy cheerleader"),
    _p("f-sky", "Sky", "energetic", F, YA, VH, "australian", 7212, "Hyper and excitable"),
    _p("f-rin", "Rin", "energetic", F, YA, HI, "japanese", 6311, "Tomboy with attitude"),
    _p("f-maya", "Maya", "energetic", F, YA, MO, "canadian", 6421, "Sporty and upbeat"),
    _p("f-jun", "Ji-woo", "energetic", F, YA, HI, "korean", 12583, "Lively best friend"),
    # ---- cool & calm ----
    _p("f-eve", "Eve", "cool", F, YA, MO, "american", 6637, "Calm AI assistant"),
    _p("f-iris", "Iris", "cool", F, YA, LO, "british", 8761, "Composed doctor"),
    _p("f-yuki", "Yuki", "cool", F, YA, LO, "japanese", 6857, "Quiet cool beauty"),
    _p("f-alina", "Alina", "cool", F, MA, MO, "russian", 12006, "Professional and precise"),
    _p("f-lin", "Lin", "cool", F, YA, MO, "chinese", 8078, "Polite receptionist"),
    # ---- male lead ----
    _p("m-ryan", "Ryan", "lead", M, YA, MO, "american", 4673, "Protagonist / friend"),
    _p("m-kenji", "Kenji", "lead", M, YA, MO, "japanese", 12961, "Gentle male lead"),
    _p("m-liam", "Liam", "lead", M, YA, MO, "british", 7177, "Easy-going hero"),
    _p("m-noah", "Noah", "lead", M, YA, LO, "canadian", 7283, "Calm best friend"),
    _p("m-arjun", "Arjun", "lead", M, YA, MO, "indian", 7393, "Warm and earnest"),
    # ---- charming ----
    _p("m-damien", "Damien", "charming", M, YA, LO, "british", 4787, "Smooth and seductive"),
    _p("m-lucas", "Lucas", "charming", M, YA, LO, "portuguese", 13553, "Latin charmer"),
    _p("m-alex", "Alex", "charming", M, YA, LO, "american", 9621, "Confident love interest"),
    _p("m-hiro", "Hiro", "charming", M, YA, LO, "japanese", 7717, "Cool gentleman"),
    _p("m-minjun", "Min-jun", "charming", M, YA, MO, "korean", 7823, "Soft-spoken heartthrob"),
    # ---- deep & commanding ----
    _p("m-marcus", "Marcus", "commanding", M, MA, VL, "american", 4993, "Deep and commanding"),
    _p("m-viktor", "Viktor", "commanding", M, MA, VL, "russian", 7933, "Cold villain"),
    _p("m-edward", "Edward", "commanding", M, OLD, LO, "british", 11066, "Aristocratic patriarch"),
    _p("m-raj", "Raj", "commanding", M, MA, LO, "indian", 8147, "Strict father figure"),
    _p("m-frank", "Frank", "commanding", M, OLD, VL, "american", 13308, "Gravelly old boss"),
    # ---- rough & energetic ----
    _p("m-jake", "Jake", "rough", M, YA, MO, "american", 8369, "Loud gym bro"),
    _p("m-brody", "Brody", "rough", M, YA, MO, "australian", 8471, "Cheeky surfer"),
    _p("m-dmitri", "Dmitri", "rough", M, YA, LO, "russian", 8581, "Hot-headed rival"),
    _p("m-takeshi", "Takeshi", "rough", M, YA, MO, "japanese", 17774, "Brash delinquent"),
    _p("m-connor", "Connor", "rough", M, YA, LO, "canadian", 8807, "Jock team captain"),
    # ---- narrator ----
    _p("n-claire", "Claire", "narrator", F, MA, MO, "british", 8923, "Warm storyteller"),
    _p("n-grace", "Grace", "narrator", F, YA, LO, "american", 9029, "Soft audiobook voice"),
    _p("n-james", "James", "narrator", M, MA, LO, "british", 9137, "Classic narrator"),
    _p("n-henry", "Henry", "narrator", M, OLD, LO, "american", 9241, "Wise old voice"),
    _p("n-sam", "Sam", "narrator", M, YA, MO, "american", 9349, "Relaxed modern narrator"),
]

DESIGN_KEYS = ("gender", "age", "pitch", "accent", "style", "vibe", "seed")

# Sample line per vibe, used when previewing a preset.
VIBE_SAMPLES = {
    "sultry": "Mmm… I was hoping you'd come and find me tonight.",
    "flirty": "Hey there~ were you looking for me? Hehe.",
    "sweet": "You came! I'm so happy to see you.",
    "breathy": "Shh… come closer. I want to tell you a secret.",
    "dominant": "Look at me. You'll do exactly as I say.",
    "charming": "You look beautiful tonight. Stay a while.",
    "shy": "Um… h-hi. I hope I'm not bothering you…",
    "energetic": "Come on, let's go! We're gonna be late, hurry up!",
    "cool": "Good evening. Everything is ready, as you asked.",
    "narrator": "The rain had stopped by the time she reached the old station.",
    "rough": "Yo, what's up? You ready to get your butt kicked or what?",
}


def defaults() -> list[dict]:
    return copy.deepcopy(DEFAULT_PRESETS)


def category_of(p: dict) -> str:
    """A preset's category (user presets without one go by gender and vibe)."""
    cat = p.get("category")
    if cat in CATEGORY:
        return cat
    if p.get("gender") == "male":
        return {"charming": "charming", "dominant": "commanding",
                "rough": "rough"}.get(p.get("vibe") or "", "lead")
    return {"sultry": "seductive", "breathy": "seductive", "flirty": "flirty",
            "dominant": "mature", "energetic": "energetic", "cool": "cool", "shy": "shy",
            "narrator": "narrator"}.get(p.get("vibe") or "", "sweet")


def best_for(p: dict) -> list[str]:
    return list(p.get("best_for") or CATEGORY[category_of(p)]["best_for"])


def sample_line(p: dict) -> str:
    return f"Hi, I'm {p.get('name', '')}. " + VIBE_SAMPLES.get(
        p.get("vibe") or "", (p.get("note") or "This is my voice") + ".")


def new_preset(gender: str = "female", base: dict | None = None) -> dict:
    p = copy.deepcopy(base) if base else {
        "gender": gender, "age": "young adult", "pitch": "moderate pitch",
        "accent": "american accent", "style": "", "vibe": "", "seed": 1, "note": "",
        "category": "sweet" if gender == "female" else "lead"}
    p["id"] = "u-" + uuid.uuid4().hex[:8]
    p["name"] = (base["name"] + " copy") if base else "New voice"
    if "best_for" not in p:
        p["best_for"] = best_for(p)
    return p


def by_id(presets: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in presets}
