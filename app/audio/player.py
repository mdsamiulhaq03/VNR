"""Gapless chunk player: enqueue audio pieces, they play back-to-back.

One output stream stays open; the callback pulls the next queued chunk. Supports
stop (drops everything queued), pause, and live volume.
"""
from __future__ import annotations

import io
import threading
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf


def _to_mono(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        data = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(data, dtype=np.float32)


def _resample(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(data) == 0:
        return data
    n = int(round(len(data) * dst / src))
    x = np.linspace(0, len(data) - 1, n, dtype=np.float64)
    return np.interp(x, np.arange(len(data)), data).astype(np.float32)


class Player:
    def __init__(self):
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._cur: np.ndarray | None = None
        self._pos = 0
        self._stream: sd.OutputStream | None = None
        self._sr: int | None = None
        self._idle = threading.Event()
        self._idle.set()
        self.volume = 1.0
        self.paused = False
        self.level = 0.0        # loudness of the audio being played right now (0..1)

    # ---- public API ----
    def enqueue_bytes(self, audio: bytes) -> None:
        data, sr = sf.read(io.BytesIO(audio), dtype="float32", always_2d=True)
        self.enqueue(data, sr)

    def enqueue(self, data: np.ndarray, sr: int) -> None:
        data = _to_mono(data)
        self._ensure_stream(sr)
        data = _resample(data, sr, self._sr)
        with self._lock:
            self._chunks.append(data)
            self._idle.clear()

    def play_bytes(self, audio: bytes) -> None:
        """Replace whatever is playing with this audio."""
        self.stop()
        self.enqueue_bytes(audio)

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._cur = None
            self._pos = 0
            self._idle.set()

    def is_playing(self) -> bool:
        return not self._idle.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def close(self) -> None:
        self.stop()
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass

    # ---- internals ----
    def _ensure_stream(self, sr: int) -> None:
        if self._stream is not None and self._stream.active:
            return
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        self._sr = sr
        self._stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32",
                                       callback=self._callback, latency="low")
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status):
        out = outdata[:, 0]
        if self.paused:
            out.fill(0)
            return
        filled = 0
        vol = self.volume
        with self._lock:
            while filled < frames:
                if self._cur is None:
                    if not self._chunks:
                        break
                    self._cur = self._chunks.popleft()
                    self._pos = 0
                take = min(frames - filled, len(self._cur) - self._pos)
                out[filled:filled + take] = self._cur[self._pos:self._pos + take] * vol
                filled += take
                self._pos += take
                if self._pos >= len(self._cur):
                    self._cur = None
            if filled < frames:
                out[filled:] = 0
            if self._cur is None and not self._chunks:
                self._idle.set()
        self.level = float(np.sqrt(np.mean(out * out))) if frames else 0.0
