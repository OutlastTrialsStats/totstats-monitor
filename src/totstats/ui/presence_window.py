from __future__ import annotations

import contextlib
import time
import tkinter as tk
from dataclasses import fields, replace
from tkinter import ttk
from typing import TYPE_CHECKING

from totstats import APP_NAME
from totstats.presence.catalog import Catalog, TrialInfo, image
from totstats.presence.render import PLACEHOLDERS, Payload, render
from totstats.presence.state import Activity, GameState, InvasionRole
from totstats.shared.i18n import Translator
from totstats.shared.settings import PresenceOptions, clean_template
from totstats.ui import window_icon

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

PREVIEW_ROWS = ("details", "state", "large_text", "small_text", "timer", "button")
PREVIEW_ELAPSED = 754
TOGGLES = tuple(item.name for item in fields(PresenceOptions) if item.name.startswith("show_"))
PREVIEW_PROFILE = "preview"


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
        self._examples: dict[str, GameState] = {}
        self._preview: dict[str, tk.StringVar] = {}

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

    @property
    def is_open(self) -> bool:
        return self._window is not None

    def close(self) -> None:
        window, self._window = self._window, None
        self._preview = {}
        if window is not None:
            with contextlib.suppress(tk.TclError):
                window.destroy()

    def _build(self) -> None:
        tr = self._tr = self._app.tr
        window = tk.Toplevel(self._root)
        self._window = window
        self._details = tk.StringVar(master=window)
        self._state = tk.StringVar(master=window)
        self._example = tk.StringVar(master=window)
        window.title(f"{APP_NAME} - {tr.t('customize.title')}")
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.minsize(560, 0)
        window_icon.apply(window)

        body = ttk.Frame(window, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        show = ttk.LabelFrame(body, text=tr.t("customize.show"), padding=8)
        show.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._toggles = {}
        for row, name in enumerate(TOGGLES):
            var = tk.BooleanVar(master=window)
            self._toggles[name] = var
            ttk.Checkbutton(show, text=tr.t(f"customize.{name}"), variable=var).grid(
                row=row, column=0, sticky="w", pady=1
            )

        self._build_preview(body, tr)
        self._build_texts(body, tr)

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text=tr.t("customize.reset"), command=self._reset).pack(side=tk.LEFT)
        ttk.Button(buttons, text=tr.t("customize.cancel"), command=self.close).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text=tr.t("customize.save"), command=self._save).pack(side=tk.LEFT)

        self._load(self._app.settings.presence)
        for var in (*self._toggles.values(), self._details, self._state, self._example):
            var.trace_add("write", self._on_change)
        self._update_preview()
        window.bind("<Escape>", lambda _event: self.close())
        window.focus_force()

    def _build_texts(self, body: ttk.Frame, tr: Translator) -> None:
        texts = ttk.LabelFrame(body, text=tr.t("customize.texts"), padding=8)
        texts.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        texts.columnconfigure(1, weight=1)
        for row, (key, var) in enumerate(
            (("customize.details", self._details), ("customize.state", self._state))
        ):
            ttk.Label(texts, text=tr.t(key)).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(texts, textvariable=var).grid(row=row, column=1, sticky="ew", pady=2)
        placeholders = ", ".join(f"{{{name}}}" for name in PLACEHOLDERS)
        hint = ttk.Label(
            texts, text=tr.t("customize.hint", placeholders=placeholders), wraplength=520, justify=tk.LEFT
        )
        hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_preview(self, body: ttk.Frame, tr: Translator) -> None:
        preview = ttk.LabelFrame(body, text=tr.t("customize.preview"), padding=8)
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        preview.columnconfigure(1, weight=1)

        self._examples = {tr.t(key): sample for key, sample in EXAMPLES}
        names = list(self._examples)
        selector = ttk.Combobox(preview, textvariable=self._example, values=names, state="readonly")
        selector.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._example.set(names[0])

        # Laid out like the Discord card itself, so it needs no labels of its own.
        self._preview = {}
        for row, name in enumerate(PREVIEW_ROWS, start=1):
            var = tk.StringVar(master=preview)
            self._preview[name] = var
            label = ttk.Label(preview, textvariable=var, wraplength=280, justify=tk.LEFT)
            if name == "details":
                with contextlib.suppress(tk.TclError):
                    label.configure(font=("TkDefaultFont", 10, "bold"))
            label.grid(row=row, column=0, columnspan=2, sticky="w", pady=1)

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
        if self._window is None or not self._preview:
            return
        state = self._examples.get(self._example.get(), EXAMPLES[0][1])
        now = time.time()
        if state.trial_started_at:
            state = replace(state, trial_started_at=now - PREVIEW_ELAPSED)
        payload = render(
            state, SAMPLE_CATALOG, PREVIEW_PROFILE, now - PREVIEW_ELAPSED, self._options(), self._tr
        )
        for name, value in _card(payload, now).items():
            self._preview[name].set(value)

    def _reset(self) -> None:
        self._load(PresenceOptions())

    def _save(self) -> None:
        options = self._options()
        self.close()
        self._app.apply_presence_options(options)


def _card(payload: Payload | None, now: float) -> dict[str, str]:
    lines = dict.fromkeys(PREVIEW_ROWS, "")
    if payload is None:
        return lines
    state = payload.state.strip()
    if payload.party_size:
        state = f"{state} ({payload.party_size[0]}/{payload.party_size[1]})"
    lines["details"] = payload.details.strip()
    lines["state"] = state
    # A filled square for trial artwork, an empty one for the default logo.
    artwork = "▣" if payload.large_image != image(None) else "□"
    lines["large_text"] = f"{artwork}  {payload.large_text}"
    if payload.small_text:
        lines["small_text"] = f"●  {payload.small_text}"
    if payload.start:
        elapsed = max(0, int(now) - payload.start)
        lines["timer"] = f"{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}"
    if payload.buttons:
        lines["button"] = f"[ {payload.buttons[0][0]} ]"
    return lines
