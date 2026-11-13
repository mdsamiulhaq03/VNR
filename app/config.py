"""Settings and per-game character voices, stored as JSON in %APPDATA%/VNReader."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

NARRATOR = "(narrator)"

DEFAULT_HOTKEYS = {"toggle": "F9", "replay": "F10", "pause": "F11", "stop": "F8",
                   "advance": "F7", "areas": "F6"}

DEFAULT_IGNORE_WORDS = ["Back", "History", "Skip", "Auto", "Save", "Load", "Q.Save", "Q.Load",
                        "QSave", "QLoad", "Prefs", "Settings", "Menu", "Log", "Config", "Hide",
                        "Close", "Options", "System", "Quick", "Main Menu", "Title", "Exit",
                        "Rollback", "Text", "Voice", "Next"]


def _default_presets() -> list[dict]:
    from .presets import defaults
    return defaults()


def _presets_version() -> int:
    from .presets import PRESETS_VERSION
    return PRESETS_VERSION


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
    quick_start: bool = True              # first sentence on fast quality: starts ~1 s sooner
    volume: float = 1.0                   # 0..1
    speed: float = 1.0                    # 0.5..2.0
    muted: bool = False
    read_narrator: bool = True
    name_mode: str = "auto"               # "auto" | "always" | "never": name on the top line
    ocr_text_region: list[int] | None = None   # [left, top, width, height] physical px
    ocr_name_region: list[int] | None = None   # optional separate name area (same format)
    ocr_language: str = "en-US"
    ocr_profile: str = "My game"          # name for this game's voice settings
    # profile -> {speaker -> character record (see app/voices.py)}
    voices: dict[str, dict[str, dict]] = field(default_factory=dict)

    # Follow a game window: {"title": str, "exe": str} or None for whole-screen mode.
    target_window: dict | None = None
    background_capture: bool = False
    # exe -> {"text": [fx, fy, fw, fh], "name": [...] | None}  (fractions of client area)
    regions_by_exe: dict[str, dict] = field(default_factory=dict)

    # OCR engine: "paddle" (PaddleOCR, best for game fonts; English) | "windows"
    ocr_engine: str = "paddle"
    ocr_gpu: bool = False                 # PaddleOCR on the GPU (DirectML)
    # OCR image cleanup: "learn" (auto + learned text colour) | "auto" | "color" | "off"
    ocr_mode: str = "learn"
    ocr_mode_v8: bool = True              # old "auto" default was moved to "learn"
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
    expressive: bool = True               # act out *stage directions*, laughs, sighs
    emotion_voices: bool = True           # emotional clone variants (seductive, sad, …)
    vocalize: bool = True                 # *moans* / *kisses* become sounds, not silence
    show_tray: bool = True                # tray icon next to the clock
    sidebar_collapsed: bool = False
    inspector_open: bool = True
    inspector_width: int = 320
    close_to_tray: bool = True            # closing the window keeps it running in the tray
    tray_hint_shown: bool = False
    # Built-in preset ids already offered once (new ones get added to old settings, but
    # a preset the user deleted is never brought back).
    preset_defaults_seen: list[str] = field(default_factory=lambda: [
        p["id"] for p in _default_presets()])
    presets_version: int = field(default_factory=lambda: _presets_version())


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


_VOICE_FIELDS = {"preset", "accent", "age", "pitch", "style", "vibe", "seed", "gender",
                 "gender_auto", "voice_override", "legacy", "vs_profile_id"}


def _replace_presets(s: "Settings") -> None:
    """A new built-in voice set: every old preset goes (the user's own too) and every
    character gets a voice from the new set. The old clones stay in vs_voice_bank until
    VoiceBank.prune() deletes them from VoiceStudio."""
    from .presets import PRESETS_VERSION, defaults
    from .voices import complete_character
    s.presets = defaults()
    s.preset_defaults_seen = sorted(p["id"] for p in s.presets)
    old_ids = {e.get("id") for e in s.vs_voice_bank.values() if e.get("id")}
    for game, chars in s.voices.items():
        rebuilt: dict[str, dict] = {}
        for name, rec in chars.items():
            if not isinstance(rec, dict):
                continue
            keep = {k: rec[k] for k in ("gender", "gender_auto") if k in rec}
            over = rec.get("voice_override") or ""
            if over and not (over.startswith("vs:") and over[3:] in old_ids):
                keep["voice_override"] = over        # the user's own pick stays
            new = complete_character(name, keep, rebuilt, s.presets)
            for k, v in rec.items():                 # bookkeeping (aliases, lines, …)
                if k not in _VOICE_FIELDS:
                    new.setdefault(k, v)
            rebuilt[name] = new
        s.voices[game] = rebuilt
    s.presets_version = PRESETS_VERSION


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self._lock = threading.RLock()
        self._migrated = False
        self.settings = self._load()
        if self._migrated:
            self.save()                          # the new voice set sticks

    def _load(self) -> Settings:
        try:
            data = _migrate(json.loads(self.path.read_text(encoding="utf-8")))
            known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
            s = Settings(**known)
        except (OSError, ValueError, TypeError):
            return Settings()
        from .presets import PRESETS_VERSION, defaults
        if int(data.get("presets_version") or 0) < PRESETS_VERSION:   # older settings
            _replace_presets(s)
            self._migrated = True
        else:
            seen = set(s.preset_defaults_seen)
            have = {p.get("id") for p in s.presets}
            for p in defaults():
                if p["id"] not in seen and p["id"] not in have:
                    s.presets.append(p)          # a new built-in voice
            s.preset_defaults_seen = sorted(seen | {p["id"] for p in defaults()})
        if not data.get("ocr_mode_v8"):            # the old default "auto" -> "learn"
            if s.ocr_mode == "auto":
                s.ocr_mode = "learn"
            s.ocr_mode_v8 = True
        return s

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
            if a.get("aliases"):                 # names it was renamed from
                b["aliases"] = list(dict.fromkeys(b.get("aliases", []) + a["aliases"]))
            rules = self.game_fixups(game)
            if not any(r.get("find", "").lower() == src.lower() for r in rules):
                rules.append({"find": src, "replace": dst, "whole_word": True,
                              "case_sensitive": False})
            self.save()

    def rename_character(self, game: str, old: str, new: str) -> str:
        """Rename a character (keeps voice, gender and counts). Renaming onto an existing
        character merges the two. The old name is kept as an alias, so when the game
        shows it again the line still goes to this character (only the speaker name is
        mapped; dialogue text is never changed). Returns the new name ('' = no change)."""
        new = (new or "").strip()
        if not new or new == old or old == NARRATOR or new == NARRATOR:
            return ""
        with self._lock:
            chars = self.characters(game)
            if old not in chars:
                return ""
            if new in chars:
                self.merge_character(game, old, new)
                return new
            rec = chars[old]
            aliases = [a for a in rec.get("aliases", []) if a.lower() != new.lower()]
            if old.lower() not in (a.lower() for a in aliases):
                aliases.append(old)
            rec["aliases"] = aliases
            # Keep the dict order stable: rebuild with the key swapped.
            items = [(new if k == old else k, v) for k, v in chars.items()]
            chars.clear()
            chars.update(items)
            self.save()
            return new

    def alias_map(self, game: str) -> dict[str, str]:
        """{old name (lower case) -> current name} for renamed characters."""
        out = {}
        for name, rec in self.characters(game).items():
            for a in rec.get("aliases", []) if isinstance(rec, dict) else []:
                out[a.lower()] = name
        return out

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
    # ---- per-game word memory (never auto-corrected) ----
    LEARN_AFTER = 3          # a new word is trusted after this many different lines

    def game_words(self, game: str) -> list[str]:
        """The user's words plus words learned from this game's lines."""
        g = self.settings.games.get(game) or {}
        learned = [w for w, n in (g.get("learned") or {}).items() if n >= self.LEARN_AFTER]
        return list(dict.fromkeys(list(g.get("words") or []) + learned))

    def word_entries(self, game: str) -> list[tuple[str, bool]]:
        """[(word, learned_automatically)] for the Fix-ups tab."""
        g = self.settings.games.get(game) or {}
        mine = list(g.get("words") or [])
        auto = [w for w, n in (g.get("learned") or {}).items()
                if n >= self.LEARN_AFTER and w.lower() not in {m.lower() for m in mine}]
        return [(w, False) for w in mine] + [(w, True) for w in sorted(auto, key=str.lower)]

    def learn_words(self, game: str, words: list[str]) -> list[str]:
        """Count words from one confirmed line; returns words that just became trusted."""
        if not words:
            return []
        new = []
        with self._lock:
            g = self.settings.games.setdefault(game, {})
            learned = g.setdefault("learned", {})
            blocked = {w.lower() for w in g.get("blocked") or []}
            for w in dict.fromkeys(words):           # once per line
                if w.lower() in blocked:
                    continue
                key = next((k for k in learned if k.lower() == w.lower()), w)
                learned[key] = int(learned.get(key, 0)) + 1
                if learned[key] == self.LEARN_AFTER:
                    new.append(key)
            if len(learned) > 3000:                  # keep it small: drop one-offs
                for k in [k for k, n in learned.items() if n < 2][:1000]:
                    learned.pop(k, None)
            self.save()
        return new

    def add_game_word(self, game: str, word: str) -> None:
        word = word.strip()
        if not word:
            return
        with self._lock:
            g = self.settings.games.setdefault(game, {})
            words = g.setdefault("words", [])
            if word.lower() not in {w.lower() for w in words}:
                words.append(word)
            g["blocked"] = [b for b in g.get("blocked") or [] if b.lower() != word.lower()]
            self.save()

    def remove_game_word(self, game: str, word: str) -> None:
        with self._lock:
            g = self.settings.games.setdefault(game, {})
            g["words"] = [w for w in g.get("words") or [] if w.lower() != word.lower()]
            learned = g.get("learned") or {}
            for k in [k for k in learned if k.lower() == word.lower()]:
                learned.pop(k)
                g.setdefault("blocked", []).append(k)       # don't learn it again
            self.save()

    # ---- learned dialogue text colour ----
    def learned_text_colour(self, game: str) -> list[int] | None:
        return (self.settings.games.get(game) or {}).get("text_color_auto")

    def record_text_colour(self, game: str, rgb) -> bool:
        """Remember the dialogue colour of one line. After 5 lines that agree, it
        becomes this game's learned text colour (re-learned if lines keep disagreeing).
        Returns True when the learned colour changed."""
        import numpy as np
        if rgb is None:
            return False
        with self._lock:
            g = self.settings.games.setdefault(game, {})
            samples = (g.get("colour_samples") or [])[-7:] + [[int(v) for v in rgb[:3]]]
            g["colour_samples"] = samples
            cur = g.get("text_color_auto")
            last5 = np.array(samples[-5:], dtype=float)
            if len(last5) < 5:
                return False
            med = np.median(last5, axis=0)
            agree = bool(np.all(np.linalg.norm(last5 - med, axis=1) < 40))
            if not agree:
                return False
            new = [int(v) for v in med]
            if cur and np.linalg.norm(np.array(cur) - med) < 40:
                return False
            g["text_color_auto"] = new
            self.save()
            return True

    def game_fixups(self, game: str) -> list[dict]:
        return self.settings.fixups.setdefault(game, [])

    def all_fixups(self, game: str) -> list[dict]:
        return list(self.settings.fixups_global) + list(self.settings.fixups.get(game, []))

    # ---- window-relative regions ----
    def window_regions(self, exe: str) -> dict:
        return self.settings.regions_by_exe.setdefault(exe.lower(), {"text": None, "name": None})
