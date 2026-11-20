from __future__ import annotations

import re
from pathlib import Path

# Steam app id of The Outlast Trials; its Proton prefix lives in steamapps/compatdata/<id>.
STEAM_APP_ID = "1304930"
PREFIX_LOGS = Path("pfx/drive_c/users/steamuser/AppData/Local/OPP/Saved/Logs")
_LIBRARY_PATH = re.compile(r'"path"\s+"([^"]+)"')


def steam_roots(home: Path) -> list[Path]:
    candidates = [
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
    ]
    roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def steam_libraries(root: Path) -> list[Path]:
    libraries = [root]
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for match in _LIBRARY_PATH.finditer(text):
        library = Path(match.group(1))
        if library not in libraries:
            libraries.append(library)
    return libraries


def find_logs_dir(home: Path) -> Path | None:
    for root in steam_roots(home):
        for library in steam_libraries(root):
            candidate = library / "steamapps" / "compatdata" / STEAM_APP_ID / PREFIX_LOGS
            if candidate.is_dir():
                return candidate
    return None


def logs_dir(home: Path) -> Path:
    """The game's log folder, or where it will appear once Steam has created the prefix."""
    return find_logs_dir(home) or (
        home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / STEAM_APP_ID / PREFIX_LOGS
    )
