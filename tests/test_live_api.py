from __future__ import annotations

import pytest
import requests
from presence_fixtures import PROFILE_ID, FakeClock

from totstats.presence.live_api import LivePresenceApi, parse_retry_after
from totstats.shared.api import API_BASE_ENV


class FakeResponse:
    def __init__(self, status: int, body: object = None, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, response: FakeResponse | Exception | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, float]] = []
        self._response = response

    def get(self, url: str, timeout: float):
        self.calls.append((url, timeout))
        if self._response is None:
            raise AssertionError("no request expected")
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


BODY = {
    "applicationId": "0e67af76-0000-0000-0000-000000000000",
    "online": True,
    "lastOnline": 1792786874,
    "properties": {
        "crossplay": True,
        "presenceState": "lobby",
        "region": "eu",
        "trialId": "-",
        "programId": "-",
        "programDifficulty": -1.0,
        "trialChain": -1.0,
        "playerCount": 2.0,
        "invasionState": "-",
    },
}


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(API_BASE_ENV, raising=False)


def _api(session: FakeSession, clock: FakeClock | None = None):
    return LivePresenceApi(
        base_url="https://example.test/api/", session=session, monotonic=clock or FakeClock()
    )


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        ("30", 30.0),
        (" 5 ", 5.0),
        (None, None),
        ("", None),
        ("soon", None),
        ("-5", None),
        ("Thu, 22 Oct 2026 17:00:00 GMT", None),
    ],
)
def test_retry_after(header, seconds):
    assert parse_retry_after(header) == seconds


@pytest.mark.parametrize(
    ("age", "received_at"), [(None, 500.0), ("7", 493.0), ("0", 500.0), ("-3", 500.0), ("x", 500.0)]
)
def test_a_sample_is_dated_back_by_its_age(age, received_at):
    headers = {"Age": age} if age is not None else {}
    session = FakeSession(FakeResponse(200, BODY, headers))
    result = _api(session, FakeClock(500.0)).get(PROFILE_ID)
    assert result.ok
    assert result.sample is not None
    assert result.sample.received_at == received_at
    assert session.calls[0][0] == f"https://example.test/api/live/presence/{PROFILE_ID}"


@pytest.mark.parametrize(
    ("status", "body", "header", "retry_after"),
    [
        pytest.param(202, None, "10", 10.0, id="queued"),
        pytest.param(429, {"error": "RATE_LIMITED"}, "60", 60.0, id="rate-limited"),
        pytest.param(503, {"error": "PRESENCE_UNAVAILABLE"}, "30", 30.0, id="unavailable"),
        pytest.param(404, {"error": "NOT_FOUND"}, None, None, id="not-found"),
    ],
)
def test_a_non_sample_status_carries_retry_after(status, body, header, retry_after):
    headers = {"Retry-After": header} if header else {}
    result = _api(FakeSession(FakeResponse(status, body, headers))).get(PROFILE_ID)
    assert not result.ok
    assert result.status == status
    assert result.queued == (status == 202)
    assert result.sample is None
    assert result.retry_after == retry_after


def test_a_network_error_has_no_status():
    result = _api(FakeSession(requests.exceptions.ConnectionError("down"))).get(PROFILE_ID)
    assert result.status is None
    assert result.error is not None


def test_a_broken_body_is_not_a_sample():
    assert not _api(FakeSession(FakeResponse(200, ValueError("html")))).get(PROFILE_ID).ok
    assert not _api(FakeSession(FakeResponse(200, {"online": "yes"}))).get(PROFILE_ID).ok


def test_a_local_backend_override_is_asked(monkeypatch):
    monkeypatch.setenv(API_BASE_ENV, "http://localhost:8084/api/")
    session = FakeSession(FakeResponse(200, BODY))
    api = LivePresenceApi(session=session, monotonic=FakeClock())
    assert api.get(PROFILE_ID).ok
    assert session.calls[0][0] == f"http://localhost:8084/api/live/presence/{PROFILE_ID}"
