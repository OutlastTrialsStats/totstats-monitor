from __future__ import annotations

import functools
import json
import locale
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from totstats.shared import paths
from totstats.shared.applog import AppLog

AUTO = "auto"
FALLBACK = "en"
# Everything str.format raises on a malformed template, e.g. "{step[0]}" with an int raises TypeError.
FORMAT_ERRORS = (KeyError, IndexError, ValueError, TypeError, AttributeError)
META = "_meta"
CODE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


class Translator:
    def __init__(self, code: str, strings: dict[str, str], fallback: dict[str, str]) -> None:
        self.code = code
        self._strings = strings
        self._fallback = fallback

    def t(self, key: str, **values: object) -> str:
        text = self._strings.get(key) or self._fallback.get(key) or key
        if not values:
            return text
        try:
            return text.format(**values)
        except FORMAT_ERRORS:
            # A broken community translation must never break the app: fall back to English.
            try:
                return self._fallback.get(key, key).format(**values)
            except FORMAT_ERRORS:
                return text


@functools.cache
def english() -> Translator:
    strings = _read(paths.locales_dir() / f"{FALLBACK}.json") or {}
    return Translator(FALLBACK, strings, strings)


def _read(path: Path) -> dict[str, str] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    strings = {key: value for key, value in raw.items() if isinstance(value, str) and value}
    meta = raw.get(META)
    if isinstance(meta, dict) and isinstance(meta.get("name"), str):
        strings[f"{META}.name"] = meta["name"]
    return strings


def _normalize(code: str) -> str:
    code = code.replace("_", "-").split(".")[0]
    base, _, region = code.partition("-")
    return f"{base.lower()}-{region.upper()}" if region else base.lower()


def detect() -> str | None:
    if sys.platform == "win32":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(langid)
            if name:
                return _normalize(name)
        except (AttributeError, OSError):
            pass
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value and value not in ("C", "POSIX"):
            return _normalize(value)
    with_locale = locale.getlocale()[0]
    return _normalize(with_locale) if with_locale else None


class Locales:
    """Language files: one JSON per language, bundled ones first, then the user's own folder."""

    def __init__(self, dirs: Iterable[Path | None], log: AppLog) -> None:
        self._log = log
        self._files: dict[str, dict[str, str]] = {}
        for directory in dirs:
            if directory is None:
                continue
            try:
                candidates = sorted(directory.glob("*.json"))
            except OSError:
                continue
            for path in candidates:
                if not CODE.match(path.stem):
                    continue
                strings = _read(path)
                if strings is None:
                    log.warning(f"Language file ignored, not valid JSON: {path.name}")
                    continue
                self._files[path.stem] = {**self._files.get(path.stem, {}), **strings}

    def available(self) -> dict[str, str]:
        return {code: strings.get(f"{META}.name", code) for code, strings in sorted(self._files.items())}

    def resolve(self, setting: str) -> str:
        wanted = detect() if setting == AUTO else setting
        if wanted:
            if wanted in self._files:
                return wanted
            base = wanted.split("-")[0]
            if base in self._files:
                return base
        return FALLBACK

    def translator(self, setting: str) -> Translator:
        code = self.resolve(setting)
        fallback = self._files.get(FALLBACK, {})
        return Translator(code, self._files.get(code, fallback), fallback)
