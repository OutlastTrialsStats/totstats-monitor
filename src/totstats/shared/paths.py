from __future__ import annotations

import os
import sys
from pathlib import Path

from totstats import APP_NAME


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    return Path(raw) if raw else Path.home() / "AppData" / "Local"


def install_dir() -> Path:
    return local_appdata() / APP_NAME


def install_exe() -> Path:
    return install_dir() / f"{APP_NAME}.exe"


def game_logs_dir() -> Path:
    return local_appdata() / "OPP" / "Saved" / "Logs"


def icon_path() -> Path:
    root = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parents[3]
    return Path(root) / "assets" / "icon.ico"
