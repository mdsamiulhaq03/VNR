"""Small voice effects: a line heard over a phone or a radio.

Pure numpy (no extra dependencies). Each effect takes mono float32 samples and the
sample rate and returns samples of the same length.
"""
from __future__ import annotations

import numpy as np

NAMES = ("phone", "radio")


def _band(x: np.ndarray, sr: int, lo: float, hi: float, soft: float = 0.15) -> np.ndarray:
    """Band-pass through an FFT mask with soft (cosine) edges, so it doesn't ring."""
    n = len(x)
    if n < 16:
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mask = np.ones_like(f)
    w_lo, w_hi = lo * soft, hi * soft
    below = f < lo
    mask[below] = 0.5 - 0.5 * np.cos(np.pi * np.clip((f[below] - (lo - w_lo)) / w_lo, 0, 1))
    above = f > hi
    mask[above] = 0.5 + 0.5 * np.cos(np.pi * np.clip((f[above] - hi) / w_hi, 0, 1))
    mask[f < lo - w_lo] = 0.0
    mask[f > hi + w_hi] = 0.0
    return np.fft.irfft(spec * mask, n).astype(np.float32)


def _clip(x: np.ndarray, drive: float) -> np.ndarray:
    return (np.tanh(x * drive) / np.tanh(drive)).astype(np.float32)


def _match_level(y: np.ndarray, x: np.ndarray, gain: float) -> np.ndarray:
    rx = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    ry = float(np.sqrt(np.mean(y * y))) if len(y) else 0.0
    if ry > 1e-6 and rx > 1e-6:
        y = y * (rx / ry) * gain
    return np.clip(y, -1.0, 1.0).astype(np.float32)


def phone(x: np.ndarray, sr: int) -> np.ndarray:
    """A phone call: 300–3400 Hz, a little crunch."""
    x = np.asarray(x, dtype=np.float32)
    y = _clip(_band(x, sr, 300.0, 3400.0), 1.8)
    return _match_level(y, x, 0.8)


def radio(x: np.ndarray, sr: int, seed: int = 7) -> np.ndarray:
    """A radio / intercom: narrower, more crunch, a little hiss."""
    x = np.asarray(x, dtype=np.float32)
    y = _clip(_band(x, sr, 500.0, 3000.0), 3.0)
    rng = np.random.default_rng(seed)
    hiss = _band(rng.standard_normal(len(y)).astype(np.float32), sr, 2000.0, 6000.0)
    level = float(np.sqrt(np.mean(y * y))) if len(y) else 0.0
    y = y + hiss * level * 0.06
    return _match_level(y, x, 0.8)


def get(name: str):
    """The effect function for a name ('' / unknown = None)."""
    return {"phone": phone, "radio": radio}.get((name or "").lower())
