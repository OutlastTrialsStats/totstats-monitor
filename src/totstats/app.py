from __future__ import annotations

import argparse
import contextlib
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
from totstats.shared.api import api_base_override
from totstats.shared.applog import AppLog
from totstats.shared.game_process import GameProcessWatcher
from totstats.shared.log_tail import LogTailer
from totstats.shared.profile_id import OwnProfileIdResolver
from totstats.shared.settings import SettingsStore
from totstats.shared.ui_queue import UiQueue
from totstats.ui.console_window import ConsoleWindow
from totstats.ui.tray import TrayIcon

TAIL_INTERVAL = 0.25
PROCESS_INTERVAL = 3.0
IDLE_INTERVAL = 1.0


class App:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._stop = threading.Event()
        self._uninstalling = False

        data_dir = None if args.dry_run else paths.install_dir()
        if data_dir is None:
            log_file = Path.cwd() / "totstats-dryrun.log"
            settings_file = None
        else:
            log_file = data_dir / "monitor.log"
            settings_file = data_dir / "settings.json"
            with contextlib.suppress(OSError):
                data_dir.mkdir(parents=True, exist_ok=True)
        self.log = AppLog(log_file, echo=not args.silent, level="DEBUG" if args.verbose else "INFO")

        self.store = SettingsStore(settings_file, self.log)
        self.settings = self.store.load()
        self.ui = UiQueue(self.log)

        ids = OwnProfileIdResolver()
        self._game = GameProcessWatcher()
        self._tailer = LogTailer(Path(args.logs_dir) if args.logs_dir else paths.game_logs_dir(), self.log)

        self._api = None if args.dry_run else ContributeApi()
        self.contribute = ContributeService(self._api, ids, self.log)
        self.contribute.enabled = self.settings.features.contribute
        self._tailer.subscribe(
            "contribute",
            self.contribute.on_line,
            ContributeService.INTERESTS,
            on_rotate=self.contribute.on_rotate,
        )

        discord = DiscordClient(
            DISCORD_CLIENT_ID, self.log, dry_run=args.dry_run and not args.presence_connect
        )
        self._live_api = None if args.dry_run and api_base_override() is None else LivePresenceApi()
        self.presence = PresenceService(
            discord, load_catalog(self.log, data_dir), ids, self.log, live_api=self._live_api
        )
        self.presence.enabled = self.settings.features.presence
        self._catalog_sync = None
        if data_dir is not None:
            self._catalog_sync = CatalogSync(self.presence.set_catalog, self.log, self._stop, data_dir)
        self._tailer.subscribe(
            "presence",
            self.presence.on_line,
            PresenceService.INTERESTS,
            on_rotate=self.presence.on_rotate,
            on_replay_complete=self.presence.on_replay_complete,
        )

    def status_text(self) -> str:
        if self.args.dry_run:
            return "Dry run"
        if not (self.settings.features.contribute or self.settings.features.presence):
            return "Paused"
        return "Monitoring" if self._game.running else "Waiting for game"

    def run(self) -> int:
        self.log.info(f"{APP_NAME} {__version__} started")
        if self.args.dry_run:
            self.log.info("Dry run: reading logs without the game, nothing is sent")

        self._root = tk.Tk()
        self._root.withdraw()
        self._root.tk.call("tk", "scaling", self._root.winfo_fpixels("1i") / 72.0)
        self._console = ConsoleWindow(self._root, self.log)
        self._tray = TrayIcon(self)

        if not self.args.dry_run:
            self._resolve_autostart()

        self.contribute.start()
        self.presence.start()
        if self._catalog_sync is not None:
            self._catalog_sync.start()
        watcher = threading.Thread(target=self._watch, name="watcher", daemon=True)
        watcher.start()
        threading.Thread(target=self._tray.run, name="tray", daemon=True).start()

        with contextlib.suppress(KeyboardInterrupt):
            self._main_loop()
        self._shutdown(watcher)
        return 0

    def quit(self) -> None:
        self.log.info("Shutting down")
        self._stop.set()

    def open_console(self) -> None:
        self._console.open()

    def toggle_autostart(self) -> None:
        if self.args.dry_run:
            self.log.info("Dry run: autostart unchanged")
            return
        self.settings.autostart = not self.settings.autostart
        if not self.settings.autostart:
            autostart.disable()
            self.log.info("Autostart disabled")
        elif autostart.enable():
            self.log.info("Autostart enabled")
        else:
            self.log.warning("Autostart entry could not be written")
        self.store.save()

    def toggle_contribute(self) -> None:
        enabled = not self.settings.features.contribute
        self.settings.features.contribute = enabled
        self.contribute.enabled = enabled
        self.store.save()
        self.log.info("Contributing enabled" if enabled else "Contributing paused")
        self._tray.update()

    def toggle_presence(self) -> None:
        enabled = not self.settings.features.presence
        self.settings.features.presence = enabled
        self.presence.set_enabled(enabled)
        self.store.save()
        if enabled and self._catalog_sync is not None:
            self._catalog_sync.refresh_soon()
        self.log.info("Rich Presence enabled" if enabled else "Rich Presence paused")
        self._tray.update()

    def uninstall(self) -> None:
        self._uninstalling = True
        if autostart.disable():
            self.log.info("Autostart removed")
        self.log.info("Uninstalling")
        self._stop.set()

    def _resolve_autostart(self) -> None:
        if self.settings.autostart is None:
            if autostart.migrate_legacy():
                self.settings.autostart = True
                self.log.info("Autostart carried over from a previous version")
            else:
                self.settings.autostart = autostart.is_enabled() or paths.is_frozen()
                if self.settings.autostart:
                    self.log.info("Autostart enabled, change it in the tray menu")
            self.store.save()

        if self.settings.autostart:
            autostart.enable()
        else:
            autostart.disable()

    def _main_loop(self) -> None:
        while not self._stop.is_set():
            self.ui.drain()
            self._console.pump()
            try:
                self._root.update()
            except tk.TclError:
                return

    def _watch(self) -> None:
        next_check = 0.0
        while not self._stop.is_set():
            if not self.args.dry_run:
                now = time.monotonic()
                if now >= next_check:
                    next_check = now + PROCESS_INTERVAL
                    if self._game.poll():
                        self._on_game_change()
                if not self._game.running:
                    self._stop.wait(IDLE_INTERVAL)
                    continue

            self._tailer.poll()
            if not self._tailer.replaying:
                self._stop.wait(TAIL_INTERVAL if self._tailer.path else IDLE_INTERVAL)

    def _on_game_change(self) -> None:
        self._tailer.reset()
        if self._game.running:
            self.log.info("Game started, monitoring")
            self.contribute.on_rotate()
            self.presence.on_game_started(self._game.started_at)
        else:
            self.log.info("Game closed, monitoring stopped")
            self.presence.on_game_stopped()
        self._tray.update()

    def _shutdown(self, watcher: threading.Thread) -> None:
        self._stop.set()
        self.contribute.stop()
        self.presence.stop()
        if self._api is not None:
            self._api.close()
        if self._live_api is not None:
            self._live_api.close()
        watcher.join(2.0)

        self._tray.stop()
        self._console.close()
        with contextlib.suppress(tk.TclError):
            self._root.destroy()

        if self._uninstalling:
            installer.schedule_uninstall()
