from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from totstats.presence.live_api import LivePresenceApi, LivePresenceResult
from totstats.shared.applog import AppLog

if TYPE_CHECKING:
    from totstats.presence.service import PresenceService

POLL_INTERVAL = 10.0
IDLE_WAIT = 5.0
RETRY_AFTER_FALLBACK = 60.0
RETRY_AFTER_MAX = 900.0
UNKNOWN_RETRY = 600.0
NETWORK_BACKOFF = (15.0, 30.0, 60.0, 120.0, 300.0)


@dataclass(frozen=True)
class LiveTarget:
    profile_id: str
    generation: int
    sample_hold: float = 0.0


class LivePresencePoller:
    def __init__(
        self,
        api: LivePresenceApi,
        source: PresenceService,
        log: AppLog,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._source = source
        self._log = log
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._due = 0.0
        self._blocked_until = 0.0
        self._poked = False
        self._failures = 0
        self._warned: set[str] = set()

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="presence-live", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 3.0) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        self._stop.set()
        self._wake.set()
        worker.join(timeout)
        if worker.is_alive():
            self._log.warning("Live presence worker did not stop in time")

    def poke(self) -> None:
        with self._lock:
            self._poked = True
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                wait = self.step()
            except Exception as exc:
                self._warn_once("crash", f"Live presence failed: {exc}")
                wait = POLL_INTERVAL
            self._wake.wait(wait)

    def step(self) -> float:
        target = self._source.live_target()
        if target is None:
            return IDLE_WAIT

        now = self._monotonic()
        with self._lock:
            wait = self._wait(now, now + target.sample_hold)
            if wait > 0.0:
                return wait
            self._poked = False

        result = self._api.get(target.profile_id)
        now = self._monotonic()
        delay, blocked = self._handle(result, target)
        with self._lock:
            self._due = now + delay
            if blocked:
                self._blocked_until = self._due
            return self._wait(now, 0.0)

    def _handle(self, result: LivePresenceResult, target: LiveTarget) -> tuple[float, bool]:
        if result.ok:
            self._recovered()
            self._source.on_sample(result.sample, target.generation)
            return POLL_INTERVAL, False
        if result.queued:
            self._recovered()
            return result.retry_after or POLL_INTERVAL, False

        status = result.status
        if status in (429, 503):
            delay = RETRY_AFTER_FALLBACK if result.retry_after is None else result.retry_after
            delay = min(max(delay, POLL_INTERVAL), RETRY_AFTER_MAX)
            self._warn_once("throttled", f"Live presence busy: HTTP {status}, retrying in {delay:.0f}s")
            return delay, True
        if status in (400, 404):
            self._warn_once(
                "gone",
                f"Live presence unavailable: HTTP {status}, profile private or unknown, "
                f"retrying in {UNKNOWN_RETRY / 60:.0f} min",
            )
            return UNKNOWN_RETRY, True

        delay = NETWORK_BACKOFF[min(self._failures, len(NETWORK_BACKOFF) - 1)]
        self._failures += 1
        self._warn_once("network", f"Live presence unreachable: {result.error}, retrying in {delay:.0f}s")
        return delay, True

    def _wait(self, now: float, not_before: float) -> float:
        due = min(self._due, now) if self._poked else self._due
        return max(0.0, max(due, not_before, self._blocked_until) - now)

    def _warn_once(self, kind: str, message: str) -> None:
        if kind in self._warned:
            self._log.debug(message)
        else:
            self._warned.add(kind)
            self._log.warning(message)

    def _recovered(self) -> None:
        self._failures = 0
        if self._warned:
            self._warned.clear()
            self._log.info("Live presence back")
