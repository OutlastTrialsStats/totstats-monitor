from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from totstats.presence.parser import PresenceSample, parse_sample
from totstats.shared.api import USER_AGENT, api_base

TIMEOUT = 10.0


@dataclass(frozen=True)
class LivePresenceResult:
    status: int | None
    sample: PresenceSample | None = None
    retry_after: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.sample is not None

    @property
    def queued(self) -> bool:
        return self.status == 202


def parse_retry_after(value: str | None) -> float | None:
    return float(value) if value is not None and value.strip().isdigit() else None


class LivePresenceApi:
    def __init__(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = (base_url or api_base()).rstrip("/")
        self._monotonic = monotonic
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def close(self) -> None:
        self._session.close()

    def get(self, profile_id: str) -> LivePresenceResult:
        try:
            response = self._session.get(f"{self._base_url}/live/presence/{profile_id}", timeout=TIMEOUT)
        except requests.RequestException as exc:
            return LivePresenceResult(None, error=str(exc))
        received_at = self._monotonic()

        status = response.status_code
        if status != 200:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            return LivePresenceResult(status, retry_after=retry_after, error=f"HTTP {status}")
        try:
            body = response.json()
        except ValueError:
            return LivePresenceResult(status, error="invalid JSON")
        age = response.headers.get("Age", "").strip()
        sample = parse_sample(body, received_at - (float(age) if age.isdigit() else 0.0))
        if sample is None:
            return LivePresenceResult(status, error="unexpected body")
        return LivePresenceResult(status, sample)
