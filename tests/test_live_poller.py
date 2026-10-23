from __future__ import annotations

import pytest
from presence_fixtures import PROFILE_ID, FakeClock, FakeLog, sample

from totstats.presence.live_api import LivePresenceResult
from totstats.presence.live_poller import (
    IDLE_WAIT,
    NETWORK_BACKOFF,
    POLL_INTERVAL,
    RETRY_AFTER_FALLBACK,
    RETRY_AFTER_MAX,
    UNKNOWN_RETRY,
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
    poller = LivePresencePoller(api, source, FakeLog(), monotonic=clock)
    return clock, api, source, poller


def test_polls_at_once_and_then_on_the_interval(rig):
    clock, api, source, poller = rig
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
    _, api, source, poller = rig
    source.target = None
    assert poller.step() == IDLE_WAIT
    assert api.calls == []


def test_a_poke_brings_the_next_poll_forward(rig):
    clock, api, _, poller = rig
    poller.step()
    clock.advance(2.0)
    poller.poke()
    poller.step()
    assert len(api.calls) == 2


@pytest.mark.parametrize("status", [429, 503])
def test_throttling_waits_for_retry_after_even_when_poked(rig, status):
    clock, api, _, poller = rig
    api.results = [failed(status, retry_after=42.0)]
    assert poller.step() == 42.0

    poller.poke()
    clock.advance(41.0)
    poller.step()
    assert len(api.calls) == 1

    clock.advance(1.0)
    poller.step()
    assert len(api.calls) == 2


def test_queued_asks_again_after_retry_after(rig):
    clock, api, source, poller = rig
    api.results = [LivePresenceResult(202, retry_after=10.0)]
    assert poller.step() == 10.0
    assert source.samples == []

    clock.advance(10.0)
    poller.step()
    assert len(api.calls) == 2
    assert len(source.samples) == 1


def test_not_found_holds_even_for_a_new_generation(rig):
    clock, api, source, poller = rig
    api.results = [failed(404)]
    assert poller.step() == UNKNOWN_RETRY

    clock.advance(UNKNOWN_RETRY - 1.0)
    poller.poke()
    source.target = LiveTarget(PROFILE_ID, 2)
    poller.step()
    assert len(api.calls) == 1


def test_not_found_asks_again_once_the_server_forgot_it(rig):
    clock, api, _, poller = rig
    api.results = [failed(404)]
    poller.step()
    clock.advance(UNKNOWN_RETRY)
    poller.step()
    assert len(api.calls) == 2


@pytest.mark.parametrize(
    ("retry_after", "wait"),
    [
        (None, RETRY_AFTER_FALLBACK),
        (0.0, POLL_INTERVAL),
        (1.0, POLL_INTERVAL),
        (86400.0, RETRY_AFTER_MAX),
    ],
)
def test_retry_after_is_clamped_or_falls_back(rig, retry_after, wait):
    clock, api, _, poller = rig
    api.results = [failed(429, retry_after=retry_after)]
    assert poller.step() == wait
    poller.poke()
    clock.advance(wait - 1.0)
    poller.step()
    assert len(api.calls) == 1


def test_a_poke_during_a_request_is_kept():
    clock = FakeClock()
    calls: list[str] = []

    class SlowApi:
        def get(self, profile_id: str) -> LivePresenceResult:
            calls.append(profile_id)
            if len(calls) == 1:
                clock.advance(1.0)
                poller.poke()
                clock.advance(5.0)
            return ok()

    poller = LivePresencePoller(SlowApi(), FakeSource(), FakeLog(), monotonic=clock)
    assert poller.step() == 0.0
    poller.step()
    assert len(calls) == 2
    assert poller.step() == POLL_INTERVAL


@pytest.mark.parametrize(
    ("elapsed", "hold", "poked", "wait"),
    [
        pytest.param(0.0, 3.0, False, POLL_INTERVAL, id="shorter-than-the-interval"),
        pytest.param(POLL_INTERVAL - 1.0, 4.0, False, 4.0, id="beyond-the-interval"),
        pytest.param(0.0, 10.0, True, 10.0, id="poked"),
    ],
)
def test_a_sample_hold_delays_the_next_poll(rig, elapsed, hold, poked, wait):
    clock, api, source, poller = rig
    poller.step()
    clock.advance(elapsed)
    source.target = LiveTarget(PROFILE_ID, 1, hold)
    if poked:
        poller.poke()
    assert poller.step() == pytest.approx(wait)
    assert len(api.calls) == 1

    clock.advance(wait)
    source.target = LiveTarget(PROFILE_ID, 1)
    poller.step()
    assert len(api.calls) == 2


@pytest.mark.parametrize("status", [401, 500])
def test_an_unexpected_status_backs_off_like_a_network_error(rig, status):
    _, api, _, poller = rig
    api.results = [failed(status)]
    assert poller.step() == NETWORK_BACKOFF[0]


def test_network_errors_back_off_and_a_success_resets(rig):
    clock, api, _, poller = rig
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
