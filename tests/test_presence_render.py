from __future__ import annotations

import pytest
from presence_fixtures import FakeLog

from totstats.presence.catalog import Catalog, parse_catalog
from totstats.presence.render import PLACEHOLDERS, render
from totstats.presence.state import Activity, GameState, InvasionRole
from totstats.shared import paths
from totstats.shared.i18n import Locales, Translator
from totstats.shared.settings import PresenceOptions

ASSETS = "https://outlasttrialsstats.com/game-assets/"

RENDER_CATALOG = {
    "maps": [
        {
            "name": "Fun Park",
            "trials": [{"id": "FP_Trial", "name": "Grind the Bad Apples", "image": "photo_FP_Trial"}],
        },
        {
            "name": "Courthouse",
            "trials": [{"id": "CHA_MT01", "name": "Escape the Courthouse", "image": "photo_CHA_MT01"}],
        },
        {
            "name": "Police Station",
            "trials": [{"id": "PSA_MT01", "name": "Teach the Police Officer", "image": "photo_PSA_MT01"}],
        },
    ]
}


def site() -> Catalog:
    catalog = parse_catalog(RENDER_CATALOG)
    assert catalog is not None
    return catalog


def test_a_known_trial_renders():
    state = GameState(activity=Activity.TRIAL, program_id="programCoreFP", trial_id="FP_Trial", difficulty=2)
    payload = render(state, site(), "pid").as_kwargs()
    assert payload["details"] == "Grind the Bad Apples"
    assert payload["state"] == "Core Program • Fun Park • Standard"
    assert payload["large_image"] == f"{ASSETS}photo_FP_Trial.webp"
    assert payload["large_text"] == "Grind the Bad Apples — Fun Park"
    assert payload["buttons"] == [
        {"label": "View Stats", "url": "https://outlasttrialsstats.com/profile/pid"}
    ]


def test_an_escalation_step_renders():
    state = GameState(
        activity=Activity.CHAIN,
        program_id="programCHAIN",
        trial_id="CHA_MT01",
        difficulty=4,
        chain_step=3,
    )
    payload = render(state, site()).as_kwargs()
    assert payload["details"] == "Escalation"
    assert payload["state"] == "Escape the Courthouse • Step 3 • Psychosurgery"
    assert payload["large_text"] == "Escape the Courthouse — Courthouse"


def test_an_invasion_renders():
    state = GameState(
        activity=Activity.TRIAL,
        program_id="programINVASION",
        trial_id="PSA_MT01",
        difficulty=3,
        invasion_role=InvasionRole.IMPOSTER,
    )
    payload = render(state, site()).as_kwargs()
    assert payload["details"] == "Teach the Police Officer"
    assert payload["state"] == "Invasion • Imposter • Intensive"
    assert payload["small_image"] == f"{ASSETS}icon_invader.webp"
    assert payload["small_text"] == "Invasion — Imposter"


def test_an_unknown_trial_uses_the_default_banner():
    state = GameState(activity=Activity.TRIAL, program_id="programCoreXX", trial_id="NEW_MT01")
    payload = render(state, site()).as_kwargs()
    assert payload["details"] == "Core Program"
    assert payload["large_image"] == f"{ASSETS}murkoff_logo.webp"
    assert payload["large_text"] == "The Outlast Trials"


@pytest.mark.parametrize(
    ("activity", "details", "description"),
    [
        (Activity.SLEEP_ROOM, "Sleep Room", "Idle"),
        (Activity.MATCHMAKING, "Sleep Room", "Looking for a group"),
        (Activity.RETURNING, "Sleep Room", "Returning from a Trial"),
        (Activity.MAIN_MENU, "Main Menu", "Idle"),
    ],
)
def test_outside_a_trial(activity, details, description):
    payload = render(GameState(activity=activity), site(), session_start=1792786874.5)
    assert payload is not None
    assert (payload.details, payload.state) == (details, description)
    assert payload.large_image == f"{ASSETS}murkoff_logo.webp"
    assert payload.large_text == "The Outlast Trials"
    assert payload.start == 1792786874


@pytest.mark.parametrize(
    "activity", [Activity.SLEEP_ROOM, Activity.MATCHMAKING, Activity.RETURNING, Activity.MAIN_MENU]
)
def test_a_leftover_trial_does_not_keep_its_banner_outside_a_trial(activity):
    state = GameState(activity=activity, program_id="programCoreFP", trial_id="FP_Trial")
    payload = render(state, site())
    assert payload is not None
    assert payload.large_image == f"{ASSETS}murkoff_logo.webp"
    assert payload.large_text == "The Outlast Trials"


