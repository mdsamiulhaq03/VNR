"""Choose a character's voice: every voice grouped by what kind of voice it is and who
it suits best, with suggestions for the character's role, search, a gender filter and
a preview button on every voice."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from ..presets import CATEGORIES, CATEGORY, ROLE_CATEGORY, best_for, category_of
from . import theme
from .components import (ColorDot, Dialog, SegmentedControl, button, icon_button, label,
                         section)

CUSTOM = "custom"


def _tags(p: dict) -> str:
    acc = (p.get("accent") or "").replace(" accent", "")
    bits = [p.get("age", ""), (p.get("pitch") or "").replace(" pitch", " pitch"),
            acc or "any accent"]
    if p.get("style") == "whisper":
        bits.append("whisper")
    return " · ".join(b for b in bits if b)


def suggestions(presets: list[dict], gender: str, role: str, used: dict[str, list[str]],
                current: str = "", n: int = 5) -> list[dict]:
    """Voices to suggest for a character: the role's category first, then the gender's
    other voices; unused before used, never the one it already has."""
    want = ROLE_CATEGORY.get(role)
    pool = [p for p in presets if p["id"] != current and category_of(p) != "narrator"
            and (gender not in ("female", "male") or p.get("gender") == gender)]
    if role == "narration" or role == "inner thoughts":
        pool = [p for p in presets if category_of(p) == "narrator" and p["id"] != current]
    pool.sort(key=lambda p: (0 if want and category_of(p) == want else 1,
                             bool(used.get(p["id"])), p.get("style") == "whisper"))
    return pool[:n]


class VoiceTile(QFrame):
    """One voice: name, what it's like, who uses it, ▶ and Use."""

    def __init__(self, p: dict, used_by: list[str], is_current: bool,
                 on_use: Callable[[str], None], on_play: Callable[[str], None]):
        super().__init__()
        self.setObjectName("card")
        self.pid = p["id"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(ColorDot(theme.gender_color(p.get("gender")), 8))
        name = QLabel(p.get("name", ""))
        name.setObjectName("strong")
        top.addWidget(name)
        if is_current:
            cur = label("Current", "pill")
            cur.setProperty("tone", "accent")
            top.addWidget(cur)
        top.addStretch(1)
        top.addWidget(icon_button("play", "Hear this voice", lambda: on_play(self.pid), size=14))
        use = button("Use", kind="" if is_current else "primary",
                     slot=lambda: on_use("preset:" + self.pid))
        use.setEnabled(not is_current)
        top.addWidget(use)
        lay.addLayout(top)
        if p.get("note"):
            lay.addWidget(label(p["note"], "muted"))
        lay.addWidget(label(_tags(p), "footnote"))
        if used_by:
            lay.addWidget(label("Used by " + ", ".join(used_by[:3]) +
                                ("…" if len(used_by) > 3 else ""), "footnote"))


class VoicePicker(Dialog):
    """Returns the chosen value in .choice ("preset:<id>", "custom", "vs:…", "sapi:…")."""

    COLS = 3

    def __init__(self, parent, character: str, rec: dict, presets: list[dict],
                 used: dict[str, list[str]], others: list[tuple[str, str | None]],
                 on_play: Callable[[str], None]):
        super().__init__(parent, f"Choose a voice for {character}",
                         "Voices are grouped by what they sound like and who they suit. "
                         "▶ plays a sample; Use gives it to this character.", "audio-lines")
        self.setMinimumSize(900, 640)
        self.character, self.rec, self.presets = character, rec, presets
        self.used, self.others, self.on_play = used, others, on_play
        self.current = ("preset:" + rec["preset"]) if rec.get("preset") and \
            not rec.get("voice_override") else (rec.get("voice_override") or CUSTOM)
        self.choice: str | None = None

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: name, accent, 'boss', 'shy'…")
        self.search.textChanged.connect(lambda _t: self._build())
        bar.addWidget(self.search, 1)
        g = rec.get("gender", "unknown")
        self.gender = SegmentedControl([("all", "All"), ("female", "Female"), ("male", "Male")],
                                       g if g in ("female", "male") else "all")
        self.gender.changed.connect(lambda _v: self._build())
        bar.addWidget(self.gender)
        self.content.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.content.addWidget(self.scroll, 1)
        self.add_button("Cancel", accept=False)
        self._build()

    # ---------------------------------------------------------- content
    def _match(self, p: dict) -> bool:
        g = self.gender.value()
        if g in ("female", "male") and p.get("gender") != g:
            return False
        q = self.search.text().strip().lower()
        if not q:
            return True
        c = CATEGORY.get(category_of(p), {})
        hay = " ".join([p.get("name", ""), p.get("note", ""), p.get("accent", ""),
                        p.get("age", ""), p.get("pitch", ""), c.get("title", ""),
                        c.get("kind", ""), " ".join(best_for(p))]).lower()
        return all(w in hay for w in q.split())

    def _grid(self, items: list[dict]) -> QWidget:
        w = QWidget()
        w.setObjectName("transparent")
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for i, p in enumerate(items):
            tile = VoiceTile(p, [n for n in self.used.get(p["id"], []) if n != self.character],
                             self.current == "preset:" + p["id"], self._use, self.on_play)
            grid.addWidget(tile, i // self.COLS, i % self.COLS)
        for c in range(self.COLS):
            grid.setColumnStretch(c, 1)
        return w

    def _build(self):
        body = QWidget()
        body.setObjectName("transparent")
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 4, 8, 4)
        v.setSpacing(10)
        searching = bool(self.search.text().strip())
        role = self.rec.get("role", "")
        if not searching:
            sug = [p for p in suggestions(self.presets, self.rec.get("gender", "unknown"),
                                          role, self.used, self.current[7:]
                                          if self.current.startswith("preset:") else "")
                   if self._match(p)]
            if sug:
                why = f"role: {role}" if role else "same gender, not used yet"
                v.addWidget(section(f"Suggested for {self.character}  ({why})"))
                v.addWidget(self._grid(sug))
        shown = 0
        for c in CATEGORIES:
            items = [p for p in self.presets if category_of(p) == c["key"] and self._match(p)]
            if not items:
                continue
            shown += len(items)
            head = QHBoxLayout()
            head.addWidget(section(c["title"]))
            head.addWidget(label(f"{c['kind']}  ·  best for {', '.join(c['best_for'])}",
                                 "muted"), 1)
            v.addLayout(head)
            v.addWidget(self._grid(items))
        if not searching:
            v.addWidget(section("Other"))
            for text, value in self.others:
                if value is None:
                    v.addWidget(label(text, "faint"))
                    continue
                row = QHBoxLayout()
                row.addWidget(label(text + ("   ✓ current" if value == self.current else "")), 1)
                b = button("Use", slot=lambda _=False, val=value: self._use(val))
                b.setEnabled(value != self.current)
                row.addWidget(b)
                v.addLayout(row)
        elif not shown:
            v.addWidget(label("No voice matches that search.", "muted"))
        v.addStretch(1)
        self.scroll.setWidget(body)

    def _use(self, value: str):
        self.choice = value
        self.accept()

    # for tests
    def tiles(self) -> list[VoiceTile]:
        w = self.scroll.widget()
        return w.findChildren(VoiceTile) if w else []
