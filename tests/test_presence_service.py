from __future__ import annotations

import itertools
import queue
import time
from types import SimpleNamespace

import pytest
from presence_fixtures import (
    PROFILE_ID,
    PROGRAM_HALLOWEEN,
    FakeClock,
    FakeLog,
    authenticated,
    lobby_map,
    phase,
    sample,
    stage,
    trial_map,
)

from totstats.presence import service as service_module
from totstats.presence.catalog import Catalog
from totstats.presence.live_api import LivePresenceResult
from totstats.presence.live_poller import LivePresencePoller
from totstats.presence.service import PresenceService
from totstats.presence.state import SAMPLE_GRACE, Activity, StateMachine
from totstats.shared.profile_id import OwnProfileIdResolver


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: queue.Queue[tuple[str, object]] = queue.Queue()

    def ensure_connected(self) -> bool:
        return True

    def next_attempt_in(self, now: float | None = None) -> float:
        return 0.0

    def update(self, payload) -> bool:
        self.sent.put(("update", payload))
        return True

    def clear(self) -> bool:
        self.sent.put(("clear", None))
        return True

    def close(self) -> None:
        pass

    def next(self) -> tuple[str, object]:
        return self.sent.get(timeout=2.0)


def _service(clock: FakeClock):
    machine = StateMachine(monotonic=clock)
    service = PresenceService(FakeDiscord(), Catalog(), OwnProfileIdResolver(), FakeLog(), machine=machine)
    return service, machine


def _live(service: PresenceService) -> None:
    service.on_line(authenticated("2026.10.22-17.03.20:000"))
    service.on_replay_complete()


def test_no_target_until_the_profile_and_the_log_are_known():
    service, _ = _service(FakeClock())
    assert service.live_target() is None

    service.on_line(authenticated("2026.10.22-17.03.20:000"))
    assert service.live_target() is None

    service.on_replay_complete()
    target = service.live_target()
    assert target is not None
    assert target.profile_id == PROFILE_ID

    service.enabled = False
    assert service.live_target() is None
    service.enabled = True
    service.game_running = False
    assert service.live_target() is None


def test_identical_samples_are_not_skipped():
    clock = FakeClock()
    service, machine = _service(clock)
    _live(service)
    service.on_line(lobby_map("2026.10.22-17.04.03:073"))
    clock.advance(SAMPLE_GRACE + 1.0)
    generation = service.live_target().generation

    service.on_sample(sample(clock(), "findingparty"), generation)
    service.on_line(lobby_map("2026.10.22-17.06.00:000"))
    assert machine.snapshot.activity is Activity.SLEEP_ROOM

    clock.advance(SAMPLE_GRACE + 1.0)
    service.on_sample(sample(clock(), "findingparty"), generation)
    assert machine.snapshot.activity is Activity.MATCHMAKING


def test_a_rotation_during_a_running_request_discards_its_sample():
    clock = FakeClock()
    service, machine = _service(clock)
    _live(service)
    service.on_line(trial_map("2026.10.22-17.05.35:493"))
    service.on_line(stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01"))
    clock.advance(SAMPLE_GRACE + 1.0)

    class RotatingApi:
        def get(self, profile_id: str) -> LivePresenceResult:
            service.on_rotate()
            service.on_line(lobby_map("2026.10.22-17.23.40:033"))
            service.on_replay_complete()
            clock.advance(SAMPLE_GRACE + 1.0)
            stale = sample(clock(), "trial", "PSA_MT01", PROGRAM_HALLOWEEN, 2)
            return LivePresenceResult(200, sample=stale)

    poller = LivePresencePoller(RotatingApi(), service, FakeLog(), monotonic=clock)
    poller.step()

    assert machine.snapshot.trial_id is None
    assert machine.snapshot.activity is Activity.SLEEP_ROOM


def test_a_sample_during_a_replay_is_dropped():
    clock = FakeClock()
    service, machine = _service(clock)
    _live(service)
    generation = service.live_target().generation
    service.on_rotate()
    service.on_sample(sample(clock(), "findingparty"), generation + 1)
    assert machine.snapshot.activity is Activity.UNKNOWN


def test_the_full_round_from_log_to_sample_and_back():
    clock = FakeClock()
    service, machine = _service(clock)
    _live(service)
    service.on_line(lobby_map("2026.10.22-17.04.03:073"))
    service.on_line(trial_map("2026.10.22-17.05.35:493"))
    service.on_line(stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01"))
    clock.advance(SAMPLE_GRACE + 1.0)
    generation = service.live_target().generation
    service.on_sample(sample(clock(), "trial", "PSA_MT01", PROGRAM_HALLOWEEN, 2, player_count=4), generation)
    assert machine.snapshot.trial_id == "PSA_MT01"

    service.on_line(phase("2026.10.22-17.22.52:648", "ReturnToLobby", "StageSuccess"))
    clock.advance(SAMPLE_GRACE + 1.0)
    service.on_sample(sample(clock(), "trial", "PSA_MT01", PROGRAM_HALLOWEEN, 2), generation)
    assert machine.snapshot.activity is Activity.RETURNING
    assert machine.snapshot.trial_id is None


def test_the_poll_after_a_trial_map_waits_for_its_descriptor():
    clock = FakeClock()
    service, _ = _service(clock)
    _live(service)

    class CountingApi:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, profile_id: str) -> LivePresenceResult:
            self.calls += 1
            return LivePresenceResult(200, sample=sample(clock(), "lobby"))

    api = CountingApi()
    poller = LivePresencePoller(api, service, FakeLog(), monotonic=clock)
    service._live = poller
    service.on_line(lobby_map("2026.10.22-17.25.08:322"))
    clock.advance(SAMPLE_GRACE + 1.0)
    poller.step()
    assert api.calls == 1

    clock.advance(1.0)
    service.on_line(trial_map("2026.10.22-17.27.04:258"))
    clock.advance(1.0)
    service.on_line(stage("2026.10.22-17.27.05:372", PROGRAM_HALLOWEEN, "ORI_MT02"))
    assert poller.step() == SAMPLE_GRACE
    clock.advance(SAMPLE_GRACE)
    poller.step()
    assert api.calls == 2


@pytest.fixture
def publishing(monkeypatch):
    ticks = itertools.count(1000.0, 100.0)
    monkeypatch.setattr(
        service_module, "time", SimpleNamespace(monotonic=lambda: next(ticks), time=time.time)
    )
    discord = FakeDiscord()
    service = PresenceService(discord, Catalog(), OwnProfileIdResolver(), FakeLog())
    _live(service)
    service.start()
    yield service, discord
    service.stop()


def test_a_changed_state_is_sent_and_an_unchanged_one_is_not(publishing):
    service, discord = publishing
    service.on_line(lobby_map("2026.10.22-17.04.03:073"))
    kind, payload = discord.next()
    assert kind == "update"
    assert (payload.details, payload.state) == ("Sleep Room", "Idle")

    service.set_catalog(Catalog())
    service.on_line(trial_map("2026.10.22-17.05.35:493"))
    kind, payload = discord.next()
    assert kind == "update"
    assert payload.details == "In a Trial"


@pytest.mark.parametrize("stop", ["game", "presence"])
def test_the_status_is_cleared_when_the_game_or_presence_stops(publishing, stop):
    service, discord = publishing
    service.on_line(lobby_map("2026.10.22-17.04.03:073"))
    assert discord.next()[0] == "update"

    if stop == "game":
        service.on_game_stopped()
    else:
        service.set_enabled(False)
    assert discord.next() == ("clear", None)
    assert discord.sent.empty()
