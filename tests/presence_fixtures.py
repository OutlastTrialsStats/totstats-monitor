"""Hand-built log lines and presence samples for the presence tests.

The log lines follow what the game writes, with addresses, session ids and tokens replaced. The
sequence in SESSION_2210 is the session from 22.10. that kept showing its first trial.
"""

from __future__ import annotations

from totstats.presence.parser import PresenceParser, PresenceSample
from totstats.presence.state import StateMachine
from totstats.shared.log_tail import LogLine, parse_line

PROFILE_ID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"

PROGRAM_HALLOWEEN = "programHalloween"
PROGRAM_CHAIN = "programCHAIN"
PROGRAM_INVASION = "programINVASION"


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeLog:
    def __init__(self) -> None:
        self.debugs: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def debug(self, message: str) -> None:
        self.debugs.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


def line(ts: str, body: str, replay: bool = False) -> LogLine:
    return parse_line(f"[{ts}][ 42]{body}", replay=replay)


def main_menu_map(ts: str) -> LogLine:
    return line(ts, "LogLoad: LoadMap: /Game/Maps/Global/MainMenu?closed")


def lobby_map(ts: str) -> LogLine:
    return line(
        ts,
        "LogLoad: LoadMap: 10.0.0.1:7785/Game/Maps/Lobby/Lobby_Persistent?PlayerSessionId=psess-x"
        f"?EncryptionToken={PROFILE_ID}?Source=Lobby",
    )


def trial_map(ts: str) -> LogLine:
    return line(
        ts,
        "LogLoad: LoadMap: 10.0.0.2:7780/Game/Maps/Global/OPP_Persistent?PlayerSessionId=psess-y"
        f"?EncryptionToken={PROFILE_ID}?Source=Lobby",
    )


def stage(
    ts: str, program: str, trial: str, difficulty: str = "Normal", players: int = 4
) -> LogLine:
    return line(
        ts,
        f"RB:  GameStageInfo changed. Program ID: {program}, Trial ID: {trial}, "
        f"Program difficulty: {difficulty}, Stage: SomeStage, Mission: {trial}, Seed: 1234, "
        f"EffectiveNumberOfPlayers: {players}",
    )


def phase(ts: str, to_phase: str, from_phase: str) -> LogLine:
    return line(ts, f"RB:  GamePhase changed to {to_phase} from {from_phase}.")


def authenticated(ts: str) -> LogLine:
    return line(ts, f"LogOnline: Client authentication succeeded. Profile ID: {PROFILE_ID}")


def sample(
    received_at: float,
    presence_state: str | None,
    trial_id: str | None = None,
    program_id: str | None = None,
    difficulty: int | None = None,
    chain_step: int | None = None,
    player_count: int | None = None,
    online: bool = True,
) -> PresenceSample:
    return PresenceSample(
        online=online,
        presence_state=presence_state,
        program_id=program_id,
        trial_id=trial_id,
        difficulty=difficulty,
        chain_step=chain_step,
        player_count=player_count,
        invasion_state=None,
        received_at=received_at,
    )


class Feeder:
    """Runs log lines through the real parser into a state machine, the way the service does."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.machine = StateMachine(monotonic=self.clock)
        self._parser = PresenceParser()

    @property
    def state(self):
        return self.machine.snapshot

    def feed(self, *lines: LogLine) -> None:
        for entry in lines:
            event = self._parser.parse(entry)
            if event is not None:
                self.machine.apply(event, entry.ts, live=not entry.replay)

    def offer(self, presence_sample: PresenceSample) -> bool:
        return self.machine.apply_sample(presence_sample)


# The 22.10. session: a Police Station trial, back to the Sleep Room, then an Orphanage trial on
# Insane that the old state machine refused to take over.
SESSION_2210_FIRST_TRIAL = (
    main_menu_map("2026.10.22-17.03.23:957"),
    lobby_map("2026.10.22-17.04.03:073"),
    trial_map("2026.10.22-17.05.35:493"),
    phase("2026.10.22-17.05.36:459", "WaitingForPlayers", "None"),
    stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01", "Normal", 4),
    phase("2026.10.22-17.07.21:680", "StageStarted", "StageReady"),
    phase("2026.10.22-17.20.51:049", "StageEnding", "StageStarted"),
    phase("2026.10.22-17.22.18:432", "StageSuccess", "StageEnding"),
    phase("2026.10.22-17.22.52:648", "ReturnToLobby", "StageSuccess"),
)
SESSION_2210_BACK_IN_LOBBY = (
    lobby_map("2026.10.22-17.23.40:033"),
    lobby_map("2026.10.22-17.25.08:322"),
)
SESSION_2210_SECOND_TRIAL = (
    trial_map("2026.10.22-17.27.04:258"),
    phase("2026.10.22-17.27.05:368", "WaitingForPlayers", "None"),
    stage("2026.10.22-17.27.05:372", PROGRAM_HALLOWEEN, "ORI_MT02", "Insane", 4),
)
SESSION_2210_SECOND_TRIAL_STARTED = (phase("2026.10.22-17.28.51:352", "StageStarted", "StageReady"),)
