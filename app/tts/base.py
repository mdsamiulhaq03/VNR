from __future__ import annotations

from abc import ABC, abstractmethod


class TTSError(Exception):
    pass


class TTSEngine(ABC):
    name = "base"

    @abstractmethod
    def synth(self, text: str, voice: str, speed: float = 1.0, **opts) -> bytes:
        """Return WAV (or any soundfile-readable) bytes. Engines ignore unknown opts."""

    def voices(self) -> list[str]:
        return ["default"]

    def available(self) -> bool:
        return True
