from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from totstats.shared.applog import AppLog


@dataclass
class Features:
    contribute: bool = True
    presence: bool = True


@dataclass
class Settings:
    autostart: bool | None = None
    features: Features = field(default_factory=Features)


def _parse(raw: object) -> Settings:
    settings = Settings()
    if not isinstance(raw, dict):
        return settings
    if isinstance(raw.get("autostart"), bool):
        settings.autostart = raw["autostart"]
    features = raw.get("features")
    if isinstance(features, dict):
        if isinstance(features.get("contribute"), bool):
            settings.features.contribute = features["contribute"]
        if isinstance(features.get("presence"), bool):
            settings.features.presence = features["presence"]
    return settings


class SettingsStore:
    def __init__(self, path: Path | None, log: AppLog) -> None:
        self._path = path
        self._log = log
        self.settings = Settings()

    def load(self) -> Settings:
        if self._path is None:
            return self.settings
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.settings
        except OSError as exc:
            self._log.warning(f"Settings not readable, using defaults: {exc}")
            return self.settings
        except ValueError as exc:
            self._log.warning(f"Settings not valid JSON, moved to settings.json.bad: {exc}")
            with contextlib.suppress(OSError):
                os.replace(self._path, self._path.with_suffix(".json.bad"))
            return self.settings
        self.settings = _parse(raw)
        return self.settings

    def save(self) -> None:
        if self._path is None:
            return
        payload = json.dumps({"version": 1, **asdict(self.settings)}, indent=2) + "\n"
        temp = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, self._path)
        except OSError as exc:
            self._log.warning(f"Settings not saved: {exc}")
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)
