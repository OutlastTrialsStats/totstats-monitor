from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from presence_fixtures import FakeLog

from totstats.presence import catalog_sync
from totstats.presence.catalog import (
    EXTRA_TRIALS,
    load_catalog,
    parse_catalog,
    read_etag,
)
from totstats.presence.catalog_sync import REFRESH_INTERVAL, RETRY_INTERVAL, CatalogSync

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


def test_without_a_cached_download_only_the_extra_trials_are_known(tmp_path):
    catalog = load_catalog(FakeLog(), tmp_path)
    assert catalog.trials == EXTRA_TRIALS
    assert catalog.program_name("programCREATOR") == "Custom Trial"
    assert catalog.difficulty_name(3) == "Intensive"


def test_a_cached_download_wins_and_a_broken_one_is_set_aside(tmp_path):
    log = FakeLog()
    (tmp_path / "site-trials.json").write_text(json.dumps(SITE_CATALOG), encoding="utf-8")
    assert load_catalog(log, tmp_path).trial("FP_Trial").name == "Grind"

    (tmp_path / "site-trials.json").write_text("{}", encoding="utf-8")
    assert load_catalog(log, tmp_path).trial("FP_Trial") is None
    assert (tmp_path / "site-trials.json.bad").exists()


class FakeSession:
    def __init__(self, status: int, body: bytes = b"", etag: str | None = None) -> None:
        self.response = SimpleNamespace(
            status_code=status, content=body, headers={"ETag": etag} if etag else {}
        )
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, headers: dict, timeout: object) -> SimpleNamespace:
        self.calls.append((url, headers))
        return self.response


@pytest.fixture
def serve(monkeypatch):
    def install(session: FakeSession) -> FakeSession:
        monkeypatch.setattr(catalog_sync.requests, "Session", lambda: session)
        return session

    return install


def sync(tmp_path, received: list) -> CatalogSync:
    return CatalogSync(received.append, FakeLog(), threading.Event(), tmp_path)


def test_a_download_is_cached_and_handed_over(tmp_path, serve):
    received: list = []
    body = json.dumps(SITE_CATALOG).encode("utf-8")
    serve(FakeSession(200, body, etag='"abc"'))
    assert sync(tmp_path, received)._refresh() is True
    assert received[0].trial("FP_Trial").name == "Grind"
    assert (tmp_path / "site-trials.json").read_bytes() == body
    assert read_etag(tmp_path) == '"abc"'

    session = serve(FakeSession(304))
    assert sync(tmp_path, received)._refresh() is True
    assert session.calls[0][1]["If-None-Match"] == '"abc"'
    assert len(received) == 1


def test_a_lost_cache_is_downloaded_again_despite_its_etag(tmp_path, serve):
    received: list = []
    (tmp_path / "site-trials.meta.json").write_text('{"etag": "\\"abc\\""}', encoding="utf-8")
    session = serve(FakeSession(200, json.dumps(SITE_CATALOG).encode("utf-8")))
    assert sync(tmp_path, received)._refresh() is True
    assert "If-None-Match" not in session.calls[0][1]
    assert len(received) == 1


def test_unusable_responses_change_nothing(tmp_path, serve):
    received: list = []
    old_shape = json.dumps({"schema": 1, "trials": {}}).encode("utf-8")
    for session in (FakeSession(200, old_shape), FakeSession(500)):
        serve(session)
        assert sync(tmp_path, received)._refresh() is False
    assert received == []
    assert not (tmp_path / "site-trials.json").exists()


def test_a_failed_first_fetch_retries_soon(tmp_path):
    stop = threading.Event()
    refresher = CatalogSync(lambda _: None, FakeLog(), stop, tmp_path, first_delay=0.0)
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            stop.set()

    results = iter([False, True])
    refresher._wait = wait
    refresher._refresh = lambda: next(results)
    refresher._run()
    assert waits == [0.0, RETRY_INTERVAL, REFRESH_INTERVAL]
