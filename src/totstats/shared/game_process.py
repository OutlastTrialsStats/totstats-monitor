from __future__ import annotations

import psutil

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
        for process in psutil.process_iter(["name", "exe"]):
            name = process.info.get("name") or ""
            exe = process.info.get("exe") or ""
            if MARKER in name or MARKER in exe:
                return process
        return None
