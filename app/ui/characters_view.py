"""Character cards: gender, voice (preset / custom / saved / Windows), accent."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import voices
from ..config import NARRATOR
from . import theme
from .presets_view import combo, gender_toggle, icon_button, shrinkable

CUSTOM = "custom"


class Avatar(QLabel):
    def __init__(self, name: str, size: int = 40):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.set_name(name, size)

    def set_name(self, name: str, size: int | None = None):
        size = size or self.width()
        initial = "N" if name == NARRATOR else (name.strip()[:1].upper() or "?")
        self.setText(initial)
        c = theme.avatar_color(name)
        self.setStyleSheet(f"background:{c}; color:#14151a; border-radius:{size // 2}px;"
                           f"font-size:{max(10, size // 2 - 4)}pt; font-weight:800;")


def voice_value(rec: dict) -> str:
    """Current choice in the Voice combo for a character record."""
    if rec.get("voice_override"):
        return rec["voice_override"]
    if rec.get("preset"):
        return "preset:" + rec["preset"]
    return CUSTOM


class CharacterCard(QFrame):
    changed = Signal(str, dict)          # name, field changes
    voice_chosen = Signal(str, str)      # name, combo value
    gender_changed = Signal(str, str)    # name, gender
    reroll = Signal(str)
    preview = Signal(str)
    menu = Signal(str, object)           # name, global position

    def __init__(self, name: str, rec: dict, sources: list[tuple[str, str]]):
        super().__init__()
        self.setObjectName("card")
        self.name = name
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        self.avatar = Avatar(name)
        top.addWidget(self.avatar)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.lbl_name = QLabel("Narrator" if name == NARRATOR else name)
        self.lbl_name.setObjectName("speaker")
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_sub = QLabel()
        self.lbl_sub.setObjectName("muted")
        self.lbl_sub.setWordWrap(True)
        info.addWidget(self.lbl_name)
        info.addWidget(self.lbl_sub)
        top.addLayout(info, 1)
        g_lay, self.gbtn = gender_toggle(self, ("female", "male", "unknown"),
                                         lambda g: (not self._loading) and
                                         self.gender_changed.emit(self.name, g))
        top.addLayout(g_lay)
        b_new = icon_button("🎲", "Give this character a different voice")
        b_new.clicked.connect(lambda: self.reroll.emit(self.name))
        b_play = icon_button("▶", "Preview this voice")
        b_play.clicked.connect(lambda: self.preview.emit(self.name))
        b_more = icon_button("⋯", "Merge or delete")
        b_more.clicked.connect(lambda: self.menu.emit(
            self.name, b_more.mapToGlobal(b_more.rect().bottomLeft())))
        top.addWidget(b_new)
        top.addWidget(b_play)
        top.addWidget(b_more)
        lay.addLayout(top)

        vrow = QHBoxLayout()
        lbl = QLabel("Voice")
        lbl.setObjectName("muted")
        vrow.addWidget(lbl)
        self.cb_voice = shrinkable(QComboBox())
        self.cb_voice.currentIndexChanged.connect(
            lambda _=0: (not self._loading) and
            self.voice_chosen.emit(self.name, self.cb_voice.currentData() or CUSTOM))
        vrow.addWidget(self.cb_voice, 2)
        self.cb_accent = combo(voices.ACCENTS, rec.get("accent", ""), ("Voice's own accent", ""))
        self.cb_accent.setToolTip("Accent for this character")
        self.cb_accent.currentIndexChanged.connect(
            lambda _=0: self._field("accent", self.cb_accent.currentData()))
        vrow.addWidget(self.cb_accent, 1)
        lay.addLayout(vrow)

        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.cb_age = combo(voices.AGES, rec.get("age", "young adult"))
        self.cb_pitch = combo(voices.PITCHES, rec.get("pitch", "moderate pitch"))
        self.cb_style = combo(["whisper"], rec.get("style", ""), ("Normal", ""))
        for cb, key, tip in ((self.cb_age, "age", "Age"), (self.cb_pitch, "pitch", "Pitch"),
                             (self.cb_style, "style", "Style")):
            cb.setToolTip(tip)
            cb.currentIndexChanged.connect(lambda _=0, c=cb, k=key: self._field(k, c.currentData()))
            cr.addWidget(cb)
        lay.addWidget(self.custom_row)
        self.set_sources(sources, voice_value(rec))
        self._loading = False
        self.update_rec(rec, {})

    def _field(self, key: str, value):
        if not self._loading:
            self.changed.emit(self.name, {key: value})

    def set_sources(self, sources: list[tuple[str, str]], current: str):
        was = self._loading
        self._loading = True
        self.cb_voice.clear()
        for label, value in sources:
            if value is None:                       # section header
                self.cb_voice.addItem(label, "")
                idx = self.cb_voice.count() - 1
                self.cb_voice.model().item(idx).setEnabled(False)
            else:
                self.cb_voice.addItem(label, value)
        i = self.cb_voice.findData(current)
        if i < 0 and current:
            self.cb_voice.addItem(current, current)
            i = self.cb_voice.count() - 1
        self.cb_voice.setCurrentIndex(max(0, i))
        self._loading = was

    def update_rec(self, rec: dict, design: dict):
        self._loading = True
        g = rec.get("gender", "unknown")
        self.gbtn.get(g, self.gbtn["unknown"]).setChecked(True)
        i = self.cb_voice.findData(voice_value(rec))
        if i >= 0:
            self.cb_voice.setCurrentIndex(i)
        for cb, key in ((self.cb_accent, "accent"), (self.cb_age, "age"),
                        (self.cb_pitch, "pitch"), (self.cb_style, "style")):
            j = cb.findData(rec.get(key, ""))
            if j >= 0:
                cb.setCurrentIndex(j)
        override = rec.get("voice_override", "")
        custom = not override and not rec.get("preset")
        self.custom_row.setVisible(custom)
        self.cb_accent.setEnabled(not override)
        self.cb_accent.setItemText(0, "Custom accent: none" if custom else "Voice's own accent")
        auto = (" (guessed)" if rec.get("gender_auto") and g != "unknown"
                and self.name != NARRATOR else "")
        gname = {"female": "Female", "male": "Male"}.get(g, "Gender unknown")
        if override.startswith("sapi:"):
            src = "Windows voice"
        elif override.startswith("vs:"):
            src = "your VoiceStudio voice"
        elif design:
            src = (f"preset {design.get('label')}" if rec.get("preset") else "custom voice")
            src += f" · {voices.instruct(design)}"
        else:
            src = ""
        self.lbl_sub.setText(f"{gname}{auto} · {src}" if src else gname)
        self.lbl_sub.setStyleSheet(f"color:{theme.gender_color(g)};" if g != "unknown" else "")
        self._loading = False


class CharactersView(QWidget):
    changed = Signal(str, dict)
    voice_chosen = Signal(str, str)
    gender_changed = Signal(str, str)
    reroll = Signal(str)
    preview = Signal(str)
    reassign_all = Signal()
    refresh = Signal()
    menu = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        t = QLabel("Characters")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch(1)
        b_ref = QPushButton("⟳ Refresh")
        b_ref.setToolTip("Reload your VoiceStudio and Windows voices")
        b_ref.clicked.connect(self.refresh.emit)
        b_all = QPushButton("Re-assign presets")
        b_all.setToolTip("Give every character a fresh preset of their gender")
        b_all.clicked.connect(self.reassign_all.emit)
        head.addWidget(b_ref)
        head.addWidget(b_all)
        lay.addLayout(head)
        self.hint = QLabel("Characters appear here when they first speak, and are remembered "
                           "for this game. Click ♀ / ♂ if the guess is wrong.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("page")
        self.list = QVBoxLayout(self.inner)
        self.list.setContentsMargins(0, 4, 4, 4)
        self.list.setSpacing(8)
        self.list.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.cards: dict[str, CharacterCard] = {}
        self.sources: list[tuple[str, str | None]] = []

    def set_sources(self, presets: list[dict], vs_profiles: list[dict], sapi_voices: list[str]):
        src: list[tuple[str, str | None]] = [("── Female presets ──", None)]
        src += [(f"♀  {p['name']}", "preset:" + p["id"]) for p in presets
                if p.get("gender") == "female"]
        src += [("── Male presets ──", None)]
        src += [(f"♂  {p['name']}", "preset:" + p["id"]) for p in presets
                if p.get("gender") == "male"]
        src += [("── Other ──", None), ("✨  Custom voice (set age / pitch below)", CUSTOM)]
        mine = [p for p in vs_profiles if not str(p.get("name", "")).startswith("VNR - ")]
        if mine:
            src += [("── My VoiceStudio voices ──", None)]
            src += [(f"🎙  {p['name']}", "vs:" + p["id"]) for p in mine]
        if sapi_voices:
            src += [("── Windows voices ──", None)]
            src += [("🪟  " + v.split(":", 1)[1].replace("Microsoft ", "")
                     .replace(" Desktop", ""), v) for v in sapi_voices]
        self.sources = src
        for card in self.cards.values():
            card.set_sources(src, card.cb_voice.currentData() or CUSTOM)

    def load(self, chars: dict[str, dict], designs: dict[str, dict]):
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        for name in sorted(chars, key=lambda n: n != NARRATOR):
            self.ensure(name, chars[name], designs.get(name, {}))

    def ensure(self, name: str, rec: dict, design: dict):
        card = self.cards.get(name)
        if card:
            card.update_rec(rec, design)
            return
        card = CharacterCard(name, rec, self.sources)
        card.update_rec(rec, design)
        card.changed.connect(self.changed.emit)
        card.voice_chosen.connect(self.voice_chosen.emit)
        card.gender_changed.connect(self.gender_changed.emit)
        card.reroll.connect(self.reroll.emit)
        card.preview.connect(self.preview.emit)
        card.menu.connect(self.menu.emit)
        self.list.insertWidget(self.list.count() - 1, card)
        self.cards[name] = card
