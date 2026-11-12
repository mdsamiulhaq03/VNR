"""Clean up a captured text area before OCR: dark text on a plain white background.

Modes:
  auto  - Otsu threshold; the minority class is treated as text.
  color - keep only pixels close to the chosen text colour (best for busy backgrounds).
  off   - no cleanup, just upscale.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps


def otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    if total == 0:
        return 127
    sum_all = np.dot(np.arange(256), hist)
    w_b = 0.0
    sum_b = 0.0
    best_t, best_var = 127, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def text_mask(img: Image.Image, mode: str = "auto", color: list[int] | None = None,
              tolerance: int = 60) -> np.ndarray | None:
    """Boolean array, True where text pixels are. None for mode 'off'."""
    if mode == "off":
        return None
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    if mode == "color" and color:
        diff = rgb - np.array(color[:3], dtype=np.int16)
        dist = np.sqrt((diff.astype(np.int32) ** 2).sum(axis=2))
        return dist <= tolerance
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    t = otsu_threshold(gray)
    bright = gray > t
    # Text covers less of the box than the background does.
    return bright if bright.mean() < 0.5 else ~bright


def process(img: Image.Image, mode: str = "auto", color: list[int] | None = None,
            tolerance: int = 60, scale: int | None = None) -> Image.Image:
    """Image ready for OCR."""
    if scale is None:                     # small text boxes need more magnification
        scale = 3 if img.height < 150 else 2
    mask = text_mask(img, mode, color, tolerance)
    if mask is None:
        out = img
    else:
        out = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), "L")
    out = out.resize((out.width * scale, out.height * scale), Image.LANCZOS)
    return ImageOps.expand(out, border=12, fill=255 if mask is not None else (0, 0, 0))


def signature(img: Image.Image, mode: str = "auto", color: list[int] | None = None,
              tolerance: int = 60) -> np.ndarray:
    """Small fingerprint used to tell when the text has stopped changing."""
    mask = text_mask(img, mode, color, tolerance)
    if mask is None:
        small = img.convert("L").resize((96, 24))
        return np.asarray(small, dtype=np.float32)
    m = Image.fromarray((mask * 255).astype(np.uint8), "L").resize((96, 24), Image.BOX)
    return np.asarray(m, dtype=np.float32)
