from __future__ import annotations

import pytest
from presence_fixtures import PROFILE_ID, FakeClock, FakeLog, sample

from totstats.presence.live_api import LivePresenceResult
from totstats.presence.live_poller import (
    GONE_BACKOFF,
    IDLE_WAIT,
    NETWORK_BACKOFF,
    POLL_INTERVAL,
    RETRY_AFTER_FALLBACK,
    RETRY_AFTER_MAX,
    LivePresencePoller,
    LiveTarget,
)


class FakeApi:
    def __init__(self) -> None:
        self.results: list[LivePresenceResult] = []
        self.calls: list[str] = []

    def get(self, profile_id: str) -> LivePresenceResult:
        self.calls.append(profile_id)
        return self.results.pop(0) if self.results else ok()


class FakeSource:
    def __init__(self) -> None:
        self.target: LiveTarget | None = LiveTarget(PROFILE_ID, 1)
        self.samples: list[tuple[object, int]] = []

    def live_target(self) -> LiveTarget | None:
        return self.target

    def on_sample(self, presence_sample, generation: int) -> None:
        self.samples.append((presence_sample, generation))


def ok() -> LivePresenceResult:
    return LivePresenceResult(200, sample=sample(0.0, "lobby"))


def failed(status: int | None, retry_after: float | None = None) -> LivePresenceResult:
    return LivePresenceResult(status, retry_after=retry_after, error="boom")


@pytest.fixture
def rig():
    clock = FakeClock()
    api = FakeApi()
    source = FakeSource()
    log = FakeLog()
    poller = LivePresencePoller(api, source, log, monotonic=clock)
    return clock, api, source, log, poller


def test_polls_at_once_and_then_on_the_interval(rig):
    clock, api, source, _, poller = rig
    assert poller.step() == POLL_INTERVAL
    assert len(api.calls) == 1
    assert source.samples[0][1] == 1

    clock.advance(5.0)
    assert poller.step() == pytest.approx(POLL_INTERVAL - 5.0)
    assert len(api.calls) == 1

    clock.advance(POLL_INTERVAL)
    poller.step()
    assert len(api.calls) == 2


def test_nothing_is_asked_without_a_target(rig):
    _, api, source, _, poller = rig
    source.target = None
    assert poller.step() == IDLE_WAIT
    assert api.calls == []


def test_a_poke_brings_the_next_poll_forward(rig):
    clock, api, _, _, poller = rig
    poller.step()
    clock.advance(2.0)
    poller.poke()
    poller.step()
    assert len(api.calls) == 2

    poller.poke(10.0)
    clock.advance(4.0)
    assert poller.step() == pytest.approx(6.0)
    clock.advance(6.0)
    poller.step()
    assert len(api.calls) == 3


@pytest.mark.parametrize("status", [429, 503])
def test_throttling_waits_for_retry_after_even_when_poked(rig, status):
    clock, api, _, _, poller = rig
    api.results = [failed(status, retry_after=42.0)]
    assert poller.step() == 42.0

    poller.poke()
    clock.advance(41.0)
    poller.step()
    assert len(api.calls) == 1

    clock.advance(1.0)
    poller.step()
    assert len(api.calls) == 2


def test_queued_asks_again_after_retry_after_without_a_warning(rig):
    clock, api, source, log, poller = rig
    api.results = [LivePresenceResult(202, retry_after=10.0)]
    assert poller.step() == 10.0
    assert source.samples == []
    assert log.warnings == []

    clock.advance(10.0)
    poller.step()
    assert len(api.calls) == 2
    assert len(source.samples) == 1


def test_throttling_without_retry_after_falls_back(rig):
    _, api, _, _, poller = rig
    api.results = [failed(429)]
    assert poller.step() == RETRY_AFTER_FALLBACK


def test_not_found_holds_until_the_next_generation(rig):
    clock, api, source, _, poller = rig
    api.results = [failed(404)]
    assert poller.step() == GONE_BACKOFF

    clock.advance(GONE_BACKOFF - 1.0)
    poller.poke()
    poller.step()
    assert len(api.calls) == 1

    source.target = LiveTarget(PROFILE_ID, 2)
    poller.step()
    assert len(api.calls) == 2


