"""Changing the data directory: the new location is used from the next start."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import config
from .db import backup
from .db import session as db_session


def current_db() -> Path:
    """The database file in use (SCI_REPORT_ANALYZER_DB_URL can point outside the data
    directory)."""
    engine = db_session._engine
    if engine is not None and engine.url.database:
        return Path(engine.url.database)
    return config.DB_PATH


def has_data(path: Path) -> bool:
    return (path / config.DB_PATH.name).exists()


def copy_data(dest: Path) -> None:
    """Copy the database (consistent snapshot, the app keeps running) and the stored
    PDFs."""
    dest.mkdir(parents=True, exist_ok=True)
    backup.snapshot(current_db(), dest / config.DB_PATH.name)
    if (pdf_dir := config.DATA_DIR / "pdfs").is_dir():
        shutil.copytree(pdf_dir, dest / pdf_dir.name, dirs_exist_ok=True)


def use_data_dir(dest: Path, *, copy: bool) -> None:
    """Remember ``dest`` as the data directory, optionally copying the current data there.

    Refuses to overwrite a directory that already holds a database.
    """
    dest = dest.expanduser().resolve()
    if dest == config.DATA_DIR:
        return
    if copy:
        if has_data(dest):
            raise FileExistsError(f"{dest} already holds a database")
        copy_data(dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    config.save_data_dir(None if dest == config.DEFAULT_DATA_DIR.resolve() else dest)
