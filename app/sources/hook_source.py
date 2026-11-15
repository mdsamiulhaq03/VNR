"""Receives lines from the VN Reader hook running inside a game (see
app/game_scripts/renpy_hook.py): exact text and speaker, the moment they're shown."""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

from .. import latency
from ..game_scripts.renpy_hook import PORT

LIVE_FOR = 3.0          # s: the hook counts as live this long after its last heartbeat


class HookSource:
    def __init__(self, on_line: Callable[..., None],
                 on_live: Callable[[bool], None] = lambda live: None, port: int = PORT):
        # on_line(speaker, text, line id, extra): extra = {"odd": the game gave no usable
        # name, "how": where the name came from}; on_live(True/False) when the hook
        # comes/goes.
        self.on_line = on_line
        self.on_live = on_live
        self.port = port
        self.game = ""                       # config.name reported by the game
        self._seen = 0.0
        self._was_live = False
        self._last_id = None
        self._sock: socket.socket | None = None
        self._stop = False
        self.error = ""

    @property
    def live(self) -> bool:
        return time.monotonic() - self._seen < LIVE_FOR

    def start(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", self.port))
            s.settimeout(0.5)
        except OSError as e:                 # port taken (a second VN Reader?)
            self.error = str(e)
            return False
        self._sock = s
        threading.Thread(target=self._run, name="hook", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        while not self._stop:
            try:
                data, _addr = self._sock.recvfrom(65536)
            except socket.timeout:
                self._check_live()
                continue
            except OSError:
                break
            self.feed(data)
        try:
            self._sock.close()
        except OSError:
            pass

    def feed(self, data: bytes) -> None:
        """Handle one packet (also used by tests)."""
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict):
            return
        self._seen = time.monotonic()
        self._check_live()
        if msg.get("t") == "hb":
            self.game = str(msg.get("game") or self.game)
        elif msg.get("t") == "say":
            text = " ".join(str(msg.get("what") or "").split())
            lid = str(msg.get("id") or "")
            if not text or (lid and lid == self._last_id):
                return                       # {w} pauses send the same line again
            self._last_id = lid
            latency.changed()
            latency.mark("hook")
            try:
                self.on_line(str(msg.get("who") or ""), text, lid,
                             {"odd": bool(msg.get("odd")), "how": str(msg.get("how") or ""),
                              "dyn": bool(msg.get("dyn"))})
            except Exception:
                pass

    def _check_live(self) -> None:
        live = self.live
        if live != self._was_live:
            self._was_live = live
            try:
                self.on_live(live)
            except Exception:
                pass
