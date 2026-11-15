"""Voices page: the editable voice presets, as a master–detail editor."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QFrame,
                               QVBoxLayout, QWidget)

from .. import voices
from ..presets import CATEGORIES, best_for, category_of
from ..voice_bank import VIBES
from . import theme
from PySide6.QtGui import QColor, QFont

from .components import (ColorDot, EmptyState, SearchField, button, icon_button, label,
                         section)


# ----------------------------------------------------------------- helpers
def shrinkable(c: QComboBox) -> QComboBox:
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(6)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return c


def nice(tag: str) -> str:
    if not tag:
        return "Normal"
    return tag.replace(" accent", "").replace(" pitch", "").capitalize()


def combo(items: list[str], current: str, first: tuple[str, str] | None = None) -> QComboBox:
    c = shrinkable(QComboBox())
    if first:
        c.addItem(first[0], first[1])
    for it in items:
        c.addItem(nice(it), it)
    i = c.findData(current)
    if i < 0 and current:                  # an old value not offered any more: still shown
        c.addItem(nice(current), current)
        i = c.count() - 1
    c.setCurrentIndex(max(0, i))
    return c


def gender_toggle(parent: QWidget, genders=("female", "male"), on_click=None):
    """Segmented ♀ / ♂ (/ ?) control. Returns (layout, {gender: button})."""
    box = QWidget()
    box.setObjectName("segmented")
    box.setAttribute(Qt.WA_StyledBackground, True)
    box.setFixedHeight(34)
    inner = QHBoxLayout(box)
    inner.setContentsMargins(3, 3, 3, 3)
    inner.setSpacing(2)
    grp = QButtonGroup(parent)
    btns = {}
    names = {"female": "Female", "male": "Male", "unknown": "Unsure"}
    icons = {"female": "venus", "male": "mars", "unknown": "circle-help"}
    for g in genders:
        b = QPushButton(names.get(g, g))
        b.setObjectName("seg")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setIcon(theme.icon(icons.get(g, "circle"), theme.TEXT_2, 14, active=theme.TEXT))
        if on_click:
            b.clicked.connect(lambda _=False, gg=g: on_click(gg))
        grp.addButton(b)
        inner.addWidget(b)
        btns[g] = b
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(box)
    lay.addStretch(1)
    return lay, btns


def form_label(text: str) -> QLabel:
    lb = label(text, "muted")
    lb.setMinimumWidth(84)
    return lb


class ListRow(QWidget):
    """A list row: optional leading widget, title, mono subtitle, trailing mono text."""

    def __init__(self, title: str, subtitle: str = "", trailing: str = "", lead=None):
        super().__init__()
        self.setObjectName("transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(10)
        if lead is not None:
            lay.addWidget(lead)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.t = QLabel(title)
        self.t.setObjectName("strong")
        self.t.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.s = label(subtitle, "footnote")
        self.s.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(self.t)
        if subtitle:
            col.addWidget(self.s)
        lay.addLayout(col, 1)
        self.r = label(trailing, "number")
        lay.addWidget(self.r)
        self.trail = QHBoxLayout()           # extra trailing widgets (e.g. a play button)
        self.trail.setSpacing(2)
        lay.addLayout(self.trail)


class MasterDetail(QSplitter):
    """Searchable list on the left, the selected item's editor on the right."""
    selected = Signal(object)

    def __init__(self, search_placeholder: str = "Search", empty=("list", "Nothing here", "")):
        super().__init__(Qt.Horizontal)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(1)
        left = QWidget()
        left.setObjectName("listPane")           # the splitter draws the dividing line
        left.setAttribute(Qt.WA_StyledBackground, True)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(10)
        self.search = SearchField(search_placeholder)
        self.search.textChanged.connect(self._filter)
        ll.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("plainList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._changed)
        ll.addWidget(self.list, 1)
        left.setMinimumWidth(230)
        self.addWidget(left)
        self.stack = QStackedWidget()
        self.empty = EmptyState(*empty)
        self.stack.addWidget(self.empty)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(self.stack)
        self.addWidget(sc)
        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.setSizes([280, 700])
        self._details: dict = {}
        self._factories: dict = {}       # key -> callable building the detail on first use
        self._items: dict = {}
        if theme.notifier is not None:
            theme.notifier.changed.connect(lambda _m: self.retheme())

    def clear_all(self):
        self.list.clear()
        for w in self._details.values():
            self.stack.removeWidget(w)
            w.deleteLater()
        self._details.clear()
        self._factories.clear()
        self._items.clear()
        self.stack.setCurrentWidget(self.empty)

    def add_header(self, text: str):
        """A group heading in the list (sentence case, secondary colour)."""
        t = text.strip()
        it = QListWidgetItem(t[:1].upper() + t[1:])
        it.setFlags(Qt.NoItemFlags)
        it.setData(Qt.UserRole, None)
        it.setData(Qt.UserRole + 2, "header")
        it.setForeground(QColor(theme.TEXT_2))
        f = theme.sans(8.5, QFont.DemiBold)
        it.setFont(f)
        it.setSizeHint(QSize(100, 30))
        self.list.addItem(it)

    def retheme(self):
        """Group headings are items, not widgets: give them the new theme's colour."""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole + 2) == "header":
                it.setForeground(QColor(theme.TEXT_2))

    def add(self, key, row: QWidget, detail, search_text: str = ""):
        """detail: the editor widget, or a callable that builds it when first shown (long
        lists stay quick to load)."""
        it = QListWidgetItem()
        it.setData(Qt.UserRole, key)
        it.setData(Qt.UserRole + 1, (search_text or str(key)).lower())
        it.setSizeHint(QSize(100, 48))
        self.list.addItem(it)
        self.list.setItemWidget(it, row)
        if callable(detail) and not isinstance(detail, QWidget):
            self._factories[key] = detail
        else:
            self.stack.addWidget(detail)
            self._details[key] = detail
        self._items[key] = it

    def set_row(self, key, row: QWidget):
        it = self._items.get(key)
        if it is not None:
            self.list.setItemWidget(it, row)

    def detail(self, key):
        w = self._details.get(key)
        if w is None and key in self._factories:
            w = self._factories.pop(key)()
            self.stack.addWidget(w)
            self._details[key] = w
        return w

    def select(self, key):
        it = self._items.get(key)
        if it is not None:
            self.list.setCurrentItem(it)

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _changed(self, it, _prev):
        key = it.data(Qt.UserRole) if it else None
        w = self.detail(key) if key is not None else None
        self.stack.setCurrentWidget(w if w is not None else self.empty)
        self.selected.emit(key)

    def _filter(self, q: str):
        q = q.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            key = it.data(Qt.UserRole)
            if key is None:
                it.setHidden(bool(q))
            else:
                it.setHidden(bool(q) and q not in (it.data(Qt.UserRole + 1) or ""))


