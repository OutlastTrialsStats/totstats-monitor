from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from totstats.shared.applog import AppLog

SITE = "https://outlasttrialsstats.com"

PROGRAM_PREFIXES = (
    ("programCore", "Core Program"),
    ("programBloodDonations", "Blood Drive"),
    ("programCREATOR", "Custom Trial"),
)
PROGRAMS = {"programchain": "Escalation", "programinvasion": "Invasion", "programcreator": "Creator"}
DIFFICULTIES = {1: "Introductory", 2: "Standard", 3: "Intensive", 4: "Psychosurgery"}


@dataclass(frozen=True)
class TrialInfo:
    name: str
    banner: str | None = None
    location: str | None = None


EXTRA_TRIALS = {
    "AE_Trial": TrialInfo("Escape the Lies", "photo_AE_Trial"),
    "DRS_MT01": TrialInfo("Kidnap the Mistress", "photo_DRS_MT01"),
    "DRS_MT02": TrialInfo("Spread the Disease", "photo_DRS_MT02"),
    "DRS_MT03": TrialInfo("Traffick the Product", "photo_DRS_MT03"),
}


@dataclass(frozen=True)
class Catalog:
    trials: dict[str, TrialInfo] = field(default_factory=dict)
    programs: dict[str, str] = field(default_factory=lambda: dict(PROGRAMS))
    difficulties: dict[int, str] = field(default_factory=lambda: dict(DIFFICULTIES))

    def trial(self, trial_id: str | None) -> TrialInfo | None:
        if not trial_id:
            return None
        return self.trials.get(trial_id) or self.trials.get(trial_id.upper())

    def program_name(self, program_id: str | None) -> str | None:
        if not program_id:
            return None
        for prefix, name in PROGRAM_PREFIXES:
            if program_id.startswith(prefix):
                return name
        return self.programs.get(program_id.lower())

    def difficulty_name(self, level: int | None) -> str | None:
        return self.difficulties.get(level)


def image(banner: str | None) -> str:
    return f"{SITE}/game-assets/{banner or 'murkoff_logo'}.webp"


def _text(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _entries(raw: dict, key: str) -> list[dict]:
    value = raw.get(key)
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def parse_catalog(raw: object) -> Catalog | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("maps"), list):
        return None

    trials = dict(EXTRA_TRIALS)
    for game_map in _entries(raw, "maps"):
        location = _text(game_map, "name")
        for entry in _entries(game_map, "trials"):
            trial_id, name = _text(entry, "id"), _text(entry, "name")
            if trial_id and name:
                trials[trial_id] = TrialInfo(name, _text(entry, "image"), location)
    if len(trials) == len(EXTRA_TRIALS):
        return None

    programs = dict(PROGRAMS)
    for entry in _entries(raw, "programs"):
        key, name = _text(entry, "key"), _text(entry, "name")
        if key and name:
            programs[key.lower()] = name

    difficulties = dict(DIFFICULTIES)
    for entry in _entries(raw, "difficulties"):
        code, name = entry.get("code"), _text(entry, "name")
        if isinstance(code, int) and not isinstance(code, bool) and name:
            difficulties[code] = name

    return Catalog(trials, programs, difficulties)


def cache_path(cache_dir: Path) -> Path:
    return cache_dir / "site-trials.json"


def load_catalog(log: AppLog, cache_dir: Path | None) -> Catalog:
    if cache_dir is not None:
        try:
            cached = parse_catalog(json.loads(cache_path(cache_dir).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            cached = None
        if cached is not None:
            log.debug(f"Trial catalog loaded: {len(cached.trials)} trials")
            return cached

    log.debug("Trial catalog not downloaded yet")
    return Catalog(dict(EXTRA_TRIALS))


def store_catalog(cache_dir: Path, body: bytes, log: AppLog) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = cache_path(cache_dir).with_suffix(".json.tmp")
        temp.write_bytes(body)
        os.replace(temp, cache_path(cache_dir))
    except OSError as exc:
        log.debug(f"Trial catalog not cached: {exc}")
