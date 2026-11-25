from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from totstats.presence.catalog import SITE, Catalog, TrialInfo, image
from totstats.presence.state import Activity, GameState, InvasionRole
from totstats.shared.i18n import Translator, english
from totstats.shared.settings import PresenceOptions

MAX_TEXT = 128
MIN_TEXT = 2

SEPARATOR = " • "
GAME_NAME = "The Outlast Trials"

TRIAL_ACTIVITIES = (Activity.PREPARING, Activity.TRIAL, Activity.CHAIN)

ROLE_ICONS = {InvasionRole.REAGENT: "icon_reagent", InvasionRole.IMPOSTER: "icon_invader"}

PLACEHOLDERS = ("trial", "location", "program", "difficulty", "step", "role", "activity", "party")

MAX_BUTTON = 32

DEFAULT_OPTIONS = PresenceOptions()

# Outside a trial: (top line, bottom line) per activity.
OUTSIDE = {
    Activity.MAIN_MENU: ("presence.main_menu", "presence.idle"),
    Activity.SLEEP_ROOM: ("presence.sleep_room", "presence.idle"),
    Activity.MATCHMAKING: ("presence.sleep_room", "presence.looking_for_group"),
    Activity.RETURNING: ("presence.sleep_room", "presence.returning"),
    Activity.PREPARING: ("presence.sleep_room", "presence.preparing_trial"),
}


def role_name(role: InvasionRole, tr: Translator) -> str:
    return tr.t(f"presence.role_{role.value}")


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


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _tidy(text: str) -> str:
    pieces = (" ".join(piece.split()) for piece in text.split(SEPARATOR.strip()))
    return SEPARATOR.join(piece for piece in pieces if piece)


def _fill(template: str, values: dict[str, str], default: str) -> str:
    """A user's template for one line; anything broken or empty falls back to the default text."""
    if not template.strip():
        return default
    try:
        fields = list(Formatter().parse(template))
    except ValueError:
        return default
    # Only bare names: no positional fields, attribute or index access, conversions or format specs,
    # so a template can neither reach into objects nor pad a line to gigabytes.
    if any(name is not None and (not name.isidentifier() or conv or spec) for _, name, spec, conv in fields):
        return default
    return _tidy(template.format_map(_Blank(values))) or default


def render(
    state: GameState,
    catalog: Catalog,
    profile_id: str | None = None,
    session_start: float | None = None,
    options: PresenceOptions | None = None,
    tr: Translator | None = None,
) -> Payload | None:
    if state.activity in (Activity.UNKNOWN, Activity.OFFLINE):
        return None
    options = options or DEFAULT_OPTIONS
    tr = tr or english()

    # Hidden elements are blanked here, once, so nothing below can leak them.
    trial = catalog.trial(state.trial_id) if state.activity in TRIAL_ACTIVITIES else None
    location = trial.location if trial is not None and options.show_location else None
    program = _program(catalog, state.program_id, tr) if options.show_program else None
    difficulty = _difficulty(catalog, state.difficulty, tr) if options.show_difficulty else None
    role = state.invasion_role

    party_id, party_size = None, None
    if options.show_party and state.party_size and state.party_size >= 2 and not state.in_invasion:
        party_id = state.party_key or "party"
        party_size = (state.party_size, max(state.party_max, state.party_size))

    if state.activity in OUTSIDE and (state.activity is not Activity.PREPARING or trial is None):
        top, bottom = OUTSIDE[state.activity]
        details, description = tr.t(top), tr.t(bottom)
    else:
        details, description = _trial_lines(state, trial, location, program, difficulty, tr)
        values = {
            "trial": trial.name if trial is not None else "",
            "location": location or "",
            "program": program or "",
            "difficulty": difficulty or "",
            "step": str(state.chain_step) if state.activity is Activity.CHAIN and state.chain_step else "",
            "role": role_name(role, tr) if role else "",
            "activity": details,
            "party": f"{party_size[0]}/{party_size[1]}" if party_size else "",
        }
        details = _fill(options.details_template, values, details)
        description = _fill(options.state_template, values, description)

    small_image, small_text = None, None
    if role and options.show_role_icon:
        small_image = image(ROLE_ICONS[role])
        small_text = _clip(tr.t("presence.invasion_role", role=role_name(role, tr)))

    start = None
    if options.show_timer:
        start = state.trial_started_at if state.in_trial else session_start

    large_text = GAME_NAME
    if trial is not None:
        large_text = f"{trial.name} — {location}" if location else trial.name

    buttons = ()
    if profile_id and options.show_stats_button:
        label = tr.t("presence.button_stats").strip()[:MAX_BUTTON]
        buttons = ((label, f"{SITE}/profile/{profile_id}"),)

    return Payload(
        details=_clip(details),
        state=_clip(description),
        large_image=image(trial.banner if trial and options.show_trial_image else None),
        large_text=_clip(large_text),
        small_image=small_image,
        small_text=small_text,
        party_id=party_id,
        party_size=party_size,
        start=int(start) if start else None,
        buttons=buttons,
    )


def _program(catalog: Catalog, program_id: str | None, tr: Translator) -> str | None:
    key, name = catalog.program(program_id)
    return tr.name(f"presence.program.{key}", name) if key else name


def _difficulty(catalog: Catalog, level: int | None, tr: Translator) -> str | None:
    return tr.name(f"presence.difficulty.{level}", catalog.difficulty_name(level))


def _trial_lines(
    state: GameState,
    trial: TrialInfo | None,
    location: str | None,
    program: str | None,
    difficulty: str | None,
    tr: Translator,
) -> tuple[str, str]:
    parts = []
    if state.activity is Activity.CHAIN:
        headline = program or tr.t("presence.program.escalation")
        if trial is not None:
            parts.append(trial.name)
        if state.chain_step:
            parts.append(tr.t("presence.step", step=state.chain_step))
    else:
        headline = trial.name if trial is not None else (program or tr.t("presence.in_trial"))
        if trial is not None and program:
            parts.append(program)
        if location:
            parts.append(location)

    if state.invasion_role is not None:
        parts = [f"{tr.t('presence.program.invasion')}{SEPARATOR}{role_name(state.invasion_role, tr)}"]
    if state.activity is Activity.PREPARING:
        parts.insert(0, tr.t("presence.preparing"))
    if difficulty:
        parts.append(difficulty)

    return headline, SEPARATOR.join(parts) if parts else GAME_NAME
