"""HTTP client for the live presence endpoint on outlasttrialsstats.com.

The game no longer logs the presence it posts about itself, so the app asks the site for it
instead. The first request only queues the profile (202); from then on the site refreshes it with
every poll of the game's presence service and answers with the raw presence (200).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from totstats.presence.parser import PresenceSample, parse_sample
from totstats.shared.api import USER_AGENT, api_base, api_base_override

STATUS_OK = 200
STATUS_QUEUED = 202


@dataclass(frozen=True)
class LivePresenceResult:
    status: int | None
    """The HTTP status, or None when no response arrived."""

    sample: PresenceSample | None = None
    retry_after: float | None = None
    error: str | None = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and self.sample is not None

    @property
    def queued(self) -> bool:
        return self.status == STATUS_QUEUED


def parse_retry_after(value: str | None) -> float | None:
    if value is None or not value.strip().isdigit():
        return None
    return float(value)


class LivePresenceApi:
    """Fetches the presence of one profile per call: our own, every few seconds.

    A dry run replays an old log, and the live presence of the profile in it says nothing about
    that log — so a dry run asks nothing, unless the base URL is pointed at a local backend on
    purpose. The endpoint only reads, so that is safe.
    """

    def __init__(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        dry_run: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = (base_url or api_base()).rstrip("/")
        self._timeout = timeout
        self._dry_run = dry_run and api_base_override() is None
        self._monotonic = monotonic
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def close(self) -> None:
        self._session.close()

    def get(self, profile_id: str) -> LivePresenceResult:
        if self._dry_run:
            return LivePresenceResult(None, dry_run=True)

        url = f"{self._base_url}/live/presence/{profile_id}"
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            return LivePresenceResult(None, error=str(exc))
        received_at = self._monotonic()

        status = response.status_code
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if status != STATUS_OK:
            return LivePresenceResult(status, retry_after=retry_after, error=f"HTTP {status}")

        try:
            body = response.json()
        except ValueError:
            return LivePresenceResult(status, error="response is not JSON")
        age = response.headers.get("Age", "").strip()
        sample = parse_sample(body, received_at - (float(age) if age.isdigit() else 0.0))
        if sample is None:
            return LivePresenceResult(status, error="response has an unexpected shape")
        return LivePresenceResult(status, sample=sample)
