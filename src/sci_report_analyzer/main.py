"""Entry point: `sci-report-analyzer [--host H] [--port P] [--data-dir DIR]`."""

from __future__ import annotations

import argparse
import contextlib
import logging
from pathlib import Path

from nicegui import app, background_tasks, ui

from . import config, i18n, livereload, stop, venue_match
from .db import backup
from .db.session import init_engine
from .ranking import datasets
from .sources.base import close_client
from .sync import reset_running_states
from .ui import (
    documents_page,
    help_page,
    pdf_viewer,
    person,
    persons,
    report,
    settings,
    venues_page,
)

logger = logging.getLogger("sci_report_analyzer")


async def _shutdown() -> None:
    await close_client()


def setup() -> None:
    logger.info("Data directory: %s (from %s)", config.DATA_DIR, config.DATA_DIR_ORIGIN)
    if db := backup.db_file(init_engine()):
        backup.daily(db)
    reset_running_states()
    i18n.load_language()
    venue_match.refresh()
    persons.register()
    person.register()
    settings.register()
    help_page.register()
    venues_page.register()
    pdf_viewer.register()
    documents_page.register()
    report.register()
    app.on_startup(lambda: background_tasks.create(datasets.keep_datasets_fresh()))
    app.on_shutdown(_shutdown)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=f"database and ranking datasets (default: {config.DATA_DIR})",
    )
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument(
        "--live-reload",
        action="store_true",
        help="watch the source code; pages offer to restart when a new version is available",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    setup()
    if args.live_reload:
        app.on_startup(livereload.start)
    stop.install_interrupt_handler()
    # uvicorn re-raises the Ctrl-C once shut down
    with contextlib.suppress(KeyboardInterrupt):
        ui.run(
            title="SciReport Analyzer",
            host=args.host,
            port=args.port,
            reload=False,
            show=not args.no_browser,
            favicon="📊",
            timeout_graceful_shutdown=3,
        )


if __name__ in {"__main__", "__mp_main__"}:
    main()
