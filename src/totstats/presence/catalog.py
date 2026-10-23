"""Trial, program and difficulty names, loaded from the website's trial catalog.

The website keeps the one catalog of trials at outlasttrialsstats.com/data/trials.json; a copy ships
as assets/trials.json and always works offline. A newer download is cached beside the settings, so
new trials show up with their real names without waiting for an app release. What only Discord
needs (role icons, the fallback banner, program labels the website does not list) lives here.
Nothing here raises — a broken catalog costs the presence its wording, not the app its startup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from totstats.shared import paths
from totstats.shared.applog import AppLog

SITE = "https://outlasttrialsstats.com"

BUNDLED_NAME = "trials.json"
CACHE_NAME = "site-trials.json"
META_NAME = "site-trials.meta.json"

# Where the banner artwork lives. Fixed on purpose: the catalog carries the artwork *keys*, the
# build decides the URL they resolve to, so a downloaded catalog can never point Discord elsewhere.
ASSET_BASE = f"{SITE}/game-assets/"
ASSET_EXT = ".webp"

DEFAULT_BANNER = "murkoff_logo"
PROFILE_URL = f"{SITE}/profile/{{profile_id}}"
INVASION_BANNERS = {"reagent": "icon_reagent", "imposter": "icon_invader"}
PROGRAM_PREFIXES = (
    ("programCore", "Core Program"),
    ("programBloodDonations", "Blood Drive"),
    ("programCREATOR", "Custom Trial"),
)

_FALLBACK_DIFFICULTIES = {1: "Introductory", 2: "Standard", 3: "Intensive", 4: "Psychosurgery"}
_FALLBACK_PROGRAMS = {
    "programchain": "Escalation",
    "programinvasion": "Invasion",
    "programcreator": "Creator",
}


@dataclass(frozen=True)
class TrialInfo:
    name: str
    banner: str | None = None
    location: str | None = None


# Trial ids the game has sent that the website catalog does not list.
EXTRA_TRIALS = {
    "AE_Trial": TrialInfo("Escape the Lies", "photo_AE_Trial"),
    "DRS_MT01": TrialInfo("Kidnap the Mistress", "photo_DRS_MT01"),
    "DRS_MT02": TrialInfo("Spread the Disease", "photo_DRS_MT02"),
    "DRS_MT03": TrialInfo("Traffick the Product", "photo_DRS_MT03"),
}


@dataclass(frozen=True)
class Catalog:
    trials: dict[str, TrialInfo] = field(default_factory=dict)
    programs: dict[str, str] = field(default_factory=lambda: dict(_FALLBACK_PROGRAMS))
    difficulties: dict[int, str] = field(default_factory=lambda: dict(_FALLBACK_DIFFICULTIES))
    invasion_banners: dict[str, str] = field(default_factory=lambda: dict(INVASION_BANNERS))

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
        return self.difficulties.get(level) if level is not None else None

    def image(self, banner: str | None) -> str:
        return f"{ASSET_BASE}{banner or DEFAULT_BANNER}{ASSET_EXT}"

    def profile_link(self, profile_id: str | None) -> str | None:
        return PROFILE_URL.replace("{profile_id}", profile_id) if profile_id else None


def _text(raw: object, key: str) -> str | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


def _entries(raw: object, key: str) -> list[dict]:
    value = raw.get(key) if isinstance(raw, dict) else None
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def parse_catalog(raw: object) -> Catalog | None:
    """Build a Catalog from the website's trials.json, or None when it is not that file."""
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

    programs = dict(_FALLBACK_PROGRAMS)
    for entry in _entries(raw, "programs"):
        key, name = _text(entry, "key"), _text(entry, "name")
        if key and name:
            programs[key.lower()] = name

    difficulties = dict(_FALLBACK_DIFFICULTIES)
    for entry in _entries(raw, "difficulties"):
        code, name = entry.get("code"), _text(entry, "name")
        if isinstance(code, int) and not isinstance(code, bool) and name:
            difficulties[code] = name

    return Catalog(trials=trials, programs=programs, difficulties=difficulties)


def bundled_path() -> Path:
    return paths.asset_path(BUNDLED_NAME)


def cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_NAME


def meta_path(cache_dir: Path) -> Path:
    return cache_dir / META_NAME


def read_catalog(path: Path) -> Catalog | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parse_catalog(raw)


def load_catalog(log: AppLog, cache_dir: Path | None) -> Catalog:
    """The best catalog available locally. Never touches the network, never raises."""
    if cache_dir is not None:
        path = cache_path(cache_dir)
        if path.exists():
            cached = read_catalog(path)
            if cached is not None:
                log.debug(f"📦 Using the downloaded trial catalog ({len(cached.trials)} trials)")
                return cached
            _set_aside(path, log)

    bundled = read_catalog(bundled_path())
    if bundled is not None:
        log.debug(f"📦 Using the bundled trial catalog ({len(bundled.trials)} trials)")
        return bundled
    log.warning("⚠️ The bundled trial catalog could not be read")
    return Catalog(trials=dict(EXTRA_TRIALS))


def store_cache(cache_dir: Path, body: bytes, etag: str | None, log: AppLog) -> bool:
    """Write the downloaded catalog byte for byte, so its ETag keeps matching."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = cache_path(cache_dir).with_suffix(".json.tmp")
        temp.write_bytes(body)
        os.replace(temp, cache_path(cache_dir))
        meta_path(cache_dir).write_text(
            json.dumps({"etag": etag or ""}, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError as exc:
        log.debug(f"📦 Could not cache the trial catalog ({exc})")
        return False


def read_etag(cache_dir: Path) -> str | None:
    try:
        raw = json.loads(meta_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    etag = raw.get("etag") if isinstance(raw, dict) else None
    return etag if isinstance(etag, str) and etag else None


def _set_aside(path: Path, log: AppLog) -> None:
    """Keep a damaged download instead of silently overwriting it on the next refresh."""
    try:
        os.replace(path, path.with_suffix(".json.bad"))
        log.warning("⚠️ The downloaded trial catalog was unreadable and has been set aside")
    except OSError:
        pass
