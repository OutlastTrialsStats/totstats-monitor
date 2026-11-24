from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

from totstats import APP_NAME
from totstats.shared import autostart, paths

if TYPE_CHECKING:
    from totstats.app import App

FALLBACK_COLOR = (203, 44, 41, 255)


def _image() -> Image.Image:
    try:
        return Image.open(paths.icon_path() if paths.is_windows() else paths.logo_path())
    except OSError:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle([4, 4, 60, 60], radius=12, fill=FALLBACK_COLOR)
        return image


class _LockedIcon(pystray.Icon):
    # update_menu() runs on the pystray thread after every click, on the Tk thread after a toggle
    # and on the watcher thread when the game starts or stops. pystray rebuilds the native menu
    # without a lock, so two rebuilds at once would destroy the same handle.
    _menu_lock = threading.Lock()

    def update_menu(self) -> None:
        with self._menu_lock:
            super().update_menu()


if sys.platform == "win32":
    from totstats.shared import windows

    class _Icon(_LockedIcon):
        def _assert_icon_handle(self) -> None:
            if not self._icon_handle:
                self._icon_handle = windows.load_icon(paths.icon_path(), windows.small_icon_size())
            if not self._icon_handle:
                super()._assert_icon_handle()

    AUTOSTART_KEY = "tray.autostart_windows"
else:
    _Icon = _LockedIcon
    AUTOSTART_KEY = "tray.autostart_login"


class TrayIcon:
    def __init__(self, app: App) -> None:
        self._app = app
        self._icon = _Icon(APP_NAME, _image(), self._title(), self._menu())

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._icon.stop()

    def update(self, menu: bool = True) -> None:
        with contextlib.suppress(Exception):
            self._icon.title = self._title()
            if menu:
                self._icon.update_menu()

    def _title(self) -> str:
        return f"{APP_NAME} - {self._app.status_text()}"

    def _post(self, fn: Callable[[], None]) -> Callable[[object, object], None]:
        return lambda _icon, _item: self._app.ui.post(fn)

    def _text(self, key: str) -> Callable[[object], str]:
        # Evaluated whenever pystray builds the menu, so a language switch only needs update_menu().
        return lambda _item: self._app.tr.t(key)

    def _language_checked(self, code: str) -> bool:
        # The setting may be "de-DE" or unknown; tick the language actually in use.
        setting = self._app.settings.language
        if code == "auto" or setting == "auto":
            return code == setting
        return code == self._app.tr.code

    def _language_menu(self) -> pystray.Menu:
        app = self._app
        choices = {"auto": None, **app.locales.available()}

        def item(code: str, name: str | None) -> pystray.MenuItem:
            return pystray.MenuItem(
                name or self._text("tray.language_auto"),
                self._post(lambda: app.set_language(code)),
                checked=lambda _item: self._language_checked(code),
                radio=True,
            )

        return pystray.Menu(*(item(code, name) for code, name in choices.items()))

    def _menu(self) -> pystray.Menu:
        app = self._app
        return pystray.Menu(
            pystray.MenuItem(
                lambda _item: app.tr.t("tray.status", status=app.status_text()), None, enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                self._text("tray.contribute"),
                self._post(app.toggle_contribute),
                checked=lambda _item: app.settings.features.contribute,
            ),
            pystray.MenuItem(
                self._text("tray.presence"),
                self._post(app.toggle_presence),
                checked=lambda _item: app.settings.features.presence,
            ),
            pystray.MenuItem(self._text("tray.presence_customize"), self._post(app.open_presence_settings)),
            pystray.MenuItem(
                self._text(AUTOSTART_KEY),
                self._post(app.toggle_autostart),
                checked=lambda _item: autostart.is_enabled(),
            ),
            pystray.MenuItem(self._text("tray.language"), self._language_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._text("tray.console"), self._post(app.open_console)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._text("tray.uninstall"), self._post(app.uninstall)),
            pystray.MenuItem(self._text("tray.exit"), self._post(app.quit)),
        )
