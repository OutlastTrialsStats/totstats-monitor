from __future__ import annotations

import threading
import time

from totstats.presence import parser
from totstats.presence.catalog import Catalog
from totstats.presence.client import DiscordClient
from totstats.presence.live_api import LivePresenceApi
from totstats.presence.live_poller import LivePresencePoller, LiveTarget
from totstats.presence.parser import MapLoad, PresenceSample, parse_event
from totstats.presence.render import Payload, render
from totstats.presence.state import GameState, StateMachine
from totstats.shared.applog import AppLog
from totstats.shared.log_tail import LogLine
from totstats.shared.profile_id import OwnProfileIdResolver

RATE_CAPACITY = 2
RATE_REFILL = 15.0
RATE_FLOOR = 5.0

SETTLE = 2.0
IDLE_WAIT = 5.0
WARN_INTERVAL = 60.0

FIELDS = (
    "activity",
    "program_id",
    "trial_id",
    "difficulty",
    "chain_step",
    "party_size",
    "party_max",
    "invasion_role",
)


def _diff(before: GameState, after: GameState) -> str:
    changes = [
        f"{name} {getattr(before, name)} -> {getattr(after, name)}"
        for name in FIELDS
        if getattr(before, name) != getattr(after, name)
    ]
    if (before.trial_started_at is None) != (after.trial_started_at is None):
        changes.append("clock started" if after.trial_started_at else "clock cleared")
    return ", ".join(changes)


class RateLimiter:
    def __init__(self) -> None:
        self._tokens = float(RATE_CAPACITY)
        self._last = 0.0
        self._sent = 0.0

    def _replenish(self, now: float) -> None:
        if self._last:
            self._tokens = min(RATE_CAPACITY, self._tokens + (now - self._last) / RATE_REFILL)
        self._last = now

    def consume(self, now: float) -> None:
        self._replenish(now)
        self._tokens = max(0.0, self._tokens - 1.0)
        self._sent = now

    def wait(self, now: float) -> float:
        self._replenish(now)
        waits = [0.0]
        if self._tokens < 1.0:
            waits.append((1.0 - self._tokens) * RATE_REFILL)
        if self._sent and now - self._sent < RATE_FLOOR:
            waits.append(RATE_FLOOR - (now - self._sent))
        return max(waits)


