"""One stable VoiceStudio voice per voice design.

Voice *design* (instruct tags + seed) re-invents the voice on every request, so the
voice can change from one sentence to the next. Instead, for each design we:
  1. generate a ~7 s reference clip with the design,
  2. check it's clean (not whispery / squeaky); if not, retry with another seed,
  3. save it as a *clone* profile "VNR - <label>",
and every line is then spoken with that clone, which keeps the voice identical.
"""
from __future__ import annotations

import threading
from typing import Callable

from . import audio_qa, voices
from .config import Config
from .tts.base import TTSError
from .tts.voicestudio import VoiceStudioEngine

PREFIX = "VNR - "
REF_TEXT = ("Hello there. This is my natural speaking voice, calm and clear. "
            "I like quiet mornings, warm tea, and long walks by the river.")
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
                self._failed.add(key)
                self.on_status(f"Couldn't make a stable voice for {design.get('label')}: {e}")
                return ""
            with self.cfg._lock:
                self.cfg.settings.vs_voice_bank[key] = {"id": pid, "label": design.get("label")}
                self.cfg.save()
        self.on_ready(key)
        return pid

    def prewarm(self, design: dict) -> None:
        if self.enabled() and not self.cached(design):
            threading.Thread(target=self.get_profile, args=(dict(design),), daemon=True).start()

    def forget(self, design: dict) -> None:
        """Drop (and delete) the clone for a design, e.g. after a re-roll."""
        key = voices.voice_key(design)
        e = self.cfg.settings.vs_voice_bank.pop(key, None)
        self._failed.discard(key)
        self.cfg.save()
        if e and e.get("id"):
            threading.Thread(target=self.vs.delete_profile, args=(e["id"],), daemon=True).start()

    def _create(self, design: dict) -> str:
        ins = voices.instruct(design)
        seed = int(design.get("seed") or 42)
        whisper = design.get("style") == "whisper"
        best, best_voiced, good = None, -1.0, 0
        for attempt in range(MAX_TRIES):
            s = seed + attempt * 7919        # deterministic retries
            wav = self.vs.synth(REF_TEXT, "default", instruct=ins, seed=s, language="en",
                                steps=REF_STEPS)
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
                                            f"[{ins} #{seed}]", best, REF_TEXT)

    def prune(self, used_keys: set[str]) -> list[str]:
        """Delete clones no character or preset uses any more (after deletes / merges)."""
        bank = self.cfg.settings.vs_voice_bank
        orphans = [k for k in list(bank) if k not in used_keys]
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
