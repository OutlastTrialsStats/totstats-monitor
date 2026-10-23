from __future__ import annotations

import pytest
import requests
from presence_fixtures import PROFILE_ID, FakeClock

from totstats.presence.live_api import LivePresenceApi, parse_retry_after
from totstats.shared.api import API_BASE_ENV, USER_AGENT


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
        self.closed = False

    def get(self, url: str, timeout: float):
        self.calls.append((url, timeout))
        if self._response is None:
            raise AssertionError("no request expected")
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def close(self) -> None:
        self.closed = True


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


def _api(session: FakeSession, clock: FakeClock | None = None, dry_run: bool = False):
    return LivePresenceApi(
        base_url="https://example.test/api/",
        session=session,
        dry_run=dry_run,
        monotonic=clock or FakeClock(),
    )


# --- Retry-After ----------------------------------------------------------------------------


def test_retry_after_in_seconds():
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after(" 5 ") == 5.0


def test_retry_after_garbage_is_ignored():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after("Thu, 22 Oct 2026 17:00:00 GMT") is None


# --- requests -------------------------------------------------------------------------------


def test_a_sample_is_dated_by_arrival():
    clock = FakeClock(500.0)
    session = FakeSession(FakeResponse(200, BODY, {"Cache-Control": "no-store"}))
    result = _api(session, clock).get(PROFILE_ID)

    assert result.ok
    assert result.sample is not None
    assert result.sample.presence_state == "lobby"
    assert result.sample.player_count == 2
    assert result.sample.trial_id is None
    assert result.sample.received_at == 500.0
    assert session.calls[0][0] == f"https://example.test/api/live/presence/{PROFILE_ID}"
    assert session.headers["User-Agent"] == USER_AGENT
    assert USER_AGENT.startswith("TOTStatsMonitor/")


@pytest.mark.parametrize(("age", "received_at"), [("7", 493.0), ("0", 500.0), ("-3", 500.0), ("x", 500.0)])
def test_a_sample_is_dated_back_by_its_age(age, received_at):
    session = FakeSession(FakeResponse(200, BODY, {"Age": age}))
    result = _api(session, FakeClock(500.0)).get(PROFILE_ID)
    assert result.sample is not None
    assert result.sample.received_at == received_at


def test_queued_carries_retry_after_and_no_sample():
    result = _api(FakeSession(FakeResponse(202, None, {"Retry-After": "10"}))).get(PROFILE_ID)
    assert result.queued
    assert not result.ok
    assert result.sample is None
    assert result.retry_after == 10.0


def test_rate_limited_carries_retry_after():
    response = FakeResponse(429, {"error": "RATE_LIMITED"}, {"Retry-After": "60"})
    result = _api(FakeSession(response)).get(PROFILE_ID)
    assert not result.ok
    assert result.status == 429
    assert result.retry_after == 60.0


def test_not_found():
    result = _api(FakeSession(FakeResponse(404, {"error": "NOT_FOUND"}))).get(PROFILE_ID)
    assert result.status == 404
    assert result.sample is None
    assert result.retry_after is None


def test_a_network_error_has_no_status():
    result = _api(FakeSession(requests.exceptions.ConnectionError("down"))).get(PROFILE_ID)
    assert result.status is None
    assert result.error is not None


def test_a_broken_body_is_not_a_sample():
    assert not _api(FakeSession(FakeResponse(200, ValueError("html")))).get(PROFILE_ID).ok
    assert not _api(FakeSession(FakeResponse(200, {"online": "yes"}))).get(PROFILE_ID).ok


def test_a_dry_run_sends_nothing():
    session = FakeSession(None)
    assert _api(session, dry_run=True).get(PROFILE_ID).dry_run
    assert session.calls == []


def test_a_dry_run_against_a_local_backend_asks(monkeypatch):
    monkeypatch.setenv(API_BASE_ENV, "http://localhost:8084/api/")
    session = FakeSession(FakeResponse(200, BODY))
    api = LivePresenceApi(session=session, dry_run=True, monotonic=FakeClock())
    assert api.get(PROFILE_ID).ok
    assert session.calls[0][0] == f"http://localhost:8084/api/live/presence/{PROFILE_ID}"
