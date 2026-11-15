"""One stable VoiceStudio voice per voice design.

Voice *design* (instruct tags + seed) re-invents the voice on every request, so the
voice can change from one sentence to the next. Instead, for each design we:
  1. generate a ~7 s reference clip with the design,
  2. check it's clean (not whispery / squeaky); if not, retry with another seed,
  3. save it as a *clone* profile "VNR - <label>",
and every line is then spoken with that clone, which keeps the voice identical.

A preset's *vibe* picks the reference script (a clone copies the delivery of its reference,
so a sultry script gives a sultry voice). Emotional *variants* (seductive, aroused, whisper,
happy, sad, angry) are clones made from the same voice reading a script in that mood; they
are made in the background the first time a line needs them.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Callable

from . import audio_qa, voices
from .config import Config
from .tts.base import TTSError
from .tts.voicestudio import VoiceStudioEngine, is_temporary

PREFIX = "VNR - "
REF_TEXT = ("Hello there. This is my natural speaking voice, calm and clear. "
            "I like quiet mornings, warm tea, and long walks by the river.")
# vibe -> (reference script, reference speed)
VIBE_SCRIPTS: dict[str, tuple[str, float]] = {
    "": (REF_TEXT, 1.0),
    "sultry": ("Mmm, hello there. Come a little closer… I don't bite. Well, not unless you "
               "ask me nicely.", 0.9),
    "flirty": ("Oh, hey you. Were you staring at me just now? Hehe… it's okay. I kind of "
               "liked it.", 1.0),
    "sweet": ("Welcome home! I missed you so much today. Come here, let me give you a big "
              "hug.", 1.0),
    "breathy": ("Shh… close your eyes. Just listen to my voice… and relax for me. "
                "Everything is going to feel so good.", 0.9),
    "dominant": ("Look at me. Good. Now, you're going to do exactly what I say… aren't "
                 "you? I thought so.", 0.9),
    "charming": ("Hey… you look beautiful tonight. Stay a while. I want to hear everything "
                 "about you.", 0.9),
    "shy": ("Um… hi. Sorry, I didn't mean to stare. I just… I wanted to say thank you, "
            "for earlier. That's all.", 0.95),
    "energetic": ("Okay, let's go! Today is going to be awesome. Come on, keep up, we've "
                  "got so much to do!", 1.0),
    "cool": ("Good evening. Everything is ready, as you asked. Let me know if you need "
             "anything else.", 0.95),
    "narrator": ("The rain had stopped by the time she reached the old station. The town "
                 "was quiet, and the lights were still on.", 0.95),
    "rough": ("Yo, what's up? Man, you look like you just woke up. Come on, the game's "
              "about to start!", 1.0),
}
VIBES = [v for v in VIBE_SCRIPTS if v]

# mood variant -> (script, reference speed)
MOOD_SCRIPTS: dict[str, tuple[str, float]] = {
    "seductive": ("Mmm… there you are. Come a little closer… I've been waiting for you all "
                  "night, you know.", 0.9),
    "aroused": ("Haah… mmh… don't stop… please, I… I can't think straight anymore… ahh…",
                0.85),
    "whisper": ("Shh… keep your voice down. Just stay right here with me… and listen.", 0.9),
    "happy": ("Oh wow, this is amazing! I'm so happy you're here, you just made my whole "
              "day!", 1.05),
    "sad": ("I'm sorry… I really thought things would be different this time. I just… "
            "miss you.", 0.9),
    "angry": ("Are you serious right now?! I told you a hundred times. Don't you dare walk "
              "away from me!", 1.05),
}
_BREATHY = ("whisper",)                    # moods whose reference may sound whispery
# Key suffix per mood; bump one to remake that mood's clones (aroused2: no longer whispery).
MOOD_KEYS = {m: m for m in MOOD_SCRIPTS} | {"aroused": "aroused2"}
VARIANT_TRIES = 3

MAX_TRIES = 5
GOOD_TAKES = 3
REF_STEPS = 16       # tested: 16 gives cleaner references than 32


class VoiceBank:
    def __init__(self, cfg: Config, vs: VoiceStudioEngine,
                 on_status: Callable[[str], None] = lambda s: None,
                 on_ready: Callable[[str], None] = lambda key: None):
        self.cfg = cfg
        self.vs = vs
        self.on_status = on_status
        self.on_ready = on_ready
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._failed: set[str] = set()
        self._building: set[str] = set()

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def cached(self, design: dict) -> str:
        e = self.cfg.settings.vs_voice_bank.get(voices.voice_key(design))
        return e.get("id", "") if e else ""

    def enabled(self) -> bool:
        return self.cfg.settings.save_vs_profiles

    def get_profile(self, design: dict) -> str:
        """Clone profile id for this design, creating it if needed (blocks ~3-10 s once).
        Returns '' if VoiceStudio is unavailable or creation failed."""
        if not self.enabled():
            return ""
        key = voices.voice_key(design)
        pid = self.cached(design)
        if pid or key in self._failed:
            return pid
        with self._lock(key):
            pid = self.cached(design)
            if pid:
                return pid
            if not self.vs.available():
                return ""
            try:
                pid = self._create(design)
            except TTSError as e:
                if not is_temporary(e):          # busy / offline: try again next time
                    self._failed.add(key)
                self.on_status(f"Couldn't make a stable voice for {design.get('label')}: {e}")
                return ""
            with self.cfg._lock:
                self.cfg.settings.vs_voice_bank[key] = {"id": pid, "label": design.get("label")}
                self.cfg.save()
        self.on_ready(key)
        return pid

    def _background(self):
        """VoiceStudio requests in here wait while a line is being voiced."""
        bg = getattr(self.vs, "background", None)
        return bg() if bg else contextlib.nullcontext()

    def prewarm(self, design: dict) -> None:
        if self.enabled() and not self.cached(design):
            def work(d=dict(design)):
                with self._background():
                    self.get_profile(d)
            threading.Thread(target=work, daemon=True).start()

    def forget(self, design: dict) -> None:
        """Drop (and delete) the clone for a design and its mood variants, e.g. after a
        re-roll."""
        key = voices.voice_key(design)
        bank = self.cfg.settings.vs_voice_bank
        ids = []
        for k in [k for k in bank if k == key or k.startswith(key + "#")]:
            e = bank.pop(k)
            if e.get("id"):
                ids.append(e["id"])
        self._failed = {k for k in self._failed if k != key and not k.startswith(key + "#")}
        self.cfg.save()

        def work():
            for pid in ids:
                self.vs.delete_profile(pid)
        if ids:
            threading.Thread(target=work, daemon=True).start()

    # ---- emotional variants ----
    def variant(self, design: dict, mood: str) -> str:
        """Clone id of this voice in that mood; '' if it isn't made yet (it's then made in
        the background, so the line itself never waits for it)."""
        if not self.enabled() or mood not in MOOD_SCRIPTS:
            return ""
        key = self._variant_key(design, mood)
        e = self.cfg.settings.vs_voice_bank.get(key)
        if e and e.get("id"):
            return e["id"]
        with self._guard:
            if key in self._failed or key in self._building:
                return ""
            self._building.add(key)
        threading.Thread(target=self._make_variant, args=(dict(design), mood, key),
                         daemon=True).start()
        return ""

    @staticmethod
    def _variant_key(design: dict, mood: str) -> str:
        return f"{voices.voice_key(design)}#{MOOD_KEYS[mood]}"

    def _make_variant(self, design: dict, mood: str, key: str) -> None:
        try:
            with self._background():
                ok = self._build_variant(design, mood, key)
            if ok:
                self.on_status(f"{design.get('label')}'s {mood} voice is ready")
        finally:
            with self._guard:
                self._building.discard(key)

    def _build_variant(self, design: dict, mood: str, key: str) -> str:
        """Make and save one mood variant (blocks a few seconds). '' if it failed."""
        with self._lock(key):
            e = self.cfg.settings.vs_voice_bank.get(key)
            if e and e.get("id"):
                return e["id"]
            base = self.get_profile(design)
            if not base or not self.vs.available():
                return ""
            try:
                pid = self._create_variant(design, mood, base)
            except TTSError as e:
                if not is_temporary(e):          # busy / offline: try again next time
                    self._failed.add(key)
                self.on_status(f"Couldn't make {design.get('label')}'s {mood} voice: {e}")
                return ""
            with self.cfg._lock:
                self.cfg.settings.vs_voice_bank[key] = {
                    "id": pid, "label": f"{design.get('label')} ({mood})"}
                self.cfg.save()
        self.on_ready(key)
        return pid

    def ensure_variant(self, design: dict, mood: str) -> str:
        """The mood variant's id, made now if needed (blocks). '' if it can't be made."""
        if not self.enabled() or mood not in MOOD_SCRIPTS:
            return ""
        return self._build_variant(dict(design), mood, self._variant_key(design, mood))

    def _create_variant(self, design: dict, mood: str, base: str) -> str:
        """The same voice in a mood: its own clone reads the mood script (so the timbre is
        kept) and that take becomes the variant's reference."""
        script, speed = MOOD_SCRIPTS[mood]
        gender, age = design.get("gender", "unknown"), design.get("age", "")
        slack = 60 if mood in ("happy", "angry") else 0
        seed = int(design.get("seed") or 42)
        best = None
        for attempt in range(VARIANT_TRIES):
            if mood == "whisper":
                # Clones ignore "whisper" (tested on OmniVoice), so the same design (same
                # seed) is asked for a whisper instead: a real, unvoiced whisper.
                wav = self.vs.synth(script, "default", speed, language="en", steps=REF_STEPS,
                                    seed=seed + attempt * 7919,
                                    instruct=voices.instruct({**design, "style": "whisper"}))
            else:
                wav = self.vs.synth(script, base, speed, language="en", steps=REF_STEPS,
                                    seed=seed + attempt * 7919)
            ok, why = audio_qa.reference_ok(wav, gender, age, slack)
            if not ok and mood in _BREATHY and why.startswith("whispery"):
                ok = True
            if ok:
                best = wav
                break
            self.on_status(f"{design.get('label')} ({mood}): trying again ({why})")
        if best is None:
            raise TTSError("every take came out whispery or squeaky")
        return self.vs.create_clone_profile(
            f"{PREFIX}{design.get('label')} ~{mood} [{voices.voice_key(design)}]", best, script)

    def _create(self, design: dict) -> str:
        ins = voices.instruct(design)
        seed = int(design.get("seed") or 42)
        whisper = design.get("style") == "whisper"
        ref_text, ref_speed = VIBE_SCRIPTS.get(design.get("vibe") or "", VIBE_SCRIPTS[""])
        best, best_voiced, good = None, -1.0, 0
        for attempt in range(MAX_TRIES):
            s = seed + attempt * 7919        # deterministic retries
            wav = self.vs.synth(ref_text, "default", ref_speed, instruct=ins, seed=s,
                                language="en", steps=REF_STEPS)
            ok, why = audio_qa.reference_ok(wav, design.get("gender", "unknown"),
                                            design.get("age", ""))
            # A whisper preset is *meant* to be unvoiced: only check pitch sanity there.
            if not ok and whisper and why.startswith("whispery"):
                ok = True
            if not ok:
                self.on_status(f"Voice '{design.get('label')}': trying again ({why})")
                continue
            voiced = audio_qa.analyse(wav)["voiced"]   # clearest take wins (also for
            if voiced > best_voiced:                    # whisper: keeps it audible)
                best, best_voiced = wav, voiced
            good += 1
            # Keep the clearest of up to GOOD_TAKES clean takes: a clone copies any
            # breathiness in its reference into every line.
            if good >= GOOD_TAKES or (not whisper and voiced >= 0.85):
                break
        if best is None:
            raise TTSError("every reference clip came out whispery or squeaky")
        return self.vs.create_clone_profile(f"{PREFIX}{design.get('label')} "
                                            f"[{ins} #{seed}]", best, ref_text)

    def prune(self, used_keys: set[str]) -> list[str]:
        """Delete clones no character or preset uses any more (after deletes / merges).
        A mood variant ("<key>#<mood>") lives as long as its voice."""
        bank = self.cfg.settings.vs_voice_bank
        current = set(MOOD_KEYS.values())
        orphans = [k for k in list(bank) if k.split("#")[0] not in used_keys
                   or ("#" in k and k.split("#", 1)[1] not in current)]   # an old mood version
        ids = []
        for k in orphans:
            e = bank.pop(k)
            if e.get("id"):
                ids.append(e["id"])
        if orphans:
            self.cfg.save()

        def work():
            for pid in ids:
                self.vs.delete_profile(pid)
        if ids:
            threading.Thread(target=work, daemon=True).start()
        return ids

    def cleanup_legacy(self) -> int:
        """Delete v3 'design' profiles made by this app (they drift between sentences)."""
        n = 0
        for p in self.vs.profiles():
            if str(p.get("name", "")).startswith(PREFIX) and p.get("kind") == "design":
                if self.vs.delete_profile(p["id"]):
                    n += 1
        return n
