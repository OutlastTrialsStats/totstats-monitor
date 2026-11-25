from __future__ import annotations

import json
import string
from pathlib import Path

import pytest
from presence_fixtures import FakeLog

from totstats.shared import i18n, paths
from totstats.shared.i18n import Locales, Translator

LOCALES = paths.locales_dir()
FILES = sorted(LOCALES.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def _write(directory: Path, code: str, data: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


ENGLISH = _load(LOCALES / "en.json")


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_locale_has_same_keys_as_english(path: Path) -> None:
    assert set(_load(path)) == set(ENGLISH)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_locale_keeps_placeholders(path: Path) -> None:
    data = _load(path)
    for key, text in ENGLISH.items():
        if isinstance(text, str):
            assert _placeholders(data[key]) == _placeholders(text), key


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_locale_has_name(path: Path) -> None:
    meta = _load(path).get("_meta")
    assert isinstance(meta, dict)
    assert isinstance(meta.get("name"), str) and meta["name"]


def test_translator_falls_back_to_english_for_missing_key() -> None:
    translator = Translator("de", {"a": "A-de"}, {"a": "A", "b": "B"})
    assert translator.t("a") == "A-de"
    assert translator.t("b") == "B"


def test_name_keeps_the_english_source_unless_this_language_translates_it() -> None:
    translator = Translator("de", {"a": "A-de"}, {"a": "A", "b": "B"})
    assert translator.name("a", "Site A") == "A-de"
    assert translator.name("b", "Site B") == "Site B"
    assert Translator("en", {"a": "A"}, {"a": "A"}).name("a", "Site A") == "Site A"


def test_translator_returns_unknown_key_itself() -> None:
    assert Translator("de", {}, {}).t("does.not.exist") == "does.not.exist"


def test_translator_falls_back_to_english_for_broken_format() -> None:
    translator = Translator("de", {"s": "Status: {stat"}, {"s": "Status: {status}"})
    assert translator.t("s", status="ok") == "Status: ok"
    translator = Translator("de", {"s": "Status: {wrong}"}, {"s": "Status: {status}"})
    assert translator.t("s", status="ok") == "Status: ok"


@pytest.mark.parametrize("broken", ["Stufe {step[0]}", "Stufe {step.name}", "Stufe {}"])
def test_translator_survives_template_errors_from_wrong_value_types(broken: str) -> None:
    tr = Translator("de", {"presence.step": broken}, {"presence.step": "Step {step}"})
    assert tr.t("presence.step", step=3) == "Step 3"


def test_user_file_adds_language(tmp_path: Path) -> None:
    user = tmp_path / "user"
    _write(user, "it", {"_meta": {"name": "Italiano"}, "tray.exit": "Esci"})
    locales = Locales([LOCALES, user], FakeLog())
    assert locales.available()["it"] == "Italiano"
    translator = locales.translator("it")
    assert translator.code == "it"
    assert translator.t("tray.exit") == "Esci"
    assert translator.t("tray.console") == ENGLISH["tray.console"]


def test_user_file_overrides_single_keys(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write(bundled, "en", {"_meta": {"name": "English"}, "a": "A", "b": "B"})
    _write(bundled, "de", {"_meta": {"name": "Deutsch"}, "a": "A-de", "b": "B-de"})
    _write(user, "de", {"b": "B-mine"})
    translator = Locales([bundled, user], FakeLog()).translator("de")
    assert translator.t("a") == "A-de"
    assert translator.t("b") == "B-mine"


def test_resolve_maps_region_to_base_language() -> None:
    assert Locales([LOCALES], FakeLog()).resolve("de-AT") == "de"


def test_resolve_unknown_falls_back_to_english() -> None:
    assert Locales([LOCALES], FakeLog()).resolve("xx") == "en"


def test_resolve_auto_uses_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    locales = Locales([LOCALES], FakeLog())
    monkeypatch.setattr(i18n, "detect", lambda: "fr-CA")
    assert locales.resolve("auto") == "fr"
    monkeypatch.setattr(i18n, "detect", lambda: None)
    assert locales.resolve("auto") == "en"
