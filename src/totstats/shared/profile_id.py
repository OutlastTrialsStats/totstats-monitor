from __future__ import annotations

import re

from totstats.shared.log_tail import LogLine

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"[?&]EncryptionToken=({UUID})",
        rf"Client authentication succeeded\. Profile ID: ({UUID})",
        rf"/presence/public/profiles/({UUID})/presence",
        rf'"dataItemId"\s*:\s*"(?:parties|matchmaking-tickets)\|[^|"]*\|({UUID})"',
        rf"\[({UUID})\],\s+Player Slot = \d+, IsLocallyControlled = Yes",
    )
)


class OwnProfileIdResolver:
    INTERESTS = (
        "EncryptionToken=",
        "Client authentication succeeded",
        "/presence/public/profiles/",
        '"parties|',
        '"matchmaking-tickets|',
        "IsLocallyControlled = Yes",
    )

    def __init__(self) -> None:
        self.profile_id: str | None = None

    def reset(self) -> None:
        self.profile_id = None

    def feed(self, line: LogLine) -> str | None:
        if self.profile_id is not None:
            return None
        for pattern in PATTERNS:
            match = pattern.search(line.raw)
            if match:
                self.profile_id = match.group(1).lower()
                return self.profile_id
        return None
