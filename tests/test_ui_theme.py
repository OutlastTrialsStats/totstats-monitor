from __future__ import annotations

import colorsys
from pathlib import Path

import pytest
import sv_ttk
from PIL import Image

from totstats.ui import theme

SUN_VALLEY = Path(sv_ttk.__file__).with_name("theme")


@pytest.mark.parametrize(
    ("rgb", "accent"),
    [
        ((0x57, 0xC8, 0xFF), True),  # Sun Valley's dark accent
        ((0x00, 0x5F, 0xB8), True),  # its light accent
        ((0x2B, 0x2B, 0x2B), False),  # a grey card
        ((0xFA, 0xFA, 0xFA), False),  # near white text
        ((0xD6, 0x45, 0x41), False),  # already red
    ],
)
def test_only_the_blue_accent_counts(rgb, accent):
    assert theme.is_accent(*rgb) is accent


def test_the_accent_turns_red_and_everything_else_stays():
    sheet = Image.new("RGBA", (3, 1))
    sheet.putpixel((0, 0), (0x57, 0xC8, 0xFF, 255))
    sheet.putpixel((1, 0), (0x2B, 0x2B, 0x2B, 255))
    sheet.putpixel((2, 0), (0x00, 0x5F, 0xB8, 90))

    recolored = theme.recolor_accent(sheet)

    r, g, b, a = recolored.getpixel((0, 0))
    hue, _saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    assert (hue < 0.03 or hue > 0.97) and a == 255
    assert value <= theme.BRAND_MAX_VALUE + 0.01
    assert recolored.getpixel((1, 0)) == (0x2B, 0x2B, 0x2B, 255)
    assert recolored.getpixel((2, 0))[3] == 90


@pytest.mark.parametrize("variant", theme.THEMES)
def test_the_bundled_sprite_lists_parse(variant):
    sprites = theme.parse_sprites((SUN_VALLEY / f"sprites_{variant}.tcl").read_text(encoding="utf-8"))
    names = {name for name, *_box in sprites}
    assert {"card", "switch-rest", "button-accent-rest"} <= names
    assert all(width > 0 and height > 0 for _name, _x, _y, width, height in sprites)


def test_a_translucent_pixel_blends_over_the_window():
    assert theme.blend((255, 255, 255, 0), "#1c1c1c") == "#1c1c1c"
    assert theme.blend((255, 255, 255, 255), "#1c1c1c") == "#ffffff"
    assert theme.blend((255, 255, 255, 51), "#000000") == "#333333"


@pytest.mark.parametrize(("value", "dark"), [(0, True), (1, False), (None, False)])
def test_the_windows_app_theme(value, dark):
    assert theme.dark_from_windows(value) is dark


@pytest.mark.parametrize(
    ("output", "dark"),
    [("'prefer-dark'\n", True), ("'prefer-light'\n", False), ("'default'\n", False), ("", None)],
)
def test_the_gnome_colour_scheme(output, dark):
    assert theme.dark_from_gnome(output) is dark


class _NoSprites:
    """A Tk interpreter whose Sun Valley keeps no sprite images where the recolour looks."""

    def __init__(self) -> None:
        self.calls = []

    def call(self, *args):
        self.calls.append(args[:2])

    def eval(self, _script):
        raise theme.tk.TclError("no such variable")


def test_a_recolour_that_copied_nothing_has_failed():
    root = type("Root", (), {"tk": _NoSprites()})()
    with pytest.raises(ValueError, match="sv_dark"):
        theme._recolor(root, SUN_VALLEY, "dark")
    assert root.tk.calls[-1] == ("image", "delete")
