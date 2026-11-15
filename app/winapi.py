"""Small ctypes wrappers around the Win32 calls VN Reader needs."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as wt
from dataclasses import dataclass

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetWindow.restype = wt.HWND
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
user32.PrintWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint]

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]

dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

GW_OWNER = 4
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    exe: str          # e.g. "MyGame.exe"

    @property
    def label(self) -> str:
        return f"{self.title}  [{self.exe}]"


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def exe_path(hwnd: int) -> str:
    """Full path of the program that owns a window ("" if it can't be read)."""
    pid = _pid(hwnd)
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _exe(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def _cloaked(hwnd) -> bool:
    val = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    except OSError:
        return False
    return val.value != 0


def list_windows() -> list[WindowInfo]:
    """Visible, titled, top-level app windows (not our own)."""
    own = os.getpid()
    out: list[WindowInfo] = []

    def cb(hwnd, _lp):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and not _cloaked(hwnd)):
            title = _title(hwnd)
            pid = _pid(hwnd)
            if title and pid != own:
                out.append(WindowInfo(int(hwnd), title, _exe(pid)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(exe: str, title: str) -> WindowInfo | None:
    """Re-find a game window after restart: same exe (and title if possible)."""
    wins = list_windows()
    exe_l = (exe or "").lower()
    same_exe = [w for w in wins if exe_l and w.exe.lower() == exe_l]
    for w in same_exe:
        if w.title == title:
            return w
    if same_exe:
        return same_exe[0]
    for w in wins:
        if title and w.title == title:
            return w
    return None


user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
GA_ROOT = 2
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def window_at(x: int, y: int) -> WindowInfo | None:
    """The top-level app window under a screen point (physical px), e.g. the game under
    a box the user just drew. None for the desktop, the taskbar or our own windows."""
    hwnd = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root, buf, 256)
    if buf.value in _SHELL_CLASSES:
        return None
    pid = _pid(root)
    if pid == os.getpid():
        return None
    title = _title(root)
    return WindowInfo(int(root), title or "(untitled window)", _exe(pid))


def foreground_window() -> WindowInfo | None:
    """The app window the user is looking at (e.g. the game after we minimized). None for
    the desktop, the taskbar or our own windows."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    if buf.value in _SHELL_CLASSES:
        return None
    pid = _pid(hwnd)
    if pid == os.getpid():
        return None
    return WindowInfo(int(hwnd), _title(hwnd) or "(untitled window)", _exe(pid))


def foreground_kind() -> str:
    """What the user is looking at: "own" (VN Reader or its bubble), "shell" (desktop,
    taskbar), "" (nothing / minimized) or the app's exe name, e.g. "Code.exe"."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    if _pid(hwnd) == os.getpid():
        return "own"
    if user32.IsIconic(hwnd):
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    if buf.value in _SHELL_CLASSES:
        return "shell"
    return _exe(_pid(hwnd)) or "?"


def is_away(kind: str, game_exe: str) -> bool:
    """Is the user in another program than the game? (Our own windows don't count.)"""
    if not game_exe or kind in ("", "own"):
        return False
    return kind.lower() != game_exe.lower()


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def title_bar(hwnd: int, dark: bool, caption: int, text: int, border: int) -> None:
    """Native title bar in the app's theme (Windows 11: exact colours; Windows 10: dark
    or light only). Colours are 0xRRGGBB; DWM wants 0x00BBGGRR."""
    def bgr(c):
        return ((c & 0xFF) << 16) | (c & 0xFF00) | ((c >> 16) & 0xFF)
    on = 1 if dark else 0
    for attr, val in ((20, on), (19, on), (35, bgr(caption)), (36, bgr(text)),
                      (34, bgr(border))):
        v = ctypes.c_int(val)
        try:
            dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), attr, ctypes.byref(v),
                                         ctypes.sizeof(v))
        except OSError:
            pass


def dark_title_bar(hwnd: int, caption: int = 0x000000, text: int = 0xF5F5F7,
                   border: int = 0x38383A) -> None:
    """Black native title bar (kept for older callers)."""
    title_bar(hwnd, True, caption, text, border)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the client area in physical screen pixels."""
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    p = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y, r.right - r.left, r.bottom - r.top


user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.GetForegroundWindow.restype = wt.HWND
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wt.DWORD, ctypes.c_size_t]
user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
ADVANCE_KEYS = {"enter": 0x0D, "space": 0x20, "ctrl": 0x11, "right": 0x27, "down": 0x28}


def _key_lparam(vk: int, up: bool) -> int:
    scan = user32.MapVirtualKeyW(vk, 0)
    lp = 1 | (scan << 16)
    if up:
        lp |= (1 << 30) | (1 << 31)
    return lp


def send_advance(hwnd: int, how: str = "enter", click_at: tuple[int, int] | None = None) -> bool:
    """Make the game go to the next line: press a key or click inside its window.

    If the game is the active window we send real input (most reliable); otherwise we
    post the message straight to the game window so it works while you're elsewhere.
    """
    if not hwnd or not is_window(hwnd):
        return False
    foreground = int(user32.GetForegroundWindow() or 0) == int(hwnd)
    if how == "click":
        x, y = click_at or (0, 0)
        lp = (int(y) << 16) | (int(x) & 0xFFFF)
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
        return True
    vk = ADVANCE_KEYS.get(how, 0x0D)
    if foreground:
        scan = user32.MapVirtualKeyW(vk, 0)
        user32.keybd_event(vk, scan, 0, 0)
        user32.keybd_event(vk, scan, 2, 0)             # KEYEVENTF_KEYUP
    else:
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, _key_lparam(vk, False))
        user32.PostMessageW(hwnd, WM_KEYUP, vk, _key_lparam(vk, True))
    return True


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture_window(hwnd: int) -> Image.Image | None:
    """Capture a window's client area even if it's covered by other windows.

    Returns None if the capture failed or came back black (some games block it).
    """
    _l, _t, w, h = client_rect_screen(hwnd)
    if w <= 0 or h <= 0:
        return None
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = w, -h          # negative = top-down rows
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        rows = gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)
    if not ok or rows != h:
        return None
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    lo, hi = img.convert("L").getextrema()
    if hi <= 8:                 # all black: capture is blocked for this window
        return None
    return img


# ---- floating overlays (the control bubble) ----
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x11        # Windows 10 2004+: hidden from screenshots / BitBlt

user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.SetWindowDisplayAffinity.restype = wt.BOOL


def make_overlay(hwnd: int) -> bool:
    """Clicks on this window never take focus from the game, and screen captures (OCR)
    never see it. Returns False if capture exclusion isn't supported (older Windows)."""
    try:
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except (OSError, AttributeError):
        return False


HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]


def keep_topmost(hwnd: int) -> None:
    """Put the window back above others (games that make themselves top-most) without
    activating it."""
    user32.SetWindowPos(hwnd, wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
