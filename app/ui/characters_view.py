"""Cast page: every character of this game, as a master–detail editor."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import voices
from ..config import NARRATOR
from . import theme
from .components import Avatar, button, icon_button, label, section
from .presets_view import (ListRow, MasterDetail, combo, form_label, gender_toggle, nice,
                           shrinkable)

CUSTOM = "custom"

__all__ = ["Avatar", "CharactersView", "CharacterCard", "CUSTOM", "voice_value"]


def voice_value(rec: dict) -> str:
    """Current choice in the Voice combo for a character record."""
    if rec.get("voice_override"):
        return rec["voice_override"]
    if rec.get("preset"):
        return "preset:" + rec["preset"]
    return CUSTOM


class CharacterCard(QWidget):
    """The editor for one character (right side of the Cast page)."""
    changed = Signal(str, dict)          # name, field changes
    voice_chosen = Signal(str, str)      # name, combo value
    gender_changed = Signal(str, str)    # name, gender
    reroll = Signal(str)
    preview = Signal(str)
    preview_moods = Signal(str)
    menu = Signal(str, object)           # name, global position

    def __init__(self, name: str, rec: dict, sources: list[tuple[str, str]]):
        super().__init__()
        self.setObjectName("page")
        self.name = name
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.avatar = Avatar(name, 44)
        top.addWidget(self.avatar)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.lbl_name = QLabel("Narrator" if name == NARRATOR else name)
        self.lbl_name.setStyleSheet("font-size: 14pt; font-weight: 600;")
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_sub = label("", "mono", wrap=True)
        info.addWidget(self.lbl_name)
        info.addWidget(self.lbl_sub)
        top.addLayout(info, 1)
        top.addWidget(button("Preview", "play", tip="Hear this character's voice",
                             slot=lambda: self.preview.emit(self.name)))
        self.btn_moods = button("Moods", "audio-lines",
                                tip="Hear this character in every mood: normal, seductive, "
                                    "aroused, whisper, happy, sad, angry (the first time, the "
                                    "mood voices are made first)",
                                slot=lambda: self.preview_moods.emit(self.name))
        top.addWidget(self.btn_moods)
        top.addWidget(icon_button("sparkles", "Give this character a different voice",
                                  lambda: self.reroll.emit(self.name)))
        b_more = icon_button("ellipsis", "Rename, merge or delete")
        b_more.clicked.connect(lambda: self.menu.emit(
            self.name, b_more.mapToGlobal(b_more.rect().bottomLeft())))
        top.addWidget(b_more)
        lay.addLayout(top)

        lay.addWidget(section("Voice"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        g_lay, self.gbtn = gender_toggle(self, ("female", "male", "unknown"),
                                         lambda g: (not self._loading) and
                                         self.gender_changed.emit(self.name, g))
        form.addRow(form_label("Gender"), g_lay)
        self.cb_voice = shrinkable(QComboBox())
        self.cb_voice.setMaximumWidth(420)
        self.cb_voice.currentIndexChanged.connect(
            lambda _=0: (not self._loading) and
            self.voice_chosen.emit(self.name, self.cb_voice.currentData() or CUSTOM))
        form.addRow(form_label("Voice"), self.cb_voice)
        self.cb_accent = combo(voices.ACCENTS, rec.get("accent", ""), ("Voice's own accent", ""))
        self.cb_accent.setMaximumWidth(420)
        self.cb_accent.currentIndexChanged.connect(
            lambda _=0: self._field("accent", self.cb_accent.currentData()))
        form.addRow(form_label("Accent"), self.cb_accent)
        self.cb_age = combo(voices.ADULT_AGES, rec.get("age_override", ""),
                            ("Voice's own age", ""))
        self.cb_age.setMaximumWidth(420)
        self.cb_age.setToolTip("Make this character sound younger or older (VoiceStudio "
                               "makes the new voice on the next line, a few seconds once)")
        self.cb_age.currentIndexChanged.connect(
            lambda _=0: self._field("age_override", self.cb_age.currentData()))
        form.addRow(form_label("Age"), self.cb_age)
        lay.addLayout(form)

        self.custom_row = QWidget()
        self.custom_row.setObjectName("transparent")
        cl = QVBoxLayout(self.custom_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        cl.addWidget(section("Custom voice"))
        cf = QFormLayout()
        cf.setHorizontalSpacing(16)
        cf.setVerticalSpacing(10)
        self.cb_pitch = combo(voices.PITCHES, rec.get("pitch", "moderate pitch"))
        self.cb_style = combo(["whisper"], rec.get("style", ""), ("Normal", ""))
        for cb, key, text in ((self.cb_pitch, "pitch", "Pitch"),
                              (self.cb_style, "style", "Style")):
            cb.setMaximumWidth(420)
            cb.currentIndexChanged.connect(lambda _=0, c=cb, k=key: self._field(k, c.currentData()))
            cf.addRow(form_label(text), cb)
        cl.addLayout(cf)
        lay.addWidget(self.custom_row)
        lay.addWidget(label("Edits apply from this character's next line.", "faint"))
        lay.addStretch(1)
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
        for text, value in sources:
            if value is None:                       # section header
                self.cb_voice.addItem(text, "")
                idx = self.cb_voice.count() - 1
                self.cb_voice.model().item(idx).setEnabled(False)
            else:
                self.cb_voice.addItem(text, value)
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
        self.avatar.set_name(self.name, 44, "" if self.name == NARRATOR else theme.gender_color(g))
        i = self.cb_voice.findData(voice_value(rec))
        if i >= 0:
            self.cb_voice.setCurrentIndex(i)
        for cb, key in ((self.cb_accent, "accent"), (self.cb_age, "age_override"),
                        (self.cb_pitch, "pitch"), (self.cb_style, "style")):
            j = cb.findData(rec.get(key, ""))
            if j >= 0:
                cb.setCurrentIndex(j)
        override = rec.get("voice_override", "")
        custom = not override and not rec.get("preset")
        self.custom_row.setVisible(custom)
        self.cb_accent.setEnabled(not override)
        self.cb_age.setEnabled(not override)
        self.btn_moods.setEnabled(not override)       # your own / Windows voice: no moods
        # Without an override the design's age *is* the voice's own age: show it.
        own = "" if rec.get("age_override") else (design or {}).get("age", "")
        self.cb_age.setItemText(0, f"Voice's own age ({own})" if own else "Voice's own age")
        self.cb_accent.setItemText(0, "Custom accent: none" if custom else "Voice's own accent")
        auto = (" (guessed)" if rec.get("gender_auto") and g != "unknown"
                and self.name != NARRATOR else "")
        gname = {"female": "female", "male": "male"}.get(g, "gender unknown")
        if override.startswith("sapi:"):
            src = "Windows voice"
        elif override.startswith("vs:"):
            src = "your VoiceStudio voice"
        elif design:
            src = (f"preset {design.get('label')}" if rec.get("preset") else "custom voice")
            src += f"  ·  {voices.instruct(design)}"
        else:
            src = ""
        n = int(rec.get("lines", 0))
        self.lbl_sub.setText(f"{gname}{auto}  ·  {n} line{'s' if n != 1 else ''}" +
                             (f"\n{src}" if src else ""))
        self._loading = False


class CharactersView(QWidget):
    changed = Signal(str, dict)
    voice_chosen = Signal(str, str)
    gender_changed = Signal(str, str)
    reroll = Signal(str)
    preview = Signal(str)
    preview_moods = Signal(str)
    reassign_all = Signal()
    refresh = Signal()
    menu = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.md = MasterDetail("Search characters", (
            "users", "No character selected",
            "Characters appear when they first speak and are remembered for this game."))
        lay.addWidget(self.md)
        self.cards: dict[str, CharacterCard] = {}
        self.sources: list[tuple[str, str | None]] = []
        self._recs: dict[str, dict] = {}

    def install_toolbar(self, header):
        header.add(button("Refresh voices", "refresh-cw", "ghost",
                          "Reload your VoiceStudio and Windows voices", slot=self.refresh.emit),
                   button("Re-assign presets", "sparkles", tip="Give every character a fresh "
                          "preset of their gender", slot=self.reassign_all.emit))

    def set_sources(self, presets: list[dict], vs_profiles: list[dict], sapi_voices: list[str]):
        src: list[tuple[str, str | None]] = [("Female presets", None)]
        src += [(f"♀  {p['name']}", "preset:" + p["id"]) for p in presets
                if p.get("gender") == "female"]
        src += [("Male presets", None)]
        src += [(f"♂  {p['name']}", "preset:" + p["id"]) for p in presets
                if p.get("gender") == "male"]
        src += [("Other", None), ("Custom voice (set age / pitch below)", CUSTOM)]
        mine = [p for p in vs_profiles if not str(p.get("name", "")).startswith("VNR - ")]
        if mine:
            src += [("My VoiceStudio voices", None)]
            src += [(p["name"], "vs:" + p["id"]) for p in mine]
        if sapi_voices:
            src += [("Windows voices", None)]
            src += [(v.split(":", 1)[1].replace("Microsoft ", "").replace(" Desktop", ""), v)
                    for v in sapi_voices]
        self.sources = src
        for card in self.cards.values():
            card.set_sources(src, card.cb_voice.currentData() or CUSTOM)

    def _row(self, name: str, rec: dict, design: dict) -> QWidget:
        g = rec.get("gender", "unknown")
        av = Avatar(name, 28)
        av.set_name(name, 28, "" if name == NARRATOR else theme.gender_color(g))
        ov = rec.get("voice_override", "")
        voice = ("Windows voice" if ov.startswith("sapi:") else "VoiceStudio voice"
                 if ov.startswith("vs:") else (design.get("label") if rec.get("preset")
                                               else "custom voice") if design else "")
        return ListRow("Narrator" if name == NARRATOR else name, voice or "",
                       str(int(rec.get("lines", 0))), av)

    def load(self, chars: dict[str, dict], designs: dict[str, dict]):
        keep = self.md.current()
        self.md.clear_all()
        self.cards.clear()
        for name in sorted(chars, key=lambda n: (n != NARRATOR,
                                                 -int(chars[n].get("lines", 0)))):
            self._add(name, chars[name], designs.get(name, {}))
        if keep in self.cards:
            self.md.select(keep)

    def _add(self, name, rec, design):
        card = CharacterCard(name, rec, self.sources)
        card.update_rec(rec, design)
        card.changed.connect(self.changed.emit)
        card.voice_chosen.connect(self.voice_chosen.emit)
        card.gender_changed.connect(self.gender_changed.emit)
        card.reroll.connect(self.reroll.emit)
        card.preview.connect(self.preview.emit)
        card.preview_moods.connect(self.preview_moods.emit)
        card.menu.connect(self.menu.emit)
        self.md.add(name, self._row(name, rec, design), card, name)
        self.cards[name] = card

    def ensure(self, name: str, rec: dict, design: dict):
        card = self.cards.get(name)
        if card:
            card.update_rec(rec, design)
            self.md.set_row(name, self._row(name, rec, design))
            return
        self._add(name, rec, design)

    def select(self, name: str):
        self.md.select(name)
