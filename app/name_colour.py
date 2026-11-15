"""Speakers from the colour of their name.

Many games colour each character's name (Mila green, Aida pink). Once a few lines have
taught us each name's colour, a name the OCR can't read can still be identified, but
only in games where the colours really differ: if every name is white, this stays off.
Ren'Py scripts give the colours directly (Character(..., color="#26d55c")).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

SAMPLES = 8          # colour samples kept per character
DISTINCT = 60.0      # every two characters' colours must be at least this far apart
MATCH = 45.0         # a measured colour this close to one character's is that character


def measure(img: Image.Image | None) -> list[int] | None:
    """Colour of the name's letters: the most vivid, bright pixels in the name area."""
    if img is None or img.width < 4 or img.height < 4:
        return None
    a = np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3)
    mx, mn = a.max(axis=1), a.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1)
    vivid = sat * mx                           # strongly coloured AND bright
    strong = a[(sat >= 0.55) & (mx >= 120)]
    if len(strong) >= max(20, 0.02 * len(a)):  # a coloured name
        top = a[vivid >= np.percentile(vivid, 95)]
    else:                                      # white / grey name: the brightest letters
        top = a[mx >= np.percentile(mx, 95)]
    if len(top) < 5:
        return None
    return [int(v) for v in np.median(top, axis=0)]


def dist(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a[:3], float) - np.asarray(b[:3], float)))


def hex_to_rgb(h: str) -> list[int] | None:
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) < 6:
        return None
    try:
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return None


def learn(rec: dict, rgb: list[int] | None) -> None:
    """Add one sample to a character record; its name_colour is the samples' median."""
    if rgb is None or rec.get("name_colour_source") == "script":
        return
    s = (rec.get("name_colour_samples") or [])[-(SAMPLES - 1):] + [[int(v) for v in rgb[:3]]]
    rec["name_colour_samples"] = s
    if len(s) >= 3:
        rec["name_colour"] = [int(v) for v in np.median(np.array(s), axis=0)]


def colours(chars: dict[str, dict]) -> dict[str, list[int]]:
    return {n: r["name_colour"] for n, r in chars.items() if r.get("name_colour")}


def colour_coded(chars: dict[str, dict]) -> bool:
    """Do this game's names have clearly different colours?"""
    c = list(colours(chars).values())
    if len(c) < 2:
        return False
    return all(dist(c[i], c[j]) >= DISTINCT for i in range(len(c)) for j in range(i + 1, len(c)))


def guess(chars: dict[str, dict], rgb: list[int] | None) -> str | None:
    """The one character whose name has this colour, if the game is colour-coded."""
    if rgb is None or not colour_coded(chars):
        return None
    near = [n for n, c in colours(chars).items() if dist(c, rgb) <= MATCH]
    return near[0] if len(near) == 1 else None
