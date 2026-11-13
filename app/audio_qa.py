"""Quick checks on generated speech, used to reject bad voice reference clips.

VoiceStudio's voice design occasionally produces a whisper or a squeaky,
unnaturally high voice. A clone made from such a clip whispers/squeaks on every
line, so reference clips are checked before they're used.
"""
from __future__ import annotations

import io

import numpy as np
import soundfile as sf


def load(wav: bytes) -> tuple[np.ndarray, int]:
    x, sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    return x.mean(axis=1), sr


def _frames(x: np.ndarray, sr: int):
    """Yields (frame, lagged window) for speech frames (skips silence)."""
    fl, hop = int(0.03 * sr), int(0.01 * sr)
    hi = int(sr / 70)
    rms_all = float(np.sqrt((x ** 2).mean())) + 1e-9
    for i in range(0, len(x) - fl - hi, hop):
        seg = x[i:i + fl + hi] - x[i:i + fl + hi].mean()
        a = seg[:fl]
        if np.sqrt((a ** 2).mean()) < 0.3 * rms_all:
            continue
        yield a, seg


def analyse(wav: bytes) -> dict:
    """{'voiced': share of speech frames that are voiced, 'f0': median pitch Hz, 'dur': s}"""
    x, sr = load(wav)
    # Downsample to 8 kHz for speed; pitch lives well below 4 kHz.
    if sr > 8000:
        step = sr // 8000
        x, sr = x[::step], sr // step
    fl = int(0.03 * sr)
    lo, hi = int(sr / 450), int(sr / 70)
    lags = np.arange(lo, hi)
    speech = voiced = 0
    pitches = []
    for a, seg in _frames(x, sr):
        speech += 1
        # Normalized cross-correlation of the frame with itself shifted by each lag.
        win = np.lib.stride_tricks.sliding_window_view(seg, fl)[lags]
        num = win @ a
        den = np.sqrt((a @ a) * (win * win).sum(axis=1)) + 1e-9
        c = num / den
        k = int(np.argmax(c))
        if c[k] > 0.7:
            voiced += 1
            pitches.append(sr / lags[k])
    return {"voiced": voiced / max(1, speech),
            "f0": float(np.median(pitches)) if pitches else 0.0,
            "dur": len(x) / sr}


_F0_RANGE = {"male": (65, 210), "female": (140, 380), "unknown": (65, 380)}


def reference_ok(wav: bytes, gender: str, age: str = "", slack: int = 0) -> tuple[bool, str]:
    """Is this a clean, natural-sounding reference clip for that kind of voice?
    slack: extra Hz allowed above the normal range (excited / angry takes)."""
    try:
        a = analyse(wav)
    except Exception as e:     # undecodable audio
        return False, f"bad audio ({e})"
    lo, hi = _F0_RANGE.get(gender, _F0_RANGE["unknown"])
    if age == "child":
        hi = 450
    hi += slack
    if a["dur"] < 3:
        return False, f"too short ({a['dur']:.1f}s)"
    if a["voiced"] < 0.5:
        return False, f"whispery ({a['voiced']:.0%} voiced)"
    if not lo <= a["f0"] <= hi:
        return False, f"unnatural pitch ({a['f0']:.0f} Hz)"
    return True, f"ok ({a['voiced']:.0%} voiced, {a['f0']:.0f} Hz)"
