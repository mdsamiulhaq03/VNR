"""Find the speaker name and the dialogue in a whole screenshot (Screenshot tab).

1. If this game's areas are saved and the screenshot matches the game window's shape,
   the saved name/dialogue areas are cut out (most accurate).
2. Otherwise the whole image is read and the dialogue is found by itself: lines are
   grouped into blocks, menu/UI text is dropped, and the biggest block in the lower
   part of the screen is the dialogue. A short line just above it is the name.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from PIL import Image

from .capture import frac_to_rect
from ..name_split import looks_like_name
from .ocr_source import clean_name_read, top_line_styled


@dataclass
class ShotResult:
    lines: list[str] = field(default_factory=list)
    boxes: list[tuple] = field(default_factory=list)       # (h, top, left, w) in image px
    info: dict = field(default_factory=dict)               # {"name": ..., "styled": ...}
    name_box: tuple | None = None                          # (left, top, w, h) in image px
    method: str = ""                                       # "saved areas" / "auto-detected"

    @property
    def found(self) -> bool:
        return bool(self.lines)


def _blocks(lines: list[str], boxes: list[tuple]) -> list[list[int]]:
    """Group line indexes into blocks of text that sit close together."""
    order = sorted(range(len(lines)), key=lambda i: boxes[i][1])
    if not order:
        return []
    med_h = statistics.median(b[0] for b in boxes) or 1.0
    blocks = [[order[0]]]
    for i in order[1:]:
        prev = boxes[blocks[-1][-1]]
        gap = boxes[i][1] - (prev[1] + prev[0])
        if gap <= 1.2 * max(med_h, prev[0]):
            blocks[-1].append(i)
        else:
            blocks.append([i])
    return blocks


def auto_detect(img: Image.Image, ocr, opts) -> ShotResult:
    reading = ocr.read_dialogue(img, opts)
    lines, boxes = list(reading.result.lines), list(reading.result.boxes)
    if not lines:
        return ShotResult(method="auto-detected")
    H = img.height
    blocks = _blocks(lines, boxes)

    def score(bl):
        chars = sum(len(lines[i]) for i in bl)
        mid = statistics.mean(boxes[i][1] + boxes[i][0] / 2 for i in bl)
        s = chars * (1.0 if mid > 0.4 * H else 0.3)
        if len(bl) >= 2 or chars >= 25:
            s *= 1.5
        return s

    best = max(blocks, key=score)
    order = sorted(best, key=lambda i: boxes[i][1])
    d_lines = [lines[i] for i in order]
    d_boxes = [boxes[i] for i in order]
    info: dict = {"name": None, "styled": top_line_styled(img, d_boxes)}
    name_box = None
    # A short line just above the dialogue block = the name (its own box).
    top = d_boxes[0]
    above = [i for i in range(len(lines)) if i not in best
             and boxes[i][1] + boxes[i][0] <= top[1]
             and top[1] - (boxes[i][1] + boxes[i][0]) <= 3 * top[0]]
    if above:
        j = max(above, key=lambda i: boxes[i][1])
        cand = clean_name_read(lines[j], getattr(opts, "ignore_words", []))
        if cand and len(lines[j].split()) <= 4:
            info["name"] = cand
            h, t, l, w = boxes[j]
            name_box = (l, t, w, h)
    return ShotResult(d_lines, d_boxes, info, name_box, "auto-detected")


def areas_from_result(res: ShotResult, size: tuple[int, int]
                      ) -> tuple[list[int] | None, list[int] | None]:
    """Name and dialogue areas [left, top, width, height] (image px) for an auto-detected
    line, made roomy enough for longer lines and names than the one on screen now.
    size: (width, height) of the image (= the game window, or the screen)."""
    if not res.found or not res.boxes:
        return None, None
    W, H = size
    bx = res.boxes                                       # (h, top, left, w)
    name_box = res.name_box
    # Name and dialogue read as one block: a top line in another colour / size that looks
    # like a name is the name box ("NATASHA" over the text).
    if not name_box and len(bx) >= 2 and res.info.get("styled")             and looks_like_name(res.lines[0], loose=True):
        h, t, l, w = bx[0]
        name_box, bx = (l, t, w, h), bx[1:]
    mh = statistics.median(b[0] for b in bx) or 12.0
    L = min(b[2] for b in bx)
    R = max(b[2] + b[3] for b in bx)
    T = min(b[1] for b in bx)
    B = max(b[1] + b[0] for b in bx)
    left = max(0.0, L - 0.8 * mh)
    # VN text boxes have about the same margin on both sides: reserve room for long lines.
    right = min(float(W), max(R + mh, W - L + 0.8 * mh))
    top = max(0.0, T - 0.5 * mh)
    name = None
    if name_box:
        nl, nt, nw, nh = name_box
        # The two areas meet between the name and the text, never overlapping.
        split = max(min(T, nt + nh), (nt + nh + T) / 2)
        top = max(top, split)
        n_left = max(0.0, nl - 0.6 * nh)
        n_top = max(0.0, nt - 0.4 * nh)
        n_right = min(float(W), nl + max(2.2 * nw, 9 * nh) + 0.6 * nh)   # longer names fit
        n_bottom = min(split, nt + nh + 0.4 * nh)
        if n_bottom - n_top >= 0.6 * nh and n_right > n_left:
            name = [int(n_left), int(n_top), int(n_right - n_left), int(n_bottom - n_top)]
    bottom = min(float(H), max(B + 0.7 * mh, T + 3 * 1.6 * mh))   # room for 3 lines
    text = [int(left), int(top), int(right - left), int(bottom - top)]
    return name, text


def from_saved_areas(img: Image.Image, text_frac, name_frac, ocr, opts) -> ShotResult:
    size = (0, 0, img.width, img.height)
    l, t, w, h = frac_to_rect(text_frac, size)
    crop = img.crop((l, t, l + w, t + h))
    reading = ocr.read_dialogue(crop, opts)
    boxes = [(bh, bt + t, bl + l, bw) for bh, bt, bl, bw in reading.result.boxes]
    info = {"name": None, "styled": reading.result.top_styled}
    name_box = None
    if name_frac:
        nl, nt, nw, nh = frac_to_rect(name_frac, size)
        info["name"] = ocr.read_name(img.crop((nl, nt, nl + nw, nt + nh)), opts)
        name_box = (nl, nt, nw, nh)
    return ShotResult(list(reading.result.lines), boxes, info, name_box, "saved areas")


def find_dialogue(img: Image.Image, ocr_area, ocr_full, opts, areas=None,
                  window_aspect: float | None = None) -> ShotResult:
    """areas: {"text": frac, "name": frac|None} saved for this game (window mode).
    window_aspect: the game window's width/height, if known. ocr_area reads cut-out
    areas; ocr_full reads whole screenshots."""
    img = img.convert("RGB")
    if areas and areas.get("text") and window_aspect:
        aspect = img.width / max(1, img.height)
        if abs(aspect - window_aspect) / window_aspect <= 0.02:
            r = from_saved_areas(img, areas["text"], areas.get("name"), ocr_area, opts)
            if r.found:
                return r
    return auto_detect(img, ocr_full, opts)
