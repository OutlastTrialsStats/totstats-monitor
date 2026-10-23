"""Application wiring and lifecycle.

Starts the four threads the app runs on and owns the shutdown order.
"""

from __future__ import annotations

import argparse
import threading
import time
import tkinter as tk
from pathlib import Path

from totstats import APP_NAME, __version__
from totstats.contribute.api import ContributeApi
from totstats.contribute.service import ContributeService
from totstats.presence import DISCORD_CLIENT_ID
from totstats.presence.catalog import load_catalog
from totstats.presence.catalog_sync import CatalogSync
from totstats.presence.client import DiscordClient
from totstats.presence.live_api import LivePresenceApi
from totstats.presence.service import PresenceService
from totstats.shared import autostart, installer, paths
from totstats.shared.applog import AppLog
from totstats.shared.game_process import GameProcessEvent, GameProcessWatcher
from totstats.shared.log_tail import LogTailer
from totstats.shared.profile_id import OwnProfileIdResolver
from totstats.shared.settings import SettingsStore
from totstats.shared.ui_queue import UiQueue
from totstats.ui.console_window import ConsoleWindow
from totstats.ui.tray import TrayCallbacks, TrayIcon

TAIL_INTERVAL = 0.25
PROCESS_INTERVAL = 3.0
IDLE_INTERVAL = 1.0


