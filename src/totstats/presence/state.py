"""Reduces presence events into one snapshot of what the player is doing.

Fed by the watcher thread (log events) and the live poller (presence samples); the service
serialises both. Every snapshot is frozen, so the presence worker can read one without locking and
compare two for equality.

The log is the fast truth: it names the trial the moment it is picked and the map the moment it
loads. A presence sample arrives seconds later and only refines that — it can say "looking for a
group" or "Escalation step 3", which the log cannot, but it never moves the player somewhere the
log says they are not.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from totstats.presence.parser import (
    Event,
    GameExit,
    MapLoad,
    PartyRoster,
    PhaseChange,
    PresenceSample,
    StageInfo,
)

PROGRAM_INVASION = "programINVASION"

PHASE_STARTED = "StageStarted"
PHASE_RETURN = "ReturnToLobby"
PHASE_ENDED = ("StageEnding", "StageSuccess", "StageFailed", "PostGameExitTimeout", PHASE_RETURN)
# Phases of a trial that is starting or running: seen on a trial map, the player is not on the way back.
PHASE_IN_TRIAL = ("WaitingForPlayers", "StageReady", PHASE_STARTED)

# A sample that was current within this long after a live log event may still describe the moment
# before it, so it is dropped. Measured on the monotonic clock.
SAMPLE_GRACE = 10.0

DEFAULT_PARTY_MAX = 4


class Activity(StrEnum):
    UNKNOWN = "unknown"
    MAIN_MENU = "mainmenu"
    SLEEP_ROOM = "lobby"
    MATCHMAKING = "findingparty"
    PREPARING = "preparingtrial"
    TRIAL = "trial"
    CHAIN = "trialchain"
    RETURNING = "returningtolobby"
    OFFLINE = "offline"


class InvasionRole(StrEnum):
    REAGENT = "reagent"
    IMPOSTER = "imposter"


_ACTIVITY_BY_PRESENCE = {
    "mainmenu": Activity.MAIN_MENU,
    "lobby": Activity.SLEEP_ROOM,
    "findingparty": Activity.MATCHMAKING,
    "preparingtrial": Activity.PREPARING,
    "trial": Activity.TRIAL,
    "trialchain": Activity.CHAIN,
    "invadingtrial": Activity.TRIAL,
    "returningtolobby": Activity.RETURNING,
}

_ACTIVITY_BY_MAP = {
    "mainmenu": Activity.MAIN_MENU,
    "lobby": Activity.SLEEP_ROOM,
    "trial": Activity.TRIAL,
}

_IN_TRIAL = (Activity.TRIAL, Activity.CHAIN)

_OUTSIDE_TRIAL_MAPS = ("mainmenu", "lobby")

# The presence states a sample may report on top of the map the log last loaded. Anything else is
# a sample from before that load and says nothing about now.
_SAMPLE_STATES_OUTSIDE_TRIAL = frozenset(("mainmenu", "lobby", "findingparty", "preparingtrial"))
_SAMPLE_STATES_BY_MAP = {
    "mainmenu": _SAMPLE_STATES_OUTSIDE_TRIAL,
    "lobby": _SAMPLE_STATES_OUTSIDE_TRIAL,
    "trial": frozenset(("trial", "trialchain", "invadingtrial", "returningtolobby")),
}
_ANY_SAMPLE_STATE = frozenset(_ACTIVITY_BY_PRESENCE)

# Once the log has seen the trial end, only the way back is still news.
_SAMPLE_STATES_WHILE_RETURNING = frozenset(("returningtolobby",))

# Only these states describe a trial, so only they may name one.
_SAMPLE_STATES_WITH_TRIAL = frozenset(("trial", "trialchain", "invadingtrial", "preparingtrial"))

# These say the player has no trial picked, so a trial only a sample named is gone with them.
_SAMPLE_STATES_WITHOUT_TRIAL = frozenset(("mainmenu", "lobby", "findingparty"))


@dataclass(frozen=True)
class GameState:
    activity: Activity = Activity.UNKNOWN
    program_id: str | None = None
    trial_id: str | None = None
    difficulty: int | None = None
    chain_step: int | None = None
    phase: str | None = None
    party_size: int | None = None
    party_max: int = DEFAULT_PARTY_MAX
    party_key: str | None = None
    invasion_role: InvasionRole | None = None
    trial_started_at: float | None = None

    @property
    def in_trial(self) -> bool:
        return self.activity in _IN_TRIAL

    @property
    def in_invasion(self) -> bool:
        """Party counts include the enemy team here, so they are not worth showing."""
        return self.invasion_role is not None or self.program_id == PROGRAM_INVASION


class ClockSync:
    """Translates log timestamps to POSIX seconds.

    The log prefix is naive: it carries no timezone. Comparing live lines against the wall clock
    tells us how far off it is, so Discord's elapsed counter stays right even if a future build
    logs UTC. Replayed lines never inform the offset — they are minutes or hours old by definition.
    """

    # Below this the difference is just tailing latency, not a timezone.
    THRESHOLD = 60.0

    def __init__(self, samples: int = 5) -> None:
        self._samples = samples
        self._offsets: list[float] = []

    def observe(self, ts: datetime | None) -> None:
        if ts is None:
            return
        self._offsets.append(time.time() - ts.timestamp())
        if len(self._offsets) > self._samples:
            self._offsets.pop(0)

    @property
    def offset(self) -> float:
        if not self._offsets:
            return 0.0
        middle = sorted(self._offsets)[len(self._offsets) // 2]
        return middle if abs(middle) > self.THRESHOLD else 0.0

    def to_posix(self, ts: datetime) -> float:
        return ts.timestamp() + self.offset


class StateMachine:
    """Applies log events in log order and presence samples as they arrive.

    apply() and apply_sample() report whether the snapshot changed. Neither is thread-safe; the
    service holds a lock around every call.
    """

    def __init__(
        self,
        clock: ClockSync | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock or ClockSync()
        self._monotonic = monotonic
        self._state = GameState()
        self._map_kind: str | None = None
        self._last_event_at: float | None = None
        # Whether trial_id came from a sample rather than the log; only then may a sample replace it.
        self._trial_from_sample = False

    @property
    def snapshot(self) -> GameState:
        return self._state

    def sample_hold(self) -> float:
        """Seconds until a sample current from now on would no longer be dropped by the grace."""
        if self._last_event_at is None:
            return 0.0
        return max(0.0, self._last_event_at + SAMPLE_GRACE - self._monotonic())

    def reset(self) -> None:
        self._state = GameState()
        self._map_kind = None
        self._last_event_at = None
        self._trial_from_sample = False

    def apply(self, event: Event, ts: datetime | None, live: bool) -> bool:
        if live:
            self.clock.observe(ts)

        before = self._state
        if isinstance(event, StageInfo):
            self._apply_stage(event)
        elif isinstance(event, PhaseChange):
            self._apply_phase(event, ts)
        elif isinstance(event, MapLoad):
            self._apply_map(event)
        elif isinstance(event, PartyRoster):
            self._apply_party(event)
        elif isinstance(event, GameExit):
            self._state = GameState(activity=Activity.OFFLINE)
            self._map_kind = None
            self._trial_from_sample = False
        changed = self._state != before
        # A replayed line is history; a sample fetched now is newer than any of it.
        if changed and live:
            self._last_event_at = self._monotonic()
        return changed

    def apply_sample(self, sample: PresenceSample) -> bool:
        before = self._state
        self._merge_sample(sample)
        return self._state != before

    # --- log events -----------------------------------------------------------------------

    def _apply_stage(self, stage: StageInfo) -> None:
        """The trial descriptor: the first word on which trial the player is in."""
        state = self._state
        if stage.trial_id != state.trial_id:
            # A new trial replaces the old one outright, gaps included: keeping the previous
            # trial's program or difficulty would describe a trial nobody is playing.
            state = replace(
                _enter_program(state, stage.program_id),
                program_id=stage.program_id,
                trial_id=stage.trial_id,
                difficulty=stage.difficulty,
                trial_started_at=None,
            )
        else:
            program_id = stage.program_id or state.program_id
            state = replace(
                _enter_program(state, program_id),
                program_id=program_id,
                difficulty=stage.difficulty or state.difficulty,
            )
        if stage.players is not None and not state.in_invasion:
            state = replace(state, party_size=stage.players)
        self._state = state
        self._trial_from_sample = False

    def _apply_phase(self, phase: PhaseChange, ts: datetime | None) -> None:
        self._state = replace(self._state, phase=phase.to_phase)
        if phase.to_phase == PHASE_STARTED and ts is not None:
            self._state = replace(self._state, trial_started_at=self.clock.to_posix(ts))
        elif phase.to_phase == PHASE_RETURN:
            self._leave_trial()
            self._state = replace(self._state, activity=Activity.RETURNING)
        elif phase.to_phase in PHASE_ENDED:
            self._clear_clock()
        # A trial that starts after a stale "returning" sample is still a trial.
        if (
            phase.to_phase in PHASE_IN_TRIAL
            and self._map_kind == "trial"
            and self._state.activity is Activity.RETURNING
        ):
            self._state = replace(self._state, activity=Activity.TRIAL)

    def _apply_map(self, load: MapLoad) -> None:
        self._map_kind = load.kind
        if load.kind in _OUTSIDE_TRIAL_MAPS:
            self._leave_trial()
        activity = _ACTIVITY_BY_MAP.get(load.kind)
        if activity is None:
            return
        # Every Escalation step loads the trial map again; that is still the same chain.
        if activity is Activity.TRIAL and self._state.activity is Activity.CHAIN:
            return
        self._state = replace(self._state, activity=activity)

    def _apply_party(self, roster: PartyRoster) -> None:
        if self._state.in_invasion:
            return
        self._state = replace(
            self._state,
            party_size=roster.members,
            party_max=roster.max_size or self._state.party_max,
            # The party id is an account identifier; Discord only needs something stable.
            party_key=hashlib.sha1(roster.item_id.encode("utf-8")).hexdigest()[:16],
        )

    # --- presence samples -----------------------------------------------------------------

    def _merge_sample(self, sample: PresenceSample) -> None:
        # Samples are only asked for while the game runs; "offline" is the server lagging behind.
        if not sample.online:
            return
        if self._last_event_at is not None and sample.received_at < self._last_event_at + SAMPLE_GRACE:
            return
        presence = sample.presence_state
        if presence is None or presence not in self._allowed_sample_states():
            return

        if presence == "returningtolobby":
            self._leave_trial()
            self._state = replace(self._state, activity=Activity.RETURNING)
            return

        state = self._state
        activity = _ACTIVITY_BY_PRESENCE[presence]
        names_trial = presence in _SAMPLE_STATES_WITH_TRIAL and sample.trial_id is not None
        if not names_trial:
            state = replace(
                state,
                activity=activity,
                chain_step=sample.chain_step,
                invasion_role=_role_for(sample),
            )
            # Nothing picked any more: a trial only a sample named was cancelled. One the log named
            # on a trial map stays, the sample just has not caught up.
            if presence in _SAMPLE_STATES_WITHOUT_TRIAL and (
                self._trial_from_sample or self._map_kind in _OUTSIDE_TRIAL_MAPS
            ):
                state = replace(state, trial_id=None, program_id=None, difficulty=None)
                self._trial_from_sample = False
        elif state.trial_id == sample.trial_id and not self._trial_from_sample:
            state = replace(
                state,
                activity=activity,
                program_id=sample.program_id or state.program_id,
                difficulty=sample.difficulty or state.difficulty,
                chain_step=sample.chain_step,
                invasion_role=_role_for(sample),
            )
        elif state.trial_id is None or self._trial_from_sample:
            state = replace(
                state,
                activity=activity,
                trial_id=sample.trial_id,
                program_id=sample.program_id,
                difficulty=sample.difficulty,
                chain_step=sample.chain_step,
                invasion_role=_role_for(sample),
            )
            self._trial_from_sample = True
        elif state.program_id not in (None, sample.program_id):
            # The log has moved on to a trial of another program; the sample is from before that.
            return
        else:
            # The log names another trial of the same program. Only within an Escalation chain does
            # the sample still know something the log does not: the step. Its role is from the
            # trial it names, which may well be another round.
            state = replace(state, activity=activity)
            if presence == "trialchain":
                state = replace(state, chain_step=sample.chain_step)
        # Own player count, reliable outside a trial. Inside one EffectiveNumberOfPlayers is
        # closer to the truth, so the sample only fills a gap there.
        if sample.player_count is not None and not state.in_invasion:
            if not state.in_trial or state.party_size is None:
                state = replace(state, party_size=sample.player_count)
        self._state = state

    def _allowed_sample_states(self) -> frozenset[str]:
        if self._state.activity is Activity.OFFLINE:
            return frozenset()
        if self._state.activity is Activity.RETURNING:
            return _SAMPLE_STATES_WHILE_RETURNING
        if self._map_kind is None:
            return _ANY_SAMPLE_STATE
        return _SAMPLE_STATES_BY_MAP.get(self._map_kind, _ANY_SAMPLE_STATE)

    # --- helpers --------------------------------------------------------------------------

    def _leave_trial(self) -> None:
        self._trial_from_sample = False
        self._state = replace(
            self._state,
            program_id=None,
            trial_id=None,
            difficulty=None,
            chain_step=None,
            invasion_role=None,
            trial_started_at=None,
        )

    def _clear_clock(self) -> None:
        if self._state.trial_started_at is not None:
            self._state = replace(self._state, trial_started_at=None)


def _enter_program(state: GameState, program_id: str | None) -> GameState:
    """A different program starts from scratch: no chain step, and no role outside Invasion."""
    if program_id == state.program_id:
        return state
    return replace(
        state,
        chain_step=None,
        invasion_role=state.invasion_role if program_id == PROGRAM_INVASION else None,
    )


def _role_for(sample: PresenceSample) -> InvasionRole | None:
    if sample.presence_state == "invadingtrial":
        return InvasionRole.IMPOSTER
    if sample.program_id == PROGRAM_INVASION:
        return InvasionRole.REAGENT
    return None
