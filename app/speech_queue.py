"""Background worker: receives lines, synthesizes them sentence by sentence, plays them.

- Every sentence of a line uses the same *cloned* VoiceStudio voice (VoiceBank), so
  the voice never changes after a full stop.
- The first sentence starts playing while later ones are still being synthesized.
- A newer line always wins: it cuts off playback and stops waiting on older work.
- If VoiceStudio fails, a Windows voice of the same gender is used.
- *Stage directions* are never spoken; the mood they describe picks an emotional variant
  of the voice, a speed and expression tags (app/emotion.py).
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
        self.emotion_voices: Callable[[], bool] = lambda: True   # emotional clone variants
        self.vocalize: Callable[[], bool] = lambda: True         # *moans* -> "Mmmh…"
        self.strength: Callable[[], str] = lambda: "normal"      # subtle / normal / strong
        self.on_done: Callable[[Line], None] = lambda line: None   # line fully played
        # Steps for a line's FIRST sentence (e.g. 8 = fast) so the voice starts sooner;
        # None = same quality as the rest.
        self.quick_steps: Callable[[], int | None] = lambda: None
        self._inflight: dict[tuple, threading.Event] = {}
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
            d = self.direct(line)
            if not d.spoken:                 # only stage directions: nothing to say
                self._wait_played(line, gen)
                continue
            speed = self.speed_getter() * d.speed
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
            plan = self.attempts(line, self._variant(line, d, profile_id))

            finished = True
            quick = self.quick_steps()
            for i, sentence in enumerate(split_sentences(d.spoken)):
                if gen != self._gen:
                    finished = False
                    break
                steps = quick if i == 0 else None
                audio = self._abandonable(
                    lambda s=sentence, st=steps: self._synth_any(plan, s, speed, st), gen)
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

    # ---- emotion ----
    def direct(self, line: Line) -> emotion.Directed:
        """The line with stage directions removed, and the mood to perform it in."""
        act = self.expressive()
        return emotion.parse(line.text, act=act, vocalize=self.vocalize(),
                             tags=self.expression_tags if act else None,
                             strength=self.strength())

    def _variant(self, line: Line, d: emotion.Directed, profile_id: str) -> str:
        """The emotional clone for this mood if it's ready, else the normal clone (the
        variant is then made in the background for next time)."""
        if not (profile_id and d.variant and line.design and self.bank is not None
                and self.emotion_voices() and hasattr(self.bank, "variant")):
            return profile_id
        return self.bank.variant(line.design, d.variant) or profile_id

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

    # ---- prefetch ----
    def prefetch(self, line: Line) -> None:
        """Start making the first sentence of a line that is probably coming (the OCR is
        still confirming it). Nothing is played; say() later finds the audio ready, or
        waits for this request instead of sending a second one."""
        if self.primary is None or not self.primary.available():
            return
        override = (line.char or {}).get("voice_override") or ""
        if override.startswith(SAPI_PREFIX):
            return                                   # Windows voices are instant anyway
        d = self.direct(line)
        profile_id = ""
        if self.bank is not None and line.design and not override:
            profile_id = self.bank.cached(line.design)
            if not profile_id and self.bank.enabled():
                return                               # its voice isn't made yet
        profile_id = self._variant(line, d, profile_id)
        attempt = next((a for a in self.attempts(line, profile_id) if a[0] is self.primary),
                       None)
        sentences = split_sentences(d.spoken) if d.spoken else []
        if attempt is None or not sentences:
            return
        engine, voice, opts = attempt
        say, o = self._prepared(engine, opts, sentences[0], self.quick_steps())
        speed = self.speed_getter() * d.speed

        def job():
            try:
                self._synth(engine, voice, o, say, speed)
            except Exception:
                pass                                 # say() will retry and report
        threading.Thread(target=job, name="prefetch", daemon=True).start()

    def _prepared(self, engine: TTSEngine, opts: dict, text: str,
                  steps: int | None) -> tuple[str, dict]:
        """Text and options exactly as sent to the engine (so prefetch and say() match)."""
        say = text
        if engine is self.primary:
            if self.expression_tags and self.expressive():
                say = emotion.add_tags(text, self.expression_tags)
            if steps:
                opts = {**opts, "steps": steps}
        else:
            say = emotion.strip_tags(text)           # a Windows voice would read "[sigh]"
        return say, opts

    def _synth_any(self, plan: list[Attempt], text: str, speed: float,
                   steps: int | None = None) -> bytes | None:
        last_err = None
        for i, (engine, voice, opts) in enumerate(plan):
            if engine is self.primary and not engine.available():
                last_err = "VoiceStudio is offline"
                continue
            say, opts = self._prepared(engine, opts, text, steps)
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
        while True:
            with self._cache_lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
                busy = self._inflight.get(key)
                if busy is None:                 # nobody is making it: we do
                    busy = self._inflight[key] = threading.Event()
                    break
            busy.wait(120)                       # same request already running: wait
            with self._cache_lock:
                if key not in self._cache and self._inflight.get(key) is busy:
                    self._inflight.pop(key, None)    # it hung: make it ourselves
        try:
            audio = engine.synth(text, voice, speed, **opts)
            with self._cache_lock:
                self._cache[key] = audio
                if len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            return audio
        finally:
            with self._cache_lock:
                self._inflight.pop(key, None)
            busy.set()
