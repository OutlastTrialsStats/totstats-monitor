"""Wires the log tailer to Discord: on_line reduces state, a worker thread publishes it.

The watcher thread must never wait on a socket, so it only ever drops the newest state into a
mailbox. The worker decides when that state is worth sending — Discord rate-limits status updates,
and most log lines do not change anything the user can see.

The live poller feeds presence samples into the same state machine from its own thread. A lock
serialises it with the watcher, and a generation counter tells it when a sample it fetched
belongs to a state that has since been thrown away.
"""

from __future__ import annotations

import threading
import time

from totstats.presence.catalog import Catalog
from totstats.presence.client import DiscordClient
from totstats.presence.live_api import LivePresenceApi
from totstats.presence.live_poller import LivePresencePoller, LiveTarget
from totstats.presence.parser import MapLoad, PresenceParser, PresenceSample
from totstats.presence.render import Payload, render
from totstats.presence.state import GameState, StateMachine
from totstats.shared.applog import AppLog
from totstats.shared.log_tail import LogLine
from totstats.shared.profile_id import OwnProfileIdResolver

# Discord drops updates that arrive faster than roughly one per fifteen seconds. Two tokens let a
# pair of quick transitions through — trial selected, trial started — without stalling either.
RATE_CAPACITY = 2
RATE_REFILL = 15.0
RATE_FLOOR = 5.0

# GameStageInfo and the presence the game posts describe the same transition about a second and a
# half apart. Waiting for quiet turns them into one update.
SETTLE = 2.0

# A replay that never reports being finished must not keep the status blank forever.
REPLAY_GUARD = 30.0

IDLE_WAIT = 5.0

WARN_INTERVAL = 60.0

# The state fields worth naming in the log. party_key is a hash of an account id and
# trial_started_at is an epoch; neither reads as anything useful in a log line.
_TRACKED = (
    "activity",
    "program_id",
    "trial_id",
    "difficulty",
    "chain_step",
    "phase",
    "party_size",
    "party_max",
    "invasion_role",
)


def _show(value: object) -> str:
    return "—" if value is None else str(value)


def _describe(before: GameState, after: GameState) -> str:
    changes = [
        f"{field} {_show(getattr(before, field))}→{_show(getattr(after, field))}"
        for field in _TRACKED
        if getattr(before, field) != getattr(after, field)
    ]
    if (before.trial_started_at is None) != (after.trial_started_at is None):
        changes.append("clock started" if after.trial_started_at else "clock cleared")
    return ", ".join(changes)


class RateLimiter:
    """Token bucket with a floor between sends, so bursts coalesce instead of being dropped."""

    def __init__(
        self, capacity: int = RATE_CAPACITY, refill: float = RATE_REFILL, floor: float = RATE_FLOOR
    ) -> None:
        self._capacity = capacity
        self._refill = refill
        self._floor = floor
        self._tokens = float(capacity)
        self._last = 0.0
        self._sent = 0.0

    def _replenish(self, now: float) -> None:
        if self._last:
            self._tokens = min(self._capacity, self._tokens + (now - self._last) / self._refill)
        self._last = now

    def allow(self, now: float) -> bool:
        self._replenish(now)
        return self._tokens >= 1.0 and (not self._sent or now - self._sent >= self._floor)

    def consume(self, now: float) -> None:
        self._replenish(now)
        self._tokens = max(0.0, self._tokens - 1.0)
        self._sent = now

    def wait_for(self, now: float) -> float:
        self._replenish(now)
        waits = []
        if self._tokens < 1.0:
            waits.append((1.0 - self._tokens) * self._refill)
        if self._sent and now - self._sent < self._floor:
            waits.append(self._floor - (now - self._sent))
        return max(waits) if waits else 0.0


