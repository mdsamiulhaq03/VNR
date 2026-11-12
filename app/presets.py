"""Built-in voice presets (3 male, 8 female). Users can edit, add and delete them."""
from __future__ import annotations

import copy
import uuid

DEFAULT_PRESETS: list[dict] = [
    # ---- female ----
    {"id": "f-sunny", "name": "Sunny", "gender": "female", "age": "teenager",
     "pitch": "high pitch", "accent": "american accent", "style": "", "seed": 103,
     "note": "Bright, energetic girl"},
    {"id": "f-hana", "name": "Hana", "gender": "female", "age": "young adult",
     "pitch": "high pitch", "accent": "japanese accent", "style": "", "seed": 211,
     "note": "Anime-style heroine"},
    {"id": "f-clara", "name": "Clara", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "british accent", "style": "", "seed": 302,
     "note": "Polite and clear"},
    {"id": "f-mia", "name": "Mia", "gender": "female", "age": "young adult",
     "pitch": "moderate pitch", "accent": "american accent", "style": "", "seed": 418,
     "note": "Friendly, casual"},
    {"id": "f-rose", "name": "Rose", "gender": "female", "age": "young adult",
     "pitch": "low pitch", "accent": "american accent", "style": "", "seed": 527,
     "note": "Cool and mature"},
    {"id": "f-elena", "name": "Elena", "gender": "female", "age": "middle-aged",
     "pitch": "low pitch", "accent": "british accent", "style": "", "seed": 619,
     "note": "Elegant older woman"},
    {"id": "f-lily", "name": "Lily", "gender": "female", "age": "child",
     "pitch": "very high pitch", "accent": "american accent", "style": "", "seed": 733,
     "note": "Little girl"},
    {"id": "f-yuki", "name": "Yuki", "gender": "female", "age": "teenager",
     "pitch": "moderate pitch", "accent": "british accent", "style": "", "seed": 845,
     "note": "Shy, soft-spoken (set Style to Whisper for a whisper)"},
    # ---- male ----
    {"id": "m-ryan", "name": "Ryan", "gender": "male", "age": "young adult",
     "pitch": "moderate pitch", "accent": "american accent", "style": "", "seed": 103,
     "note": "Protagonist / friend"},
    {"id": "m-kai", "name": "Kai", "gender": "male", "age": "teenager",
     "pitch": "high pitch", "accent": "american accent", "style": "", "seed": 958,
     "note": "Boyish"},
    {"id": "m-james", "name": "James", "gender": "male", "age": "middle-aged",
     "pitch": "low pitch", "accent": "british accent", "style": "", "seed": 1061,
     "note": "Mature: teacher, father"},
]

DESIGN_KEYS = ("gender", "age", "pitch", "accent", "style", "seed")


def defaults() -> list[dict]:
    return copy.deepcopy(DEFAULT_PRESETS)


def new_preset(gender: str = "female", base: dict | None = None) -> dict:
    p = copy.deepcopy(base) if base else {
        "gender": gender, "age": "young adult", "pitch": "moderate pitch",
        "accent": "american accent", "style": "", "seed": 1, "note": ""}
    p["id"] = "u-" + uuid.uuid4().hex[:8]
    p["name"] = (base["name"] + " copy") if base else "New voice"
    return p


def by_id(presets: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in presets}
