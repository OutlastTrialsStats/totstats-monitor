"""Polls our own live presence while the game runs and hands every sample to the service.

Runs on its own thread, presence-live, because a request can take seconds and neither the watcher
nor the Discord worker may wait on it. The service says whom to ask and when the answer is
obsolete; this module only decides when to ask and how long to back off.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from totstats.presence.live_api import LivePresenceApi, LivePresenceResult
from totstats.presence.parser import PresenceSample
from totstats.shared.applog import AppLog

POLL_INTERVAL = 10.0

# How often to look again while there is nobody to ask: no game, no profile id yet, a replay.
IDLE_WAIT = 5.0

RETRY_AFTER_FALLBACK = 60.0
RETRY_AFTER_MAX = 900.0
# The server remembers an unknown profile this long, so asking again sooner learns nothing.
GONE_BACKOFF = 600.0
NETWORK_BACKOFF = (15.0, 30.0, 60.0, 120.0, 300.0)

_GONE = (400, 404)
_THROTTLED = (429, 503)


@dataclass(frozen=True)
class LiveTarget:
    profile_id: str
    generation: int
    """The service's generation when the request went out; a sample from an older one is stale."""
    sample_hold: float = 0.0
    """Seconds from now until a sample would no longer be dropped as too close to a log event."""


class LiveSource(Protocol):
    def live_target(self) -> LiveTarget | None: ...

    def on_sample(self, sample: PresenceSample, generation: int) -> None: ...


class LivePresencePoller:
    def __init__(
        self,
        api: LivePresenceApi,
        source: LiveSource,
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
        self._thread: threading.Thread | None = None

        self._due = 0.0
        self._blocked_until = 0.0
        self._poked_due: float | None = None
        # A 404 holds for GONE_BACKOFF, or until the service moves on to a new generation: a game
        # start or a new log.
        self._gone_generation: int | None = None
        self._network_failures = 0
        self._warned: set[str] = set()
        self._dry_run_noted = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-live", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        thread = self._thread
        self._stop.set()
        self._wake.set()
        if thread is None:
            return
        self._thread = None
        thread.join(timeout)
        if thread.is_alive():
            self._log.warning("live presence poller did not stop in time")

    def poke(self, delay: float = 0.0) -> None:
        """Poll within delay seconds instead of on the regular schedule; a backoff still holds.

        A poke that arrives while a request is on the wire still counts once it is back.
        """
        with self._lock:
            due = self._monotonic() + delay
            self._poked_due = due if self._poked_due is None else min(self._poked_due, due)
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                wait = self.step()
            except Exception as exc:  # noqa: BLE001 - a bad response must not end the polling
                self._warn_once("crash", f"⚠️ Live presence lookup failed: {exc}")
                wait = POLL_INTERVAL
            self._wake.wait(wait)

    def step(self) -> float:
        """Polls if one is due and returns how long to sleep before looking again."""
        target = self._source.live_target()
        if target is None:
            return IDLE_WAIT

        now = self._monotonic()
        not_before = now + target.sample_hold
        with self._lock:
            if self._gone_generation is not None and target.generation != self._gone_generation:
                self._gone_generation = None
                self._blocked_until = 0.0
                self._due = now
            wait = self._wait_locked(now, not_before)
            if wait > 0.0:
                return wait
            self._gone_generation = None
            if self._poked_due is not None and self._poked_due <= now:
                self._poked_due = None

        result = self._api.get(target.profile_id)
        return self._handle(result, target)

    def _handle(self, result: LivePresenceResult, target: LiveTarget) -> float:
        now = self._monotonic()
        if result.dry_run:
            if not self._dry_run_noted:
                self._dry_run_noted = True
                self._log.debug("🧪 Dry run: the live presence is not requested")
            return self._schedule(now, POLL_INTERVAL)

        if result.ok:
            self._recovered()
            self._source.on_sample(result.sample, target.generation)
            return self._schedule(now, POLL_INTERVAL)

        if result.queued:
            self._recovered()
            return self._schedule(now, result.retry_after or POLL_INTERVAL)

        status = result.status
        if status in _THROTTLED:
            delay = result.retry_after if result.retry_after is not None else RETRY_AFTER_FALLBACK
            delay = min(max(delay, POLL_INTERVAL), RETRY_AFTER_MAX)
            self._warn_once(
                "throttled",
                f"⏳ outlasttrialsstats.com is busy (HTTP {status}); asking for the live presence again "
                f"in {delay:.0f}s",
            )
            return self._block(now, delay)
        if status in _GONE:
            with self._lock:
                self._gone_generation = target.generation
            self._warn_once(
                "gone",
                f"ℹ️ No live presence for this profile (HTTP {status}); it may be private or not "
                f"known to outlasttrialsstats.com yet. Escalation steps and the Invasion role stay "
                f"hidden; asking again in {GONE_BACKOFF / 60:.0f} min or at the next game start",
            )
            return self._block(now, GONE_BACKOFF)

        delay = NETWORK_BACKOFF[min(self._network_failures, len(NETWORK_BACKOFF) - 1)]
        self._network_failures += 1
        self._warn_once(
            "network",
            f"⚠️ Could not reach outlasttrialsstats.com for the live presence ({result.error}); "
            f"retrying in {delay:.0f}s",
        )
        return self._block(now, delay)

    def _schedule(self, now: float, delay: float) -> float:
        with self._lock:
            self._due = now + delay
            return self._wait_locked(now, 0.0)

    def _block(self, now: float, delay: float) -> float:
        with self._lock:
            self._blocked_until = now + delay
            self._due = self._blocked_until
            return self._wait_locked(now, 0.0)

    def _wait_locked(self, now: float, not_before: float) -> float:
        """Seconds until the next poll is due; called with the lock held."""
        due = self._due if self._poked_due is None else min(self._due, self._poked_due)
        return max(0.0, max(due, not_before, self._blocked_until) - now)

    def _warn_once(self, kind: str, message: str) -> None:
        """One warning per kind of failure until a lookup succeeds again; the rest go to debug."""
        if kind in self._warned:
            self._log.debug(message)
            return
        self._warned.add(kind)
        self._log.warning(message)

    def _recovered(self) -> None:
        self._network_failures = 0
        if self._warned:
            self._warned.clear()
            self._log.info("✅ Live presence is back")