def test_not_found_asks_again_once_the_server_forgot_it(rig):
    clock, api, _, _, poller = rig
    api.results = [failed(404)]
    poller.step()
    clock.advance(GONE_BACKOFF)
    poller.step()
    assert len(api.calls) == 2


@pytest.mark.parametrize("retry_after", [0.0, 1.0])
def test_a_short_retry_after_still_waits_one_interval(rig, retry_after):
    clock, api, _, _, poller = rig
    api.results = [failed(429, retry_after=retry_after)]
    assert poller.step() == POLL_INTERVAL
    poller.poke()
    clock.advance(POLL_INTERVAL - 1.0)
    poller.step()
    assert len(api.calls) == 1


def test_a_huge_retry_after_is_capped(rig):
    _, api, _, _, poller = rig
    api.results = [failed(429, retry_after=86400.0)]
    assert poller.step() == RETRY_AFTER_MAX


def test_a_poke_during_a_request_is_kept(rig):
    clock, api, _, _, poller = rig

    class SlowApi:
        def get(self, profile_id: str) -> LivePresenceResult:
            api.calls.append(profile_id)
            clock.advance(1.0)
            poller.poke()
            clock.advance(5.0)
            return ok()

    poller._api = SlowApi()
    assert poller.step() == 0.0
    poller._api = api
    poller.step()
    assert len(api.calls) == 2
    assert poller.step() == POLL_INTERVAL


def test_a_sample_hold_delays_even_a_poke(rig):
    clock, api, source, _, poller = rig
    poller.step()
    source.target = LiveTarget(PROFILE_ID, 1, 10.0)
    poller.poke()
    assert poller.step() == pytest.approx(10.0)
    clock.advance(1.0)
    source.target = LiveTarget(PROFILE_ID, 1, 10.0)
    assert poller.step() == pytest.approx(10.0)
    clock.advance(10.0)
    source.target = LiveTarget(PROFILE_ID, 1)
    poller.step()
    assert len(api.calls) == 2
    assert poller.step() == POLL_INTERVAL


def test_a_sample_hold_shorter_than_the_interval_changes_nothing(rig):
    _, _, source, _, poller = rig
    poller.step()
    source.target = LiveTarget(PROFILE_ID, 1, 3.0)
    assert poller.step() == POLL_INTERVAL


def test_a_sample_hold_beyond_the_interval_delays_the_regular_poll(rig):
    clock, api, source, _, poller = rig
    poller.step()
    clock.advance(POLL_INTERVAL - 1.0)
    source.target = LiveTarget(PROFILE_ID, 1, 4.0)
    assert poller.step() == pytest.approx(4.0)
    assert len(api.calls) == 1


@pytest.mark.parametrize("status", [401, 500])
def test_an_unexpected_status_backs_off_like_a_network_error(rig, status):
    _, api, _, _, poller = rig
    api.results = [failed(status)]
    assert poller.step() == NETWORK_BACKOFF[0]


def test_network_errors_back_off_and_a_success_resets(rig):
    clock, api, _, _, poller = rig
    api.results = [failed(None) for _ in range(len(NETWORK_BACKOFF) + 1)]
    waits = []
    for _ in range(len(NETWORK_BACKOFF) + 1):
        wait = poller.step()
        waits.append(wait)
        clock.advance(wait)
    assert waits == [*NETWORK_BACKOFF, NETWORK_BACKOFF[-1]]

    assert poller.step() == POLL_INTERVAL
    clock.advance(POLL_INTERVAL)
    api.results = [failed(None)]
    assert poller.step() == NETWORK_BACKOFF[0]


def test_each_kind_of_failure_warns_once(rig):
    clock, api, _, log, poller = rig
    api.results = [failed(None), failed(None), failed(429, 1.0), failed(429, 1.0)]
    for _ in range(4):
        clock.advance(poller.step())
    assert len(log.warnings) == 2

    poller.step()
    assert log.infos == ["✅ Live presence is back"]

    api.results = [failed(None)]
    clock.advance(POLL_INTERVAL)
    poller.step()
    assert len(log.warnings) == 3


def test_a_dry_run_result_is_no_sample(rig):
    _, api, source, _, poller = rig
    api.results = [LivePresenceResult(None, dry_run=True)]
    assert poller.step() == POLL_INTERVAL
    assert source.samples == []