def test_the_clock_waits_for_the_round_to_start_instead_of_counting_the_session():
    waiting = GameState(activity=Activity.TRIAL, trial_id="PSA_MT01")
    assert render(waiting, site(), session_start=1792786874.5).start is None

    started = GameState(activity=Activity.TRIAL, trial_id="PSA_MT01", trial_started_at=1792787874.5)
    assert render(started, site(), session_start=1792786874.5).start == 1792787874


@pytest.mark.parametrize("activity", [Activity.OFFLINE, Activity.UNKNOWN])
def test_no_status_while_offline_or_unknown(activity):
    assert render(GameState(activity=activity), site(), "pid", 1792786874.0) is None


TRIAL = GameState(
    activity=Activity.TRIAL,
    program_id="programCoreFP",
    trial_id="FP_Trial",
    difficulty=2,
    party_size=3,
    trial_started_at=1792787874.5,
)
CHAIN = GameState(
    activity=Activity.CHAIN,
    program_id="programCHAIN",
    trial_id="CHA_MT01",
    difficulty=4,
    chain_step=3,
    party_size=2,
)
INVASION = GameState(
    activity=Activity.TRIAL,
    program_id="programINVASION",
    trial_id="PSA_MT01",
    difficulty=3,
    invasion_role=InvasionRole.REAGENT,
)
DEFAULT_TRIAL_STATE = "Core Program • Fun Park • Standard"


def german() -> Translator:
    return Locales([paths.locales_dir()], FakeLog()).translator("de")


def test_default_options_change_nothing():
    for state in (TRIAL, CHAIN, INVASION, GameState(activity=Activity.SLEEP_ROOM)):
        assert render(state, site(), "pid", 1.0) == render(state, site(), "pid", 1.0, PresenceOptions())


def test_hiding_the_trial_image_keeps_the_logo():
    payload = render(TRIAL, site(), options=PresenceOptions(show_trial_image=False))
    assert payload.large_image == f"{ASSETS}murkoff_logo.webp"
    assert payload.large_text == "Grind the Bad Apples — Fun Park"


def test_hiding_the_program():
    assert render(TRIAL, site(), options=PresenceOptions(show_program=False)).state == "Fun Park • Standard"


def test_hiding_the_program_of_an_escalation_falls_back_to_its_name():
    assert render(CHAIN, site(), options=PresenceOptions(show_program=False)).details == "Escalation"


def test_hiding_the_location():
    payload = render(TRIAL, site(), options=PresenceOptions(show_location=False))
    assert payload.state == "Core Program • Standard"
    assert "Fun Park" not in payload.large_text


def test_hiding_the_difficulty():
    payload = render(CHAIN, site(), options=PresenceOptions(show_difficulty=False))
    assert payload.state == "Escape the Courthouse • Step 3"


def test_hiding_everything_on_the_bottom_line_shows_the_game_name():
    options = PresenceOptions(show_program=False, show_location=False, show_difficulty=False)
    assert render(TRIAL, site(), options=options).state == "The Outlast Trials"


def test_party_size_shows_and_hides():
    assert render(TRIAL, site()).party_size == (3, 4)
    payload = render(TRIAL, site(), options=PresenceOptions(show_party=False))
    assert payload.party_size is None
    assert "party_size" not in payload.as_kwargs()


def test_hiding_the_timer():
    hidden = PresenceOptions(show_timer=False)
    assert render(TRIAL, site(), session_start=1.0).start == 1792787874
    assert render(TRIAL, site(), session_start=1.0, options=hidden).start is None
    sleeping = GameState(activity=Activity.SLEEP_ROOM)
    assert render(sleeping, site(), session_start=1.0, options=hidden).start is None


def test_hiding_the_role_icon():
    payload = render(INVASION, site(), options=PresenceOptions(show_role_icon=False))
    assert payload.small_image is None
    assert payload.small_text is None
    assert payload.state == "Invasion • Reagent • Intensive"


def test_hiding_the_stats_button():
    assert render(TRIAL, site(), "pid", options=PresenceOptions(show_stats_button=False)).buttons == ()


def test_templates_fill_their_placeholders():
    options = PresenceOptions(
        details_template="{trial} ({difficulty})",
        state_template="{program} • {location} • {party} • {activity}",
    )
    payload = render(TRIAL, site(), options=options)
    assert payload.details == "Grind the Bad Apples (Standard)"
    assert payload.state == "Core Program • Fun Park • 3/4 • Grind the Bad Apples"


def test_templates_know_the_step_and_the_role():
    chain = render(CHAIN, site(), options=PresenceOptions(state_template="{trial} - Step {step}/5"))
    assert chain.state == "Escape the Courthouse - Step 3/5"
    invasion = render(INVASION, site(), options=PresenceOptions(details_template="{role} in {location}"))
    assert invasion.details == "Reagent in Police Station"
    plain = render(TRIAL, site(), options=PresenceOptions(details_template="{trial} • {step} • {role}"))
    assert plain.details == "Grind the Bad Apples"


