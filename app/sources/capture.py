"""Grab the reading area, either from a fixed screen spot or relative to a game window."""
from __future__ import annotations

from dataclasses import dataclass

import mss
from PIL import Image

from .. import winapi

Rect = list[int]       # [left, top, width, height] physical px
Frac = list[float]     # [fx, fy, fw, fh] fractions of the window's client area


class WindowLost(Exception):
    pass


@dataclass
class CaptureSpec:
    mode: str = "screen"                 # "screen" | "window"
    text: list | None = None             # Rect (screen) or Frac (window)
    exe: str = ""
    title: str = ""
    hwnd: int = 0
    background: bool = False
    name: list | None = None             # optional separate name area (same format as text)


def frac_to_rect(frac: Frac, client: tuple[int, int, int, int]) -> Rect:
    cl, ct, cw, ch = client
    fx, fy, fw, fh = frac
    return [int(cl + fx * cw), int(ct + fy * ch), max(1, int(fw * cw)), max(1, int(fh * ch))]


def rect_to_frac(rect: Rect, client: tuple[int, int, int, int]) -> Frac:
    cl, ct, cw, ch = client
    l, t, w, h = rect
    return [(l - cl) / cw, (t - ct) / ch, w / cw, h / ch]


class Capturer:
    """Create one per thread (mss handles aren't shareable across threads)."""

    def __init__(self, on_note=lambda s: None):
        self._sct = mss.mss()
        self.on_note = on_note
        self._noted_black = False

    def close(self):
        self._sct.close()

    def _grab_screen(self, rect: Rect) -> Image.Image:
        l, t, w, h = rect
        shot = self._sct.grab({"left": l, "top": t, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def resolve(self, spec: CaptureSpec) -> int:
        if spec.hwnd and winapi.is_window(spec.hwnd):
            return spec.hwnd
        w = winapi.find_window(spec.exe, spec.title)
        if not w:
            raise WindowLost(f"Game window lost ({spec.exe or spec.title})")
        spec.hwnd = w.hwnd
        return w.hwnd

    def grab(self, spec: CaptureSpec) -> Image.Image | None:
        """The reading area image, or None if it isn't set / the game is minimized."""
        return self.grab_pair(spec)[0]

    def grab_pair(self, spec: CaptureSpec) -> tuple[Image.Image | None, Image.Image | None]:
        """(reading area, name area or None), both cut from ONE frame so the name and
        the dialogue always belong together. (None, None) if not set / minimized."""
        if not spec.text:
            return None, None
        rects: list[Rect]
        if spec.mode == "screen":
            rects = [list(spec.text)] + ([list(spec.name)] if spec.name else [])
            return self._crop_from_screen(rects, bool(spec.name))

        hwnd = self.resolve(spec)
        if winapi.is_minimized(hwnd):
            return None, None
        client = winapi.client_rect_screen(hwnd)
        if client[2] <= 0 or client[3] <= 0:
            return None, None
        if spec.background:
            full = winapi.capture_window(hwnd)
            if full is not None:
                size = (0, 0, full.width, full.height)
                return (_crop(full, frac_to_rect(spec.text, size)),
                        _crop(full, frac_to_rect(spec.name, size)) if spec.name else None)
            if not self._noted_black:
                self._noted_black = True
                self.on_note("This game blocks background capture: keep it visible on screen")
        rects = [frac_to_rect(spec.text, client)] + \
                ([frac_to_rect(spec.name, client)] if spec.name else [])
        return self._crop_from_screen(rects, bool(spec.name))

    def _crop_from_screen(self, rects: list[Rect], with_name: bool):
        if not with_name:
            return self._grab_screen(rects[0]), None
        # One grab of the area covering both, then cut each out.
        l = min(r[0] for r in rects)
        t = min(r[1] for r in rects)
        r_ = max(r[0] + r[2] for r in rects)
        b = max(r[1] + r[3] for r in rects)
        full = self._grab_screen([l, t, r_ - l, b - t])
        return tuple(_crop(full, [x - l, y - t, w, h]) for x, y, w, h in rects)


def _crop(img: Image.Image, rect: Rect) -> Image.Image:
    l, t, w, h = rect
    return img.crop((l, t, l + w, t + h))
