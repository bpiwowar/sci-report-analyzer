"""Choosing a colour: a palette (the Material hues, light to dark) for a quick pick, or any
other colour (a hex code or the spectrum) confirmed with OK."""

from __future__ import annotations

import re
from collections.abc import Callable

from nicegui import ui

from ..i18n import _

# Each hue in its shades 200, 400, 500, 700, 900 (one column per hue).
PALETTE = [
    ["#ef9a9a", "#ef5350", "#f44336", "#d32f2f", "#b71c1c"],  # red
    ["#f48fb1", "#ec407a", "#e91e63", "#c2185b", "#880e4f"],  # pink
    ["#ce93d8", "#ab47bc", "#9c27b0", "#7b1fa2", "#4a148c"],  # purple
    ["#b39ddb", "#7e57c2", "#673ab7", "#512da8", "#311b92"],  # deep purple
    ["#9fa8da", "#5c6bc0", "#3f51b5", "#303f9f", "#1a237e"],  # indigo
    ["#90caf9", "#42a5f5", "#2196f3", "#1976d2", "#0d47a1"],  # blue
    ["#81d4fa", "#29b6f6", "#03a9f4", "#0288d1", "#01579b"],  # light blue
    ["#80deea", "#26c6da", "#00bcd4", "#0097a7", "#006064"],  # cyan
    ["#80cbc4", "#26a69a", "#009688", "#00796b", "#004d40"],  # teal
    ["#a5d6a7", "#66bb6a", "#4caf50", "#388e3c", "#1b5e20"],  # green
    ["#c5e1a5", "#9ccc65", "#8bc34a", "#689f38", "#33691e"],  # light green
    ["#e6ee9c", "#d4e157", "#cddc39", "#afb42b", "#827717"],  # lime
    ["#fff59d", "#ffee58", "#ffeb3b", "#fbc02d", "#f57f17"],  # yellow
    ["#ffe082", "#ffca28", "#ffc107", "#ffa000", "#ff6f00"],  # amber
    ["#ffcc80", "#ffa726", "#ff9800", "#f57c00", "#e65100"],  # orange
    ["#ffab91", "#ff7043", "#ff5722", "#e64a19", "#bf360c"],  # deep orange
    ["#bcaaa4", "#8d6e63", "#795548", "#5d4037", "#3e2723"],  # brown
    ["#eeeeee", "#bdbdbd", "#9e9e9e", "#616161", "#212121"],  # grey
]
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# The swatches are one element: a click on one emits its colour.
_PICKED = "(e) => { const c = e.target.dataset.c; if (c) emit(c); }"


def _swatches() -> str:
    cells = [
        f'<div data-c="{hue[shade]}" title="{hue[shade]}" class="colour-swatch"'
        f' style="background:{hue[shade]}"></div>'
        for shade in range(len(PALETTE[0]))
        for hue in PALETTE
    ]
    return (
        f'<div style="display:grid;grid-template-columns:repeat({len(PALETTE)},18px);gap:3px">'
        + "".join(cells)
        + "</div>"
    )


ui.add_css(
    ".colour-swatch{width:18px;height:18px;border-radius:3px;cursor:pointer;"
    "box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}"
    ".colour-swatch:hover{transform:scale(1.25);box-shadow:0 0 0 2px #fff,0 0 0 3px #555}",
    shared=True,
)


class ColourMenu(ui.menu):
    """A menu (opened by its parent) choosing a colour: ``on_pick`` gets its hex code."""

    def __init__(self, on_pick: Callable[[str], None], value: str | None = None) -> None:
        super().__init__()
        self._on_pick = on_pick
        with self, ui.column().classes("gap-2 p-2"):
            self.swatches = ui.html(_swatches(), sanitize=False)
            self.swatches.on("click", lambda e: self._pick(e.args), js_handler=_PICKED)
            # OK before the spectrum: always within the window.
            with ui.row().classes("items-center gap-2 no-wrap w-full"):
                self.preview = ui.element("div").classes("colour-swatch shrink-0")
                self.code = (
                    ui.input(_("Other colour"), placeholder="#rrggbb")
                    .props("dense outlined")
                    .classes("grow")
                    .on("keydown.enter", self._confirm)
                )
                self.code.on_value_change(lambda e: self._show(e.value))
                ui.button(_("OK"), on_click=self._confirm).props("dense unelevated")
            with ui.expansion(_("Spectrum")).props("dense").classes("w-full"):
                self.spectrum = (
                    ui.element("q-color")
                    .props("no-header no-footer default-view=spectrum")
                    .style("width:100%;max-width:360px")
                    .on("change", lambda e: self.code.set_value(e.args))
                )
        self.set_color(value)

    def set_color(self, colour: str | None) -> ColourMenu:
        self.code.value = colour or ""
        self._show(colour)
        return self

    def _show(self, colour: str | None) -> None:
        ok = bool(colour and _HEX.match(colour))
        self.preview.style(f"background:{colour if ok else 'transparent'}")
        if ok:
            self.spectrum.props["model-value"] = colour
            self.spectrum.update()

    def _confirm(self) -> None:
        colour = (self.code.value or "").strip()
        if not _HEX.match(colour):
            ui.notify(_("A colour is a hex code, e.g. #2196f3"), type="warning")
            return
        self._pick(colour)

    def _pick(self, colour: str) -> None:
        self.close()
        self.set_color(colour)
        self._on_pick(colour)


class ColourInput(ui.color_input):
    """``ui.color_input`` with the palette menu instead of Quasar's picker."""

    def __init__(self, label: str | None = None, *, value: str = "", on_change=None) -> None:
        super().__init__(label, value=value, on_change=on_change, preview=True)
        self.picker.delete()
        with self.button:
            self.picker = ColourMenu(self.set_value, value)

    def open_picker(self) -> ColourInput:
        self.picker.set_color(self.value)
        self.picker.open()
        return self
