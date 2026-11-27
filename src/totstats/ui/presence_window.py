from __future__ import annotations

import contextlib
import time
import tkinter as tk
from dataclasses import fields, replace
from tkinter import ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageTk

from totstats import APP_NAME
from totstats.presence.catalog import Catalog, TrialInfo
from totstats.presence.render import PLACEHOLDERS, render
from totstats.presence.state import Activity, GameState, InvasionRole
from totstats.shared import paths
from totstats.shared.i18n import Translator
from totstats.shared.settings import PresenceOptions, clean_template
from totstats.ui import graphics, theme, window_icon
from totstats.ui.discord_card import CardText, DiscordCard

if TYPE_CHECKING:
    from totstats.app import App

SAMPLE_CATALOG = Catalog(
    trials={
        "FP_Trial": TrialInfo("Grind the Bad Apples", "photo_FP_Trial", "Fun Park"),
        "CHA_MT01": TrialInfo("Escape the Courthouse", "photo_CHA_MT01", "Courthouse"),
        "PSA_MT01": TrialInfo("Teach the Police Officer", "photo_PSA_MT01", "Police Station"),
    }
)

EXAMPLES = (
    (
        "customize.example_trial",
        GameState(
            activity=Activity.TRIAL,
            program_id="programCoreFP",
            trial_id="FP_Trial",
            difficulty=2,
            party_size=3,
            trial_started_at=1.0,
        ),
    ),
    (
        "customize.example_escalation",
        GameState(
            activity=Activity.CHAIN,
            program_id="programCHAIN",
            trial_id="CHA_MT01",
            difficulty=4,
            chain_step=3,
            party_size=2,
            trial_started_at=1.0,
        ),
    ),
    (
        "customize.example_invasion",
        GameState(
            activity=Activity.TRIAL,
            program_id="programINVASION",
            trial_id="PSA_MT01",
            difficulty=3,
            invasion_role=InvasionRole.IMPOSTER,
            trial_started_at=1.0,
        ),
    ),
    ("customize.example_sleep_room", GameState(activity=Activity.SLEEP_ROOM, party_size=2)),
)

PREVIEW_ELAPSED = 754
TOGGLES = tuple(item.name for item in fields(PresenceOptions) if item.name.startswith("show_"))
PREVIEW_PROFILE = "preview"
# Sizes in pixels at 100 % display scale.
LOGO = 40
ICON = 18
WRAP = 360


