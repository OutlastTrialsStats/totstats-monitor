from __future__ import annotations

import contextlib
import tkinter as tk

from totstats import APP_NAME
from totstats.shared import paths
from totstats.shared.applog import AppLog

BACKGROUND = "#1e1e1e"
FOREGROUND = "#cccccc"


class ConsoleWindow:
    def __init__(self, root: tk.Tk, log: AppLog) -> None:
        self._root = root
        self._log = log
        self._window: tk.Toplevel | None = None
        self._text: tk.Text | None = None
        self._last_seq = 0

    def open(self) -> None:
        if self._window is not None:
            try:
                self._window.deiconify()
                self._window.lift()
                self._window.focus_force()
                return
            except tk.TclError:
                self._window = None

        window = tk.Toplevel(self._root)
        window.title(f"{APP_NAME} - Console")
        window.geometry("700x400")
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.configure(bg=BACKGROUND)
        with contextlib.suppress(tk.TclError):
            window.iconbitmap(str(paths.icon_path()))

        text = tk.Text(
            window,
            bg=BACKGROUND,
            fg=FOREGROUND,
            insertbackground=FOREGROUND,
            font=("Consolas", 10),
            state=tk.DISABLED,
            wrap=tk.WORD,
            borderwidth=0,
        )
        scrollbar = tk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)

        self._window = window
        self._text = text
        self._last_seq = 0
        self.pump()

    def close(self) -> None:
        window, self._window, self._text = self._window, None, None
        if window is not None:
            with contextlib.suppress(tk.TclError):
                window.destroy()

    def pump(self) -> None:
        if self._text is None:
            return
        records = self._log.since(self._last_seq)
        if not records:
            return
        try:
            self._text.configure(state=tk.NORMAL)
            for record in records:
                self._text.insert(tk.END, record.format() + "\n")
            self._text.see(tk.END)
            self._text.configure(state=tk.DISABLED)
        except tk.TclError:
            self._window = None
            self._text = None
        self._last_seq = records[-1].seq
