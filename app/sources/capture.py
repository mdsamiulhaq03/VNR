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
        if not spec.text:
            return None
        if spec.mode == "screen":
            return self._grab_screen(spec.text)

        hwnd = self.resolve(spec)
        if winapi.is_minimized(hwnd):
            return None
        client = winapi.client_rect_screen(hwnd)
        if client[2] <= 0 or client[3] <= 0:
            return None
        if spec.background:
            full = winapi.capture_window(hwnd)
            if full is not None:
                l, t, w, h = frac_to_rect(spec.text, (0, 0, full.width, full.height))
                return full.crop((l, t, l + w, t + h))
            if not self._noted_black:
                self._noted_black = True
                self.on_note("This game blocks background capture: keep it visible on screen")
        return self._grab_screen(frac_to_rect(spec.text, client))