class _Mailbox:
    """A single slot: only the newest state matters, so a burst collapses into one send."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._value: GameState | None = None
        self._closed = False

    def put(self, value: GameState) -> None:
        with self._lock:
            self._value = value
        self._event.set()

    def nudge(self) -> None:
        self._event.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._event.set()

    def take(self, timeout: float) -> tuple[GameState | None, bool]:
        self._event.wait(timeout)
        self._event.clear()
        with self._lock:
            value, self._value = self._value, None
            return value, self._closed


class PresenceService:
    INTERESTS = PresenceParser.INTERESTS + OwnProfileIdResolver.INTERESTS

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

        self._parser = PresenceParser()
        self._machine = machine or StateMachine()
        # Guards the state machine, _generation and _replaying: the watcher and the live poller
        # both write them.
        self._lock = threading.Lock()
        self._generation = 0
        self._live = None if live_api is None else LivePresencePoller(live_api, self, log)
        self._mailbox = _Mailbox()
        self._limiter = RateLimiter()
        self._worker: threading.Thread | None = None

        self._state = GameState()
        self._session_start: float | None = None
        self._last_sent: Payload | None = None
        self._pending: Payload | None = None
        self._held = False
        self._changed_at = 0.0
        self._replaying = True
        self._live_since = 0.0
        self._unknown_trials: set[str] = set()
        self._warned_at = 0.0
        self._retired = False

        # Read by the worker, written by the tray. Parsing continues either way: switching the
        # feature on mid-session has to show the trial you are in right now, and by then there is
        # no replay left to rebuild it from.
        self.enabled = True
        self.game_running = True

    def start(self) -> None:
        if self._worker is not None:
            return
        if not self._client.configured:
            self._log.warning("⚠️ Discord Rich Presence is not configured and stays off")
            return
        self._worker = threading.Thread(target=self._run, name="presence-rpc", daemon=True)
        self._worker.start()
        if self._live is not None:
            self._live.start()

    def stop(self, timeout: float = 3.0) -> None:
        worker = self._worker
        if self._live is not None:
            self._live.stop(timeout)
        self._mailbox.close()
        if worker is None:
            return
        self._worker = None
        worker.join(timeout)
        if worker.is_alive():
            self._log.warning("presence worker did not stop in time")

    # --- watcher thread -------------------------------------------------------------------

    def on_line(self, line: LogLine) -> None:
        try:
            self._feed(line)
        except Exception as exc:  # noqa: BLE001 - the watcher thread must survive a bad line
            now = time.monotonic()
            if now - self._warned_at > WARN_INTERVAL:
                self._warned_at = now
                self._log.warning(f"⚠️ Rich Presence could not read a log line: {exc}")

    def on_rotate(self) -> None:
        self._log.debug("🎮 New log file, rebuilding the game state")
        with self._lock:
            self._reset_machine()
            self._unknown_trials.clear()
            self._publish()
        self._poke_live()

    def on_replay_complete(self) -> None:
        """Nothing is published during a replay, so a mid-session start shows one state, not ten."""
        with self._lock:
            if not self._replaying:
                return
            self._replaying = False
            state = _describe(GameState(), self._machine.snapshot) or "nothing known yet"
            self._log.debug(f"🎮 Caught up with the log: {state}")
            self._publish()
        self._poke_live()

    def _feed(self, line: LogLine) -> None:
        self._ids.feed(line)
        event = self._parser.parse(line)
        if event is None:
            return
        with self._lock:
            before = self._machine.snapshot
            if not self._machine.apply(event, line.ts, live=not line.replay):
                return
            # A replay walks through every transition of the session; only the result is worth
            # a line.
            if not self._replaying:
                changed = _describe(before, self._machine.snapshot)
                if changed:
                    self._log.debug(f"🎮 {changed}")
            self._note_unknown_trial()
            self._publish()
        if isinstance(event, MapLoad) and not line.replay:
            self._poke_live()

    # --- live poller thread ---------------------------------------------------------------

    def live_target(self) -> LiveTarget | None:
        """Whom the poller should ask about, or None while nothing is worth asking for."""
        if not self.enabled or not self.game_running:
            return None
        profile_id = self._ids.profile_id
        with self._lock:
            # A sample merged into a half-replayed state would be overwritten by the rest of it.
            if profile_id is None or self._replaying:
                return None
            return LiveTarget(profile_id, self._generation, self._machine.sample_hold())

    def on_sample(self, sample: PresenceSample, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._replaying:
                self._log.debug("🌐 Dropped a live presence sample from before the state was reset")
                return
            before = self._machine.snapshot
            if not self._machine.apply_sample(sample):
                return
            changed = _describe(before, self._machine.snapshot)
            if changed:
                self._log.debug(f"🌐 {changed}")
            self._note_unknown_trial()
            self._publish()

    def _poke_live(self) -> None:
        if self._live is not None:
            self._live.poke()

    def _reset_machine(self) -> None:
        """Called with the lock held. Every sample in flight belongs to the old state from here."""
        self._machine.reset()
        self._generation += 1
        self._replaying = True
        self._live_since = 0.0

    def _note_unknown_trial(self) -> None:
        trial_id = self._machine.snapshot.trial_id
        if not trial_id or trial_id in self._unknown_trials:
            return
        if self._catalog.trial(trial_id) is None:
            self._unknown_trials.add(trial_id)
            self._log.debug(f"❓ No catalog entry for trial {trial_id}")

    def _publish(self) -> None:
        if self._replaying:
            if not self._live_since:
                self._live_since = time.monotonic()
            return
        self._mailbox.put(self._machine.snapshot)

    # --- main thread ----------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._mailbox.nudge()
        if enabled:
            self._poke_live()

    def set_catalog(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._mailbox.nudge()

    def on_game_started(self, started_at: float | None = None) -> None:
        self.game_running = True
        self._session_start = started_at or time.time()
        with self._lock:
            self._reset_machine()
        self._mailbox.nudge()
        self._poke_live()

    def on_game_stopped(self) -> None:
        self.game_running = False
        self._session_start = None
        with self._lock:
            self._reset_machine()
            snapshot = self._machine.snapshot
        self._mailbox.put(snapshot)

    # --- worker thread --------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001 - one dead feature must not take the app with it
            self._log.error(f"❌ Discord Rich Presence stopped: {exc}")
        finally:
            self._take_down()
            self._client.close()

    def _loop(self) -> None:
        wait = IDLE_WAIT
        while True:
            state, closed = self._mailbox.take(wait)
            if closed:
                return
            if state is not None and state != self._state:
                self._state = state
                self._changed_at = time.monotonic()
            wait = self._tick()

    def _tick(self) -> float:
        """Publishes what is due and returns how long to sleep before looking again."""
        self._escape_stuck_replay()

        if not self.enabled or not self.game_running:
            self._retire()
            return IDLE_WAIT
        self._retired = False
        # A dry run never sees a game process, so the anchor falls back to the first status shown.
        if self._session_start is None:
            self._session_start = time.time()

        payload = render(self._state, self._catalog, self._ids.profile_id, self._session_start)
        if payload == self._last_sent:
            return IDLE_WAIT
        if payload != self._pending:
            self._pending = payload
            self._held = False

        now = time.monotonic()
        settling = self._changed_at and now - self._changed_at < SETTLE
        if settling:
            return SETTLE - (now - self._changed_at)
        if not self._limiter.allow(now):
            hold = self._limiter.wait_for(now)
            self._hold_once(f"⏳ Discord only takes a new status every few seconds; {hold:.0f}s to go")
            return hold
        if not self._client.ensure_connected():
            self._hold_once("⏳ Waiting for Discord before the status can change")
            return max(self._client.next_attempt_in(now), 1.0)

        sent = self._client.clear() if payload is None else self._client.update(payload)
        if not sent:
            return max(self._client.next_attempt_in(now), 1.0)
        self._limiter.consume(now)
        self._last_sent = payload
        self._held = False
        return IDLE_WAIT

    def _hold_once(self, message: str) -> None:
        """One line per held update, not one per pass through the loop."""
        if self._held:
            return
        self._held = True
        self._log.debug(message)

    def _escape_stuck_replay(self) -> None:
        """The tailer always reports the end of a replay; publish anyway if it somehow does not."""
        if not self._replaying or not self._live_since:
            return
        if time.monotonic() - self._live_since > REPLAY_GUARD:
            self._log.debug("🎮 The log replay never reported finishing; showing the status anyway")
            with self._lock:
                self._replaying = False
                snapshot = self._machine.snapshot
            self._mailbox.put(snapshot)
            self._poke_live()

    def _retire(self) -> None:
        if self._retired:
            return
        self._retired = True
        self._take_down()
        self._client.close()

    def _take_down(self) -> None:
        """Clear the status, but only when one is actually on the profile."""
        if self._last_sent is None:
            return
        self._client.clear()
        self._last_sent = None
        self._pending = None
