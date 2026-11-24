from __future__ import annotations

import json

import pytest
from presence_fixtures import FakeLog

from totstats.shared.settings import MAX_TEMPLATE, PresenceOptions, Settings, SettingsStore, _parse


def test_an_old_settings_file_without_the_new_keys_loads(tmp_path):
    path = tmp_path / "settings.json"
    old = {"version": 1, "autostart_asked": True, "features": {"contribute": False, "presence": True}}
    path.write_text(json.dumps(old), encoding="utf-8")
    settings = SettingsStore(path, FakeLog()).load()
    assert settings.autostart_asked is True
    assert settings.features.contribute is False
    assert settings.language == "auto"
    assert settings.presence == PresenceOptions()


def test_settings_survive_a_save_and_load(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path, FakeLog())
    store.settings.language = "de"
    store.settings.presence = PresenceOptions(show_party=False, details_template="{trial}")
    store.save()
    loaded = SettingsStore(path, FakeLog()).load()
    assert loaded.language == "de"
    assert loaded.presence == PresenceOptions(show_party=False, details_template="{trial}")


@pytest.mark.parametrize("language", ["auto", "en", "de", "pt-BR"])
def test_a_valid_language_is_kept(language):
    assert _parse({"language": language}).language == language


@pytest.mark.parametrize("language", [None, 3, True, "", "x" * 17, ["de"]])
def test_a_broken_language_falls_back_to_auto(language):
    assert _parse({"language": language}).language == "auto"


def test_presence_options_are_read():
    raw = {
        "presence": {
            "show_trial_image": False,
            "show_program": False,
            "show_location": True,
            "show_difficulty": False,
            "show_party": False,
            "show_timer": False,
            "show_role_icon": False,
            "show_stats_button": False,
            "details_template": "{trial}",
            "state_template": "{program} • {party}",
        }
    }
    assert _parse(raw).presence == PresenceOptions(
        show_trial_image=False,
        show_program=False,
        show_location=True,
        show_difficulty=False,
        show_party=False,
        show_timer=False,
        show_role_icon=False,
        show_stats_button=False,
        details_template="{trial}",
        state_template="{program} • {party}",
    )


@pytest.mark.parametrize("presence", [None, "yes", 1, ["show_party"]])
def test_a_broken_presence_block_gives_defaults(presence):
    assert _parse({"presence": presence}).presence == PresenceOptions()


def test_wrong_types_inside_presence_give_defaults():
    raw = {
        "presence": {
            "show_party": "false",
            "show_timer": 0,
            "show_program": None,
            "details_template": 5,
            "state_template": ["x"],
            "unknown": True,
        }
    }
    assert _parse(raw).presence == PresenceOptions()


def test_templates_are_trimmed_and_capped():
    raw = {"presence": {"details_template": "  {trial}  ", "state_template": "y" * (MAX_TEMPLATE + 50)}}
    presence = _parse(raw).presence
    assert presence.details_template == "{trial}"
    assert presence.state_template == "y" * MAX_TEMPLATE


@pytest.mark.parametrize("raw", [None, [], "settings", 42])
def test_a_broken_file_gives_defaults(raw):
    assert _parse(raw) == Settings()
