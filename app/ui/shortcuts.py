"""Keyboard shortcuts sheet (F1): every hotkey with its current key."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from .components import Dialog, kbd, label

# Keys that only work inside VN Reader's window (not changeable).
APP_KEYS = [("Ctrl+1 … Ctrl+7", "Go to a page"), ("Ctrl+,", "Settings"),
            ("Ctrl+B", "Show or hide the sidebar"), ("Ctrl+I", "Show or hide the inspector"),
            ("Ctrl+Return", "Start or stop reading"), ("Ctrl+Shift+S", "Game setup"),
            ("F1", "This list")]


def rows(hotkeys: dict[str, str], labels: dict[str, str]) -> list[tuple[str, str]]:
    """(action, key) for the global hotkeys, in the order Settings shows them."""
    return [(text, hotkeys.get(action, "") or "Not set") for action, text in labels.items()]


class ShortcutsDialog(Dialog):
    def __init__(self, parent, hotkeys: dict[str, str], labels: dict[str, str], on_change=None):
        super().__init__(parent, "Keyboard shortcuts",
                         "These work while you're in the game. Change them in Settings → "
                         "Hotkeys.", "keyboard")
        grid_w = QWidget()
        grid_w.setObjectName("transparent")
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        r = 0
        for text, key in rows(hotkeys, labels):
            grid.addWidget(label(text), r, 0)
            grid.addWidget(kbd(key), r, 1)
            r += 1
        grid.addWidget(label("In VN Reader's window", "section"), r, 0, 1, 2)
        r += 1
        for key, text in APP_KEYS:
            grid.addWidget(label(text), r, 0)
            grid.addWidget(kbd(key), r, 1)
            r += 1
        grid.setColumnStretch(0, 1)
        self.content.addWidget(grid_w)
        if on_change is not None:
            b = self.add_button("Change shortcuts…", accept=False)
            b.clicked.disconnect()
            b.clicked.connect(lambda: (self.reject(), on_change()))
        self.add_button("Done", "primary").setDefault(True)
