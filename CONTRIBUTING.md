# Contributing

Thanks for helping out. This is a small Windows-only app; the setup is correspondingly small.

## Development setup

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the project itself in editable mode, which is what puts the
`src/` layout on the import path. Without it `python -m totstats` finds nothing.

## Running it without the game

Starting the app for real installs it into `%LOCALAPPDATA%` and sends data to the live API.
Use `--dry-run` instead:

```powershell
# Copy a session's logs somewhere safe first; the game overwrites OPP.log.
New-Item -ItemType Directory -Force C:\temp\logs | Out-Null
Copy-Item "$env:LOCALAPPDATA\OPP\Saved\Logs\*.log" C:\temp\logs

.venv\Scripts\python -m totstats --dry-run --verbose --logs-dir C:\temp\logs
```

A dry run reads the given directory whether or not the game is running, keeps its settings in
memory and touches neither the registry nor the installation. It replays the whole file on
startup, so a recorded session runs end to end.

| Flag | Effect |
|---|---|
| `--dry-run` | Read logs without the game, send nothing, touch nothing |
| `--logs-dir PATH` | Read from PATH instead of `%LOCALAPPDATA%\OPP\Saved\Logs` |
| `--presence-connect` | Talk to Discord for real during a dry run, instead of only logging the status |
| `--verbose` | Log at debug level, including replayed lines |
| `--no-install` | Do not copy the executable into `%LOCALAPPDATA%` and relaunch |
| `--silent` | No console output; this is what the autostart entry uses |

## Before opening a pull request

```powershell
.venv\Scripts\ruff check .
```

CI runs the same command and the build job depends on it.

To produce the executable:

```powershell
.venv\Scripts\python build.py
```

`build.py` refuses to build if the version in `src/totstats/__init__.py` disagrees with
`.release-please-manifest.json`. Both are updated by `release-please`; do not bump either by hand.

## Code layout

```
src/totstats/
├─ __main__.py    argparse CLI
├─ app.py         wiring and lifecycle — the only place that knows every component
├─ shared/        infrastructure both features need
├─ contribute/    the contribute feature
├─ presence/      the Discord Rich Presence feature
└─ ui/            tray icon and console window
```

The rule that keeps this workable: **`shared/` knows nothing about features, and features know
nothing about each other.** Anything Discord-specific goes in `presence/`, not in `shared/` and
never in `contribute/`.

`shared/log_tail.py` is the piece to understand first. The game's log is read once, by one
tailer, and dispatched to subscribers — reading it twice would double the I/O and let two
features disagree about what the game is doing. A feature subscribes with a list of literal
substrings it cares about, and only lines containing one of them reach it.

A feature is a package under `src/totstats/` with a service class exposing `INTERESTS`,
`on_line(line)` and `on_rotate()`; `app.py` subscribes it to the tailer. Sinks run on the watcher
thread and must not block — enqueue and return.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), described in
[doc/commit-convention.md](doc/commit-convention.md). They drive the version bump and the
changelog, so the type you pick has consequences.
