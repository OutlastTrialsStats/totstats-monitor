from __future__ import annotations

import base64
import colorsys
import contextlib
import io
import os
import re
import subprocess
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import ttk

from PIL import Image

from totstats.shared import paths

THEMES = ("dark", "light")
# Sun Valley paints its accent blue. Anything that saturated and in this hue band is the accent.
ACCENT_HUES = (0.48, 0.70)
ACCENT_MIN_SATURATION = 0.25
# The red of the app's logo. The dark theme's accent is a very bright blue; red that bright turns pink.
BRAND_HUE = 0.005
BRAND_MAX_VALUE = 0.86

# Sun Valley sizes its fonts in pixels, which Tk does not scale with the display. Points do.
FONT_POINTS = {
    "SunValleyCaptionFont": 9,
    "SunValleyBodyFont": 10,
    "SunValleyBodyStrongFont": 10,
    "SunValleyBodyLargeFont": 13,
    "SunValleySubtitleFont": 15,
    "SunValleyTitleFont": 21,
    "SunValleyTitleLargeFont": 30,
    "SunValleyDisplayFont": 51,
}

SPRITE = re.compile(r"^\s*([\w-]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\\?\s*$")


@dataclass(frozen=True)
class Palette:
    dark: bool
    # The window, and Sun Valley's card on top of it; the card is measured from the theme itself.
    window: str
    surface: str
    line: str
    muted: str
    card: str
    card_text: str
    card_muted: str
    card_line: str
    card_button: str
    card_button_text: str
    tooltip: str
    tooltip_text: str
    online: str = "#23a55a"
    avatar: str = "#5865f2"
    artwork: str = "#1e1f22"


DARK = Palette(
    dark=True,
    window="#1c1c1c",
    surface="#2b2b2b",
    line="#3a3a3a",
    muted="#a8a8a8",
    card="#111214",
    card_text="#f2f3f5",
    card_muted="#b5bac1",
    card_line="#2b2d31",
    card_button="#4e5058",
    card_button_text="#ffffff",
    tooltip="#000000",
    tooltip_text="#dbdee1",
)

LIGHT = Palette(
    dark=False,
    window="#fafafa",
    surface="#ffffff",
    line="#e5e5e5",
    muted="#5c5c5c",
    card="#ffffff",
    card_text="#060607",
    card_muted="#5c5e66",
    card_line="#ebedef",
    card_button="#e3e5e8",
    card_button_text="#060607",
    tooltip="#111214",
    tooltip_text="#f2f3f5",
)


def is_accent(r: int, g: int, b: int) -> bool:
    hue, saturation, _value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return saturation >= ACCENT_MIN_SATURATION and ACCENT_HUES[0] <= hue <= ACCENT_HUES[1]


def recolor_accent(image: Image.Image) -> Image.Image:
    """The same picture with Sun Valley's blue turned into the logo's red, shading kept."""
    rgba = image.convert("RGBA")
    data = bytearray(rgba.tobytes())
    for offset in range(0, len(data), 4):
        r, g, b, a = data[offset:offset + 4]
        if a and is_accent(r, g, b):
            _hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            red = colorsys.hsv_to_rgb(BRAND_HUE, saturation, min(value, BRAND_MAX_VALUE))
            data[offset:offset + 3] = bytes(round(channel * 255) for channel in red)
    return Image.frombytes("RGBA", rgba.size, bytes(data))


def blend(rgba: tuple[int, int, int, int], background: str) -> str:
    """The colour a translucent pixel shows over an opaque background."""
    r, g, b, a = rgba
    base = (int(background[1:3], 16), int(background[3:5], 16), int(background[5:7], 16))
    pairs = zip((r, g, b), base, strict=True)
    mixed = (round(channel * a / 255 + under * (255 - a) / 255) for channel, under in pairs)
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def parse_sprites(text: str) -> list[tuple[str, int, int, int, int]]:
    sprites = []
    for line in text.splitlines():
        match = SPRITE.match(line)
        if match:
            name, *box = match.groups()
            sprites.append((name, *(int(value) for value in box)))
    return sprites


def dark_from_windows(apps_use_light_theme: int | None) -> bool:
    # Missing value: Windows 10 before 1809, which only had a light app theme.
    return apps_use_light_theme == 0


def dark_from_gnome(color_scheme: str) -> bool | None:
    value = color_scheme.strip().strip("'\"").lower()
    if not value:
        return None
    return value == "prefer-dark"


def system_prefers_dark() -> bool:
    if paths.is_windows():
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return dark_from_windows(int(value))
        except (OSError, ValueError):
            return dark_from_windows(None)
    if os.environ.get("GTK_THEME", "").lower().endswith(":dark"):
        return True
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0 and (dark := dark_from_gnome(result.stdout)) is not None:
            return dark
    # A game's companion: most players run a dark desktop, and dark is what the app was designed in.
    return True


