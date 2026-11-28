from __future__ import annotations

import contextlib
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from totstats.presence.render import GAME_NAME, Payload
from totstats.shared import paths
from totstats.ui import graphics
from totstats.ui.artwork import Artwork
from totstats.ui.theme import Palette

# Sizes in Discord's own pixels, multiplied by the display scale when drawn.
WIDTH = 380
PAD = 14
AVATAR = 36
STATUS = 14
LARGE = 76
SMALL = 26
RING = 3
BUTTON = 32
RADIUS = 8
GAP = 12
ELLIPSIS = "…"


@dataclass(frozen=True)
class CardText:
    you: str
    online: str
    playing: str


def elapsed(start: int | None, now: float) -> str:
    if not start:
        return ""
    seconds = max(0, int(now) - start)
    hours, minutes, seconds = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def card_lines(payload: Payload) -> tuple[str, str]:
    """The two text lines as Discord shows them: blanks dropped, the party size after the state."""
    details = payload.details.strip()
    state = payload.state.strip()
    if payload.party_size:
        state = f"{state} ({payload.party_size[0]}/{payload.party_size[1]})".strip()
    return details, state


def _family() -> str:
    if paths.is_windows():
        return "Segoe UI"
    return tkfont.nametofont("TkDefaultFont").actual("family")


