from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from totstats.shared import paths


def ensure_installed() -> bool:
    if not paths.is_frozen():
        return False
    target = paths.install_exe()
    try:
        if Path(sys.executable).resolve() == target.resolve():
            return False
    except OSError:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, target)
    subprocess.Popen([str(target), *sys.argv[1:]], creationflags=subprocess.CREATE_NO_WINDOW)
    return True


def schedule_uninstall() -> None:
    install_dir = paths.install_dir()
    if not paths.is_frozen() or not install_dir.exists():
        return
    subprocess.Popen(
        f'ping -n 3 127.0.0.1 >nul && rmdir /s /q "{install_dir}"',
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