class PresenceWindow:
    def __init__(self, root: tk.Tk, app: App) -> None:
        self._root = root
        self._app = app
        self._window: tk.Toplevel | None = None
        self._tr: Translator | None = None
        self._toggles: dict[str, tk.BooleanVar] = {}
        self._details: tk.StringVar | None = None
        self._state: tk.StringVar | None = None
        self._example: tk.StringVar | None = None
        self._card: DiscordCard | None = None
        self._target: ttk.Entry | None = None
        self._images: list[ImageTk.PhotoImage] = []
        self._logo: Image.Image | None = None

    def open(self) -> None:
        if self._window is not None:
            try:
                self._window.deiconify()
                self._window.lift()
                self._window.focus_force()
                return
            except tk.TclError:
                self._window = None
        try:
            self._build()
        except tk.TclError:
            self.close()
        except Exception:
            # Never leave a half-built, hidden window behind for the next open() to show.
            self.close()
            raise

    @property
    def is_open(self) -> bool:
        return self._window is not None

    def close(self) -> None:
        window, self._window = self._window, None
        self._card = None
        self._target = None
        self._images = []
        if window is not None:
            with contextlib.suppress(tk.TclError):
                window.destroy()

    def _build(self) -> None:
        tr = self._tr = self._app.tr
        palette = theme.apply(self._root)
        self._app.artwork.retry_failed()
        window = tk.Toplevel(self._root)
        # Hidden while it is built, so the title bar is themed before it first appears.
        window.withdraw()
        self._window = window
        self._details = tk.StringVar(master=window)
        self._state = tk.StringVar(master=window)
        self._example = tk.StringVar(master=window, value=EXAMPLES[0][0])
        window.title(f"{APP_NAME} - {tr.t('customize.title')}")
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.resizable(False, False)
        window_icon.apply(window)

        outer = ttk.Frame(window)
        outer.pack(fill=tk.BOTH, expand=True)
        self._build_header(outer, tr)

        body = ttk.Frame(outer, padding=(20, 8, 20, 16))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, uniform="column")
        body.columnconfigure(1, weight=1, uniform="column")
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self._build_toggles(left, tr, palette)
        self._build_preview(right, tr, palette)
        self._build_texts(right, tr, palette)
        self._build_footer(outer, tr, palette)

        self._load(self._app.settings.presence)
        for var in (*self._toggles.values(), self._details, self._state, self._example):
            var.trace_add("write", self._on_change)
        self._update_preview()
        window.bind("<Escape>", lambda _event: self.close())
        theme.title_bar(window, palette)
        window.deiconify()
        window.focus_force()

    def _px(self, value: float) -> int:
        return round(value * self._root.winfo_fpixels("1i") / 96.0)

    def _image(self, picture: Image.Image) -> ImageTk.PhotoImage:
        photo = ImageTk.PhotoImage(picture, master=self._window)
        self._images.append(photo)
        return photo

    def _logo_picture(self) -> Image.Image:
        if self._logo is None:
            try:
                with Image.open(paths.logo_path()) as logo:
                    self._logo = logo.convert("RGBA")
            except OSError:
                self._logo = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        return self._logo

    def _build_header(self, parent: ttk.Frame, tr: Translator) -> None:
        header = ttk.Frame(parent, padding=(20, 16, 20, 0))
        header.pack(fill=tk.X)
        logo = self._image(graphics.fit(self._logo_picture(), self._px(LOGO)))
        ttk.Label(header, image=logo).pack(side=tk.LEFT, padx=(0, 12))
        titles = ttk.Frame(header)
        titles.pack(side=tk.LEFT, fill=tk.X)
        ttk.Label(titles, text=tr.t("customize.title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=tr.t("customize.subtitle"), style="Muted.TLabel").pack(anchor="w")

    def _build_toggles(self, parent: ttk.Frame, tr: Translator, palette: theme.Palette) -> None:
        ttk.Label(parent, text=tr.t("customize.show"), style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        card = ttk.Frame(parent, style="Card.TFrame", padding=(6, 4))
        card.pack(fill=tk.X)
        card.columnconfigure(1, weight=1)
        self._toggles = {}
        for index, name in enumerate(TOGGLES):
            row = index * 2
            if index:
                divider = tk.Frame(card, height=1, bg=palette.line)
                divider.grid(row=row - 1, column=0, columnspan=3, sticky="ew")
            var = tk.BooleanVar(master=self._window)
            self._toggles[name] = var
            icon = self._image(graphics.icon(name, self._px(ICON), palette.muted))
            icon_label = ttk.Label(card, image=icon, style="Card.TLabel")
            icon_label.grid(row=row, column=0, padx=(8, 12), pady=10)
            text = ttk.Label(card, text=tr.t(f"customize.{name}"), style="Card.TLabel")
            text.grid(row=row, column=1, sticky="w")
            switch = ttk.Checkbutton(card, variable=var, style="Card.Switch.TCheckbutton")
            switch.grid(row=row, column=2, padx=(8, 4))
            # The whole row toggles, like a Windows settings row.
            for widget in (icon_label, text):
                widget.bind("<Button-1>", lambda _event, flag=var: flag.set(not flag.get()))

    def _build_preview(self, parent: ttk.Frame, tr: Translator, palette: theme.Palette) -> None:
        heading = ttk.Frame(parent)
        heading.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(heading, text=tr.t("customize.preview"), style="Section.TLabel").pack(side=tk.LEFT)
        examples = ttk.Frame(heading)
        examples.pack(side=tk.RIGHT)
        for key, _sample in EXAMPLES:
            ttk.Radiobutton(
                examples, text=tr.t(key), value=key, variable=self._example, style="Toggle.TButton"
            ).pack(side=tk.LEFT, padx=(2, 0))

        text = CardText(
            tr.t("customize.card_you"), tr.t("customize.card_online"), tr.t("customize.card_playing")
        )
        self._card = DiscordCard(parent, palette, self._app.artwork, self._logo_picture(), text)
        self._card.canvas.pack(anchor="w")

    def _build_texts(self, parent: ttk.Frame, tr: Translator, palette: theme.Palette) -> None:
        ttk.Label(parent, text=tr.t("customize.texts"), style="Section.TLabel").pack(anchor="w", pady=(16, 6))
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
        card.pack(fill=tk.X)
        card.columnconfigure(1, weight=1)
        entries = []
        inputs = (("customize.details", self._details), ("customize.state", self._state))
        for row, (key, var) in enumerate(inputs):
            label = ttk.Label(card, text=tr.t(key), style="Card.TLabel")
            label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            entry = ttk.Entry(card, textvariable=var, style="Card.TEntry")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            entry.bind("<FocusIn>", lambda _event, target=entry: self._set_target(target))
            entries.append(entry)
        self._target = entries[0]

        # Wrapped by hand into two rows: Tk has no flowing layout.
        per_row = (len(PLACEHOLDERS) + 1) // 2
        for start in range(0, len(PLACEHOLDERS), per_row):
            chips = tk.Frame(card, bg=palette.surface)
            gap = 6 if start else 10
            chips.grid(row=2 + start // per_row, column=0, columnspan=2, sticky="w", pady=(gap, 0))
            for name in PLACEHOLDERS[start:start + per_row]:
                ttk.Button(
                    chips, text=f"{{{name}}}", style="Placeholder.TButton", takefocus=False,
                    command=lambda placeholder=name: self._insert(placeholder),
                ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(
            card, text=tr.t("customize.placeholders_hint"), style="Hint.Card.TLabel",
            wraplength=self._px(WRAP), justify=tk.LEFT,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_footer(self, parent: ttk.Frame, tr: Translator, palette: theme.Palette) -> None:
        tk.Frame(parent, height=1, bg=palette.line).pack(fill=tk.X)
        footer = ttk.Frame(parent, padding=(20, 12))
        footer.pack(fill=tk.X)
        reset = self._image(graphics.icon("reset", self._px(14), palette.muted))
        ttk.Button(footer, text=tr.t("customize.reset"), image=reset, compound=tk.LEFT,
                   command=self._reset).pack(side=tk.LEFT)
        ttk.Button(footer, text=tr.t("customize.save"), style="Accent.TButton", width=12,
                   command=self._save).pack(side=tk.RIGHT)
        ttk.Button(footer, text=tr.t("customize.cancel"), width=12,
                   command=self.close).pack(side=tk.RIGHT, padx=(0, 8))

    def _set_target(self, entry: ttk.Entry) -> None:
        self._target = entry

    def _insert(self, name: str) -> None:
        entry = self._target
        if entry is None:
            return
        with contextlib.suppress(tk.TclError):
            # Like typing: a selection is replaced, not pushed aside.
            if entry.selection_present():
                entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            entry.insert(tk.INSERT, f"{{{name}}}")
            entry.focus_set()

    def _load(self, options: PresenceOptions) -> None:
        for name, var in self._toggles.items():
            var.set(getattr(options, name))
        self._details.set(options.details_template)
        self._state.set(options.state_template)

    def _options(self) -> PresenceOptions:
        return PresenceOptions(
            **{name: var.get() for name, var in self._toggles.items()},
            details_template=clean_template(self._details.get()),
            state_template=clean_template(self._state.get()),
        )

    def _on_change(self, *_args: object) -> None:
        try:
            self._update_preview()
        except tk.TclError:
            self.close()

    def _update_preview(self) -> None:
        if self._window is None or self._card is None:
            return
        samples = dict(EXAMPLES)
        state = samples.get(self._example.get(), EXAMPLES[0][1])
        now = time.time()
        if state.trial_started_at:
            state = replace(state, trial_started_at=now - PREVIEW_ELAPSED)
        payload = render(
            state, SAMPLE_CATALOG, PREVIEW_PROFILE, now - PREVIEW_ELAPSED, self._options(), self._tr
        )
        self._card.show(payload, now)

    def _reset(self) -> None:
        self._load(PresenceOptions())

    def _save(self) -> None:
        options = self._options()
        self.close()
        self._app.apply_presence_options(options)
