"""Built-in voice presets: 15 female and 4 male, all adults.

The *vibe* picks the script the voice's reference clip is recorded with (see
app/voice_bank.py), which sets its everyday delivery: sultry, flirty, sweet, breathy,
dominant or charming. Users can edit, add and delete presets.
"""
from __future__ import annotations

import copy
import uuid

# Version of the built-in set. Settings from an older version get the whole set replaced
# (see Config._load).
PRESETS_VERSION = 2

DEFAULT_PRESETS: list[dict] = [
    # ---- female ----
    {"id": "f-scarlett", "name": "Scarlett", "gender": "female", "age": "young adult",
     "pitch": "low pitch", "accent": "american accent", "style": "", "vibe": "sultry",
     "seed": 3011, "note": "Husky seductress"},
    {"id": "f-natasha", "name": "Natasha", "gender": "female", "age": "young adult",
     "pitch": "low pitch", "accent": "russian accent", "style": "", "vibe": "sultry",
     "seed": 3127, "note": "Cool femme fatale"},
    {"id": "f-valentina", "name": "Valentina", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "portuguese accent", "style": "", "vibe": "flirty",
     "seed": 3241, "note": "Warm Latina tease"},
    {"id": "f-chloe", "name": "Chloe", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "british accent", "style": "", "vibe": "flirty",
     "seed": 3359, "note": "Teasing British flirt"},
    {"id": "f-ruby", "name": "Ruby", "gender": "female", "age": "young adult",
     "pitch": "high pitch", "accent": "american accent", "style": "", "vibe": "flirty",
     "seed": 3467, "note": "Bubbly and flirty"},
    {"id": "f-sakura", "name": "Sakura", "gender": "female", "age": "young adult",
     "pitch": "high pitch", "accent": "japanese accent", "style": "", "vibe": "sweet",
     "seed": 3581, "note": "Sweet anime girlfriend"},
    {"id": "f-yumi", "name": "Yumi", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "japanese accent", "style": "whisper", "vibe": "breathy",
     "seed": 3697, "note": "Breathy whisper"},
    {"id": "f-jasmine", "name": "Jasmine", "gender": "female", "age": "young adult",
     "pitch": "low pitch", "accent": "indian accent", "style": "", "vibe": "sultry",
     "seed": 3803, "note": "Smooth and sensual"},
    {"id": "f-mei", "name": "Mei", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "chinese accent", "style": "", "vibe": "sweet",
     "seed": 3919, "note": "Soft and alluring"},
    {"id": "f-seoyeon", "name": "Seo-yeon", "gender": "female", "age": "young adult",
     "pitch": "high pitch", "accent": "korean accent", "style": "", "vibe": "sweet",
     "seed": 4021, "note": "Cute idol type"},
    {"id": "f-kiara", "name": "Kiara", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "australian accent", "style": "", "vibe": "flirty",
     "seed": 4133, "note": "Sun-kissed flirt"},
    {"id": "f-bella", "name": "Bella", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "american accent", "style": "", "vibe": "sweet",
     "seed": 4241, "note": "Girl next door"},
    {"id": "f-raven", "name": "Raven", "gender": "female", "age": "young adult",
     "pitch": "very low pitch", "accent": "american accent", "style": "whisper", "vibe": "breathy",
     "seed": 4357, "note": "Dark husky whisper"},
    {"id": "f-vivian", "name": "Vivian", "gender": "female", "age": "middle-aged",
     "pitch": "low pitch", "accent": "british accent", "style": "", "vibe": "dominant",
     "seed": 4463, "note": "Elegant mature temptress"},
    {"id": "f-lena", "name": "Lena", "gender": "female", "age": "middle-aged",
     "pitch": "low pitch", "accent": "american accent", "style": "", "vibe": "dominant",
     "seed": 4567, "note": "Confident and commanding"},
    # ---- male ----
    {"id": "m-ryan", "name": "Ryan", "gender": "male", "age": "young adult",
     "pitch": "moderate pitch", "accent": "american accent", "style": "", "vibe": "",
     "seed": 4673, "note": "Protagonist / friend"},
    {"id": "m-damien", "name": "Damien", "gender": "male", "age": "young adult",
     "pitch": "low pitch", "accent": "british accent", "style": "", "vibe": "charming",
     "seed": 4787, "note": "Smooth and seductive"},
    {"id": "m-kenji", "name": "Kenji", "gender": "male", "age": "young adult",
     "pitch": "moderate pitch", "accent": "japanese accent", "style": "", "vibe": "",
     "seed": 4889, "note": "Gentle male lead"},
    {"id": "m-marcus", "name": "Marcus", "gender": "male", "age": "middle-aged",
     "pitch": "very low pitch", "accent": "american accent", "style": "", "vibe": "dominant",
     "seed": 4993, "note": "Deep and commanding"},
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
}


def defaults() -> list[dict]:
    return copy.deepcopy(DEFAULT_PRESETS)


def sample_line(p: dict) -> str:
    return f"Hi, I'm {p.get('name', '')}. " + VIBE_SAMPLES.get(
        p.get("vibe") or "", (p.get("note") or "This is my voice") + ".")


def new_preset(gender: str = "female", base: dict | None = None) -> dict:
    p = copy.deepcopy(base) if base else {
        "gender": gender, "age": "young adult", "pitch": "moderate pitch",
        "accent": "american accent", "style": "", "vibe": "", "seed": 1, "note": ""}
    p["id"] = "u-" + uuid.uuid4().hex[:8]
    p["name"] = (base["name"] + " copy") if base else "New voice"
    return p


def by_id(presets: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in presets}
