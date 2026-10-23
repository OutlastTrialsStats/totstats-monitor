from __future__ import annotations

import pytest

from totstats.presence.catalog import Catalog, parse_catalog
from totstats.presence.render import render
from totstats.presence.state import Activity, GameState, InvasionRole

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
    assert payload["state"] == "Fun Park • Standard"
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


@pytest.mark.parametrize("activity", [Activity.OFFLINE, Activity.UNKNOWN])
def test_no_status_while_offline_or_unknown(activity):
    assert render(GameState(activity=activity), site(), "pid", 1792786874.0) is None