class DiscordCard:
    """The status as it appears in a Discord profile, drawn on a canvas."""

    def __init__(
        self, parent: tk.Misc, palette: Palette, artwork: Artwork, fallback: Image.Image, text: CardText
    ) -> None:
        self._palette = palette
        self._artwork = artwork
        self._fallback = fallback
        self._text = text
        self._scale = parent.winfo_fpixels("1i") / 96.0
        background = ttk.Style(parent).lookup("TFrame", "background") or palette.card
        self.canvas = tk.Canvas(
            parent, width=self._px(WIDTH), height=self._px(200), highlightthickness=0, bd=0, bg=background
        )
        family = _family()
        self._font = tkfont.Font(root=parent, family=family, size=10)
        self._bold = tkfont.Font(root=parent, family=family, size=10, weight="bold")
        self._small = tkfont.Font(root=parent, family=family, size=9)
        self._small_bold = tkfont.Font(root=parent, family=family, size=9, weight="bold")
        self._photos: list[ImageTk.PhotoImage] = []
        self._payload: Payload | None = None
        self._now = 0.0

    def show(self, payload: Payload | None, now: float) -> None:
        self._payload = payload
        self._now = now
        self._draw()

    def _px(self, value: float) -> int:
        return round(value * self._scale)

    def _photo(self, image: Image.Image) -> ImageTk.PhotoImage:
        photo = ImageTk.PhotoImage(image, master=self.canvas)
        self._photos.append(photo)
        return photo

    def _redraw(self) -> None:
        with contextlib.suppress(tk.TclError):
            if self.canvas.winfo_exists():
                self._draw()

    def _draw(self) -> None:
        canvas, palette, px = self.canvas, self._palette, self._px
        canvas.delete("all")
        self._photos = []
        payload = self._payload
        if payload is None:
            canvas.configure(height=px(GAP))
            return

        width = px(WIDTH)
        x, y = px(PAD), px(PAD)
        content = width - 2 * x
        # Background first, its height settled at the end.
        background = canvas.create_image(0, 0, anchor="nw")

        avatar = graphics.dot(px(AVATAR), palette.avatar, palette.avatar, 0)
        canvas.create_image(x, y, anchor="nw", image=self._photo(avatar))
        status = graphics.dot(px(STATUS), palette.online, palette.card, px(RING))
        corner = px(AVATAR + 2)
        canvas.create_image(x + corner, y + corner, anchor="se", image=self._photo(status))
        name_x = x + px(AVATAR + 10)
        canvas.create_text(name_x, y + px(AVATAR) // 2, anchor="sw", text=self._text.you,
                           font=self._bold, fill=palette.card_text)
        canvas.create_text(name_x, y + px(AVATAR) // 2, anchor="nw", text=self._text.online,
                           font=self._small, fill=palette.card_muted)
        y += px(AVATAR + GAP)
        canvas.create_line(x, y, x + content, y, fill=palette.card_line)
        y += px(GAP)

        canvas.create_text(x, y, anchor="nw", text=self._text.playing, font=self._small_bold,
                           fill=palette.card_muted)
        y += self._small_bold.metrics("linespace") + px(8)

        bottom = self._activity(x, y, content, payload)
        if payload.buttons:
            bottom += px(GAP)
            self._button(x, bottom, content, payload.buttons[0][0])
            bottom += px(BUTTON)
        height = bottom + px(PAD)

        canvas.itemconfigure(background, image=self._photo(_box(width, height, px(RADIUS), palette.card)))
        canvas.configure(height=height)

    def _activity(self, x: int, y: int, width: int, payload: Payload) -> int:
        canvas, palette, px = self.canvas, self._palette, self._px
        picture = self._artwork.get(payload.large_image, self._redraw) or self._fallback
        # The site's default artwork is a white logo on nothing; give it the dark ground it is made for.
        cover = graphics.rounded(picture, px(LARGE), px(RADIUS), palette.artwork)
        large = canvas.create_image(x, y, anchor="nw", image=self._photo(cover))
        self._tooltip(large, payload.large_text.strip(), x, y)
        if payload.small_image:
            corner_x, corner_y = x + px(LARGE) + px(RING + 2), y + px(LARGE) + px(RING + 2)
            icon = self._artwork.get(payload.small_image, self._redraw)
            small_picture = icon if icon is not None else Image.new("RGBA", (1, 1), palette.card_line)
            small = canvas.create_image(
                corner_x, corner_y, anchor="se",
                image=self._photo(graphics.circle(small_picture, px(SMALL), palette.card, px(RING))),
            )
            small_text = (payload.small_text or "").strip()
            self._tooltip(small, small_text, corner_x - px(SMALL), corner_y - px(SMALL))

        text_x = x + px(LARGE + GAP)
        room = x + width - text_x
        details, state = card_lines(payload)
        timer = elapsed(payload.start, self._now)
        rows = [(GAME_NAME, self._bold, palette.card_text)]
        rows += [(line, self._font, palette.card_text) for line in (details, state) if line]
        line_height = self._font.metrics("linespace")
        block = line_height * (len(rows) + (1 if timer else 0))
        top = y + max(0, (px(LARGE) - block) // 2)
        for text, font, color in rows:
            canvas.create_text(text_x, top, anchor="nw", text=_fit(text, font, room), font=font, fill=color)
            top += line_height
        if timer:
            size = self._small_bold.metrics("ascent")
            pad = graphics.icon("gamepad", size + px(2), palette.online)
            middle = top + line_height // 2
            canvas.create_image(text_x, middle, anchor="w", image=self._photo(pad))
            canvas.create_text(text_x + pad.width + px(5), middle, anchor="w", text=timer,
                               font=self._small_bold, fill=palette.online)
            top += line_height
        return max(y + px(LARGE), top)

    def _button(self, x: int, y: int, width: int, label: str) -> None:
        palette, px = self._palette, self._px
        shape = _box(width, px(BUTTON), px(RADIUS), palette.card_button)
        self.canvas.create_image(x, y, anchor="nw", image=self._photo(shape))
        text = _fit(label, self._bold, width - px(16))
        self.canvas.create_text(x + width // 2, y + px(BUTTON) // 2, text=text,
                                font=self._bold, fill=palette.card_button_text)

    def _tooltip(self, item: int, text: str, x: int, above: int) -> None:
        # Shown above the picture, where the card always has room; below it could leave the card.
        if not text:
            return
        canvas, palette, px = self.canvas, self._palette, self._px

        def show(_event: object) -> None:
            canvas.delete("tooltip")
            label = canvas.create_text(x + px(8), above - px(10), anchor="sw", text=text, font=self._small,
                                       fill=palette.tooltip_text, tags="tooltip")
            left, top, right, bottom = canvas.bbox(label)
            box = canvas.create_rectangle(left - px(8), top - px(5), right + px(8), bottom + px(5),
                                          fill=palette.tooltip, outline="", tags="tooltip")
            canvas.tag_lower(box, label)

        canvas.tag_bind(item, "<Enter>", show)
        canvas.tag_bind(item, "<Leave>", lambda _event: canvas.delete("tooltip"))


def _box(width: int, height: int, radius: int, fill: str) -> Image.Image:
    shape = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shape).rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=fill)
    return shape


def _fit(text: str, font: tkfont.Font, width: int) -> str:
    if font.measure(text) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle].rstrip() + ELLIPSIS) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ELLIPSIS
