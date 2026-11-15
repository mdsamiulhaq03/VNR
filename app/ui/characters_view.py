"""Cast page: every character of this game, as a master–detail editor.

Each character has a voice card (which voice, what it's like, who it suits) with
"Change voice…" (the grouped voice picker, app/ui/voice_picker.py) and a Role, which
steers suggestions and the ✨ "different voice" button. The list can be grouped by
voice type, or sorted by name or by lines.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import voices
from ..config import NARRATOR
from ..presets import CATEGORIES, CATEGORY, category_of
from . import theme
from .components import Avatar, button, icon_button, label, section
from .presets_view import (ListRow, MasterDetail, combo, form_label, gender_toggle,
                           shrinkable)

CUSTOM = "custom"
GROUPS = [("voice", "Group by voice type"), ("colour", "Group by colour"),
          ("name", "Sort by name"), ("lines", "Sort by most lines")]


def _hue(hex_colour: str) -> float:
    from PySide6.QtGui import QColor
    c = QColor(hex_colour)
    h = c.hslHueF()
    return 2.0 if h < 0 or c.hslSaturationF() < 0.12 else h      # greys last

__all__ = ["Avatar", "CharactersView", "CharacterCard", "CUSTOM", "voice_value"]


def voice_value(rec: dict) -> str:
    """Current voice choice for a character record."""
    if rec.get("voice_override"):
        return rec["voice_override"]
    if rec.get("preset"):
        return "preset:" + rec["preset"]
    return CUSTOM


def role_combo(current: str) -> QComboBox:
    c = shrinkable(QComboBox())
    c.addItem("Not set", "")
    for cat in CATEGORIES:
        c.addItem(f"— {cat['title']} —", None)
        c.model().item(c.count() - 1).setEnabled(False)
        for r in cat["best_for"]:
            c.addItem("   " + r[:1].upper() + r[1:], r)
    i = c.findData(current) if current else 0
    c.setCurrentIndex(max(0, i))
    return c


class CharacterCard(QWidget):
    """The editor for one character (right side of the Cast page)."""
    changed = Signal(str, dict)          # name, field changes
    voice_chosen = Signal(str, str)      # name, voice value (kept for older callers)
    pick_voice = Signal(str)             # name: open the voice picker
    role_changed = Signal(str, str)      # name, role
    gender_changed = Signal(str, str)    # name, gender
    reroll = Signal(str)
    preview = Signal(str)
    preview_moods = Signal(str)
    menu = Signal(str, object)           # name, global position

    def __init__(self, name: str, rec: dict, presets: dict[str, dict] | None = None):
        super().__init__()
        self.setObjectName("page")
        self.name = name
        self.presets = presets if isinstance(presets, dict) else {}   # (old callers: a list)
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.avatar = Avatar(name, 48)
        top.addWidget(self.avatar)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.lbl_name = QLabel("Narrator" if name == NARRATOR else name)
        self.lbl_name.setObjectName("pageTitle")
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_sub = label("", "footnote", wrap=True)
        info.addWidget(self.lbl_name)
        info.addWidget(self.lbl_sub)
        top.addLayout(info, 1)
        self.btn_moods = button("Moods", "audio-lines",
                                tip="Hear this character in every mood: normal, seductive, "
                                    "aroused, whisper, happy, sad, angry (the first time, the "
                                    "mood voices are made first)",
                                slot=lambda: self.preview_moods.emit(self.name))
        top.addWidget(self.btn_moods)
        b_more = icon_button("ellipsis", "Rename, merge or delete")
        b_more.clicked.connect(lambda: self.menu.emit(
            self.name, b_more.mapToGlobal(b_more.rect().bottomLeft())))
        top.addWidget(b_more)
        lay.addLayout(top)

        # ---- the voice
        lay.addWidget(section("Voice"))
        self.voice_box = QFrame()
        self.voice_box.setObjectName("card")
        vb = QHBoxLayout(self.voice_box)
        vb.setContentsMargins(16, 14, 14, 14)
        vb.setSpacing(10)
        txt = QVBoxLayout()
        txt.setSpacing(3)
        self.lbl_voice = QLabel("")
        self.lbl_voice.setObjectName("headline")
        self.lbl_kind = label("", "muted", wrap=True)
        self.lbl_tags = label("", "footnote", wrap=True)
        for w in (self.lbl_voice, self.lbl_kind, self.lbl_tags):
            txt.addWidget(w)
        vb.addLayout(txt, 1)
        vb.addWidget(button("Preview", "play", tip="Hear this character's voice",
                            slot=lambda: self.preview.emit(self.name)), 0, Qt.AlignTop)
        vb.addWidget(icon_button("sparkles", "A different voice (from the role's group if "
                                 "a role is set)", lambda: self.reroll.emit(self.name)),
                     0, Qt.AlignTop)
        vb.addWidget(button("Change voice…", "audio-lines", "primary",
                            tip="All voices, grouped by kind and who they suit",
                            slot=lambda: self.pick_voice.emit(self.name)), 0, Qt.AlignTop)
        lay.addWidget(self.voice_box)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        self.cb_role = role_combo(rec.get("role", ""))
        self.cb_role.setMaximumWidth(420)
        self.cb_role.setToolTip("Who this character is: the voice picker suggests voices "
                                "that suit the role, and choosing one switches to a fitting "
                                "voice")
        self.cb_role.currentIndexChanged.connect(
            lambda _=0: (not self._loading) and self.cb_role.currentData() is not None and
            self.role_changed.emit(self.name, self.cb_role.currentData()))
        form.addRow(form_label("Role"), self.cb_role)
        g_lay, self.gbtn = gender_toggle(self, ("female", "male", "unknown"),
                                         lambda g: (not self._loading) and
                                         self.gender_changed.emit(self.name, g))
        form.addRow(form_label("Gender"), g_lay)
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
        self.cb_effect = QComboBox()
        for text, val in (("None", ""), ("On the phone", "phone"), ("Over the radio", "radio")):
            self.cb_effect.addItem(text, val)
        self.cb_effect.setCurrentIndex(max(0, self.cb_effect.findData(rec.get("effect", ""))))
        self.cb_effect.setMaximumWidth(420)
        self.cb_effect.setToolTip("Every line of this character sounds like a call or a "
                                  "radio. A name tag like “(on the phone)” does this for one "
                                  "line only.")
        self.cb_effect.currentIndexChanged.connect(
            lambda _=0: self._field("effect", self.cb_effect.currentData()))
        form.addRow(form_label("Sound"), self.cb_effect)
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
        lay.addWidget(label("Changes apply from this character's next line.", "footnote"))
        lay.addStretch(1)
        self._loading = False
        self.update_rec(rec, {})

    def _field(self, key: str, value):
        if not self._loading:
            self.changed.emit(self.name, {key: value})

    def set_sources(self, _sources, _current=""):
        """(Kept for older callers: voices are now chosen in the voice picker.)"""

    def update_rec(self, rec: dict, design: dict):
        self._loading = True
        g = rec.get("gender", "unknown")
        self.gbtn.get(g, self.gbtn["unknown"]).setChecked(True)
        self.avatar.set_name(self.name, 48, "" if self.name == NARRATOR else theme.char_colour(rec))
        j = self.cb_role.findData(rec.get("role", "")) if rec.get("role") else 0
        self.cb_role.setCurrentIndex(max(0, j))
        for cb, key in ((self.cb_accent, "accent"), (self.cb_age, "age_override"),
                        (self.cb_pitch, "pitch"), (self.cb_style, "style")):
            j = cb.findData(rec.get(key, ""))
            if j >= 0:
                cb.setCurrentIndex(j)
        override = rec.get("voice_override", "")
        preset = self.presets.get(rec.get("preset") or "") if not override else None
        custom = not override and not preset
        self.custom_row.setVisible(custom)
        self.cb_accent.setEnabled(not override)
        self.cb_age.setEnabled(not override)
        self.btn_moods.setEnabled(not override)       # your own / Windows voice: no moods
        own = "" if rec.get("age_override") else (design or {}).get("age", "")
        self.cb_age.setItemText(0, f"Voice's own age ({own})" if own else "Voice's own age")
        self.cb_accent.setItemText(0, "Custom accent: none" if custom else "Voice's own accent")

        # voice card
        if override.startswith("sapi:"):
            self.lbl_voice.setText(override.split(":", 1)[1].replace("Microsoft ", "")
                                   .replace(" Desktop", ""))
            self.lbl_kind.setText("Windows voice (no emotions)")
            self.lbl_tags.setText("")
        elif override.startswith("vs:"):
            self.lbl_voice.setText("Your VoiceStudio voice")
            self.lbl_kind.setText("A voice from your own VoiceStudio library")
            self.lbl_tags.setText(override[3:])
        elif preset:
            cat = CATEGORY[category_of(preset)]
            self.lbl_voice.setText(preset.get("name", ""))
            self.lbl_kind.setText(f"{cat['title']}: {cat['kind'].lower()}. Best for "
                                  f"{', '.join(preset.get('best_for') or cat['best_for'])}."
                                  + (f"\n{preset['note']}" if preset.get("note") else ""))
            self.lbl_tags.setText(voices.instruct(design) if design else "")
        else:
            self.lbl_voice.setText("Custom voice")
            self.lbl_kind.setText("Made from the settings below")
            self.lbl_tags.setText(voices.instruct(design) if design else "")

        auto = (" (guessed)" if rec.get("gender_auto") and g != "unknown"
                and self.name != NARRATOR else "")
        gname = {"female": "female", "male": "male"}.get(g, "gender unknown")
        n = int(rec.get("lines", 0))
        role = rec.get("role", "")
        self.lbl_sub.setText(f"{gname.capitalize()}{auto}, {n} line{'s' if n != 1 else ''}" +
                             (f", {role}" if role else ""))
        self._loading = False


class CharactersView(QWidget):
    changed = Signal(str, dict)
    voice_chosen = Signal(str, str)
    pick_voice = Signal(str)
    role_changed = Signal(str, str)
    gender_changed = Signal(str, str)
    reroll = Signal(str)
    preview = Signal(str)
    preview_moods = Signal(str)
    reassign_all = Signal()
    refresh = Signal()
    menu = Signal(str, object)
    group_changed = Signal(str)
    merge_tagged = Signal()              # merge "X (thinking)" records into X

    def __init__(self, group: str = "voice"):
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
        self.presets: dict[str, dict] = {}
        self.group = group
        self._chars: dict[str, dict] = {}
        self._designs: dict[str, dict] = {}

    def install_toolbar(self, header):
        self.cb_group = QComboBox()
        for key, text in GROUPS:
            self.cb_group.addItem(text, key)
        self.cb_group.setCurrentIndex(max(0, self.cb_group.findData(self.group)))
        self.cb_group.currentIndexChanged.connect(self._group_chosen)
        self.b_merge_tags = button("Merge mood duplicates", "merge", "ghost",
                                   tip="Characters like “Zalton (thinking)” become Zalton, "
                                       "said in that mood",
                                   slot=self.merge_tagged.emit)
        self.b_merge_tags.hide()
        self._update_merge_button()
        header.add(self.b_merge_tags, self.cb_group, None,
                   button("Refresh voices", "refresh-cw", "ghost",
                          "Reload your VoiceStudio and Windows voices", slot=self.refresh.emit),
                   button("Re-assign voices", "sparkles", tip="Give every character a fresh "
                          "voice that suits their gender and role", slot=self.reassign_all.emit))

    def _update_merge_button(self):
        if not hasattr(self, "b_merge_tags"):
            return
        from ..emotion import split_speaker_tag
        n = sum(1 for k in self._chars if k != NARRATOR and split_speaker_tag(k)[0] != k)
        self.b_merge_tags.setText(f"Merge mood duplicates ({n})")
        self.b_merge_tags.setVisible(n > 0)

    def _group_chosen(self, _i):
        self.group = self.cb_group.currentData()
        self.group_changed.emit(self.group)
        self.load(self._chars, self._designs)

    def set_sources(self, presets: list[dict], vs_profiles: list[dict], sapi_voices: list[str]):
        """Voices that aren't presets (for the picker's "Other" section) and the presets
        (for the voice cards)."""
        self.presets = {p["id"]: p for p in presets}
        src: list[tuple[str, str | None]] = [("Custom voice (set age / pitch on the card)",
                                              CUSTOM)]
        mine = [p for p in vs_profiles if not str(p.get("name", "")).startswith("VNR - ")]
        if mine:
            src += [("My VoiceStudio voices", None)]
            src += [(p["name"], "vs:" + p["id"]) for p in mine]
        if sapi_voices:
            src += [("Windows voices", None)]
            src += [(v.split(":", 1)[1].replace("Microsoft ", "").replace(" Desktop", ""), v)
                    for v in sapi_voices]
        self.sources = src
        if self._chars:                      # voice cards and groups depend on the presets
            self.load(self._chars, self._designs)

    def group_of(self, name: str, rec: dict) -> str:
        if name == NARRATOR:
            return "Narrator"
        ov = rec.get("voice_override", "")
        if ov:
            return "Your own and Windows voices"
        p = self.presets.get(rec.get("preset") or "")
        if not p:
            return "Custom voices"
        return CATEGORY[category_of(p)]["title"]

    def _row(self, name: str, rec: dict, design: dict) -> QWidget:
        av = Avatar(name, 30)
        av.set_name(name, 30, "" if name == NARRATOR else theme.char_colour(rec))
        ov = rec.get("voice_override", "")
        voice = ("Windows voice" if ov.startswith("sapi:") else "VoiceStudio voice"
                 if ov.startswith("vs:") else (design.get("label") if rec.get("preset")
                                               else "Custom voice") if design else "")
        if rec.get("role"):
            voice = f"{voice}, {rec['role']}" if voice else rec["role"]
        row = ListRow("Narrator" if name == NARRATOR else name, voice or "",
                      str(int(rec.get("lines", 0))), av)
        # Hear the voice without opening the card.
        row.trail.addWidget(icon_button("play", f"Hear {'the narrator' if name == NARRATOR else name}",
                                        lambda _=False, n=name: self.preview.emit(n), size=14))
        return row

    def load(self, chars: dict[str, dict], designs: dict[str, dict]):
        self._chars, self._designs = chars, designs
        self._update_merge_button()
        keep = self.md.current()
        self.md.clear_all()
        self.cards.clear()
        names = list(chars)
        lines = lambda n: -int(chars[n].get("lines", 0))      # noqa: E731
        if self.group == "name":
            names.sort(key=lambda n: (n != NARRATOR, n.lower()))
        elif self.group == "lines":
            names.sort(key=lambda n: (n != NARRATOR, lines(n)))
        elif self.group == "colour":                 # like the game's own name colours
            names.sort(key=lambda n: (n != NARRATOR, _hue(theme.char_colour(chars[n])),
                                      lines(n)))
        else:
            order = ["Narrator"] + [c["title"] for c in CATEGORIES] + \
                ["Custom voices", "Your own and Windows voices"]
            names.sort(key=lambda n: (order.index(self.group_of(n, chars[n])), lines(n)))
        last = None
        for name in names:
            if self.group == "voice":
                gname = self.group_of(name, chars[name])
                if gname != last:
                    self.md.add_header(gname)
                    last = gname
            self._add(name, chars[name], designs.get(name, {}))
        if keep in self.cards:
            self.md.select(keep)

    def _add(self, name, rec, design):
        card = CharacterCard(name, rec, self.presets)
        card.update_rec(rec, design)
        card.changed.connect(self.changed.emit)
        card.voice_chosen.connect(self.voice_chosen.emit)
        card.pick_voice.connect(self.pick_voice.emit)
        card.role_changed.connect(self.role_changed.emit)
        card.gender_changed.connect(self.gender_changed.emit)
        card.reroll.connect(self.reroll.emit)
        card.preview.connect(self.preview.emit)
        card.preview_moods.connect(self.preview_moods.emit)
        card.menu.connect(self.menu.emit)
        self.md.add(name, self._row(name, rec, design), card,
                    f"{name} {rec.get('role', '')} {self.group_of(name, rec)}")
        self.cards[name] = card

    def ensure(self, name: str, rec: dict, design: dict):
        self._chars.setdefault(name, rec)
        self._designs[name] = design
        self._update_merge_button()
        card = self.cards.get(name)
        if card:
            card.update_rec(rec, design)
            self.md.set_row(name, self._row(name, rec, design))
            return
        self._add(name, rec, design)

    def select(self, name: str):
        self.md.select(name)
