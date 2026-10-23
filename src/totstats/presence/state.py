from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from totstats.presence.parser import (
    MAP_LOBBY,
    MAP_MAIN_MENU,
    MAP_TRIAL,
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
PHASE_IN_TRIAL = ("WaitingForPlayers", "StageReady", PHASE_STARTED)

SAMPLE_GRACE = 10.0

CLOCK_SAMPLES = 5
CLOCK_THRESHOLD = 60.0


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


ACTIVITIES = {
    "mainmenu": Activity.MAIN_MENU,
    "lobby": Activity.SLEEP_ROOM,
    "findingparty": Activity.MATCHMAKING,
    "preparingtrial": Activity.PREPARING,
    "trial": Activity.TRIAL,
    "trialchain": Activity.CHAIN,
    "invadingtrial": Activity.TRIAL,
    "returningtolobby": Activity.RETURNING,
}

OUTSIDE_MAPS = (MAP_MAIN_MENU, MAP_LOBBY)

OUTSIDE_STATES = ("mainmenu", "lobby", "findingparty", "preparingtrial")
STATES_BY_MAP = {
    MAP_MAIN_MENU: OUTSIDE_STATES,
    MAP_LOBBY: OUTSIDE_STATES,
    MAP_TRIAL: ("trial", "trialchain", "invadingtrial", "returningtolobby"),
}
TRIAL_STATES = ("trial", "trialchain", "invadingtrial", "preparingtrial")
NO_TRIAL_STATES = ("mainmenu", "lobby", "findingparty")


@dataclass(frozen=True)
class GameState:
    activity: Activity = Activity.UNKNOWN
    program_id: str | None = None
    trial_id: str | None = None
    difficulty: int | None = None
    chain_step: int | None = None
    party_size: int | None = None
    party_max: int = 4
    party_key: str | None = None
    invasion_role: InvasionRole | None = None
    trial_started_at: float | None = None

    @property
    def in_trial(self) -> bool:
        return self.activity in (Activity.TRIAL, Activity.CHAIN)

    @property
    def in_invasion(self) -> bool:
        return self.invasion_role is not None or self.program_id == PROGRAM_INVASION


class StateMachine:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._offsets: deque[float] = deque(maxlen=CLOCK_SAMPLES)
        self._state = GameState()
        self._map_kind: str | None = None
        self._last_event_at: float | None = None
        self._trial_from_sample = False

    @property
    def snapshot(self) -> GameState:
        return self._state

    def sample_hold(self) -> float:
        if self._last_event_at is None:
            return 0.0
        return max(0.0, self._last_event_at + SAMPLE_GRACE - self._monotonic())

    def reset(self) -> None:
        self._state = GameState()
        self._map_kind = None
        self._last_event_at = None
        self._trial_from_sample = False

    def apply(self, event: Event, ts: datetime | None, live: bool) -> bool:
        if live and ts is not None:
            self._offsets.append(time.time() - ts.timestamp())

        before = self._state
        if isinstance(event, StageInfo):
            self._apply_stage(event)
        elif isinstance(event, PhaseChange):
            self._apply_phase(event.to_phase, ts)
        elif isinstance(event, MapLoad):
            self._apply_map(event.kind)
        elif isinstance(event, PartyRoster):
            self._apply_party(event)
        elif isinstance(event, GameExit):
            self._state = GameState(activity=Activity.OFFLINE)
            self._map_kind = None
            self._trial_from_sample = False
        changed = self._state != before
        if changed and live:
            self._last_event_at = self._monotonic()
        return changed

    def apply_sample(self, sample: PresenceSample) -> bool:
        before = self._state
        if not sample.online:
            return False
        if self._last_event_at is not None and sample.received_at < self._last_event_at + SAMPLE_GRACE:
            return False
        presence = sample.presence_state
        if presence is None or presence not in self._allowed_states():
            return False

        if presence == "returningtolobby":
            self._leave_trial()
            self._state = replace(self._state, activity=Activity.RETURNING)
            return self._state != before

        state = before
        activity = ACTIVITIES[presence]
        names_trial = presence in TRIAL_STATES and sample.trial_id is not None
        if names_trial and state.trial_id not in (None, sample.trial_id) and not self._trial_from_sample:
            if state.program_id not in (None, sample.program_id):
                return False
            state = replace(state, activity=activity)
            if presence == "trialchain":
                state = replace(state, chain_step=sample.chain_step)
        else:
            state = replace(
                state, activity=activity, chain_step=sample.chain_step, invasion_role=_role_for(sample)
            )
            if not names_trial:
                if presence in NO_TRIAL_STATES and (
                    self._trial_from_sample or self._map_kind in OUTSIDE_MAPS
                ):
                    state = replace(state, trial_id=None, program_id=None, difficulty=None)
                    self._trial_from_sample = False
            elif state.trial_id == sample.trial_id and not self._trial_from_sample:
                state = replace(
                    state,
                    program_id=sample.program_id or state.program_id,
                    difficulty=sample.difficulty or state.difficulty,
                )
            else:
                state = replace(
                    state,
                    trial_id=sample.trial_id,
                    program_id=sample.program_id,
                    difficulty=sample.difficulty,
                )
                self._trial_from_sample = True

        if sample.player_count is not None and not state.in_invasion:
            if not state.in_trial or state.party_size is None:
                state = replace(state, party_size=sample.player_count)
        self._state = state
        return state != before

    def _allowed_states(self) -> tuple[str, ...]:
        if self._state.activity is Activity.OFFLINE:
            return ()
        if self._state.activity is Activity.RETURNING:
            return ("returningtolobby",)
        return STATES_BY_MAP.get(self._map_kind, tuple(ACTIVITIES))

    def _apply_stage(self, stage: StageInfo) -> None:
        state = self._state
        if stage.trial_id != state.trial_id:
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

    def _apply_phase(self, phase: str, ts: datetime | None) -> None:
        if phase == PHASE_STARTED and ts is not None:
            self._state = replace(self._state, trial_started_at=self._posix(ts))
        elif phase == PHASE_RETURN:
            self._leave_trial()
            self._state = replace(self._state, activity=Activity.RETURNING)
        elif phase in PHASE_ENDED:
            self._state = replace(self._state, trial_started_at=None)
        returning = self._state.activity is Activity.RETURNING
        if returning and phase in PHASE_IN_TRIAL and self._map_kind == MAP_TRIAL:
            self._state = replace(self._state, activity=Activity.TRIAL)

    def _apply_map(self, kind: str) -> None:
        self._map_kind = kind
        if kind in OUTSIDE_MAPS:
            self._leave_trial()
        activity = Activity(kind)
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
            party_key=hashlib.sha1(roster.item_id.encode("utf-8")).hexdigest()[:16],
        )

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

    def _posix(self, ts: datetime) -> float:
        offset = sorted(self._offsets)[len(self._offsets) // 2] if self._offsets else 0.0
        return ts.timestamp() + (offset if abs(offset) > CLOCK_THRESHOLD else 0.0)


def _enter_program(state: GameState, program_id: str | None) -> GameState:
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
