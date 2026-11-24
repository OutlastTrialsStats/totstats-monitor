from __future__ import annotations

import contextlib
import tkinter as tk

from totstats.shared import paths


def apply(window: tk.Toplevel) -> None:
    # Windows takes the multi-size .ico; Tk elsewhere cannot read .ico, so it gets the PNG.
    with contextlib.suppress(tk.TclError):
        if paths.is_windows():
            window.iconbitmap(str(paths.icon_path()))
        else:
            image = tk.PhotoImage(master=window, file=str(paths.logo_path()))
            window.iconphoto(False, image)
            window._icon_image = image  # keep a reference, Tk drops images nobody holds
