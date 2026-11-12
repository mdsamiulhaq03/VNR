"""System-wide hotkeys via RegisterHotKey (works while the game has focus, no admin)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, Qt
from PySide6.QtGui import QKeySequence

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_SPECIAL = {
    Qt.Key_Space: 0x20, Qt.Key_Pause: 0x13, Qt.Key_ScrollLock: 0x91, Qt.Key_Insert: 0x2D,
    Qt.Key_Delete: 0x2E, Qt.Key_Home: 0x24, Qt.Key_End: 0x23, Qt.Key_PageUp: 0x21,
    Qt.Key_PageDown: 0x22, Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27,
    Qt.Key_Down: 0x28, Qt.Key_Print: 0x2C, Qt.Key_Backspace: 0x08, Qt.Key_Tab: 0x09,
    Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D, Qt.Key_Escape: 0x1B,
    Qt.Key_Minus: 0xBD, Qt.Key_Equal: 0xBB, Qt.Key_Comma: 0xBC, Qt.Key_Period: 0xBE,
    Qt.Key_Slash: 0xBF, Qt.Key_Semicolon: 0xBA, Qt.Key_BracketLeft: 0xDB,
    Qt.Key_BracketRight: 0xDD, Qt.Key_Backslash: 0xDC, Qt.Key_Apostrophe: 0xDE,
    Qt.Key_QuoteLeft: 0xC0,
}


def parse(seq: str) -> tuple[int, int] | None:
    """'Ctrl+Shift+F9' -> (modifiers, virtual key). None if empty/unsupported."""
    ks = QKeySequence(seq)
    if ks.isEmpty():
        return None
    combo = ks[0]
    key = combo.key()
    mods = combo.keyboardModifiers()
    vk = None
    k = int(key.value) if hasattr(key, "value") else int(key)
    if Qt.Key_F1.value <= k <= Qt.Key_F24.value:
        vk = 0x70 + (k - Qt.Key_F1.value)
    elif ord("A") <= k <= ord("Z") or ord("0") <= k <= ord("9"):
        vk = k
    else:
        vk = _SPECIAL.get(key)
    if vk is None:
        return None
    m = 0
    if mods & Qt.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.AltModifier:
        m |= MOD_ALT
    if mods & Qt.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        m |= MOD_WIN
    return m, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, handler: Callable[[int], None]):
        super().__init__()
        self.handler = handler

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self.handler(int(msg.wParam))
                return True, 0
        return False, 0


class HotkeyManager:
    """Register named actions to key sequences. Hotkeys go to the given window handle."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._actions: dict[int, Callable[[], None]] = {}
        self._ids: list[int] = []
        self._filter = _Filter(self._fire)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def _fire(self, hk_id: int) -> None:
        fn = self._actions.get(hk_id)
        if fn:
            fn()

    def clear(self) -> None:
        for i in self._ids:
            user32.UnregisterHotKey(self.hwnd, i)
        self._ids.clear()
        self._actions.clear()

    def set(self, bindings: dict[str, tuple[str, Callable[[], None]]]) -> list[str]:
        """bindings: action -> (key sequence, callback). Returns error messages."""
        self.clear()
        errors = []
        for n, (action, (seq, fn)) in enumerate(bindings.items(), start=1):
            if not seq:
                continue
            parsed = parse(seq)
            if not parsed:
                errors.append(f"{action}: can't use '{seq}' as a hotkey")
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(self.hwnd, n, mods | MOD_NOREPEAT, vk):
                self._ids.append(n)
                self._actions[n] = fn
            else:
                errors.append(f"{action}: '{seq}' is already used by another app")
        return errors