def apply(root: tk.Tk) -> Palette:
    """Use Sun Valley in the system's light or dark variant and return the matching colours."""
    import sv_ttk

    dark = system_prefers_dark()
    variant = "dark" if dark else "light"
    theme_dir = Path(sv_ttk.__file__).with_name("theme")
    sv_ttk.set_theme(variant, root)
    # Sun Valley sets its window colours on <<ThemeChanged>>, which a withdrawn root never receives.
    with contextlib.suppress(tk.TclError):
        root.tk.call("configure_colors")
    if not getattr(root, "_totstats_fonts_ready", False):
        root._totstats_fonts_ready = True
        _use_points(root)
    # Each variant once, and again on the next opening should it have failed.
    recolored = getattr(root, "_totstats_recolored", set())
    root._totstats_recolored = recolored
    for name in THEMES:
        if name not in recolored:
            with contextlib.suppress(OSError, ValueError, tk.TclError):
                _recolor(root, theme_dir, name)
                recolored.add(name)
    palette = DARK if dark else LIGHT
    with contextlib.suppress(OSError, ValueError, IndexError):
        palette = replace(palette, surface=_card_surface(theme_dir, variant, palette.window))
    _styles(root, palette)
    return palette


def _card_surface(theme_dir: Path, theme: str, window: str) -> str:
    # Sun Valley's card is a translucent sprite; what shows is its middle laid over the window.
    sprites = parse_sprites((theme_dir / f"sprites_{theme}.tcl").read_text(encoding="utf-8"))
    for name, x, y, width, height in sprites:
        if name == "card":
            with Image.open(theme_dir / f"spritesheet_{theme}.png") as sheet:
                pixel = sheet.convert("RGBA").getpixel((x + width // 2, y + height // 2))
            return blend(pixel, window)
    raise ValueError("no card sprite")


def _styles(root: tk.Tk, palette: Palette) -> None:
    # Widgets on a card need the card's colour where their own images are transparent.
    style = ttk.Style(root)
    fixed = ("Consolas", 9) if paths.is_windows() else "TkFixedFont"
    style.configure("Card.TLabel", background=palette.surface)
    style.configure("Hint.Card.TLabel", background=palette.surface, foreground=palette.muted,
                    font="SunValleyCaptionFont")
    style.configure("Muted.TLabel", foreground=palette.muted, font="SunValleyCaptionFont")
    style.configure("Section.TLabel", font="SunValleyBodyStrongFont")
    style.configure("Title.TLabel", font="SunValleySubtitleFont")
    style.configure("Card.Switch.TCheckbutton", background=palette.surface)
    style.configure("Card.TEntry", background=palette.surface)
    style.configure("Placeholder.TButton", background=palette.surface, font=fixed, padding=(6, 0, 6, 1))


def _use_points(root: tk.Tk) -> None:
    for font, points in FONT_POINTS.items():
        with contextlib.suppress(tk.TclError):
            root.tk.call("font", "configure", font, "-size", points)


def _recolor(root: tk.Tk, theme_dir: Path, theme: str) -> None:
    # Each sprite is its own Tk image, copied out of a sheet. Copy them again out of a recoloured
    # sheet: only the accent changes, every other pixel comes back as it was.
    with Image.open(theme_dir / f"spritesheet_{theme}.png") as original:
        sheet = recolor_accent(original)
    sprites = parse_sprites((theme_dir / f"sprites_{theme}.tcl").read_text(encoding="utf-8"))
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    source = f"totstats_sheet_{theme}"
    root.tk.call("image", "create", "photo", source, "-format", "png",
                 "-data", base64.b64encode(buffer.getvalue()).decode("ascii"))
    copied = 0
    try:
        for name, x, y, width, height in sprites:
            with contextlib.suppress(tk.TclError):
                target = root.tk.eval(f"set ttk::theme::sv_{theme}::I({name})")
                root.tk.call(target, "copy", source, "-from", x, y, x + width, y + height,
                             "-compositingrule", "set")
                copied += 1
    finally:
        root.tk.call("image", "delete", source)
    # Nothing copied: Sun Valley keeps its sprites somewhere else now. Not done, so tried again.
    if not copied:
        raise ValueError(f"no sprite of sv_{theme} recoloured")


def title_bar(window: tk.Toplevel, palette: Palette) -> None:
    """Match the Windows title bar to the theme; elsewhere the window manager decides."""
    if not paths.is_windows():
        return
    with contextlib.suppress(tk.TclError, OSError, AttributeError):
        from totstats.shared import windows

        window.update_idletasks()
        windows.set_dark_title_bar(window.winfo_id(), palette.dark)
