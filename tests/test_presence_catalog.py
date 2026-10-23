"""The website trial catalog: parsing, the bundled copy, what it renders and how it is refreshed."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from presence_fixtures import FakeLog

from totstats.presence.catalog import (
    Catalog,
    bundled_path,
    load_catalog,
    parse_catalog,
    read_catalog,
    read_etag,
)
from totstats.presence.catalog_sync import CATALOG_URLS, CatalogSync
from totstats.presence.render import render
from totstats.presence.state import Activity, GameState, InvasionRole

ASSETS = "https://outlasttrialsstats.com/game-assets/"

SITE_CATALOG = {
    "difficulties": [
        {"key": "Easy", "code": 1, "name": "Easy Mode"},
        {"key": "Undefined", "code": None, "name": "Undefined"},
    ],
    "programs": [{"key": "programchain", "name": "Chain", "icon": "task_escalation"}],
    "maps": [
        {
            "key": "fp",
            "name": "Fun Park",
            "coreProgram": {"id": "programCoreFP", "poster": "Poster_Program_02"},
            "trials": [
                {"id": "FP_Trial", "key": "fp_trial", "name": "Grind", "image": "photo_FP"},
                {"id": "FPB_MT01", "name": "No Image"},
                {"name": "No Id"},
            ],
        }
    ],
}


def bundled() -> Catalog:
    catalog = read_catalog(bundled_path())
    assert catalog is not None
    return catalog


def test_the_website_shape_is_parsed():
    catalog = parse_catalog(SITE_CATALOG)
    assert catalog is not None
    trial = catalog.trial("FP_Trial")
    assert (trial.name, trial.banner, trial.location) == ("Grind", "photo_FP", "Fun Park")
    assert catalog.trial("FPB_MT01").banner is None
    assert catalog.program_name("programCHAIN") == "Chain"
    assert catalog.program_name("programINVASION") == "Invasion"
    assert catalog.difficulty_name(1) == "Easy Mode"
    assert catalog.difficulty_name(4) == "Psychosurgery"


def test_discord_only_labels_come_from_the_app():
    catalog = parse_catalog(SITE_CATALOG)
    assert catalog.program_name("programCoreFP") == "Core Program"
    assert catalog.program_name("programCore") == "Core Program"
    assert catalog.program_name("programBloodDonations") == "Blood Drive"
    assert catalog.program_name("programHalloween") is None
    assert catalog.trial("DRS_MT01").name == "Kidnap the Mistress"


def test_anything_but_the_website_shape_is_rejected():
    assert parse_catalog(None) is None
    assert parse_catalog([]) is None
    assert parse_catalog({"schema": 1, "trials": {"PS_Trial": {"name": "Kill the Snitch"}}}) is None
    assert parse_catalog({"maps": "nope"}) is None
    assert parse_catalog({"maps": [{"name": "Empty", "trials": []}]}) is None


def test_the_bundled_copy_parses():
    catalog = bundled()
    assert len(catalog.trials) > 50
    assert catalog.program_name("programCREATOR") == "Custom Trial"
    assert catalog.difficulty_name(3) == "Intensive"


def test_a_known_trial_renders_as_before():
    state = GameState(
        activity=Activity.TRIAL, program_id="programCoreFP", trial_id="FP_Trial", difficulty=2
    )
    payload = render(state, bundled(), "pid").as_kwargs()
    assert payload["details"] == "Grind the Bad Apples"
    assert payload["state"] == "Fun Park • Standard"
    assert payload["large_image"] == f"{ASSETS}photo_FP_Trial.webp"
    assert payload["large_text"] == "Grind the Bad Apples — Fun Park"
    assert payload["buttons"] == [
        {"label": "View Stats", "url": "https://outlasttrialsstats.com/profile/pid"}
    ]


def test_an_escalation_step_renders_as_before():
    state = GameState(
        activity=Activity.CHAIN,
        program_id="programCHAIN",
        trial_id="CHA_MT01",
        difficulty=4,
        chain_step=3,
    )
    payload = render(state, bundled()).as_kwargs()
    assert payload["details"] == "Escalation"
    assert payload["state"] == "Escape the Courthouse • Step 3 • Psychosurgery"
    assert payload["large_text"] == "Escape the Courthouse — Courthouse"


def test_an_invasion_renders_as_before():
    state = GameState(
        activity=Activity.TRIAL,
        program_id="programINVASION",
        trial_id="PSA_MT01",
        difficulty=3,
        invasion_role=InvasionRole.IMPOSTER,
    )
    payload = render(state, bundled()).as_kwargs()
    assert payload["details"] == "Teach the Police Officer"
    assert payload["state"] == "Invasion • Imposter • Intensive"
    assert payload["small_image"] == f"{ASSETS}icon_invader.webp"
    assert payload["small_text"] == "Invasion — Imposter"


def test_an_unknown_trial_uses_the_default_banner():
    state = GameState(activity=Activity.TRIAL, program_id="programCoreXX", trial_id="NEW_MT01")
    payload = render(state, bundled()).as_kwargs()
    assert payload["details"] == "Core Program"
    assert payload["large_image"] == f"{ASSETS}murkoff_logo.webp"
    assert payload["large_text"] == "The Outlast Trials"


def test_a_cached_download_wins_and_a_broken_one_is_set_aside(tmp_path):
    log = FakeLog()
    (tmp_path / "site-trials.json").write_text(json.dumps(SITE_CATALOG), encoding="utf-8")
    assert load_catalog(log, tmp_path).trial("FP_Trial").name == "Grind"

    (tmp_path / "site-trials.json").write_text("{}", encoding="utf-8")
    assert load_catalog(log, tmp_path).trial("FP_Trial").name == "Grind the Bad Apples"
    assert (tmp_path / "site-trials.json.bad").exists()


class FakeSession:
    def __init__(self, status: int, body: bytes = b"", etag: str | None = None) -> None:
        self.response = SimpleNamespace(
            status_code=status, content=body, headers={"ETag": etag} if etag else {}
        )
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, headers: dict, timeout: object) -> SimpleNamespace:
        self.calls.append((url, headers))
        return self.response


def sync(tmp_path, received: list) -> CatalogSync:
    return CatalogSync(received.append, FakeLog(), threading.Event(), tmp_path)


def test_the_sync_fetches_from_the_website():
    assert CATALOG_URLS == ("https://outlasttrialsstats.com/data/trials.json",)


def test_a_download_is_cached_and_handed_over(tmp_path):
    received: list = []
    body = json.dumps(SITE_CATALOG).encode("utf-8")
    session = FakeSession(200, body, etag='"abc"')
    assert sync(tmp_path, received)._fetch(session, CATALOG_URLS[0], {}) is True
    assert received[0].trial("FP_Trial").name == "Grind"
    assert (tmp_path / "site-trials.json").read_bytes() == body
    assert read_etag(tmp_path) == '"abc"'


def test_not_modified_and_unusable_bodies_change_nothing(tmp_path):
    received: list = []
    assert sync(tmp_path, received)._fetch(FakeSession(304), CATALOG_URLS[0], {}) is True
    old_shape = json.dumps({"schema": 1, "trials": {}}).encode("utf-8")
    assert sync(tmp_path, received)._fetch(FakeSession(200, old_shape), CATALOG_URLS[0], {}) is False
    assert received == []
    assert not (tmp_path / "site-trials.json").exists()
