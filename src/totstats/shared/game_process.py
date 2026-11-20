from __future__ import annotations

import psutil

from totstats.shared import paths

MARKER = "TOTClient"


class GameProcessWatcher:
    def __init__(self) -> None:
        self._process: psutil.Process | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    @property
    def started_at(self) -> float | None:
        if self._process is None:
            return None
        try:
            return self._process.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def poll(self) -> bool:
        was_running = self.running
        if not self._alive():
            self._process = self._find()
        return self.running != was_running

    def _alive(self) -> bool:
        if self._process is None:
            return False
        try:
            return self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _find(self) -> psutil.Process | None:
        # Under Proton the process is the Wine loader and Linux truncates its name to 15
        # characters, so the first argument is the other place the game's name shows up. Only
        # that one: later arguments also hold the launcher and tools that merely mention the game.
        fields = ["name", "exe"] if paths.is_windows() else ["name", "cmdline"]
        for process in psutil.process_iter(fields):
            info = process.info
            if MARKER in (info.get("name") or "") or MARKER in (info.get("exe") or ""):
                return process
            cmdline = info.get("cmdline")
            if cmdline and MARKER in cmdline[0]:
                return process
        return None
