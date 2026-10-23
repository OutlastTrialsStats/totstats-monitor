from __future__ import annotations

import requests

from totstats.shared.api import API_BASE, USER_AGENT

TIMEOUT = 10.0


class ContributeApi:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def close(self) -> None:
        self._session.close()

    def contribute(self, contributor_id: str, profile_id: str) -> int:
        response = self._session.put(
            f"{API_BASE}/profile/contribute",
            params={"contributor": contributor_id, "profile": profile_id},
            timeout=TIMEOUT,
        )
        return response.status_code
