"""Background worker: receives lines, synthesizes them sentence by sentence, plays them.

- Every sentence of a line uses the same *cloned* VoiceStudio voice (VoiceBank), so
  the voice never changes after a full stop.
- The first sentence starts playing while later ones are still being synthesized.
- A newer line always wins: it cuts off playback and stops waiting on older work.
- If VoiceStudio fails, a Windows voice of the same gender is used.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from . import emotion, voices
from .text_clean import split_sentences
from .tts.base import TTSEngine, TTSError
from .tts.sapi_fallback import PREFIX as SAPI_PREFIX
from .tts.sapi_fallback import pitch_for


@dataclass
class Line:
    game: str
    speaker: str
    text: str
    char: dict = field(default_factory=dict)     # character record (voice_override etc.)
    language: str = ""                           # ISO 639-1, e.g. "en"
    design: dict = field(default_factory=dict)   # effective voice design (app/voices.py)


Attempt = tuple[TTSEngine, str, dict]            # engine, voice id, synth options
_ABANDONED = object()


class SpeechQueue:
    def __init__(self, primary: TTSEngine, fallback: TTSEngine | None, player,
                 speed_getter: Callable[[], float] = lambda: 1.0,
                 on_status: Callable[[str], None] = lambda s: None,
                 bank=None, cache_size: int = 256):
        self.primary = primary
        self.fallback = fallback
        self.player = player
        self.bank = bank
        self.expression_tags: set[str] = set()       # filled from VoiceStudio at startup
        self.expressive: Callable[[], bool] = lambda: True
        self.on_done: Callable[[Line], None] = lambda line: None   # line fully played
        self.speed_getter = speed_getter
        self.on_status = on_status
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_size = cache_size
        self._cache_lock = threading.Lock()
        self._cond = threading.Condition()
        self._pending: Line | None = None
        self._gen = 0
        self.last: Line | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="speech", daemon=True)
        self._thread.start()

    def say(self, line: Line) -> None:
        with self._cond:
            self._gen += 1
            self._pending = line
            self._cond.notify_all()
        self.player.stop()

    def replay(self) -> None:
        if self.last:
            self.say(self.last)

    def stop(self) -> None:
        with self._cond:
            self._gen += 1
            self._pending = None
            self._cond.notify_all()
        self.player.stop()

    def shutdown(self) -> None:
        with self._cond:
            self._stop = True
            self._gen += 1
            self._cond.notify_all()
        self.player.stop()

    # ---- routing ----
    def attempts(self, line: Line, profile_id: str = "") -> list[Attempt]:
        rec = line.char or {}
        d = line.design or rec
        override = rec.get("voice_override") or ""
        sapi_try: Attempt | None = None
        if self.fallback is not None:
            if override.startswith(SAPI_PREFIX):
                sapi_try = (self.fallback, override, {})
            else:
                voice = getattr(self.fallback, "voice_for_gender", lambda g, s: "")(
                    d.get("gender", ""), d.get("seed", 0))
                sapi_try = (self.fallback, voice, {"pitch": pitch_for(d)})
        if override.startswith(SAPI_PREFIX):
            return [sapi_try] if sapi_try else []

        lang = {"language": line.language} if line.language else {}
        design = {"instruct": voices.instruct(d), "seed": d.get("seed"), **lang}
        out: list[Attempt] = []
        if override.startswith("vs:"):
            out.append((self.primary, override[3:], lang))
        else:
            if profile_id:
                out.append((self.primary, profile_id, lang))
            out.append((self.primary, "default", design))   # only if the clone failed
        if sapi_try:
            out.append(sapi_try)
        return out

    # ---- worker ----
    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                line, gen, self._pending = self._pending, self._gen, None
            self.last = line
            speed = self.speed_getter()
            override = (line.char or {}).get("voice_override") or ""

            profile_id = ""
            if self.bank is not None and line.design and not override \
                    and self.primary.available():
                profile_id = self.bank.cached(line.design)
                if not profile_id and self.bank.enabled():
                    self.on_status(f"Preparing {line.design.get('label')}'s voice (first time "
                                   "only)…")
                    r = self._abandonable(lambda: self.bank.get_profile(line.design), gen)
                    if r is _ABANDONED:
                        continue
                    profile_id = r or ""
                    if not profile_id and self.bank.enabled() and r is not None:
                        self.on_status("Couldn't prepare a stable voice: using the designed "
                                       "voice (it may vary between sentences)")
            plan = self.attempts(line, profile_id)

            finished = True
            for sentence in split_sentences(line.text):
                if gen != self._gen:
                    finished = False
                    break
                audio = self._abandonable(lambda s=sentence: self._synth_any(plan, s, speed),
                                          gen)
                if audio is _ABANDONED or audio is None:
                    finished = audio is None          # a failed line still counts as done
                    break
                with self._cond:
                    if gen != self._gen:     # a newer line arrived meanwhile
                        finished = False
                        break
                    try:
                        self.player.enqueue_bytes(audio)
                    except Exception as e:   # bad audio / no output device
                        self.on_status(f"Playback error: {e}")
                        break
            if finished:
                self._wait_played(line, gen)

    def _wait_played(self, line: Line, gen: int) -> None:
        """Tell on_done once the line has fully played (used for auto-advance)."""
        playing = getattr(self.player, "is_playing", lambda: False)
        with self._cond:
            while gen == self._gen and not self._stop and \
                    (playing() or getattr(self.player, "paused", False)):
                self._cond.wait(0.1)
            if gen != self._gen or self._stop:
                return
        try:
            self.on_done(line)
        except Exception:
            pass

    def _abandonable(self, fn: Callable, gen: int):
        """Run fn in a helper thread; stop waiting as soon as a newer line arrives
        (the work itself finishes in the background, e.g. a voice still gets saved)."""
        box: dict = {}
        done = threading.Event()

        def job():
            try:
                box["r"] = fn()
            except Exception as e:      # never kill the worker
                self.on_status(f"Speech error: {e}")
            finally:
                done.set()
                with self._cond:
                    self._cond.notify_all()

        threading.Thread(target=job, daemon=True).start()
        with self._cond:
            while not done.is_set() and gen == self._gen and not self._stop:
                self._cond.wait(0.2)
        if not done.is_set() or gen != self._gen:
            return _ABANDONED
        return box.get("r")

    def _synth_any(self, plan: list[Attempt], text: str, speed: float) -> bytes | None:
        last_err = None
        for i, (engine, voice, opts) in enumerate(plan):
            if engine is self.primary and not engine.available():
                last_err = "VoiceStudio is offline"
                continue
            say = text
            if engine is self.primary and self.expression_tags and self.expressive():
                say = emotion.add_tags(text, self.expression_tags)
            try:
                audio = self._synth(engine, voice, opts, say, speed)
                if i > 0 and engine is self.fallback and engine is not plan[0][0]:
                    self.on_status(f"{last_err or 'VoiceStudio failed'}: using a Windows voice")
                return audio
            except TTSError as e:
                last_err = str(e)
        self.on_status(f"TTS error: {last_err}")
        return None

    def _synth(self, engine: TTSEngine, voice: str, opts: dict, text: str,
               speed: float) -> bytes:
        key = (engine.name, voice, tuple(sorted(opts.items())), round(speed, 2), text)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        audio = engine.synth(text, voice, speed, **opts)
        with self._cache_lock:
            self._cache[key] = audio
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return audio
