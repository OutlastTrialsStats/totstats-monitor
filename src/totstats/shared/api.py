from __future__ import annotations

import os

from totstats import APP_NAME, __version__

API_BASE = "https://outlasttrialsstats.com/api"
API_BASE_ENV = "TOTSTATS_API_BASE"
USER_AGENT = f"{APP_NAME}/{__version__}"


def api_base_override() -> str | None:
    return os.environ.get(API_BASE_ENV, "").strip().rstrip("/") or None


def api_base() -> str:
    return api_base_override() or API_BASE
