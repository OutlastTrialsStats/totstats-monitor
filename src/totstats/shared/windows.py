from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
from pathlib import Path

SM_CXSMICON = 49
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
PER_MONITOR_AWARE = 2
PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
SMALL_ICON = 16

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = (
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    wintypes.INT,
    wintypes.INT,
    wintypes.UINT,
)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)


def enable_dpi_awareness() -> None:
    with contextlib.suppress(AttributeError):
        if user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2):
            return
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(PER_MONITOR_AWARE)


def small_icon_size() -> int:
    return user32.GetSystemMetrics(SM_CXSMICON) or SMALL_ICON


def load_icon(path: Path, size: int) -> int | None:
    return user32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE) or None
