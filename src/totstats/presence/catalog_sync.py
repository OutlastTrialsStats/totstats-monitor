from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

import requests

from totstats.presence.catalog import SITE, Catalog, parse_catalog, store_catalog
from totstats.shared.api import USER_AGENT
from totstats.shared.applog import AppLog

REFRESH_INTERVAL = 6 * 3600.0
RETRY_INTERVAL = 60.0
TIMEOUT = (5.0, 10.0)


class CatalogSync:
    def __init__(
        self,
        on_catalog: Callable[[Catalog], None],
        log: AppLog,
        stop: threading.Event,
        cache_dir: Path,
        first_delay: float = 5.0,
    ) -> None:
        self._on_catalog = on_catalog
        self._log = log
        self._stop = stop
        self._cache_dir = cache_dir
        self._first_delay = first_delay
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="presence-catalog", daemon=True)
        self._worker.start()

    def refresh_soon(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        delay = self._first_delay
        while not self._stop.is_set():
            self._wait(delay)
            if self._stop.is_set():
                break
            delay = REFRESH_INTERVAL if self._refresh() else RETRY_INTERVAL
        self._session.close()

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _refresh(self) -> bool:
        try:
            response = self._session.get(f"{SITE}/data/trials.json", timeout=TIMEOUT)
            response.raise_for_status()
            catalog = parse_catalog(json.loads(response.content.decode("utf-8")))
        except (requests.RequestException, ValueError) as exc:
            self._log.debug(f"Trial catalog not updated: {exc}")
            return False
        if catalog is None:
            self._log.debug("Trial catalog not usable")
            return False

        store_catalog(self._cache_dir, response.content, self._log)
        self._log.info(f"Trial catalog updated: {len(catalog.trials)} trials")
        self._on_catalog(catalog)
        return True
