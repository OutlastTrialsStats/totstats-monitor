from __future__ import annotations

import sys

if sys.platform == "win32":
    from totstats.shared.autostart_windows import disable, enable, is_enabled
else:
    from totstats.shared.autostart_linux import disable, enable, is_enabled

__all__ = ["disable", "enable", "is_enabled"]
