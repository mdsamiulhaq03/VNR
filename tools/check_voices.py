"""Check that the built-in voices really sound different (needs VoiceStudio running).

Each preset says the same line with its voice design; for every voice we measure the
pitch (median f0), a timbre fingerprint (average log spectrum in 32 bands) and the
speaking rate. Two voices of one category that are close on all three are reported.

    python tools/check_voices.py            # all categories
    python tools/check_voices.py shy flirty # only these
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import audio_qa, presets, voices  # noqa: E402

URL = "http://127.0.0.1:3900/v1/audio/speech"
LINE = "I wasn't sure you'd come tonight, but I'm really glad you did. Shall we go inside?"
F0_CLOSE = 6.0       # Hz (accents differ within a category, so only near-identical pitch)
TIMBRE_CLOSE = 0.01  # cosine distance of the spectra (all voices are within ~0.02)
RATE_CLOSE = 0.08    # relative difference of the durations


def measure(p: dict, session: requests.Session) -> dict:
    body = {"model": "tts-1", "input": LINE, "voice": "default", "response_format": "wav",
            "instruct": voices.instruct(p), "seed": int(p["seed"]), "language": "en",
            "num_step": 24}
    wav = session.post(URL, json=body, timeout=120).content
    a = audio_qa.analyse(wav)
    x, sr = sf.read(io.BytesIO(wav), dtype="float32")
    x = x if x.ndim == 1 else x.mean(axis=1)
    n = 1024
    frames = [x[i:i + n] for i in range(0, len(x) - n, n // 2)]
    loud = [f for f in frames if np.sqrt((f * f).mean()) > 0.02]
    spec = np.mean([np.abs(np.fft.rfft(f * np.hanning(n))) for f in loud], axis=0)
    bands = np.array_split(spec[: n // 4], 32)          # 0 .. sr/4 (6 kHz at 24 kHz)
    timbre = np.log1p(np.array([b.mean() for b in bands]))
    return {"f0": a["f0"], "timbre": timbre, "dur": a["dur"]}


def cos_dist(a, b) -> float:
    return float(1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def main(only: list[str]) -> int:
    s = requests.Session()
    ps = presets.defaults()
    cats = only or [c["key"] for c in presets.CATEGORIES]
    problems = 0
    for cat in cats:
        items = [p for p in ps if presets.category_of(p) == cat]
        m = {p["id"]: measure(p, s) for p in items}
        print(f"\n{presets.CATEGORY[cat]['title']}")
        for p in items:
            r = m[p["id"]]
            print(f"  {p['name']:<10} f0 {r['f0']:6.1f} Hz   {r['dur']:4.1f} s   "
                  f"{voices.instruct(p)}")
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                ra, rb = m[a["id"]], m[b["id"]]
                df0 = abs(ra["f0"] - rb["f0"])
                dt = cos_dist(ra["timbre"], rb["timbre"])
                dr = abs(ra["dur"] - rb["dur"]) / max(ra["dur"], rb["dur"])
                if df0 < F0_CLOSE and dt < TIMBRE_CLOSE and dr < RATE_CLOSE:
                    problems += 1
                    print(f"  !! {a['name']} and {b['name']} sound alike "
                          f"(pitch {df0:.0f} Hz apart, timbre {dt:.3f}, rate {dr:.2f})")
    print(f"\n{problems} pair(s) too alike")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
