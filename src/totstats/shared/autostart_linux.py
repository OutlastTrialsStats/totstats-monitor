from __future__ import annotations

from pathlib import Path

from totstats import APP_NAME
from totstats.shared import paths

FILE_NAME = "totstatsmonitor.desktop"


def desktop_file() -> Path:
    return paths.xdg_dir("XDG_CONFIG_HOME", ".config") / "autostart" / FILE_NAME


def _quote(arg: str) -> str:
    # Desktop Entry spec: three layers, applied in this order. Quoted Exec arguments escape
    # these four characters, "%" is a field code, and the file's string syntax doubles every
    # backslash that the first layer produced.
    for char in ("\\", '"', "`", "$"):
        arg = arg.replace(char, "\\" + char)
    arg = arg.replace("%", "%%").replace("\\", "\\\\")
    return f'"{arg}"'


def _command() -> str:
    exe = _quote(str(paths.app_executable()))
    return f"{exe} --silent" if paths.is_frozen() else f"{exe} -m totstats --silent"


def is_enabled() -> bool:
    return desktop_file().is_file()


def enable() -> bool:
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={_command()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    path = desktop_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError:
        return False
    return True


def disable() -> bool:
    try:
        desktop_file().unlink()
    except OSError:
        return False
    return True
