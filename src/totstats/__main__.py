from __future__ import annotations

import argparse
from collections.abc import Sequence

from totstats import APP_NAME, __version__
from totstats.shared import installer, windows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Companion app for outlasttrialsstats.com.")
    parser.add_argument(
        "--silent", action="store_true", help="run without console output; used by the autostart entry"
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="do not copy the executable into %%LOCALAPPDATA%% and relaunch",
    )
    parser.add_argument(
        "--logs-dir",
        metavar="PATH",
        help="read game logs from PATH instead of %%LOCALAPPDATA%%\\OPP\\Saved\\Logs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "read the logs regardless of whether the game is running and send nothing to the "
            "API; use with --logs-dir to replay a recorded session"
        ),
    )
    parser.add_argument(
        "--presence-connect",
        action="store_true",
        help=(
            "during a dry run, still show the status on Discord; the only way to see the Rich "
            "Presence without the game running"
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="log at debug level")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    windows.enable_dpi_awareness()
    if not (args.no_install or args.dry_run) and installer.ensure_installed():
        return 0

    from totstats.app import App

    return App(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
