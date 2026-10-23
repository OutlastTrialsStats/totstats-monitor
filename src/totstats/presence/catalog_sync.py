from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

import requests

from totstats.presence.catalog import SITE, Catalog, cache_path, parse_catalog, read_etag, store_cache
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
        self._downloaded = cache_path(cache_dir).exists()
        self._unreachable = False
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
            try:
                ok = self._refresh()
            except Exception as exc:
                self._log.debug(f"Trial catalog refresh failed: {exc}")
                ok = False
            self._downloaded = self._downloaded or ok
            delay = REFRESH_INTERVAL if self._downloaded else RETRY_INTERVAL
        self._session.close()

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _refresh(self) -> bool:
        etag = read_etag(self._cache_dir) if cache_path(self._cache_dir).exists() else None
        headers = {"If-None-Match": etag} if etag else {}
        try:
            response = self._session.get(f"{SITE}/data/trials.json", headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if not self._unreachable:
                self._unreachable = True
                self._log.debug(f"Trial catalog unreachable: {exc}")
            return False
        self._unreachable = False

        if response.status_code == 304:
            self._log.debug("Trial catalog unchanged")
            return True
        if response.status_code != 200:
            self._log.debug(f"Trial catalog not updated: HTTP {response.status_code}")
            return False
        try:
            catalog = parse_catalog(json.loads(response.content.decode("utf-8")))
        except ValueError:
            catalog = None
        if catalog is None:
            self._log.debug("Trial catalog not usable")
            return False

        store_cache(self._cache_dir, response.content, response.headers.get("ETag"), self._log)
        self._log.info(f"Trial catalog updated: {len(catalog.trials)} trials")
        self._on_catalog(catalog)
        return True
