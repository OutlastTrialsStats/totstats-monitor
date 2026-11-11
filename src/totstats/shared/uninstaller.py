from __future__ import annotations

import shutil
import subprocess

from totstats.shared import paths

UNINSTALLER = "unins000.exe"


def start() -> bool:
    """Open the Inno Setup uninstaller that sits next to the executable (Windows only).

    The uninstaller asks for confirmation, closes this app itself and removes the program, the
    autostart entry and the data folder. Nothing here changes before the user has confirmed.
    """
    uninstaller = paths.app_executable().parent / UNINSTALLER
    if not (paths.is_windows() and uninstaller.is_file()):
        return False
    subprocess.Popen([str(uninstaller)], close_fds=True)
    return True


def remove_data() -> None:
    """Linux has no installer: remove settings, log and cache. The binary is the user's to delete."""
    shutil.rmtree(paths.data_dir(), ignore_errors=True)
