from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

# Drawn this many times larger, then scaled down: Pillow has no anti-aliasing of its own.
SUPERSAMPLE = 4
GRID = 24
STROKE = 1.7


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw, float]:
    image = Image.new("RGBA", (size * SUPERSAMPLE, size * SUPERSAMPLE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), size * SUPERSAMPLE / GRID


def _finish(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.Resampling.LANCZOS)


def icon(name: str, size: int, color: str) -> Image.Image:
    """A line icon on a 24-unit grid, in the style of the Windows 11 icons around it."""
    image, draw, k = _canvas(size)
    width = max(1, round(STROKE * k))

    def box(x1: float, y1: float, x2: float, y2: float) -> list[float]:
        return [x1 * k, y1 * k, x2 * k, y2 * k]

    def line(*points: float) -> None:
        draw.line([value * k for value in points], fill=color, width=width, joint="curve")

    if name == "show_trial_image":
        draw.rounded_rectangle(box(3, 4, 21, 20), radius=2 * k, outline=color, width=width)
        draw.ellipse(box(7, 8, 11, 12), outline=color, width=width)
        line(21, 16, 15, 11, 6, 20)
    elif name == "show_program":
        line(4, 7, 20, 7)
        line(4, 12, 20, 12)
        line(4, 17, 14, 17)
    elif name == "show_location":
        draw.ellipse(box(6, 3, 18, 15), outline=color, width=width)
        line(7, 12.5, 12, 21, 17, 12.5)
        draw.ellipse(box(10.3, 7.3, 13.7, 10.7), outline=color, width=width)
    elif name == "show_difficulty":
        draw.arc(box(4, 7, 20, 23), start=180, end=360, fill=color, width=width)
        line(12, 15, 16, 10)
    elif name == "show_party":
        draw.ellipse(box(5, 4, 12, 11), outline=color, width=width)
        draw.arc(box(2, 13, 15, 26), start=180, end=360, fill=color, width=width)
        draw.arc(box(13, 4, 20, 11), start=270, end=90, fill=color, width=width)
        draw.arc(box(13, 13, 22, 26), start=270, end=360, fill=color, width=width)
    elif name == "show_timer":
        draw.ellipse(box(3, 3, 21, 21), outline=color, width=width)
        line(12, 7, 12, 12, 15.5, 14)
    elif name == "show_role_icon":
        line(12, 3, 20, 6, 20, 12)
        line(12, 3, 4, 6, 4, 12)
        draw.arc(box(4, 2, 20, 22), start=0, end=180, fill=color, width=width)
    elif name == "show_stats_button":
        draw.rounded_rectangle(box(3, 7, 21, 17), radius=4 * k, outline=color, width=width)
        line(8, 12, 16, 12)
    elif name == "gamepad":
        draw.rounded_rectangle(box(2, 7, 22, 18), radius=5 * k, outline=color, width=width)
        line(7, 10.5, 7, 14.5)
        line(5, 12.5, 9, 12.5)
        draw.ellipse(box(15, 10, 17, 12), fill=color)
        draw.ellipse(box(17.5, 12.5, 19.5, 14.5), fill=color)
    elif name == "reset":
        # Clockwise from the upper left round to the left side, the arrow head at its start.
        draw.arc(box(4, 4, 20, 20), start=210, end=150, fill=color, width=width)
        line(5, 3.5, 5, 8, 9.5, 8)
    return _finish(image, size)


def rounded(image: Image.Image, size: int, radius: int, backing: str | None = None) -> Image.Image:
    """Cover a size x size square with the picture, on `backing` if given, and round its corners."""
    picture = _cover(image, size * SUPERSAMPLE)
    if backing:
        tile = Image.new("RGBA", picture.size, backing)
        tile.alpha_composite(picture)
        picture = tile
    mask = Image.new("L", picture.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, picture.width - 1, picture.height - 1], radius=radius * SUPERSAMPLE, fill=255
    )
    picture.putalpha(_multiply(picture.getchannel("A"), mask))
    return picture.resize((size, size), Image.Resampling.LANCZOS)


def circle(image: Image.Image, size: int, ring: str | None = None, ring_width: int = 0) -> Image.Image:
    """The picture cut to a circle, optionally inside a ring the colour of what lies behind it."""
    outer = size * SUPERSAMPLE
    result = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    draw = ImageDraw.Draw(result)
    inset = ring_width * SUPERSAMPLE
    if ring:
        draw.ellipse([0, 0, outer - 1, outer - 1], fill=ring)
    inner = outer - 2 * inset
    picture = _cover(image, inner)
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
    result.paste(picture, (inset, inset), _multiply(picture.getchannel("A"), mask))
    return result.resize((size, size), Image.Resampling.LANCZOS)


def dot(size: int, color: str, ring: str, ring_width: int) -> Image.Image:
    return circle(Image.new("RGBA", (1, 1), color), size, ring, ring_width)


def fit(image: Image.Image, size: int) -> Image.Image:
    """Scaled to fit a size x size square, aspect ratio kept."""
    picture = image.convert("RGBA")
    picture.thumbnail((size, size), Image.Resampling.LANCZOS)
    return picture


def _cover(image: Image.Image, size: int) -> Image.Image:
    picture = image.convert("RGBA")
    scale = size / min(picture.width, picture.height)
    picture = picture.resize(
        (max(size, round(picture.width * scale)), max(size, round(picture.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (picture.width - size) // 2
    top = (picture.height - size) // 2
    return picture.crop((left, top, left + size, top + size))


def _multiply(first: Image.Image, second: Image.Image) -> Image.Image:
    return ImageChops.multiply(first, second)
