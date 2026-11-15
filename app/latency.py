"""How long each line takes from 'text changed on screen' to 'voice heard'.

The OCR thread calls changed() whenever the reading area changes (the last change
before a line is read is when its text appeared), stages along the way call
mark(), speak() arms the trace, and the player calls audio_started() when sound
starts. One line per spoken line goes to latency.log next to settings.json, e.g.

    1.02s  stable 0.09 | ocr1 0.27 | prefetch 0.27 | confirmed 0.41 | speak 0.42 |
           synth 0.86 (hit) | audio 1.02  "Hello there."

Everything is cheap and thread-safe; audio_started() runs in the audio callback, so
the file is written by a separate thread.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

_lock = threading.Lock()
_cur: dict | None = None          # {"t0", "marks": [(stage, secs, note)], "armed", "text"}
_out: "queue.SimpleQueue[str]" = queue.SimpleQueue()
_writer: threading.Thread | None = None
on_result: Callable[[float, str], None] = lambda total, text: None   # UI hook
last_total: float | None = None


def log_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "VNReader" / "latency.log"


def changed() -> None:
    """The reading area changed: a new line may be appearing. Starts a new trace."""
    global _cur
    with _lock:
        _cur = {"t0": time.monotonic(), "marks": [], "armed": False, "text": ""}


def mark(stage: str, note: str = "") -> None:
    with _lock:
        if _cur is not None and not _cur.get("done"):
            _cur["marks"].append((stage, time.monotonic() - _cur["t0"], note))


def speak(text: str) -> None:
    """The line is handed to the speech queue: the next audio start ends the trace."""
    with _lock:
        if _cur is None or _cur.get("done"):
            return
        _cur["marks"].append(("speak", time.monotonic() - _cur["t0"], ""))
        _cur["armed"] = True
        _cur["text"] = text


def audio_started() -> None:
    """Sound started playing (called from the audio callback: no I/O here)."""
    global last_total
    with _lock:
        if _cur is None or not _cur["armed"] or _cur.get("done"):
            return
        total = time.monotonic() - _cur["t0"]
        _cur["done"] = True
        _cur["marks"].append(("audio", total, ""))
        marks, text = list(_cur["marks"]), _cur["text"]
        last_total = total
    _out.put(format_line(total, marks, text))
    _ensure_writer()
    try:
        on_result(total, text)
    except Exception:
        pass


def format_line(total: float, marks: list, text: str) -> str:
    parts = " | ".join(f"{s} {t:.2f}" + (f" ({n})" if n else "") for s, t, n in marks)
    short = text if len(text) <= 60 else text[:57] + "..."
    return f"{time.strftime('%H:%M:%S')}  {total:.2f}s  {parts}  {short!r}"


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    _writer = threading.Thread(target=_write_loop, name="latency-log", daemon=True)
    _writer.start()


def _write_loop() -> None:
    path = log_path()
    while True:
        line = _out.get()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 512 * 1024:   # keep it small
                path.replace(path.with_suffix(".old.log"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
