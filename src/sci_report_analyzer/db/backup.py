"""Backups of the database (not of the stored PDFs), in ``backups/`` next to the DB file.

Two kinds, both consistent copies made with SQLite's online backup API:

- **daily** (``daily-YYYY-MM-DD.sqlite``): at most one a day, made at startup; the last
  ``DAILY_KEEP`` are kept.
- **pre-migration** (``pre-migration-<time>-<from>-to-<to>.sqlite``): made before alembic
  upgrades an existing database (never a new one). A ``<backup>.pending`` marker is written
  with it and removed once the upgrade succeeded, so a marker left behind means that
  migration failed: its backup is never removed nor overwritten (each attempt gets its own
  file), and a warning says where it is. Only successful ones are pruned, beyond
  ``MIGRATION_KEEP``.

To restore one: stop the app, copy the backup over the database file (removing its
``-wal`` / ``-shm`` files if any).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

logger = logging.getLogger(__name__)

DAILY_KEEP = 7
MIGRATION_KEEP = 10
PENDING = ".pending"


def db_file(engine: Engine) -> Path | None:
    """The engine's SQLite file (None: another database, or in memory)."""
    db = engine.url.database
    if engine.dialect.name != "sqlite" or not db or db == ":memory:":
        return None
    return Path(db)


def backup_dir(db: Path) -> Path:
    return db.parent / "backups"


def snapshot(src: Path, dest: Path) -> None:
    """Consistent copy of ``src`` into ``dest`` (written aside, then renamed: never half done)."""
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    s = sqlite3.connect(src)
    try:
        d = sqlite3.connect(part)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()
    part.replace(dest)


def failed_migrations(db: Path) -> list[Path]:
    """Backups of migrations that did not finish (their marker is still there)."""
    d = backup_dir(db)
    if not d.is_dir():
        return []
    return sorted(m.with_name(m.name.removesuffix(PENDING)) for m in d.glob(f"*{PENDING}"))


def before_migration(db: Path, current: str, head: str) -> Path:
    """Back up ``db`` before upgrading it from ``current`` to ``head``; returns the backup
    (its marker is to be removed with ``migration_done``)."""
    for b in failed_migrations(db):
        logger.warning("An earlier migration failed; the database before it is kept in %s", b)
    d = backup_dir(db)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = d / f"pre-migration-{stamp}-{current}-to-{head}.sqlite"
    n = 1
    while dest.exists() or _marker(dest).exists():
        dest = d / f"pre-migration-{stamp}-{n}-{current}-to-{head}.sqlite"
        n += 1
    _marker(dest).write_text(f"migrating {current} -> {head}\n")
    try:
        snapshot(db, dest)
    except BaseException:
        _marker(dest).unlink()
        raise
    logger.info("Database backed up before migrating (%s -> %s): %s", current, head, dest)
    return dest


def migration_done(backup: Path) -> None:
    """The upgrade succeeded: drop the marker, prune the older successful backups."""
    _marker(backup).unlink(missing_ok=True)
    done = [
        p for p in sorted(backup.parent.glob("pre-migration-*.sqlite")) if not _marker(p).exists()
    ]
    for p in done[:-MIGRATION_KEEP]:
        p.unlink()


def daily(db: Path, today: datetime | None = None) -> Path | None:
    """Today's backup, unless made already (or no database yet); keeps the last ``DAILY_KEEP``."""
    if not db.exists():
        return None
    d = backup_dir(db)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"daily-{(today or datetime.now()):%Y-%m-%d}.sqlite"
    if dest.exists():
        return None
    snapshot(db, dest)
    logger.info("Daily database backup: %s", dest)
    for p in sorted(d.glob("daily-*.sqlite"))[:-DAILY_KEEP]:
        p.unlink()
    return dest


def _marker(backup: Path) -> Path:
    return backup.with_name(backup.name + PENDING)
