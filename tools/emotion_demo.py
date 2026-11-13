"""Listen to one preset in every mood.

    python tools/emotion_demo.py [preset-id] [out-dir] ["line"]

Makes the preset's voice and its emotional variants in VoiceStudio (with a throw-away
settings file, so your own settings aren't touched), speaks the line once per mood into
out-dir, prints how breathy each take is, then deletes the clones it made.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio_qa, emotion, presets, voices  # noqa: E402
from app.config import Config  # noqa: E402
from app.tts.voicestudio import VoiceStudioEngine  # noqa: E402
from app.voice_bank import VoiceBank  # noqa: E402

LINES = {
    "": "And this might end up being very fun.",
    "seductive": "*Seductively* And this might end up being very fun.",
    "aroused": "*moans softly* And this might end up being very fun.",
    "whisper": "*whispers* And this might end up being very fun.",
    "happy": "*smiles happily* And this might end up being very fun.",
    "sad": "*sadly* And this might end up being very fun.",
    "angry": "*angrily* And this might end up being very fun.",
}


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "f-natasha"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="vnr-moods-"))
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config(Path(tempfile.mkdtemp()) / "settings.json")
    cfg.settings.save_vs_profiles = True
    vs = VoiceStudioEngine(cfg.settings.voicestudio_url)
    if not vs.available():
        sys.exit("VoiceStudio isn't running")
    tags = vs.expression_tags()
    bank = VoiceBank(cfg, vs, on_status=lambda s: print("  ", s))
    design = voices.effective({"preset": pid}, presets.by_id(cfg.settings.presets))
    print(f"{design['label']}: {voices.instruct(design)}, vibe={design.get('vibe') or '-'}")
    made: list[str] = []
    try:
        t = time.time()
        base = bank.get_profile(design)
        if not base:
            sys.exit("couldn't make the voice")
        made.append(base)
        print(f"voice ready in {time.time() - t:.0f}s")
        for mood, text in LINES.items():
            d = emotion.parse(text, tags=tags)
            voice = base
            if mood:
                t = time.time()
                voice = bank._create_variant(design, mood, base)
                made.append(voice)
                print(f"{mood} variant in {time.time() - t:.0f}s")
            wav = vs.synth(d.spoken, voice, d.speed, language="en")
            name = f"{pid}-{mood or 'neutral'}.wav"
            (out / name).write_bytes(wav)
            a = audio_qa.analyse(wav)
            print(f"  {name:28} '{d.spoken}' x{d.speed}  voiced {a['voiced']:.0%}  "
                  f"f0 {a['f0']:.0f} Hz")
        print(f"\nlisten: {out}")
    finally:
        for p in made:
            vs.delete_profile(p)


if __name__ == "__main__":
    main()