def test_hidden_elements_leave_their_placeholder_empty():
    options = PresenceOptions(
        show_location=False,
        show_party=False,
        show_difficulty=False,
        state_template="{location} • {difficulty} • {program} • {party}",
    )
    assert render(TRIAL, site(), options=options).state == "Core Program"


def test_unknown_placeholders_are_empty():
    options = PresenceOptions(details_template="{trial} • {nonsense}")
    assert render(TRIAL, site(), options=options).details == "Grind the Bad Apples"


@pytest.mark.parametrize(
    "template", ["{trial", "trial}", "{0}", "{}", "{trial.name}", "{trial[0]}", "{trial!x}", "{trial:d}"]
)
def test_malformed_templates_fall_back_to_the_default(template):
    options = PresenceOptions(details_template=template, state_template=template)
    payload = render(TRIAL, site(), options=options)
    assert payload.details == "Grind the Bad Apples"
    assert payload.state == DEFAULT_TRIAL_STATE


def test_an_empty_result_falls_back_to_the_default():
    options = PresenceOptions(show_location=False, details_template=" • {location} • ", state_template="   ")
    payload = render(TRIAL, site(), options=options)
    assert payload.details == "Grind the Bad Apples"
    assert payload.state == "Core Program • Standard"


def test_templates_tidy_whitespace_and_separators():
    options = PresenceOptions(details_template="  {trial}   •  •   {location}  •  ")
    assert render(TRIAL, site(), options=options).details == "Grind the Bad Apples • Fun Park"


def test_templates_are_clipped():
    options = PresenceOptions(details_template="{trial} " * 20)
    assert len(render(TRIAL, site(), options=options).details) == 128


@pytest.mark.parametrize(
    "state",
    [
        GameState(activity=Activity.SLEEP_ROOM, trial_id="FP_Trial"),
        GameState(activity=Activity.MAIN_MENU),
        GameState(activity=Activity.MATCHMAKING),
        GameState(activity=Activity.RETURNING, trial_id="FP_Trial"),
        GameState(activity=Activity.PREPARING),
    ],
)
def test_templates_only_apply_during_a_trial(state):
    options = PresenceOptions(details_template="custom {trial}", state_template="custom")
    assert render(state, site(), options=options) == render(state, site())


def test_templates_apply_while_preparing_a_known_trial():
    state = GameState(activity=Activity.PREPARING, program_id="programCoreFP", trial_id="FP_Trial")
    assert render(state, site()).state == "Preparing • Core Program • Fun Park"
    payload = render(state, site(), options=PresenceOptions(details_template="Soon: {trial}"))
    assert payload.details == "Soon: Grind the Bad Apples"


def test_every_placeholder_is_filled():
    names = {"trial", "location", "program", "difficulty", "step", "role", "activity", "party"}
    assert set(PLACEHOLDERS) == names
    template = " ".join("{" + name + "}" for name in PLACEHOLDERS)
    payload = render(CHAIN, site(), options=PresenceOptions(state_template=template))
    assert payload.state == "Escape the Courthouse Courthouse Escalation Psychosurgery 3 Escalation 2/4"


def test_german_texts():
    tr = german()
    sleeping = render(GameState(activity=Activity.SLEEP_ROOM), site(), tr=tr)
    assert (sleeping.details, sleeping.state) == ("Schlafraum", "Untätig")

    chain = render(CHAIN, site(), "pid", tr=tr)
    assert chain.state == "Escape the Courthouse • Stufe 3 • Psychosurgery"
    assert chain.buttons[0][0] == "Statistiken ansehen"

    invasion = render(INVASION, site(), tr=tr)
    assert invasion.state == "Invasion • Reagenz • Intensive"
    assert invasion.small_text == "Invasion — Reagenz"

    preparing = render(GameState(activity=Activity.PREPARING), site(), tr=tr)
    assert (preparing.details, preparing.state) == ("Schlafraum", "Bereitet eine Prüfung vor")

    templated = render(INVASION, site(), tr=tr, options=PresenceOptions(details_template="{role}"))
    assert templated.details == "Reagenz"


@pytest.mark.parametrize("template", ["{trial:>999999999}", "{trial!r}", "{trial:.3}"])
def test_template_with_conversion_or_format_spec_falls_back(template: str):
    options = PresenceOptions(details_template=template)
    assert render(TRIAL, site(), options=options).details == "Grind the Bad Apples"


def test_long_translated_button_label_is_cut_to_discords_limit():
    tr = Translator("xx", {"presence.button_stats": "x" * 50}, {})
    payload = render(TRIAL, site(), "pid", tr=tr)
    assert payload.buttons[0][0] == "x" * 32
