"""Play time and per-game history (Games tab).

PlayClock counts seconds only while reading is on AND the game is in front.
PlayLog keeps sessions, script lines seen, notes and hidden state per game in
playlog.json next to settings.json (saved every 30 s and when a session ends).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "VNReader" / "playlog.json"


@dataclass
class Session:
    start: float                     # epoch seconds
    end: float = 0.0
    seconds: float = 0.0             # counted play time (game in front, reading on)
    lines: int = 0
    words: int = 0
    new_chars: int = 0
    delays: list[float] = field(default_factory=list)      # voice delays (s), max 500
    sources: dict[str, int] = field(default_factory=dict)  # "hook" / "script" / "ocr"
    fixed: int = 0                   # OCR misreads corrected by the script
    days: dict[str, float] = field(default_factory=dict)   # "YYYY-MM-DD" -> seconds

    @property
    def avg_delay(self) -> float | None:
        return sum(self.delays) / len(self.delays) if self.delays else None

    @property
    def p90_delay(self) -> float | None:
        if not self.delays:
            return None
        d = sorted(self.delays)
        return d[min(len(d) - 1, int(len(d) * 0.9))]


class PlayLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.RLock()
        self.games: dict[str, dict] = {}
        self._dirty = False
        self._saved = 0.0
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict):
                self.games = raw.get("games", {}) or {}
        except (OSError, ValueError):
            pass

    # ---- records ----
    def game(self, name: str) -> dict:
        with self._lock:
            g = self.games.setdefault(name, {})
            g.setdefault("sessions", [])
            g.setdefault("seen", [])
            g.setdefault("notes", "")
            g.setdefault("hidden", False)
            return g

    def sessions(self, name: str) -> list[Session]:
        with self._lock:
            out = []
            for d in self.game(name)["sessions"]:
                try:
                    out.append(Session(**d))
                except TypeError:
                    pass
            return out

    def put_session(self, name: str, s: Session) -> None:
        """Add or update a session (matched by its start time)."""
        with self._lock:
            lst = self.game(name)["sessions"]
            d = asdict(s)
            d["delays"] = d["delays"][-500:]
            for i, old in enumerate(lst):
                if old.get("start") == s.start:
                    lst[i] = d
                    break
            else:
                lst.append(d)
            self._dirty = True

    def mark_seen(self, name: str, line_id: str) -> None:
        if not line_id:
            return
        with self._lock:
            g = self.game(name)
            seen = g.setdefault("_seen_set", None)
            if seen is None:
                seen = g["_seen_set"] = set(g["seen"])
            if line_id not in seen:
                seen.add(line_id)
                g["seen"].append(line_id)
                self._dirty = True

    def seen_count(self, name: str) -> int:
        return len(self.game(name)["seen"])

    def set_notes(self, name: str, text: str) -> None:
        with self._lock:
            self.game(name)["notes"] = text
            self._dirty = True

    def set_hidden(self, name: str, hidden: bool) -> None:
        with self._lock:
            self.game(name)["hidden"] = hidden
            self._dirty = True

    def set_total_lines(self, name: str, n: int) -> None:
        with self._lock:
            if self.game(name).get("script_lines") != n:
                self.game(name)["script_lines"] = n
                self._dirty = True

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            if old in self.games and new not in self.games:
                self.games[new] = self.games.pop(old)
                self._dirty = True

    def remove(self, name: str) -> None:
        with self._lock:
            if self.games.pop(name, None) is not None:
                self._dirty = True

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not (self._dirty or force):
                return
            data = {"version": 1, "games": {
                n: {k: v for k, v in g.items() if not k.startswith("_")}
                for n, g in self.games.items()}}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
                tmp.replace(self.path)
                self._dirty, self._saved = False, time.time()
            except OSError:
                pass

    # ---- summaries ----
    def total_seconds(self, name: str) -> float:
        return sum(s.seconds for s in self.sessions(name))

    def per_day(self, name: str | None = None) -> dict[str, float]:
        """{"YYYY-MM-DD": seconds} for one game, or all games when name is None."""
        out: dict[str, float] = {}
        for n in ([name] if name else list(self.games)):
            for s in self.sessions(n):
                for day, sec in s.days.items():
                    out[day] = out.get(day, 0.0) + sec
        return out

    def last_days(self, name: str | None, n: int = 14,
                  today: date | None = None) -> list[tuple[str, float]]:
        today = today or date.today()
        per = self.per_day(name)
        return [((today - timedelta(days=k)).isoformat(),
                 per.get((today - timedelta(days=k)).isoformat(), 0.0))
                for k in range(n - 1, -1, -1)]

    def records(self, name: str) -> dict:
        ss = [s for s in self.sessions(name) if s.seconds > 0 or s.lines]
        per = self.per_day(name)
        delays = [d for s in ss for d in s.delays]
        days = sorted(per)
        return {
            "sessions": len(ss),
            "total": sum(s.seconds for s in ss),
            "longest": max((s.seconds for s in ss), default=0.0),
            "average": (sum(s.seconds for s in ss) / len(ss)) if ss else 0.0,
            "best_day": max(per.items(), key=lambda kv: kv[1]) if per else None,
            "first": min((s.start for s in ss), default=0.0),
            "days_played": len([d for d in per.values() if d > 0]),
            "streak": _streak(days),
            "words": sum(s.words for s in ss),
            "lines": sum(s.lines for s in ss),
            "fastest_delay": min(delays) if delays else None,
            "avg_delay": (sum(delays) / len(delays)) if delays else None,
        }


def _streak(days: list[str]) -> int:
    """Longest run of consecutive days played."""
    best = cur = 0
    prev = None
    for d in days:
        day = date.fromisoformat(d)
        cur = cur + 1 if prev and day - prev == timedelta(days=1) else 1
        best, prev = max(best, cur), day
    return best


def milestones(lines: int, hours: float, top_char_lines: int, streak: int) -> list[str]:
    """Badges earned, e.g. "1,000 lines"."""
    out = [f"{n:,} lines" for n in (100, 1000, 5000, 10000, 50000) if lines >= n]
    out += [f"{h} hour{'s' if h != 1 else ''}" for h in (1, 10, 50, 100, 500) if hours >= h]
    out += [f"{n:,} lines with one character" for n in (500, 1000, 5000) if top_char_lines >= n]
    out += [f"{n} days in a row" for n in (3, 7, 30) if streak >= n]
    return out


def fmt_duration(seconds: float, short: bool = False) -> str:
    s = int(seconds)
    h, m = s // 3600, (s % 3600) // 60
    if short:
        return f"{h} h {m:02d} m" if h else f"{m} m"
    return f"{h}:{m:02d}:{s % 60:02d}"


class PlayClock:
    """Counts play time for one session. tick(now, counting) is called often (every
    250 ms); only time while `counting` is True is added."""

    MAX_STEP = 5.0                    # never add more than this per tick (sleep, hangs)

    def __init__(self, log: PlayLog, game: str, now: float | None = None):
        self.log, self.game = log, game
        self.session = Session(start=now if now is not None else time.time())
        self._last: float | None = None
        self.counting = False
        self._last_put = 0.0

    def tick(self, now: float, counting: bool) -> None:
        if counting and self.counting and self._last is not None:
            step = max(0.0, min(self.MAX_STEP, now - self._last))
            self.session.seconds += step
            day = datetime.fromtimestamp(now).date().isoformat()
            self.session.days[day] = self.session.days.get(day, 0.0) + step
        self.counting, self._last = counting, now
        if now - self._last_put >= 30:
            self.flush(now)

    def line(self, text: str, source: str, new_char: bool = False, fixed: bool = False):
        s = self.session
        s.lines += 1
        s.words += len(text.split())
        s.new_chars += int(new_char)
        s.sources[source] = s.sources.get(source, 0) + 1
        s.fixed += int(fixed)

    def delay(self, seconds: float) -> None:
        self.session.delays.append(round(seconds, 3))

    def flush(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._last_put = now
        self.session.end = now
        self.log.put_session(self.game, self.session)
        self.log.save()
