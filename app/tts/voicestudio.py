"""Client for a locally running VoiceStudio (https://github.com/debpalash/voicestudio).

Speech:   POST /v1/audio/speech  {"input", "voice", "instruct", "seed", "language", ...}
Profiles: GET/POST /profiles, DELETE /profiles/{id}
A character's voice is either a saved profile, or a voice *design* (instruct tags + seed).
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time

import requests

from .base import TTSEngine, TTSError

# VoiceStudio makes one thing at a time and answers 429 ("busy") to anything sent
# meanwhile. So requests are sent one at a time; the line being read goes first and
# background work (new voices, mood voices) waits its turn.
BUSY_WAIT_FG = 30.0        # how long a line keeps retrying a 429
BUSY_WAIT_BG = 120.0       # how long background work keeps retrying a 429
TEMPORARY = ("HTTP 429", "HTTP 503", "unreachable")   # errors worth retrying later


def is_temporary(err: Exception | str) -> bool:
    """VoiceStudio was busy or not reachable: try again later, not a real failure."""
    return any(t in str(err) for t in TEMPORARY)


class VoiceStudioEngine(TTSEngine):
    name = "voicestudio"

    def __init__(self, base_url: str = "http://127.0.0.1:3900", api_key: str = "",
                 timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._ok_until = 0.0
        self._ok = False
        self.num_step: int | None = None    # diffusion steps: 6 instant, 12 balanced, None = best
        self._gate = threading.Condition()
        self._busy = False
        self._fg_waiting = 0
        self._bg_waiting = 0
        self._local = threading.local()
        self.info: dict = {}                 # /health: {"device", "version", "engine", ...}
        self.last_error = ""
        self.last_error_at = 0.0

    # ---- status (for the status light) ----
    @property
    def busy(self) -> bool:
        """A request is being made right now."""
        return self._busy

    @property
    def queued(self) -> int:
        """Requests waiting for their turn."""
        return self._fg_waiting + self._bg_waiting

    def _error(self, text: str) -> TTSError:
        self.last_error, self.last_error_at = text, time.time()
        return TTSError(text)

    # ---- one request at a time ----
    @contextlib.contextmanager
    def background(self):
        """Requests made inside this block (on this thread) wait for line requests."""
        old = getattr(self._local, "bg", False)
        self._local.bg = True
        try:
            yield
        finally:
            self._local.bg = old

    def _is_bg(self) -> bool:
        return getattr(self._local, "bg", False)

    @contextlib.contextmanager
    def _turn(self):
        bg = self._is_bg()
        with self._gate:
            if bg:
                self._bg_waiting += 1
            else:
                self._fg_waiting += 1
            try:
                while self._busy or (bg and self._fg_waiting > 0):
                    self._gate.wait()
            finally:
                if bg:
                    self._bg_waiting -= 1
                else:
                    self._fg_waiting -= 1
            self._busy = True
        try:
            yield
        finally:
            with self._gate:
                self._busy = False
                self._gate.notify_all()

    def _post(self, path: str, timeout: float, **kw) -> requests.Response:
        """POST in our turn; a 429 (VoiceStudio busy, e.g. with its own UI) is retried."""
        limit = BUSY_WAIT_BG if self._is_bg() else BUSY_WAIT_FG
        start = time.monotonic()
        attempt = 0
        while True:
            try:
                with self._turn():
                    r = self._session.post(self._url(path), headers=self._headers(),
                                           timeout=timeout, **kw)
            except requests.RequestException as e:
                self.mark_offline()
                raise self._error(f"VoiceStudio unreachable: {e}") from e
            if r.status_code != 429 or time.monotonic() - start >= limit:
                return r
            attempt += 1
            time.sleep(min(1.5, 0.3 * attempt))

    def _headers(self) -> dict[str, str]:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _url(self, path: str) -> str:
        return self.base_url + path

    def available(self) -> bool:
        now = time.monotonic()
        if now < self._ok_until:
            return self._ok
        try:
            r = self._session.get(self._url("/health"), headers=self._headers(), timeout=2)
            self._ok = r.status_code < 500
            try:
                j = r.json()
                if isinstance(j, dict):
                    self.info = j
            except ValueError:
                pass
        except requests.RequestException:
            self._ok = False
        self._ok_until = now + (10 if self._ok else 3)
        return self._ok

    def mark_offline(self) -> None:
        self._ok, self._ok_until = False, time.monotonic() + 3

    # ---- speech ----
    def synth(self, text: str, voice: str, speed: float = 1.0, *, instruct: str = "",
              seed: int | None = None, language: str = "", steps: int | None = None,
              **_opts) -> bytes:
        body: dict = {"model": "tts-1", "input": text, "voice": voice or "default",
                      "response_format": "wav"}
        if abs(speed - 1.0) > 1e-3:
            body["speed"] = round(speed, 2)
        if instruct:
            body["instruct"] = instruct
        if seed is not None:
            body["seed"] = int(seed)
        if language:
            body["language"] = language
        if steps or self.num_step:
            body["num_step"] = int(steps or self.num_step)
        r = self._post("/v1/audio/speech", self.timeout, json=body)
        if r.status_code == 429:
            raise self._error("VoiceStudio HTTP 429: still busy (is something else using it?)")
        if r.status_code != 200:
            raise self._error(f"VoiceStudio HTTP {r.status_code}: {r.text[:300]}")
        if "json" in r.headers.get("content-type", "") or not r.content:
            raise self._error(f"VoiceStudio returned no audio: {r.text[:300]}")
        return r.content

    # ---- profiles ----
    def profiles(self) -> list[dict]:
        """[{id, name, kind}] of the user's saved voices."""
        try:
            r = self._session.get(self._url("/profiles"), headers=self._headers(), timeout=5)
            r.raise_for_status()
            return [{"id": p.get("id"), "name": p.get("name") or p.get("id"),
                     "kind": p.get("kind", "")} for p in r.json() if p.get("id")]
        except (requests.RequestException, ValueError):
            return []

    def voices(self) -> list[str]:
        return ["default"] + [p["id"] for p in self.profiles()]

    def create_design_profile(self, name: str, instruct: str, vd_states: str, seed: int,
                              language: str = "English") -> str:
        """Save a designed voice as a profile so it never drifts. Returns the profile id."""
        data = {"name": name, "kind": "design", "vd_states": vd_states, "instruct": instruct,
                "seed": str(int(seed)), "language": language or "Auto", "ref_text": ""}
        r = self._post("/profiles", 60, data=data)
        if r.status_code != 200:
            raise self._error(f"Couldn't create voice profile: HTTP {r.status_code} {r.text[:200]}")
        pid = (r.json() or {}).get("id")
        if not pid:
            raise TTSError("VoiceStudio didn't return a profile id")
        return pid

    def create_clone_profile(self, name: str, wav: bytes, ref_text: str,
                             language: str = "English") -> str:
        """Save a voice cloned from a reference clip. Every line spoken with it keeps
        the same voice (unlike designed voices, which vary from sentence to sentence)."""
        data = {"name": name, "kind": "clone", "ref_text": ref_text,
                "language": language or "Auto"}
        files = {"ref_audio": ("reference.wav", wav, "audio/wav")}
        r = self._post("/profiles", 120, data=data, files=files)
        if r.status_code != 200:
            raise self._error(f"Couldn't save voice: HTTP {r.status_code} {r.text[:200]}")
        pid = (r.json() or {}).get("id")
        if not pid:
            raise TTSError("VoiceStudio didn't return a profile id")
        return pid

    def delete_profile(self, profile_id: str) -> bool:
        try:
            r = self._session.delete(self._url(f"/profiles/{profile_id}"),
                                     headers=self._headers(), timeout=15)
            return r.status_code in (200, 204, 404)
        except requests.RequestException:
            return False

    def expression_tags(self) -> set[str]:
        """Tags like [laughter] the active engine can perform (empty if none)."""
        try:
            j = self._session.get(self._url("/engines/tts"), headers=self._headers(),
                                  timeout=5).json()
            act = next((b for b in j.get("backends", []) if b.get("id") == j.get("active")), {})
            return set((act.get("expression_caps") or {}).get("tags") or [])
        except (requests.RequestException, ValueError, AttributeError):
            return set()

    def describe(self, text: str) -> dict:
        """Free text ('old man, deep voice') -> {"attrs": {...}, "instruct": "..."}."""
        r = self._session.post(self._url("/design/describe"), json={"description": text},
                               headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    # Quick test: python -m app.tts.voicestudio "Hello there" ["female, young adult, high pitch"]
    from app.audio.player import Player

    text = sys.argv[1] if len(sys.argv) > 1 else "Hello! This is VN Reader."
    ins = sys.argv[2] if len(sys.argv) > 2 else ""
    eng = VoiceStudioEngine()
    print("available:", eng.available())
    print("profiles:", eng.profiles())
    t = time.time()
    wav = eng.synth(text, "default", instruct=ins, seed=42)
    print(f"synth {len(wav)} bytes in {time.time() - t:.1f}s")
    p = Player()
    p.play_bytes(wav)
    p.wait()
