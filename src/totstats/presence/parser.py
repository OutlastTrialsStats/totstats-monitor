from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

STAGE_INFO_RE = re.compile(
    r"GameStageInfo changed\..*?Program ID:\s*(?P<program>\w+).*?Trial ID:\s*(?P<trial>\w+)"
    r".*?Program difficulty:\s*(?P<difficulty>\w+).*?EffectiveNumberOfPlayers:\s*(?P<players>\d+)"
)
PHASE_RE = re.compile(r"GamePhase changed to (?P<to>\w+)")
SOURCE_RE = re.compile(r"[?&]Source=(\w+)")

MAP_MAIN_MENU = "mainmenu"
MAP_LOBBY = "lobby"
MAP_TRIAL = "trial"

MAPS = (
    ("/Game/Maps/Global/MainMenu", MAP_MAIN_MENU),
    ("/Game/Maps/Lobby/Lobby_Persistent", MAP_LOBBY),
    ("/Game/Maps/Global/OPP_Persistent", MAP_TRIAL),
)

DIFFICULTIES = {"Easy": 1, "Normal": 2, "Hard": 3, "Insane": 4}

INTERESTS = (
    "GameStageInfo changed",
    "GamePhase changed to ",
    "LogLoad: LoadMap:",
    '"parties|',
    "LogExit: Exiting.",
)


@dataclass(frozen=True)
class PresenceSample:
    online: bool
    presence_state: str | None
    program_id: str | None
    trial_id: str | None
    difficulty: int | None
    chain_step: int | None
    player_count: int | None
    received_at: float


@dataclass(frozen=True)
class StageInfo:
    program_id: str | None
    trial_id: str | None
    difficulty: int | None
    players: int | None


@dataclass(frozen=True)
class PhaseChange:
    to_phase: str


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
    return value if isinstance(value, str) and value not in ("", "-") else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    number = int(value)
    return number if number > 0 else None


def parse_event(raw: str) -> Event | None:
    if "GameStageInfo changed" in raw:
        match = STAGE_INFO_RE.search(raw)
        if match is None:
            return None
        difficulty = DIFFICULTIES.get(match["difficulty"])
        return StageInfo(match["program"], match["trial"], difficulty, _count(int(match["players"])))
    if "GamePhase changed to " in raw:
        match = PHASE_RE.search(raw)
        return PhaseChange(match["to"]) if match else None
    if "LogLoad: LoadMap:" in raw:
        for needle, kind in MAPS:
            if needle in raw:
                source = SOURCE_RE.search(raw)
                return MapLoad(kind, source[1] if source else None)
        return None
    if '"parties|' in raw:
        return _party(raw)
    if "LogExit: Exiting." in raw:
        return GameExit()
    return None


def _party(raw: str) -> PartyRoster | None:
    try:
        message = json.loads(raw[raw.index("{") :])
    except ValueError:
        return None
    item_id = _text(message.get("dataItemId")) if isinstance(message, dict) else None
    if item_id is None or not item_id.startswith("parties|"):
        return None
    data = message.get("data")
    party = data.get("partyData") if isinstance(data, dict) else None
    team = _text(party.get("team1")) if isinstance(party, dict) else None
    members = len([member for member in (team or "").split("|") if member])
    if members == 0:
        return None
    return PartyRoster(item_id, members, _count(data.get("maxSize")))


def parse_sample(body: object, received_at: float) -> PresenceSample | None:
    if not isinstance(body, dict) or not isinstance(body.get("online"), bool):
        return None
    props = body.get("properties")
    if not isinstance(props, dict):
        props = {}
    difficulty = _count(props.get("programDifficulty"))
    return PresenceSample(
        online=body["online"],
        presence_state=_text(props.get("presenceState")),
        program_id=_text(props.get("programId")),
        trial_id=_text(props.get("trialId")),
        difficulty=difficulty if difficulty is not None and difficulty <= 4 else None,
        chain_step=_count(props.get("trialChain")),
        player_count=_count(props.get("playerCount")),
        received_at=received_at,
    )
