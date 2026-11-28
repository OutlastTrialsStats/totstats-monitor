from __future__ import annotations

import contextlib
import io
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import requests
from PIL import Image

from totstats.presence.catalog import SITE
from totstats.shared.api import USER_AGENT
from totstats.shared.applog import AppLog

ASSETS = f"{SITE}/game-assets/"
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.webp")
TIMEOUT = (5.0, 10.0)
MAX_BYTES = 2 * 1024 * 1024
CHUNK = 64 * 1024
# The site may redraw a picture under the same name; a cached copy older than this is fetched again.
STALE_AFTER = 7 * 24 * 3600.0

Post = Callable[[Callable[[], None]], None]


def cache_name(url: str) -> str | None:
    """The file name to keep a site asset under, or None for anything that is not one."""
    if not url.startswith(ASSETS):
        return None
    name = url[len(ASSETS):]
    return name if NAME.fullmatch(name) else None


def decode(body: bytes) -> Image.Image:
    with Image.open(io.BytesIO(body)) as image:
        return image.convert("RGBA")


def _open(path: Path) -> Image.Image | None:
    try:
        return decode(path.read_bytes())
    except Exception:  # noqa: BLE001 - a damaged cache file must never break the preview
        return None


def _download(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True)
    try:
        response.raise_for_status()
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES:
            raise ValueError(f"{declared} bytes")
        body = bytearray()
        for chunk in response.iter_content(CHUNK):
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError(f"over {MAX_BYTES} bytes")
        return bytes(body)
    finally:
        response.close()


class Artwork:
    """Trial artwork and role icons for the status preview, downloaded once and kept on disk.

    Everything but the download runs on the UI thread; the download reports back through `post`.
    Offline, only what is already on disk is shown.
    """

    def __init__(
        self,
        cache_dir: Path | None,
        log: AppLog,
        post: Post,
        clock: Callable[[], float] = time.time,
        online: bool = True,
    ) -> None:
        self._dir = cache_dir
        self._online = online
        self._log = log
        self._post = post
        self._clock = clock
        self._images: dict[str, Image.Image] = {}
        self._pending: dict[str, list[Callable[[], None]]] = {}
        self._failed: set[str] = set()

    def get(self, url: str, on_ready: Callable[[], None]) -> Image.Image | None:
        """The picture if it is at hand, else None; `on_ready` runs once a download has brought one."""
        if url in self._images:
            return self._images[url]
        name = cache_name(url)
        if name is None or url in self._failed:
            return None
        if self._dir is not None and (cached := _open(self._dir / name)) is not None:
            self._images[url] = cached
            if self._is_stale(self._dir / name):
                # Shown as it is while a fresh copy comes in; offline, the old one simply stays.
                self._start(url, name, on_ready)
            return cached
        self._start(url, name, on_ready)
        return None

    def retry_failed(self) -> None:
        self._failed.clear()

    def _is_stale(self, path: Path) -> bool:
        try:
            return self._clock() - path.stat().st_mtime > STALE_AFTER
        except OSError:
            return False

    def _start(self, url: str, name: str, on_ready: Callable[[], None]) -> None:
        if not self._online:
            return
        waiting = self._pending.get(url)
        if waiting is not None:
            if on_ready not in waiting:
                waiting.append(on_ready)
            return
        self._pending[url] = [on_ready]
        threading.Thread(target=self._fetch, args=(url, name), name="artwork", daemon=True).start()

    def _fetch(self, url: str, name: str) -> None:
        # Whatever goes wrong, the UI thread must hear back, or the address stays pending for good.
        try:
            body = _download(url)
            picture = decode(body)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            self._post(lambda: self._done(url, None, error))
            return
        if self._dir is not None:
            self._store(name, body)
        self._post(lambda: self._done(url, picture, None))

    def _store(self, name: str, body: bytes) -> None:
        temp = self._dir / f"{name}.tmp"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(body)
            os.replace(temp, self._dir / name)
        except OSError as exc:
            self._log.debug(f"Artwork not cached: {exc}")
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)

    def _done(self, url: str, picture: Image.Image | None, error: str | None) -> None:
        callbacks = self._pending.pop(url, [])
        if picture is None:
            self._log.debug(f"Artwork not loaded, {url.rsplit('/', 1)[-1]}: {error}")
            # A stale copy that could not be refreshed is still better than nothing.
            if url not in self._images:
                self._failed.add(url)
            return
        self._images[url] = picture
        for callback in callbacks:
            callback()
