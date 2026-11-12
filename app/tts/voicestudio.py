"""Client for a locally running VoiceStudio (https://github.com/debpalash/voicestudio).

Speech:   POST /v1/audio/speech  {"input", "voice", "instruct", "seed", "language", ...}
Profiles: GET/POST /profiles, DELETE /profiles/{id}
A character's voice is either a saved profile, or a voice *design* (instruct tags + seed).
"""
from __future__ import annotations

import sys
import time

import requests

from .base import TTSEngine, TTSError


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
        self.num_step: int | None = 16      # quality/speed: 8 fast, 16 balanced, None = best

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
        for attempt in range(8):
            try:
                r = self._session.post(self._url("/v1/audio/speech"), json=body,
                                       headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as e:
                self.mark_offline()
                raise TTSError(f"VoiceStudio unreachable: {e}") from e
            if r.status_code != 429:         # 429 = GPU busy with a previous line
                break
            time.sleep(min(2.0, 0.3 * (attempt + 1)))
        if r.status_code != 200:
            raise TTSError(f"VoiceStudio HTTP {r.status_code}: {r.text[:300]}")
        if "json" in r.headers.get("content-type", "") or not r.content:
            raise TTSError(f"VoiceStudio returned no audio: {r.text[:300]}")
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
        try:
            r = self._session.post(self._url("/profiles"), data=data, headers=self._headers(),
                                   timeout=60)
        except requests.RequestException as e:
            raise TTSError(f"VoiceStudio unreachable: {e}") from e
        if r.status_code != 200:
            raise TTSError(f"Couldn't create voice profile: HTTP {r.status_code} {r.text[:200]}")
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
        try:
            r = self._session.post(self._url("/profiles"), data=data, files=files,
                                   headers=self._headers(), timeout=120)
        except requests.RequestException as e:
            raise TTSError(f"VoiceStudio unreachable: {e}") from e
        if r.status_code != 200:
            raise TTSError(f"Couldn't save voice: HTTP {r.status_code} {r.text[:200]}")
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
