"""HTTP client for the contribute endpoint."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from totstats.shared.api import DEFAULT_API_BASE, USER_AGENT

STATUS_OK = 200
STATUS_ALREADY_KNOWN = 208


@dataclass(frozen=True)
class ContributeResult:
    profile_id: str
    status: int | None
    ok: bool
    already_known: bool
    error: str | None = None
    dry_run: bool = False


class ContributeApi:
    """Reports one profile id per call, over a pooled session: the endpoint is called in bursts
    whenever a lobby fills up, and a handshake per player would be pure waste."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_BASE,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        dry_run: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._dry_run = dry_run
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def close(self) -> None:
        self._session.close()

    def contribute(self, contributor_id: str, profile_id: str) -> ContributeResult:
        if self._dry_run:
            # Replaying an old log must never reach the live API.
            return ContributeResult(profile_id, None, True, False, None, dry_run=True)

        url = f"{self._base_url}/profile/contribute"
        try:
            response = self._session.put(
                url,
                params={"contributor": contributor_id, "profile": profile_id},
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            return ContributeResult(profile_id, None, False, False, str(exc))

        status = response.status_code
        return ContributeResult(
            profile_id=profile_id,
            status=status,
            ok=status in (STATUS_OK, STATUS_ALREADY_KNOWN),
            already_known=status == STATUS_ALREADY_KNOWN,
            error=None if status in (STATUS_OK, STATUS_ALREADY_KNOWN) else f"HTTP {status}",
        )
