"""Settings and per-game character voices, stored as JSON in %APPDATA%/VNReader."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

NARRATOR = "(narrator)"

DEFAULT_HOTKEYS = {"toggle": "F9", "replay": "F10", "pause": "F11", "stop": "F8",
                   "advance": "F7"}

DEFAULT_IGNORE_WORDS = ["Back", "History", "Skip", "Auto", "Save", "Load", "Q.Save", "Q.Load",
                        "QSave", "QLoad", "Prefs", "Settings", "Menu", "Log", "Config", "Hide",
                        "Close", "Options", "System", "Quick", "Main Menu", "Title", "Exit",
                        "Rollback", "Text", "Voice", "Next"]


def _default_presets() -> list[dict]:
    from .presets import defaults
    return defaults()


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "VNReader"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    voicestudio_url: str = "http://127.0.0.1:3900"
    voicestudio_api_key: str = ""
    save_vs_profiles: bool = True         # store each character's voice in VoiceStudio
    vs_quality: str = "balanced"          # "fast" | "balanced" | "best"
    volume: float = 1.0                   # 0..1
    speed: float = 1.0                    # 0.5..2.0
    muted: bool = False
    read_narrator: bool = True
    name_mode: str = "auto"               # "auto" | "always" | "never": name on the top line
    ocr_text_region: list[int] | None = None   # [left, top, width, height] physical px
    ocr_language: str = "en-US"
    ocr_profile: str = "My game"          # name for this game's voice settings
    # profile -> {speaker -> character record (see app/voices.py)}
    voices: dict[str, dict[str, dict]] = field(default_factory=dict)

    # Follow a game window: {"title": str, "exe": str} or None for whole-screen mode.
    target_window: dict | None = None
    background_capture: bool = False
    # exe -> {"text": [fx, fy, fw, fh]}  (fractions of client area)
    regions_by_exe: dict[str, dict] = field(default_factory=dict)

    # OCR image cleanup: "auto" | "color" | "off"
    ocr_mode: str = "auto"
    text_color: list[int] | None = None   # [r, g, b]
    color_tolerance: int = 60

    # Fix-up rules: {"find", "replace", "whole_word", "case_sensitive"}
    fixups_global: list[dict] = field(default_factory=list)
    fixups: dict[str, list[dict]] = field(default_factory=dict)   # profile -> rules

    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))

    # Voice presets (see app/presets.py) and a global accent for every voice ("" = off).
    presets: list[dict] = field(default_factory=_default_presets)
    accent_override: str = ""
    # voice key ("instruct|seed") -> {"id": VoiceStudio clone profile id, "label": str}
    vs_voice_bank: dict[str, dict] = field(default_factory=dict)

    # OCR clean-up: lines made of these words are menu buttons, not dialogue.
    ignore_words: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_WORDS))
    ocr_autocorrect: bool = True

    # Recent games: profile -> {"exe", "title", "last_played", "lines"}
    games: dict[str, dict] = field(default_factory=dict)

    read_names: bool = False              # speak "Sayori." before her line
    auto_advance: bool = False            # go to the next line when reading is done
    advance_key: str = "enter"            # "enter" | "space" | "click" | "ctrl"
    advance_delay: float = 0.8            # seconds to wait after the voice finishes
    expressive: bool = True               # laughs / sighs / surprise via VoiceStudio tags


def _migrate(data: dict) -> dict:
    # v1/v2 stored voices as {speaker: "voice string"}.
    voices = data.get("voices") or {}
    for game, chars in list(voices.items()):
        if not isinstance(chars, dict):
            voices[game] = {}
            continue
        for name, rec in list(chars.items()):
            if isinstance(rec, str):
                override = "" if rec in ("", "default") else (
                    rec if rec.startswith(("sapi:", "vs:")) else "vs:" + rec)
                chars[name] = {"voice_override": override, "legacy": True}
    return data


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self._lock = threading.RLock()
        self.settings = self._load()

    def _load(self) -> Settings:
        try:
            data = _migrate(json.loads(self.path.read_text(encoding="utf-8")))
            known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
            return Settings(**known)
        except (OSError, ValueError, TypeError):
            return Settings()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.settings), indent=2, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)

    # ---- per-game characters ----
    def characters(self, game: str) -> dict[str, dict]:
        with self._lock:
            return self.settings.voices.setdefault(game, {})

    def character(self, game: str, speaker: str) -> dict:
        """The record for a speaker; created (with a fresh voice design) if new."""
        from .voices import complete_character   # local import: avoids a cycle
        key = speaker or NARRATOR
        with self._lock:
            chars = self.characters(game)
            rec = chars.get(key)
            if rec is None or rec.get("legacy") or "preset" not in rec:
                others = {k: v for k, v in chars.items() if k != key}
                keep = {k: rec[k] for k in ("gender", "gender_auto", "voice_override")
                        if rec and k in rec and not rec.get("legacy")}
                if rec and rec.get("legacy"):
                    keep["voice_override"] = rec.get("voice_override", "")
                chars[key] = complete_character(key, keep, others, self.settings.presets)
                self.save()
            return chars[key]

    def design(self, game: str, speaker: str) -> dict:
        """Effective voice design (preset + character + global accent)."""
        from .presets import by_id
        from .voices import effective
        rec = self.character(game, speaker)
        return effective(rec, by_id(self.settings.presets), self.settings.accent_override,
                         speaker or NARRATOR)

    def touch_game(self, game: str, exe: str = "", title: str = "") -> None:
        import time
        g = self.settings.games.setdefault(game, {})
        if exe:
            g["exe"], g["title"] = exe, title
        g["last_played"] = time.time()
        self.save()

    def count_line(self, game: str, speaker: str) -> None:
        import time
        with self._lock:
            g = self.settings.games.setdefault(game, {})
            g["lines"] = int(g.get("lines", 0)) + 1
            g["last_played"] = time.time()
            rec = self.character(game, speaker)
            rec["lines"] = int(rec.get("lines", 0)) + 1
            rec["last_seen"] = time.time()
            self.save()

    def delete_game(self, game: str) -> None:
        s = self.settings
        with self._lock:
            exe = (s.games.get(game) or {}).get("exe", "")
            s.games.pop(game, None)
            s.voices.pop(game, None)
            s.fixups.pop(game, None)
            if exe and not any((i.get("exe") or "").lower() == exe.lower()
                               for i in s.games.values()):
                s.regions_by_exe.pop(exe.lower(), None)
                t = s.target_window or {}
                if (t.get("exe") or "").lower() == exe.lower():
                    s.target_window = None
            if s.ocr_profile == game:
                rest = self.recent_games()
                s.ocr_profile = rest[0][0] if rest else "My game"
            self.save()

    def delete_character(self, game: str, name: str) -> bool:
        if name == NARRATOR:
            return False
        with self._lock:
            removed = self.characters(game).pop(name, None) is not None
            self.save()
            return removed

    def merge_character(self, game: str, src: str, dst: str) -> None:
        """Fold a misread duplicate (src) into the real character (dst)."""
        if src == dst or src == NARRATOR:
            return
        with self._lock:
            chars = self.characters(game)
            a = chars.pop(src, None)
            if a is None:
                return
            b = self.character(game, dst)
            b["lines"] = int(b.get("lines", 0)) + int(a.get("lines", 0))
            rules = self.game_fixups(game)
            if not any(r.get("find", "").lower() == src.lower() for r in rules):
                rules.append({"find": src, "replace": dst, "whole_word": True,
                              "case_sensitive": False})
            self.save()

    def used_voice_keys(self) -> set[str]:
        """Voice keys still needed by some character or preset (for pruning clones)."""
        from .presets import by_id
        from .voices import effective, voice_key
        ps = by_id(self.settings.presets)
        acc = self.settings.accent_override
        keys = {voice_key(effective({"preset": pid}, ps, acc)) for pid in ps}
        for game, chars in self.settings.voices.items():
            for name, rec in chars.items():
                if "preset" in rec and not rec.get("voice_override"):
                    keys.add(voice_key(effective(rec, ps, acc, name)))
        return keys

    def recent_games(self) -> list[tuple[str, dict]]:
        names = set(self.settings.games) | {g for g, c in self.settings.voices.items()
                                            if len(c) > 1}
        items = [(n, self.settings.games.get(n, {})) for n in names]
        return sorted(items, key=lambda kv: -kv[1].get("last_played", 0))

    def update_character(self, game: str, speaker: str, **changes) -> dict:
        with self._lock:
            rec = self.character(game, speaker)
            rec.update(changes)
            self.save()
            return rec

    # ---- fix-ups ----
    def game_fixups(self, game: str) -> list[dict]:
        return self.settings.fixups.setdefault(game, [])

    def all_fixups(self, game: str) -> list[dict]:
        return list(self.settings.fixups_global) + list(self.settings.fixups.get(game, []))

    # ---- window-relative regions ----
    def window_regions(self, exe: str) -> dict:
        return self.settings.regions_by_exe.setdefault(exe.lower(), {"text": None})
