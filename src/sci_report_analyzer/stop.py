"""Stopping the server: Ctrl-C in the terminal (as TensorBoard: say so, then quit without a
traceback), the quit button in the header, or webapp-manager (the app served at
http://<name>.localhost, started on demand and stopped when idle: see its PROTOCOL.md)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from html import escape

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import Client, app, ui
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


def quit_button(compact: bool = False) -> None:
    """A header button that stops the server (after confirmation when a sync is running);
    ``compact``: smaller."""
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
        "flat round color=white dense" + (" size=sm" if compact else "")
    ).tooltip(_("Quit SciReport Analyzer"))


def _stopped_page(text: str) -> str:
    """JavaScript replacing the page by ``text``."""
    html = f'<p style="font:16px sans-serif;margin:3em;text-align:center">{escape(text)}</p>'
    return "document.body.innerHTML = " + json.dumps(html)


def stop() -> None:
    logger.info("Quit from the app")
    ui.run_javascript(_stopped_page(_("SciReport Analyzer has stopped. You can close this tab.")))
    ui.timer(0.3, app.shutdown, once=True)


# ---- webapp-manager --------------------------------------------------------------------------

STATUS_PATH = "/.well-known/webapps/status"
SHUTDOWN_PATH = "/.well-known/webapps/shutdown"
STOPPED_BY_MANAGER = N_("SciReport Analyzer was stopped. Reload the page to start it again.")


def webapps_status() -> dict:
    """A running sync keeps the app up (no idle stop)."""
    from .sync import any_running

    if any_running():
        return {"busy": True, "reason": "sync running", "retry_after": 60}
    return {"busy": False}


async def webapps_shutdown(request: Request) -> JSONResponse:
    """Stop asked by the manager: refused when idle and a sync runs; otherwise the open tabs
    say so (and stop reconnecting, which would start the app again), then the server quits."""
    try:
        reason = (await request.json()).get("reason", "user")
    except ValueError:
        reason = "user"
    if reason == "idle" and webapps_status()["busy"]:
        return JSONResponse({"busy": True}, status_code=409)
    logger.info("Stopped by webapp-manager (%s)", reason)
    script = _stopped_page(_(STOPPED_BY_MANAGER)) + "; window.socket?.disconnect()"
    for client in list(Client.instances.values()):
        if client.has_socket_connection:
            client.run_javascript(script)
    asyncio.get_running_loop().call_later(0.3, app.shutdown)
    return JSONResponse({"accepted": True}, status_code=202)


def install_webapps_protocol() -> None:
    app.add_api_route(STATUS_PATH, webapps_status, methods=["GET"])
    app.add_api_route(SHUTDOWN_PATH, webapps_shutdown, methods=["POST"])
