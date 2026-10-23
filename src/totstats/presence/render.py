from __future__ import annotations

from dataclasses import dataclass

from totstats.presence.catalog import SITE, Catalog, TrialInfo, image
from totstats.presence.state import Activity, GameState, InvasionRole

MAX_TEXT = 128
MIN_TEXT = 2

SEPARATOR = " • "
GAME_NAME = "The Outlast Trials"

ROLE_ICONS = {InvasionRole.REAGENT: "icon_reagent", InvasionRole.IMPOSTER: "icon_invader"}


@dataclass(frozen=True)
class Payload:
    details: str
    state: str
    large_image: str
    large_text: str
    small_image: str | None = None
    small_text: str | None = None
    party_id: str | None = None
    party_size: tuple[int, int] | None = None
    start: int | None = None
    buttons: tuple[tuple[str, str], ...] = ()

    def as_kwargs(self) -> dict:
        payload = {
            "details": self.details,
            "state": self.state,
            "large_image": self.large_image,
            "large_text": self.large_text,
        }
        if self.small_image:
            payload["small_image"] = self.small_image
            payload["small_text"] = self.small_text
        if self.party_size:
            payload["party_id"] = self.party_id
            payload["party_size"] = list(self.party_size)
        if self.start:
            payload["start"] = self.start
        if self.buttons:
            payload["buttons"] = [{"label": label, "url": url} for label, url in self.buttons]
        return payload

    def summary(self) -> str:
        return f"{self.details} | {self.state}"


def _clip(text: str) -> str:
    return text.strip().ljust(MIN_TEXT)[:MAX_TEXT]


def render(
    state: GameState,
    catalog: Catalog,
    profile_id: str | None = None,
    session_start: float | None = None,
) -> Payload | None:
    if state.activity in (Activity.UNKNOWN, Activity.OFFLINE):
        return None

    trial = catalog.trial(state.trial_id)
    details, description = _describe(
        state, trial, catalog.program_name(state.program_id), catalog.difficulty_name(state.difficulty)
    )

    role = state.invasion_role
    small_image = image(ROLE_ICONS[role]) if role else None
    small_text = f"Invasion — {role.value.title()}" if role else None

    party_id, party_size = None, None
    if state.party_size and state.party_size >= 2 and not state.in_invasion:
        party_id = state.party_key or "party"
        party_size = (state.party_size, max(state.party_max, state.party_size))

    start = state.trial_started_at if state.in_trial and state.trial_started_at else session_start

    large_text = GAME_NAME
    if trial is not None:
        large_text = f"{trial.name} — {trial.location}" if trial.location else trial.name

    return Payload(
        details=_clip(details),
        state=_clip(description),
        large_image=image(trial.banner if trial else None),
        large_text=_clip(large_text),
        small_image=small_image,
        small_text=small_text,
        party_id=party_id,
        party_size=party_size,
        start=int(start) if start else None,
        buttons=(("View Stats", f"{SITE}/profile/{profile_id}"),) if profile_id else (),
    )


def _describe(
    state: GameState, trial: TrialInfo | None, program: str | None, difficulty: str | None
) -> tuple[str, str]:
    if state.activity is Activity.MAIN_MENU:
        return "Main Menu", "Idle"
    if state.activity is Activity.MATCHMAKING:
        return "Sleep Room", "Looking for a group"
    if state.activity is Activity.RETURNING:
        return "Sleep Room", "Returning from a Trial"
    if state.activity is Activity.SLEEP_ROOM:
        return "Sleep Room", "Idle"
    if state.activity is Activity.PREPARING and trial is None:
        return "Sleep Room", "Preparing a Trial"

    parts = []
    if state.activity is Activity.CHAIN:
        headline = program or "Escalation"
        if trial is not None:
            parts.append(trial.name)
        if state.chain_step:
            parts.append(f"Step {state.chain_step}")
    else:
        headline = trial.name if trial is not None else (program or "In a Trial")
        if trial is not None and trial.location:
            parts.append(trial.location)

    if state.invasion_role is not None:
        parts = [f"Invasion{SEPARATOR}{state.invasion_role.value.title()}"]
    if state.activity is Activity.PREPARING:
        parts.insert(0, "Preparing")
    if difficulty:
        parts.append(difficulty)

    return headline, SEPARATOR.join(parts) if parts else GAME_NAME
