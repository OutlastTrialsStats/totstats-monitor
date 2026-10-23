from __future__ import annotations

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

from totstats.presence.catalog import Catalog
from totstats.presence.live_api import LivePresenceResult
from totstats.presence.live_poller import LivePresencePoller
from totstats.presence.service import PresenceService
from totstats.presence.state import SAMPLE_GRACE, Activity, StateMachine
from totstats.shared.profile_id import OwnProfileIdResolver


class FakeDiscord:
    configured = False


def _service(clock: FakeClock):
    machine = StateMachine(monotonic=clock)
    log = FakeLog()
    service = PresenceService(FakeDiscord(), Catalog(), OwnProfileIdResolver(), log, machine=machine)
    return service, machine, log


def _live(service: PresenceService) -> None:
    """Gets the service past its start-up replay with the profile id known."""
    service.on_line(authenticated("2026.10.22-17.03.20:000"))
    service.on_replay_complete()


def test_no_target_until_the_profile_and_the_log_are_known():
    service, _, _ = _service(FakeClock())
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


def test_every_reset_starts_a_new_generation():
    service, _, _ = _service(FakeClock())
    _live(service)
    generations = [service.live_target().generation]

    service.on_rotate()
    service.on_replay_complete()
    generations.append(service.live_target().generation)

    service.on_game_stopped()
    service.on_game_started()
    service.on_line(authenticated("2026.10.22-17.03.20:000"))
    service.on_replay_complete()
    generations.append(service.live_target().generation)

    assert generations == sorted(set(generations))
    assert len(generations) == 3


def test_a_current_sample_is_applied():
    clock = FakeClock()
    service, machine, _ = _service(clock)
    _live(service)
    service.on_line(lobby_map("2026.10.22-17.04.03:073"))
    clock.advance(SAMPLE_GRACE + 1.0)

    target = service.live_target()
    service.on_sample(sample(clock(), "findingparty"), target.generation)
    assert machine.snapshot.activity is Activity.MATCHMAKING


def test_identical_samples_are_not_skipped():
    clock = FakeClock()
    service, machine, _ = _service(clock)
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
    service, machine, _ = _service(clock)
    _live(service)
    service.on_line(trial_map("2026.10.22-17.05.35:493"))
    service.on_line(stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01"))
    clock.advance(SAMPLE_GRACE + 1.0)

    class RotatingApi:
        """The watcher rotates to a new log while the request is on the wire."""

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
    service, machine, _ = _service(clock)
    _live(service)
    generation = service.live_target().generation
    service.on_rotate()
    service.on_sample(sample(clock(), "findingparty"), generation + 1)
    assert machine.snapshot.activity is Activity.UNKNOWN


def test_the_full_round_from_log_to_sample_and_back():
    clock = FakeClock()
    service, machine, _ = _service(clock)
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
    service, _, _ = _service(clock)
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