class App:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._stop = threading.Event()
        self._uninstalling = False

        # A dry run must not touch the real installation, so it logs beside the working directory.
        log_file: Path | None = None
        if args.dry_run:
            log_file = Path.cwd() / "totstats-dryrun.log"
        else:
            try:
                paths.install_dir().mkdir(parents=True, exist_ok=True)
                log_file = paths.app_log_path()
            except OSError:
                pass

        self.log = AppLog(
            file_path=log_file,
            echo_stdout=not args.silent,
            level="DEBUG" if args.verbose else "INFO",
        )
        self.log.rotate_if_large()

        self.store = SettingsStore(None if args.dry_run else paths.settings_path(), self.log)
        self.settings = self.store.load()

        self.ui = UiQueue(self.log)
        self._root: tk.Tk | None = None
        self._console: ConsoleWindow | None = None
        self._tray: TrayIcon | None = None

        logs_dir = Path(args.logs_dir) if args.logs_dir else paths.default_game_logs_dir()
        self._ids = OwnProfileIdResolver()
        self._game = GameProcessWatcher()
        self._tailer = LogTailer(logs_dir, log=self.log)
        self._api = ContributeApi(dry_run=args.dry_run)
        self.contribute = ContributeService(self._api, self._ids, self.log)
        self.contribute.enabled = self.settings.features.contribute

        self._tailer.subscribe(
            self.contribute.on_line,
            needles=ContributeService.INTERESTS,
            on_rotate=self.contribute.on_rotate,
            name="contribute",
        )

        # A dry run keeps its hands off the installation: no catalog cache on disk.
        cache_dir = None if args.dry_run else paths.install_dir()
        catalog = load_catalog(self.log, cache_dir)
        self._discord = DiscordClient(
            DISCORD_CLIENT_ID, self.log, dry_run=args.dry_run and not args.presence_connect
        )
        self._live_api = LivePresenceApi(dry_run=args.dry_run)
        self.presence = PresenceService(
            self._discord, catalog, self._ids, self.log, live_api=self._live_api
        )
        self.presence.enabled = self.settings.features.presence
        self._catalog_sync = (
            None
            if cache_dir is None
            else CatalogSync(self.presence.set_catalog, self.log, self._stop, cache_dir, catalog)
        )

        self._tailer.subscribe(
            self.presence.on_line,
            needles=PresenceService.INTERESTS,
            on_rotate=self.presence.on_rotate,
            on_replay_complete=self.presence.on_replay_complete,
            name="presence",
        )

    def status_text(self) -> str:
        if self.args.dry_run:
            return "Dry run"
        if not (self.settings.features.contribute or self.settings.features.presence):
            return "Paused"
        return "Monitoring" if self._game.running else "Waiting for game"

    def run(self) -> int:
        self.log.info(f"🚀 {APP_NAME} v{__version__} started")
        if self.args.dry_run:
            self.log.info("🧪 Dry run: reading logs without the game, nothing is sent")

        self._root = tk.Tk()
        self._root.withdraw()
        # Nothing stretches the process any more, so Tk has to scale its own points or the text
        # comes out tiny on a scaled display.
        self._root.tk.call("tk", "scaling", self._root.winfo_fpixels("1i") / 72.0)
        self._console = ConsoleWindow(self._root, self.log, paths.icon_path())

        # Before the tray starts, so its checkbox reads a settled value.
        if not self.args.dry_run:
            self._resolve_autostart()

        self.contribute.start()
        self.presence.start()
        if self._catalog_sync is not None:
            self._catalog_sync.start()

        watcher = threading.Thread(target=self._watch_loop, name="watcher", daemon=True)
        watcher.start()

        self._tray = TrayIcon(
            self.ui,
            TrayCallbacks(
                open_console=self._open_console,
                uninstall=self._uninstall,
                quit=self.request_quit,
                status_text=self.status_text,
                autostart_enabled=lambda: bool(self.settings.autostart),
                contribute_enabled=lambda: self.settings.features.contribute,
                presence_enabled=lambda: self.settings.features.presence,
                toggle_autostart=self._toggle_autostart,
                toggle_contribute=self._toggle_contribute,
                toggle_presence=self._toggle_presence,
            ),
            paths.icon_path(),
        )
        tray_thread = threading.Thread(target=self._tray.run, name="tray", daemon=True)
        tray_thread.start()

        try:
            self._main_loop()
        except KeyboardInterrupt:
            pass
        self._shutdown(watcher)
        return 0

    def request_quit(self) -> None:
        self.log.info("🛑 Shutting down…")
        self._stop.set()

    def _open_console(self) -> None:
        if self._console is not None:
            self._console.open()

    def _resolve_autostart(self) -> None:
        """Settle autostart once, then keep the registry matching the answer.

        A frozen build opts in by default; an entry from an older version counts the same. Once
        the answer exists — here or from the tray — it is what applies, including "no". Only a
        settings file that has never recorded one gets the default, which is what makes the tray
        toggle stick across restarts.
        """
        if self.settings.autostart is None:
            if autostart.migrate_legacy():
                self.settings.autostart = True
                self.log.info("✅ Autostart carried over from a previous version")
            else:
                self.settings.autostart = autostart.is_enabled() or paths.is_frozen()
                if self.settings.autostart:
                    self.log.info("✅ Starting with Windows — untick it in the tray to stop that")
            self.store.save()

        if self.settings.autostart:
            autostart.enable()
        else:
            autostart.disable()

    def _toggle_autostart(self) -> None:
        if self.args.dry_run:
            self.log.info("🧪 Dry run: the autostart entry is left untouched")
            return
        enabled = not bool(self.settings.autostart)
        self.settings.autostart = enabled
        if enabled and not autostart.enable():
            self.log.warning("⚠️ Could not write the autostart entry")
        elif not enabled:
            autostart.disable()
        self.store.save()
        self.log.info("✅ Autostart enabled" if enabled else "ℹ️ Autostart disabled")

    def _toggle_contribute(self) -> None:
        enabled = not self.settings.features.contribute
        self.settings.features.contribute = enabled
        self.contribute.enabled = enabled
        self.store.save()
        self.log.info(
            "✅ Contributing player data" if enabled else "⏸️ Contributing paused — nothing is sent"
        )
        self._update_tray()

    def _toggle_presence(self) -> None:
        enabled = not self.settings.features.presence
        self.settings.features.presence = enabled
        self.presence.set_enabled(enabled)
        self.store.save()
        if enabled and self._catalog_sync is not None:
            self._catalog_sync.refresh_soon()
        self.log.info(
            "✅ Discord Rich Presence enabled"
            if enabled
            else "⏸️ Discord Rich Presence paused — your Discord status is cleared"
        )
        self._update_tray()

    def _uninstall(self) -> None:
        self._uninstalling = True
        if autostart.disable():
            self.log.info("✅ Autostart removed")
        self.log.info("🗑️ Uninstalling…")
        self._stop.set()

    def _main_loop(self) -> None:
        assert self._root is not None
        while not self._stop.is_set():
            self.ui.drain(timeout=0.05)
            if self._console is not None and self._console.is_open:
                self._console.pump()
            try:
                self._root.update()
            except tk.TclError:
                return

    def _watch_loop(self) -> None:
        next_process_check = 0.0
        while not self._stop.is_set():
            if not self.args.dry_run:
                now = time.monotonic()
                if now >= next_process_check:
                    next_process_check = now + PROCESS_INTERVAL
                    event = self._game.poll()
                    if event is not None:
                        self._on_game_event(event)

                if not self._game.running:
                    self._stop.wait(IDLE_INTERVAL)
                    continue

            self._tailer.poll()
            if self._tailer.replaying:
                continue
            self._stop.wait(TAIL_INTERVAL if self._tailer.current_file else IDLE_INTERVAL)

    def _on_game_event(self, event: GameProcessEvent) -> None:
        if event.running:
            self.log.info("🎮 The Outlast Trials detected, monitoring started")
            self._ids.reset()
            self._tailer.reset()
            self.contribute.on_rotate()
            self.presence.on_game_started(event.started_at)
        else:
            self.log.info("🛑 The Outlast Trials closed, monitoring stopped")
            self._tailer.reset()
            self.presence.on_game_stopped()
        self._update_tray()

    def _update_tray(self) -> None:
        if self._tray is not None:
            self._tray.set_status(self.status_text())

    def _shutdown(self, watcher: threading.Thread) -> None:
        self._stop.set()
        self.contribute.stop()
        self.presence.stop()
        self._api.close()
        self._live_api.close()
        watcher.join(2.0)
        self._tailer.close()

        if self._tray is not None:
            self._tray.stop()
        if self._console is not None:
            self._console.close()
        if self._root is not None:
            try:
                self._root.destroy()
            except tk.TclError:
                pass

        # Deleting the install directory has to be the very last thing: it takes monitor.log
        # with it, and on a frozen build the running executable lives inside it.
        if self._uninstalling:
            installer.schedule_uninstall()
