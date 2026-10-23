from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

from totstats import APP_NAME
from totstats.shared import paths, windows

if TYPE_CHECKING:
    from totstats.app import App

FALLBACK_COLOR = (203, 202, 153, 255)


def _image() -> Image.Image:
    try:
        return Image.open(paths.icon_path())
    except OSError:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle([4, 4, 60, 60], radius=12, fill=FALLBACK_COLOR)
        return image


class _Icon(pystray.Icon):
    def _assert_icon_handle(self) -> None:
        if not self._icon_handle:
            self._icon_handle = windows.load_icon(paths.icon_path(), windows.small_icon_size())
        if not self._icon_handle:
            super()._assert_icon_handle()


class TrayIcon:
    def __init__(self, app: App) -> None:
        self._app = app
        self._icon = _Icon(APP_NAME, _image(), self._title(), self._menu())

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._icon.stop()

    def update(self) -> None:
        with contextlib.suppress(Exception):
            self._icon.title = self._title()

    def _title(self) -> str:
        return f"{APP_NAME} - {self._app.status_text()}"

    def _post(self, fn: Callable[[], None]) -> Callable[[object, object], None]:
        return lambda _icon, _item: self._app.ui.post(fn)

    def _menu(self) -> pystray.Menu:
        app = self._app
        return pystray.Menu(
            pystray.MenuItem(lambda _item: f"Status: {app.status_text()}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Contribute player data",
                self._post(app.toggle_contribute),
                checked=lambda _item: app.settings.features.contribute,
            ),
            pystray.MenuItem(
                "Discord Rich Presence",
                self._post(app.toggle_presence),
                checked=lambda _item: app.settings.features.presence,
            ),
            pystray.MenuItem(
                "Start with Windows",
                self._post(app.toggle_autostart),
                checked=lambda _item: bool(app.settings.autostart),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Console", self._post(app.open_console)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Uninstall", self._post(app.uninstall)),
            pystray.MenuItem("Exit", self._post(app.quit)),
        )
