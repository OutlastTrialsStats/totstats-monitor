from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from totstats.presence.render import Payload
from totstats.shared.applog import AppLog

if TYPE_CHECKING:
    from pypresence import Presence

BACKOFF = (5.0, 10.0, 20.0, 40.0, 60.0, 120.0, 300.0)


class DiscordClient:
    def __init__(self, client_id: str, log: AppLog, dry_run: bool = False) -> None:
        self._client_id = client_id
        self._log = log
        self._dry_run = dry_run
        self._rpc: Presence | None = None
        self._attempt = 0
        self._next_attempt = 0.0
        self._announced = False

    def next_attempt_in(self, now: float) -> float:
        return max(0.0, self._next_attempt - now)

    def ensure_connected(self) -> bool:
        if self._dry_run or self._rpc is not None:
            return True
        now = time.monotonic()
        if now < self._next_attempt:
            return False
        try:
            from pypresence import Presence

            rpc = Presence(self._client_id, connection_timeout=5)
            rpc.connect()
        except Exception as exc:
            self._retry(now)
            if self._announced:
                self._log.debug(f"Discord connection failed: {exc}")
            else:
                self._announced = True
                self._log.info("Discord not running, connecting once it starts")
            return False
        self._rpc = rpc
        self._attempt = 0
        self._announced = False
        self._log.info("Connected to Discord")
        return True

    def update(self, payload: Payload) -> bool:
        if self._dry_run:
            self._log.info(f"Dry run, would show: {payload.summary()}")
            return True
        if self._rpc is None:
            return False
        try:
            self._rpc.update(**payload.as_kwargs())
        except Exception as exc:
            self._drop(f"Discord update failed: {exc}")
            return False
        self._log.info(f"Discord status: {payload.summary()}")
        return True

    def clear(self) -> bool:
        if self._dry_run:
            self._log.info("Dry run, would clear the Discord status")
            return True
        if self._rpc is None:
            return False
        try:
            self._rpc.clear()
        except Exception as exc:
            self._drop(f"Discord clear failed: {exc}")
            return False
        self._log.debug("Discord status cleared")
        return True

    def close(self) -> None:
        rpc, self._rpc = self._rpc, None
        if rpc is not None:
            with contextlib.suppress(Exception):
                rpc.close()

    def _retry(self, now: float) -> None:
        self._next_attempt = now + BACKOFF[min(self._attempt, len(BACKOFF) - 1)]
        self._attempt += 1

    def _drop(self, message: str) -> None:
        self._log.debug(message)
        self._log.info("Discord connection lost, reconnecting")
        self.close()
        self._attempt = 0
        self._retry(time.monotonic())
