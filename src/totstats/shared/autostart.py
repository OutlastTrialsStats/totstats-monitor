from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

from totstats import APP_NAME
from totstats.shared import paths

RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
LEGACY_NAMES = ("OutlastTrialsMonitor",)


def _command() -> str:
    if paths.is_frozen():
        return f'"{paths.install_exe()}" --silent'
    python = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(python):
        python = sys.executable
    return f'"{python}" "{Path(sys.argv[0]).resolve()}" --silent'


def _open(access: int) -> winreg.HKEYType:
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def _has(name: str) -> bool:
    try:
        with _open(winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, name)
    except OSError:
        return False
    return True


def _delete(name: str) -> bool:
    try:
        with _open(winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        return False
    return True


def is_enabled() -> bool:
    return _has(APP_NAME)


def migrate_legacy() -> bool:
    found = [name for name in LEGACY_NAMES if _has(name)]
    if not found:
        return False
    enable()
    for name in found:
        _delete(name)
    return True


def enable() -> bool:
    try:
        with _open(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
    except OSError:
        return False
    return True


def disable() -> bool:
    removed = _delete(APP_NAME)
    for name in LEGACY_NAMES:
        removed = _delete(name) or removed
    return removed
