"""``--live-reload``: watch the sources and offer a restart when the code changes.

A running Python process cannot safely swap in new code, so applying an update means
restarting the server; open pages then reconnect and reload on their own (NiceGUI reloads
a page whose client is unknown to the new server).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from nicegui import background_tasks, ui

from .i18n import _, ngettext

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).parent
enabled = False
changed: set[str] = set()


def start() -> None:
    """Start watching the package sources (call once, at app startup)."""
    global enabled
    enabled = True
    background_tasks.create(_watch(), name="live-reload watcher")


async def _watch() -> None:
    from watchfiles import awatch

    def only_code(_change, path: str) -> bool:
        return path.endswith((".py", ".mako")) and "__pycache__" not in path

    async for changes in awatch(PACKAGE_DIR, watch_filter=only_code):
        for _change, path in changes:
            changed.add(str(Path(path).relative_to(PACKAGE_DIR)))
        logger.info("Code changed: %s", ", ".join(sorted(changed)))


def restart() -> None:
    """Re-execute the server process with the same arguments."""
    logger.info("Restarting to apply the new version")
    argv = [sys.executable, "-m", "sci_report_analyzer.main", *sys.argv[1:]]
    asyncio.get_running_loop().call_later(0.5, os.execv, sys.executable, argv)


def banner() -> None:
    """In the menu bar (of every page, see theme._menu), on its colour: a new version is
    available, to restart (its text in a tooltip, the bar's room kept)."""
    if not enabled:
        return
    from .sync import any_running

    state = {"dismissed": 0, "confirm": False}
    box = ui.row().classes("items-center no-wrap gap-1 shrink-0 text-sm")
    box.set_visibility(False)
    box.mark("live-reload")

    def apply() -> None:
        busy = any_running()
        if busy and not state["confirm"]:
            state["confirm"] = True
            button.text = _("Restart anyway")
            label.text = _("A sync is running and would be interrupted.")
            return
        ui.notify(_("Restarting… the page will reload by itself"), type="info")
        restart()

    def later() -> None:
        state["dismissed"] = len(changed)
        box.set_visibility(False)

    with box:
        with ui.icon("system_update"):
            label = ui.tooltip()
        button = ui.button(_("Restart & reload"), icon="restart_alt", on_click=apply)
        button.props("dense outline color=white")
        ui.button(icon="close", on_click=later).props("dense flat round color=white").tooltip(
            _("Later")
        )

    def check() -> None:
        # Shown again if more files change after "Later".
        if changed and len(changed) > state["dismissed"] and not box.visible:
            label.text = ngettext(
                "New version available ({n} file changed)",
                "New version available ({n} files changed)",
                len(changed),
            ).format(n=len(changed))
            box.set_visibility(True)

    ui.timer(1.0, check)
