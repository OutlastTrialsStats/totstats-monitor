from __future__ import annotations

import contextlib
import queue
from collections.abc import Callable

from totstats.shared.applog import AppLog


class UiQueue:
    def __init__(self, log: AppLog) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._log = log

    def post(self, fn: Callable[[], None]) -> None:
        self._queue.put(fn)

    def drain(self) -> None:
        with contextlib.suppress(queue.Empty):
            fn = self._queue.get(timeout=0.05)
            while True:
                try:
                    fn()
                except Exception as exc:
                    self._log.error(f"UI action failed: {exc}")
                fn = self._queue.get_nowait()