# ------------------------------------------------------------------ preset editor
class PresetCard(QWidget):
    changed = Signal(str, dict)
    reroll = Signal(str)
    preview = Signal(str)
    duplicate = Signal(str)
    delete = Signal(str)

    def __init__(self, p: dict):
        super().__init__()
        self.setObjectName("page")
        self.pid = p["id"]
        self._loading = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.ed_name = QLineEdit(p.get("name", ""))
        self.ed_name.setObjectName("titleEdit")
        self.ed_name.setToolTip("Rename this voice")
        self.ed_name.editingFinished.connect(
            lambda: self._emit({"name": self.ed_name.text().strip() or "Voice"}))
        top.addWidget(self.ed_name, 1)
        top.addWidget(button("Preview", "play", tip="Hear this voice",
                             slot=lambda: self.preview.emit(self.pid)))
        top.addWidget(icon_button("sparkles", "Different person, same settings",
                                  lambda: self.reroll.emit(self.pid)))
        top.addWidget(icon_button("copy", "Duplicate", lambda: self.duplicate.emit(self.pid)))
        top.addWidget(icon_button("trash-2", "Delete preset", lambda: self.delete.emit(self.pid)))
        lay.addLayout(top)
        self.lbl_note = label("", "mono", wrap=True)
        lay.addWidget(self.lbl_note)

        lay.addWidget(section("Voice design"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        g_lay, self.gbtn = gender_toggle(self, on_click=lambda g: self._emit({"gender": g}))
        form.addRow(form_label("Gender"), g_lay)
        self.cb_age = combo(voices.ADULT_AGES, p.get("age", ""))
        self.cb_pitch = combo(voices.PITCHES, p.get("pitch", ""))
        self.cb_accent = combo(voices.ACCENTS, p.get("accent", ""), ("Any accent", ""))
        self.cb_style = combo(["whisper"], p.get("style", ""), ("Normal", ""))
        self.cb_vibe = combo(VIBES, p.get("vibe", ""), ("Neutral", ""))
        self.cb_vibe.setToolTip("How the voice talks: its reference clip is recorded in this "
                                "mood")
        self.cb_cat = shrinkable(QComboBox())
        for c in CATEGORIES:
            self.cb_cat.addItem(c["title"], c["key"])
        self.cb_cat.setCurrentIndex(max(0, self.cb_cat.findData(category_of(p))))
        self.cb_cat.setMaximumWidth(320)
        self.cb_cat.setToolTip("The group this voice is listed under (Voices and Cast pages)")
        self.cb_cat.currentIndexChanged.connect(
            lambda _=0: self._emit({"category": self.cb_cat.currentData()}))
        form.addRow(form_label("Category"), self.cb_cat)
        self.ed_best = QLineEdit(", ".join(best_for(p)))
        self.ed_best.setMaximumWidth(320)
        self.ed_best.setPlaceholderText("e.g. heroine, childhood friend")
        self.ed_best.setToolTip("Roles this voice suits, separated by commas: the Cast page "
                                "suggests it for characters with these roles")
        self.ed_best.editingFinished.connect(lambda: self._emit(
            {"best_for": [r.strip() for r in self.ed_best.text().split(",") if r.strip()]}))
        form.addRow(form_label("Best for"), self.ed_best)
        for cb, key, text in ((self.cb_age, "age", "Age"), (self.cb_pitch, "pitch", "Pitch"),
                              (self.cb_accent, "accent", "Accent"),
                              (self.cb_style, "style", "Style"),
                              (self.cb_vibe, "vibe", "Vibe")):
            cb.setMaximumWidth(320)
            cb.currentIndexChanged.connect(lambda _=0, c=cb, k=key: self._emit({k: c.currentData()}))
            form.addRow(form_label(text), cb)
        lay.addLayout(form)
        lay.addWidget(label("Every character using this preset changes with it. The dice "
                            "button keeps the settings but picks a different person.", "faint",
                            wrap=True))
        lay.addStretch(1)
        self._loading = False
        self.update_preset(p, False)

    def _emit(self, changes: dict):
        if not self._loading:
            self.changed.emit(self.pid, changes)

    def update_preset(self, p: dict, ready: bool):
        self._loading = True
        g = p.get("gender", "female")
        self.gbtn.get(g, self.gbtn["female"]).setChecked(True)
        if self.ed_name.text() != p.get("name"):
            self.ed_name.setText(p.get("name", ""))
        for cb, key in ((self.cb_age, "age"), (self.cb_pitch, "pitch"),
                        (self.cb_accent, "accent"), (self.cb_style, "style"),
                        (self.cb_vibe, "vibe")):
            i = cb.findData(p.get(key, ""))
            if i >= 0:
                cb.setCurrentIndex(i)
        i = self.cb_cat.findData(category_of(p))
        if i >= 0:
            self.cb_cat.setCurrentIndex(i)
        if not self.ed_best.hasFocus():
            self.ed_best.setText(", ".join(best_for(p)))
        state = "voice ready" if ready else "made on first use"
        note = p.get("note") or ""
        self.lbl_note.setText(f"{voices.instruct(p)}  ·  {state}" + (f"\n{note}" if note else ""))
        self._loading = False


class PresetsView(QWidget):
    changed = Signal(str, dict)
    reroll = Signal(str)
    preview = Signal(str)
    duplicate = Signal(str)
    delete = Signal(str)
    add = Signal(str)              # gender
    reset = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.md = MasterDetail("Search voices", ("audio-lines", "Pick a voice",
                                                "Choose a preset on the left to edit it."))
        lay.addWidget(self.md)
        self.cards: dict[str, PresetCard] = {}
        self._presets: dict[str, dict] = {}

    def install_toolbar(self, header):
        header.add(button("Reset to built-in", "rotate-ccw", "ghost",
                          "Restore the built-in presets", slot=self.reset.emit),
                   None,
                   button("Add female voice", "plus", tip="Add a female voice",
                          slot=lambda: self.add.emit("female")),
                   button("Add male voice", "plus", tip="Add a male voice",
                          slot=lambda: self.add.emit("male")))

    def _row(self, p: dict, ready: bool) -> QWidget:
        dot = ColorDot(theme.gender_color(p.get("gender")), 8)
        sub = (f"{nice(p.get('age', ''))}, {nice(p.get('pitch', '')).lower()}, "
               f"{nice(p.get('accent', '').replace(' accent', '')) or 'any accent'}")
        row = ListRow(p.get("name", ""), sub, "", dot)
        if ready:
            row.r.setText("Ready")
            row.r.setToolTip("This voice is saved in VoiceStudio and starts at once")
        return row

    def load(self, presets: list[dict], ready: set[str]):
        keep = self.md.current()
        self.md.clear_all()
        self.cards.clear()
        self._presets.clear()
        for c in CATEGORIES:
            items = [p for p in presets if category_of(p) == c["key"]]
            if not items:
                continue
            self.md.add_header(f"{c['title']} · {', '.join(c['best_for'])}")
            for p in items:
                self._presets[p["id"]] = p
                self.md.add(p["id"], self._row(p, p["id"] in ready),
                            lambda pid=p["id"], r=p["id"] in ready: self._card(pid, r),
                            f"{p.get('name', '')} {p.get('note', '')} {p.get('accent', '')} "
                            f"{c['title']} {' '.join(best_for(p))}".lower())
        self.md.select(keep if keep in self._presets else (presets[0]["id"] if presets else None))

    def _card(self, pid: str, ready: bool) -> "PresetCard":
        p = self._presets[pid]
        card = PresetCard(p)
        card.changed.connect(self.changed.emit)
        card.reroll.connect(self.reroll.emit)
        card.preview.connect(self.preview.emit)
        card.duplicate.connect(self.duplicate.emit)
        card.delete.connect(self.delete.emit)
        card.update_preset(p, ready)
        self.cards[pid] = card
        return card

    def update(self, p: dict, ready: bool):
        c = self.cards.get(p["id"])
        if c:
            c.update_preset(p, ready)
            self.md.set_row(p["id"], self._row(p, ready))
