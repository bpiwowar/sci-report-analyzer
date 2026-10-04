"""The settings' unsaved changes: each screen's edits, shown in the left panel, saved or
discarded from there (all at once) or from the screen (its own).

A screen says what it edits while it is built (``track``): its state, compared with the
state saved (or loaded), and its save. Discarding a screen's changes builds it again (from
the saved settings).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

from ..i18n import _

# How often the screens are checked for changes (seconds).
CHECK_EVERY = 0.4


@dataclass
class Screen:
    """A settings screen (a tab) and its edits."""

    name: str
    label: str
    state: Callable[[], Any] | None = None  # None: nothing to save (saved at once, or none)
    save: Callable[[], bool | None] | None = None  # False: not saved (e.g. an invalid rule)
    saved: Any = None
    rebuild: Callable[[], None] | None = None
    cancel_buttons: list[ui.button] = field(default_factory=list)

    def dirty(self) -> bool:
        return self.state is not None and self.state() != self.saved

    def mark_saved(self) -> None:
        self.saved = copy.deepcopy(self.state()) if self.state else None

    def do_save(self) -> bool:
        if self.save is None or self.save() is False:
            return False
        self.mark_saved()
        return True

    def discard(self) -> None:
        if self.rebuild:
            self.rebuild()


# The screen being built (``track`` registers its edits on it).
_building: Screen | None = None


def track(state: Callable[[], Any], save: Callable[[], bool | None]) -> Callable[[], bool]:
    """Called while a screen is built: ``state`` gives its edits (a copy is kept to compare
    with), ``save`` saves them (returning False when it did not). Returns the save to use
    for the screen's own Save button."""
    screen = _building
    if screen is None:  # (built outside the settings page)
        return lambda: save() is not False
    screen.state, screen.save = state, save
    return screen.do_save


def cancel_button() -> None:
    """The screen's "Cancel changes" button: its edits discarded (no confirmation)."""
    screen = _building
    if screen is None:
        return
    b = (
        ui.button(_("Cancel changes"), icon="undo", on_click=screen.discard)
        .props("flat")
        .mark(f"settings-cancel-{screen.name}")
    )
    b.set_enabled(False)
    screen.cancel_buttons.append(b)


class Edits:
    """The screens of a settings page, and its left panel's view of their changes."""

    def __init__(self) -> None:
        self.screens: dict[str, Screen] = {}
        self.tabs: dict[str, ui.tab] = {}

    def screen(self, name: str, label: str, build: Callable[[], None]) -> None:
        """Build a screen (again when its changes are discarded)."""
        screen = self.screens[name] = Screen(name, label)

        @ui.refreshable
        def body() -> None:
            global _building
            screen.state = screen.save = None
            screen.cancel_buttons.clear()
            before, _building = _building, screen
            try:
                build()
            finally:
                _building = before
            screen.mark_saved()

        body()
        screen.rebuild = lambda: (body.refresh(), self.update())

    def dirty(self) -> list[Screen]:
        return [s for s in self.screens.values() if s.dirty()]

    def panel(self) -> None:
        """Below the screens' list: the unsaved ones, and their Save / Cancel."""
        with ui.column().classes("gap-1 px-2 mt-3 w-full"):
            self.summary = ui.label().classes("text-xs text-orange-9").mark("settings-unsaved")
            with ui.row().classes("gap-1 no-wrap"):
                self.save_button = (
                    ui.button(_("Save"), icon="save", on_click=self.save_all)
                    .props("dense")
                    .mark("settings-save-all")
                )
                self.cancel_button = (
                    ui.button(_("Cancel"), on_click=self.confirm_cancel)
                    .props("dense flat")
                    .mark("settings-cancel-all")
                )
        self.update()
        ui.timer(CHECK_EVERY, self.update)

    def update(self) -> None:
        """Mark the screens with unsaved changes (and enable what saves or discards them)."""
        dirty = self.dirty()
        names = {s.name for s in dirty}
        for name, tab in self.tabs.items():
            if name in names:
                tab.props("alert=orange")
            else:
                tab.props(remove="alert")
        for s in self.screens.values():
            for b in s.cancel_buttons:
                b.set_enabled(s.name in names)
        self.summary.text = (
            _("Unsaved: {screens}").format(screens=", ".join(_(s.label) for s in dirty))
            if dirty
            else ""
        )
        self.save_button.set_enabled(bool(dirty))
        self.cancel_button.set_enabled(bool(dirty))

    def save_all(self) -> None:
        for s in self.dirty():
            s.do_save()  # (each says it was saved, or why not)
        self.update()

    def confirm_cancel(self) -> None:
        dirty = self.dirty()
        if not dirty:
            return
        with ui.dialog() as dlg, ui.card():
            ui.label(_("Discard the unsaved changes?")).classes("font-medium")
            ui.label(", ".join(_(s.label) for s in dirty)).classes("text-sm")

            def ok() -> None:
                for s in dirty:
                    s.discard()
                dlg.close()
                ui.notify(_("Changes discarded"))

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Keep them"), on_click=dlg.close).props("flat")
                ui.button(_("Discard"), on_click=ok).props("color=negative").mark(
                    "settings-cancel-confirm"
                )
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()
