"""Start VoiceStudio when it isn't running (only for a VoiceStudio on this PC)."""
from __future__ import annotations

import os
import subprocess
import time
from urllib.parse import urlparse

EXE_NAME = "VoiceStudio.exe"
RETRY_AFTER = 90.0        # never start it twice within this time
GIVE_UP_AFTER = 120.0     # how long a start may take before we say it failed


def default_exe() -> str:
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "Programs", "VoiceStudio", EXE_NAME) if base else ""


def find_exe(setting: str = "") -> str:
    """The VoiceStudio program: the one set in Settings, else where its installer puts
    it. '' if neither exists."""
    for p in (setting, default_exe()):
        if p and os.path.isfile(p):
            return p
    return ""


def is_local(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def is_running(name: str = EXE_NAME) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return name.lower() in out.lower()


def launch(exe: str) -> bool:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags,
                         close_fds=True)
        return True
    except OSError:
        return False


class AutoStarter:
    """Remembers when VoiceStudio was last started, so it is started at most once per
    RETRY_AFTER seconds, and says when a start took too long."""

    def __init__(self, now=time.monotonic):
        self._now = now
        self.started_at = 0.0

    @property
    def starting(self) -> bool:
        return bool(self.started_at) and self._now() - self.started_at < GIVE_UP_AFTER

    def timed_out(self) -> bool:
        return bool(self.started_at) and self._now() - self.started_at >= GIVE_UP_AFTER

    def ready(self):
        """VoiceStudio answered: nothing is starting any more."""
        self.started_at = 0.0

    def maybe_start(self, url: str, exe_setting: str = "", enabled: bool = True) -> str:
        """Start VoiceStudio if that makes sense. Returns what happened:
        'started', 'running' (already open, still loading), 'recent' (started a moment
        ago), 'not-local', 'not-found', 'off' or 'failed'."""
        if not enabled or os.environ.get("VNR_NO_AUTOSTART"):
            return "off"
        if not is_local(url):
            return "not-local"
        if self.started_at and self._now() - self.started_at < RETRY_AFTER:
            return "recent"
        exe = find_exe(exe_setting)
        if not exe:
            return "not-found"
        if is_running(os.path.basename(exe)):
            self.started_at = self._now()        # it's open: give it time to load
            return "running"
        if not launch(exe):
            return "failed"
        self.started_at = self._now()
        return "started"
