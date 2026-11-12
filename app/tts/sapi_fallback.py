"""Windows SAPI voices: used when VoiceStudio isn't running (or when picked by the user).

Characters still sound different offline: the voice is chosen by gender and the
character's pitch tag (plus a small per-character offset) shifts the pitch.
"""
from __future__ import annotations

import os
import tempfile
import threading
from xml.sax.saxutils import escape

from .base import TTSEngine, TTSError

PREFIX = "sapi:"
SVSF_IS_XML = 8

_PITCH_LEVEL = {"very low pitch": -6, "low pitch": -3, "moderate pitch": 0,
                "high pitch": 3, "very high pitch": 6}


def pitch_for(rec: dict) -> int:
    """SAPI pitch (-10..10) for a character record."""
    base = _PITCH_LEVEL.get(rec.get("pitch", ""), 0)
    jitter = (int(rec.get("seed") or 0) % 5) - 2          # -2..2 so same-tag voices differ
    return max(-10, min(10, base + jitter))


class SapiEngine(TTSEngine):
    name = "sapi"

    def __init__(self):
        self._lock = threading.Lock()
        self._voices: list[tuple[str, str]] | None = None   # (description, gender)

    def _com(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        return comtypes.client

    def available(self) -> bool:
        try:
            import comtypes  # noqa: F401
            return True
        except ImportError:
            return False

    def _load(self) -> list[tuple[str, str]]:
        if self._voices is None:
            out = []
            try:
                v = self._com().CreateObject("SAPI.SpVoice")
                toks = v.GetVoices()
                for i in range(toks.Count):
                    t = toks.Item(i)
                    try:
                        g = (t.GetAttribute("Gender") or "").lower()
                    except Exception:
                        g = ""
                    out.append((t.GetDescription(), g))
            except Exception:
                pass
            self._voices = out
        return self._voices

    def voices(self) -> list[str]:
        with self._lock:
            return [PREFIX + d for d, _g in self._load()]

    def voice_for_gender(self, gender: str, seed: int = 0) -> str:
        with self._lock:
            vs = self._load()
        match = [d for d, g in vs if g == gender] or [d for d, _g in vs]
        if not match:
            return ""
        return PREFIX + match[int(seed or 0) % len(match)]

    def synth(self, text: str, voice: str, speed: float = 1.0, *, pitch: int = 0,
              **_opts) -> bytes:
        with self._lock:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                client = self._com()
                v = client.CreateObject("SAPI.SpVoice")
                want = voice[len(PREFIX):] if voice.startswith(PREFIX) else ""
                if want:
                    toks = v.GetVoices()
                    for i in range(toks.Count):
                        if toks.Item(i).GetDescription() == want:
                            v.Voice = toks.Item(i)
                            break
                # SAPI rate is -10..10; map 0.5x..2x roughly onto it.
                v.Rate = max(-10, min(10, int(round((speed - 1.0) * 10))))
                stream = client.CreateObject("SAPI.SpFileStream")
                stream.Open(path, 3)  # SSFMCreateForWrite
                v.AudioOutputStream = stream
                if pitch:
                    v.Speak(f'<pitch absmiddle="{int(pitch)}">{escape(text)}</pitch>',
                            SVSF_IS_XML)
                else:
                    v.Speak(text)
                stream.Close()
                with open(path, "rb") as f:
                    return f.read()
            except Exception as e:
                raise TTSError(f"SAPI failed: {e}") from e
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
