"""Stopping the server: Ctrl-C in the terminal (as TensorBoard: say so, then quit without a
traceback) or the quit button in the header."""

from __future__ import annotations

import json
import logging
import sys
from html import escape

from nicegui import app, ui
from nicegui.server import Server

from .i18n import N_, _

logger = logging.getLogger(__name__)

MESSAGE = N_("Shutting down SciReport Analyzer… (press Ctrl-C again to force)")


def install_interrupt_handler() -> None:
    """Announce the shutdown on the first Ctrl-C (uvicorn quits; a second one forces it)."""
    handle_exit = Server.handle_exit

    def announce(self, sig, frame) -> None:
        if not self.should_exit:
            print(f"\n{_(MESSAGE)}", file=sys.stderr, flush=True)
        handle_exit(self, sig, frame)

    Server.handle_exit = announce


def quit_button() -> None:
    """A header button that stops the server (after confirmation when a sync is running)."""
    from .sync import any_running

    def ask() -> None:
        busy = any_running()
        with ui.dialog() as dialog, ui.card():
            ui.label(_("Quit SciReport Analyzer?")).classes("text-lg")
            if busy:
                ui.label(_("A sync is running and would be interrupted."))
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dialog.close).props("flat")
                ui.button(_("Quit"), icon="power_settings_new", on_click=stop).mark(
                    "quit-confirm"
                ).props("color=negative")
        dialog.open()

    ui.button(icon="power_settings_new", on_click=ask).mark("quit").props(
        "flat round color=white dense"
    ).tooltip(_("Quit SciReport Analyzer"))


def stop() -> None:
    logger.info("Quit from the app")
    text = escape(_("SciReport Analyzer has stopped. You can close this tab."))
    ui.run_javascript(
        "document.body.innerHTML = "
        + json.dumps(f'<p style="font:16px sans-serif;margin:3em;text-align:center">{text}</p>')
    )
    ui.timer(0.3, app.shutdown, once=True)
