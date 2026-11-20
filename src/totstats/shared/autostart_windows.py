from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

from totstats import APP_NAME
from totstats.shared import paths

RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    if paths.is_frozen():
        return f'"{paths.app_executable()}" --silent'
    python = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(python):
        python = sys.executable
    return f'"{python}" "{Path(sys.argv[0]).resolve()}" --silent'


def _open(access: int) -> winreg.HKEYType:
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def is_enabled() -> bool:
    try:
        with _open(winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False
    return True


def enable() -> bool:
    try:
        with _open(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
    except OSError:
        return False
    return True


def disable() -> bool:
    try:
        with _open(winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        return False
    return True
