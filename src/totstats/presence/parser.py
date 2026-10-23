"""Turns game log lines and live presence responses into typed presence events.

Pure and stateless: every function here maps one line, or one response body, to at most one event,
so the state machine in state.py owns all the interpretation. Anything the game or the API words
differently after a patch has to degrade to None here rather than raise — the watcher thread and
the live poller run this.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from totstats.shared.log_tail import LogLine

# RB:  GameStageInfo changed. Program ID: programCoreCH, Trial ID: CHJ_MT03,
#      Program difficulty: Normal, Stage: CourthouseJudicial, Mission: CHJ_MT03, Seed: 617617,
#      EffectiveNumberOfPlayers: 1
STAGE_INFO_RE = re.compile(
    r"GameStageInfo changed\..*?Program ID:\s*(?P<program>\w+).*?Trial ID:\s*(?P<trial>\w+)"
    r".*?Program difficulty:\s*(?P<difficulty>\w+).*?Stage:\s*(?P<stage>\w+)"
    r".*?EffectiveNumberOfPlayers:\s*(?P<players>\d+)"
)

# RB:  GamePhase changed to StageStarted from StageReady.
PHASE_RE = re.compile(r"GamePhase changed to (?P<to>\w+) from (?P<from>\w+)")

# LogLoad: LoadMap: 1.2.3.4:7778/Game/Maps/Global/OPP_Persistent?...?Source=Lobby?game=...
SOURCE_RE = re.compile(r"[?&]Source=(\w+)")

MAP_MAIN_MENU = "mainmenu"
MAP_LOBBY = "lobby"
MAP_TRIAL = "trial"

_MAPS = (
    ("/Game/Maps/Global/MainMenu", MAP_MAIN_MENU),
    ("/Game/Maps/Lobby/Lobby_Persistent", MAP_LOBBY),
    ("/Game/Maps/Global/OPP_Persistent", MAP_TRIAL),
)

# The log names the difficulty; the live presence carries it as a number.
DIFFICULTY_BY_TEXT = {"Easy": 1, "Normal": 2, "Hard": 3, "Insane": 4}


@dataclass(frozen=True)
class PresenceSample:
    """Our own presence as the game server last saw it, fetched from outlasttrialsstats.com.

    The game stopped logging the presence it posts, so this is the only source for what the log
    never says: looking for a group, preparing, Escalation steps and the Invasion role. It lags
    the log by seconds, which is why state.py treats it as a supplement, never as the truth.
    """

    online: bool
    presence_state: str | None
    program_id: str | None
    trial_id: str | None
    difficulty: int | None
    chain_step: int | None
    player_count: int | None
    invasion_state: str | None

    received_at: float
    """Monotonic time the response arrived."""


@dataclass(frozen=True)
class StageInfo:
    program_id: str | None
    trial_id: str | None
    difficulty: int | None
    stage: str | None
    players: int | None


@dataclass(frozen=True)
class PhaseChange:
    to_phase: str
    from_phase: str


@dataclass(frozen=True)
class MapLoad:
    kind: str
    source: str | None


@dataclass(frozen=True)
class PartyRoster:
    item_id: str
    members: int
    max_size: int | None


@dataclass(frozen=True)
class GameExit:
    pass


Event = StageInfo | PhaseChange | MapLoad | PartyRoster | GameExit


def _text(value: object) -> str | None:
    """The game marks an unset text as "-" or ""."""
    return value if isinstance(value, str) and value not in ("", "-") else None


def _count(value: object) -> int | None:
    """The game sends every number as a float and marks an unset one as -1."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    number = int(value)
    return number if number > 0 else None


def _difficulty(value: object) -> int | None:
    number = _count(value)
    return number if number is not None and 1 <= number <= 4 else None


def _chain_step(value: object) -> int | None:
    """-1 outside a chain, 0 inside a plain trial; only a positive step means Escalation."""
    return _count(value)


class PresenceParser:
    INTERESTS = (
        "GameStageInfo changed",
        "GamePhase changed to ",
        "LogLoad: LoadMap:",
        '"parties|',
        "LogExit: Exiting.",
    )

    def parse(self, line: LogLine) -> Event | None:
        raw = line.raw
        if "GameStageInfo changed" in raw:
            return self._stage_info(raw)
        if "GamePhase changed to " in raw:
            return self._phase(raw)
        if "LogLoad: LoadMap:" in raw:
            return self._map(raw)
        if '"parties|' in raw:
            return self._party(raw)
        if "LogExit: Exiting." in raw:
            return GameExit()
        return None

    def _stage_info(self, raw: str) -> StageInfo | None:
        match = STAGE_INFO_RE.search(raw)
        if match is None:
            return None
        return StageInfo(
            program_id=match.group("program"),
            trial_id=match.group("trial"),
            difficulty=DIFFICULTY_BY_TEXT.get(match.group("difficulty")),
            stage=match.group("stage"),
            players=_count(int(match.group("players"))),
        )

    def _phase(self, raw: str) -> PhaseChange | None:
        match = PHASE_RE.search(raw)
        if match is None:
            return None
        return PhaseChange(to_phase=match.group("to"), from_phase=match.group("from"))

    def _map(self, raw: str) -> MapLoad | None:
        for needle, kind in _MAPS:
            if needle in raw:
                source = SOURCE_RE.search(raw)
                return MapLoad(kind=kind, source=source.group(1) if source else None)
        return None

    def _party(self, raw: str) -> PartyRoster | None:
        """The party roster, from the only RTA message that carries our own party.

        team1 is the roster as pipe-joined profile ids. allowList is the invite list and
        matchProfileIds are matchmaking candidates — neither is a party size.
        """
        message = _load_json(raw, raw.find("{"))
        if message is None:
            return None
        item_id = _text(message.get("dataItemId"))
        if item_id is None or not item_id.startswith("parties|"):
            return None
        data = message.get("data")
        if not isinstance(data, dict):
            return None
        party_data = data.get("partyData")
        if not isinstance(party_data, dict):
            return None
        team = _text(party_data.get("team1"))
        if team is None:
            return None
        members = len([member for member in team.split("|") if member])
        if members == 0:
            return None
        return PartyRoster(item_id=item_id, members=members, max_size=_count(data.get("maxSize")))


def _load_json(raw: str, start: int) -> dict | None:
    if start < 0:
        return None
    try:
        parsed = json.loads(raw[start:])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_sample(body: object, received_at: float) -> PresenceSample | None:
    """One raw presence object from the live API, or None when it is not the shape we expect.

    A profile without presence has properties null; the game's placeholders become None here.
    """
    if not isinstance(body, dict):
        return None
    online = body.get("online")
    if not isinstance(online, bool):
        return None
    props = body.get("properties")
    if not isinstance(props, dict):
        props = {}
    return PresenceSample(
        online=online,
        presence_state=_text(props.get("presenceState")),
        program_id=_text(props.get("programId")),
        trial_id=_text(props.get("trialId")),
        difficulty=_difficulty(props.get("programDifficulty")),
        chain_step=_chain_step(props.get("trialChain")),
        player_count=_count(props.get("playerCount")),
        invasion_state=_text(props.get("invasionState")),
        received_at=received_at,
    )