class PresenceService:
    INTERESTS = parser.INTERESTS + OwnProfileIdResolver.INTERESTS

    def __init__(
        self,
        client: DiscordClient,
        catalog: Catalog,
        ids: OwnProfileIdResolver,
        log: AppLog,
        live_api: LivePresenceApi | None = None,
        machine: StateMachine | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._ids = ids
        self._log = log
        self._machine = machine or StateMachine()
        self._live = None if live_api is None else LivePresencePoller(live_api, self, log)
        self._limiter = RateLimiter()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

        self._generation = 0
        self._replaying = True
        self._latest: GameState | None = None
        self._unknown_trials: set[str] = set()
        self._warned_at = 0.0

        self._state = GameState()
        self._session_start: float | None = None
        self._last_sent: Payload | None = None
        self._pending: Payload | None = None
        self._held = False
        self._changed_at = 0.0
        self._retired = False

        self.enabled = True
        self.game_running = True

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="presence", daemon=True)
        self._worker.start()
        if self._live is not None:
            self._live.start()

    def stop(self, timeout: float = 3.0) -> None:
        if self._live is not None:
            self._live.stop(timeout)
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        self._stop.set()
        self._wake.set()
        worker.join(timeout)
        if worker.is_alive():
            self._log.warning("Presence worker did not stop in time")

    def on_line(self, line: LogLine) -> None:
        try:
            self._ids.feed(line)
            event = parse_event(line.raw)
            if event is None:
                return
            with self._lock:
                before = self._machine.snapshot
                if not self._machine.apply(event, line.ts, live=not line.replay):
                    return
                if not self._replaying and (changed := _diff(before, self._machine.snapshot)):
                    self._log.debug(f"Log: {changed}")
                self._note_unknown_trial()
                self._publish()
            if isinstance(event, MapLoad) and not line.replay:
                self._poke_live()
        except Exception as exc:
            now = time.monotonic()
            if now - self._warned_at > WARN_INTERVAL:
                self._warned_at = now
                self._log.warning(f"Presence log line failed: {exc}")

    def on_rotate(self) -> None:
        self._log.debug("Log file rotated, rebuilding the game state")
        with self._lock:
            self._reset_machine()
            self._unknown_trials.clear()
            self._publish()
        self._poke_live()

    def on_replay_complete(self) -> None:
        with self._lock:
            if not self._replaying:
                return
            self._replaying = False
            state = _diff(GameState(), self._machine.snapshot) or "nothing known"
            self._log.debug(f"Log replayed: {state}")
            self._publish()
        self._poke_live()

    def live_target(self) -> LiveTarget | None:
        if not self.enabled or not self.game_running:
            return None
        profile_id = self._ids.profile_id
        with self._lock:
            if profile_id is None or self._replaying:
                return None
            return LiveTarget(profile_id, self._generation, self._machine.sample_hold())

    def on_sample(self, sample: PresenceSample, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._replaying:
                self._log.debug("Live presence sample dropped: state was reset")
                return
            before = self._machine.snapshot
            if not self._machine.apply_sample(sample):
                return
            if changed := _diff(before, self._machine.snapshot):
                self._log.debug(f"Live: {changed}")
            self._note_unknown_trial()
            self._publish()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._wake.set()
        if enabled:
            self._poke_live()

    def set_catalog(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._wake.set()

    def on_game_started(self, started_at: float | None = None) -> None:
        self.game_running = True
        self._session_start = started_at or time.time()
        with self._lock:
            self._reset_machine()
        self._wake.set()
        self._poke_live()

    def on_game_stopped(self) -> None:
        self.game_running = False
        self._session_start = None
        with self._lock:
            self._reset_machine()
            self._latest = self._machine.snapshot
        self._wake.set()

    def _poke_live(self) -> None:
        if self._live is not None:
            self._live.poke()

    def _reset_machine(self) -> None:
        self._machine.reset()
        self._generation += 1
        self._replaying = True

    def _note_unknown_trial(self) -> None:
        trial_id = self._machine.snapshot.trial_id
        if trial_id and trial_id not in self._unknown_trials and self._catalog.trial(trial_id) is None:
            self._unknown_trials.add(trial_id)
            self._log.debug(f"Trial not in catalog: {trial_id}")

    def _publish(self) -> None:
        if not self._replaying:
            self._latest = self._machine.snapshot
            self._wake.set()

    def _run(self) -> None:
        try:
            wait = IDLE_WAIT
            while True:
                self._wake.wait(wait)
                self._wake.clear()
                if self._stop.is_set():
                    return
                with self._lock:
                    state, self._latest = self._latest, None
                if state is not None and state != self._state:
                    self._state = state
                    self._changed_at = time.monotonic()
                wait = self._tick()
        except Exception as exc:
            self._log.error(f"Presence stopped: {exc}")
        finally:
            self._take_down()
            self._client.close()

    def _tick(self) -> float:
        if not self.enabled or not self.game_running:
            if not self._retired:
                self._retired = True
                self._take_down()
                self._client.close()
            return IDLE_WAIT
        self._retired = False
        if self._session_start is None:
            self._session_start = time.time()

        payload = render(self._state, self._catalog, self._ids.profile_id, self._session_start)
        if payload == self._last_sent:
            return IDLE_WAIT
        if payload != self._pending:
            self._pending = payload
            self._held = False

        now = time.monotonic()
        if self._changed_at and now - self._changed_at < SETTLE:
            return SETTLE - (now - self._changed_at)
        hold = self._limiter.wait(now)
        if hold > 0.0:
            self._hold_once(f"Discord status held: rate limit, {hold:.0f}s left")
            return hold
        if not self._client.ensure_connected():
            self._hold_once("Discord status held: not connected")
            return max(self._client.next_attempt_in(now), 1.0)

        sent = self._client.clear() if payload is None else self._client.update(payload)
        if not sent:
            return max(self._client.next_attempt_in(now), 1.0)
        self._limiter.consume(now)
        self._last_sent = payload
        self._held = False
        return IDLE_WAIT

    def _hold_once(self, message: str) -> None:
        if not self._held:
            self._held = True
            self._log.debug(message)

    def _take_down(self) -> None:
        if self._last_sent is not None:
            self._client.clear()
            self._last_sent = None
            self._pending = None
