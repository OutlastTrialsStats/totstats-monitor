from __future__ import annotations

import os
import sys
from pathlib import Path

from totstats import APP_NAME
from totstats.shared import proton


def is_windows() -> bool:
    return sys.platform == "win32"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_executable() -> Path:
    return Path(sys.executable)


def local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    return Path(raw) if raw else Path.home() / "AppData" / "Local"


def xdg_dir(variable: str, default: str) -> Path:
    raw = os.environ.get(variable)
    return Path(raw) if raw else Path.home() / default


def data_dir() -> Path:
    if is_windows():
        return local_appdata() / APP_NAME
    return xdg_dir("XDG_DATA_HOME", ".local/share") / APP_NAME


def game_logs_dir() -> Path:
    if is_windows():
        return local_appdata() / "OPP" / "Saved" / "Logs"
    return proton.logs_dir(Path.home())


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parents[3])


def icon_path() -> Path:
    return resource_root() / "assets" / "icon.ico"


def logo_path() -> Path:
    return resource_root() / "assets" / "logo.png"


def locales_dir() -> Path:
    return resource_root() / "locales"


def user_locales_dir() -> Path:
    return data_dir() / "locales"
