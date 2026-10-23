"""Keeps the trial catalog fresh from the repository, in the background.

The app never waits for this: presence starts on the bundled catalog and swaps to a newer one
whenever the download succeeds. A user without internet must not see a single warning about it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import requests

from totstats.presence.catalog import Catalog, parse_catalog, read_etag, store_cache
from totstats.shared.api import USER_AGENT
from totstats.shared.applog import AppLog

CATALOG_URLS = (
    "https://raw.githubusercontent.com/OutlastTrialsStats/contribute-data/main/assets/trials.json",
)

REFRESH_INTERVAL = 6 * 3600.0
FIRST_DELAY = 5.0
TIMEOUT = (5.0, 10.0)

STATUS_OK = 200
STATUS_NOT_MODIFIED = 304


class CatalogSync:
    """Fetches the catalog on its own thread and hands newer revisions to a callback."""

    def __init__(
        self,
        on_catalog: Callable[[Catalog], None],
        log: AppLog,
        stop: threading.Event,
        cache_dir: Path,
        current: Catalog | None = None,
        urls: Sequence[str] = CATALOG_URLS,
        interval: float = REFRESH_INTERVAL,
        first_delay: float = FIRST_DELAY,
    ) -> None:
        self._on_catalog = on_catalog
        self._log = log
        self._stop = stop
        self._cache_dir = cache_dir
        self._urls = tuple(urls)
        self._interval = interval
        self._first_delay = first_delay
        self._revision = current.revision if current is not None else 0
        self._quiet = False
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
                return
            try:
                self._refresh()
            except Exception as exc:  # noqa: BLE001 - a refresh must never take the thread down
                self._log.debug(f"📦 Trial catalog refresh failed: {exc}")
            delay = self._interval

    def _wait(self, seconds: float) -> None:
        """Sleep, but wake early for shutdown or for a toggle that just turned presence on."""
        self._wake.wait(seconds)
        self._wake.clear()

    def _refresh(self) -> None:
        headers = {"User-Agent": USER_AGENT}
        etag = read_etag(self._cache_dir)
        if etag:
            headers["If-None-Match"] = etag

        with requests.Session() as session:
            for url in self._urls:
                if self._stop.is_set():
                    return
                if self._fetch(session, url, headers):
                    return

    def _fetch(self, session: requests.Session, url: str, headers: dict[str, str]) -> bool:
        try:
            response = session.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if not self._quiet:
                self._quiet = True
                self._log.debug(f"📦 Could not reach the trial catalog ({exc})")
            return False

        self._quiet = False
        if response.status_code == STATUS_NOT_MODIFIED:
            self._log.debug("📦 Trial catalog is up to date")
            return True
        if response.status_code != STATUS_OK:
            self._log.debug(f"📦 Trial catalog returned status {response.status_code}")
            return False

        try:
            catalog = parse_catalog(json.loads(response.content.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            catalog = None
        if catalog is None:
            self._log.debug("📦 Downloaded trial catalog was not usable")
            return False

        if catalog.revision < self._revision:
            self._log.debug(f"📦 Ignoring older trial catalog (revision {catalog.revision})")
            return True

        store_cache(self._cache_dir, response.content, response.headers.get("ETag"), self._log)
        if catalog.revision > self._revision:
            self._log.info(
                f"📦 Trial catalog updated: {len(catalog.trials)} trials "
                f"(revision {catalog.revision})"
            )
        self._revision = catalog.revision
        self._on_catalog(catalog)
        return True
