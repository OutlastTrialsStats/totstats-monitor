"""Where the outlasttrialsstats.com API lives, and how the app introduces itself to it."""

from __future__ import annotations

import os

from totstats import APP_NAME, __version__

DEFAULT_API_BASE = "https://outlasttrialsstats.com/api"

# Points the read-only live presence lookup at a local backend; only meant for development.
API_BASE_ENV = "TOTSTATS_API_BASE"

USER_AGENT = f"{APP_NAME}/{__version__}"


def api_base_override() -> str | None:
    raw = os.environ.get(API_BASE_ENV, "").strip()
    return raw.rstrip("/") or None


def api_base() -> str:
    return api_base_override() or DEFAULT_API_BASE
