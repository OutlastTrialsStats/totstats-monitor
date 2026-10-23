from __future__ import annotations

import contextlib
import queue
import re
import threading

import requests

from totstats.contribute.api import ContributeApi
from totstats.shared.applog import AppLog
from totstats.shared.log_tail import LogLine
from totstats.shared.profile_id import OwnProfileIdResolver

PLAYER_RE = re.compile(
    r"Player Init Replicated\. Player Id = (?P<name>.*?) \[[^\]]*\] "
    r"\[(?P<id>[0-9a-fA-F-]{36})\],\s+Player Slot = (?P<slot>\d+), "
    r"IsLocallyControlled = (?P<local>Yes|No)"
)


class ContributeService:
    INTERESTS = ("Player Init Replicated",) + OwnProfileIdResolver.INTERESTS

    def __init__(self, api: ContributeApi | None, ids: OwnProfileIdResolver, log: AppLog) -> None:
        self._api = api
        self._ids = ids
        self._log = log
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=512)
        self._worker: threading.Thread | None = None
        self._seen: set[str] = set()
        self._pending: list[str] = []
        self.enabled = True

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="contribute", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 3.0) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        worker.join(timeout)
        if worker.is_alive():
            self._log.warning("Contribute worker did not stop in time")

    def on_rotate(self) -> None:
        self._seen.clear()
        self._pending.clear()
        self._ids.reset()

    def on_line(self, line: LogLine) -> None:
        if not self.enabled:
            return

        own_id = self._ids.feed(line)
        if own_id is not None:
            self._log.info(f"Player ID found: {own_id[:8]}")
            pending, self._pending = self._pending, []
            for profile_id in pending:
                self._enqueue(profile_id)

        match = PLAYER_RE.search(line.raw)
        if match is None or match["local"] == "Yes":
            return
        profile_id = match["id"].lower()
        if profile_id in self._seen:
            return
        self._seen.add(profile_id)

        message = f"New player: {match['name']} (slot {match['slot']})"
        if line.replay:
            self._log.debug(message)
        else:
            self._log.info(message)

        if self._ids.profile_id is None:
            self._pending.append(profile_id)
        else:
            self._enqueue(profile_id)

    def _enqueue(self, profile_id: str) -> None:
        try:
            self._queue.put_nowait(profile_id)
        except queue.Full:
            self._log.warning(f"Contribute queue full: dropped {profile_id[:8]}")

    def _run(self) -> None:
        while (profile_id := self._queue.get()) is not None:
            contributor_id = self._ids.profile_id
            if contributor_id is not None:
                self._send(contributor_id, profile_id)

    def _send(self, contributor_id: str, profile_id: str) -> None:
        short = profile_id[:8]
        if self._api is None:
            self._log.info(f"Dry run, not sent: {short}")
            return
        try:
            status = self._api.contribute(contributor_id, profile_id)
        except requests.RequestException as exc:
            self._log.warning(f"Contribute failed for {short}: {exc}")
            return
        if status == 200:
            self._log.info(f"Player sent: {short}")
        elif status == 208:
            self._log.info(f"Player already known: {short}")
        else:
            self._log.warning(f"Contribute failed for {short}: HTTP {status}")
