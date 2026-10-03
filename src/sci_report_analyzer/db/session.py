"""Engine / session management and migrations."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .. import config
from . import backup

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _regexp(pattern: str | None, text: str | None) -> bool:
    """SQLite's ``text REGEXP pattern`` (Python syntax; inline flags such as ``(?i)``)."""
    if pattern is None or text is None:
        return False
    rx = _compile(pattern)
    return bool(rx and rx.search(text))


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    dbapi_conn.create_function("regexp", 2, _regexp, deterministic=True)
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def init_engine(url: str | None = None, *, migrate: bool = True) -> Engine:
    """Create the global engine and bring the schema up to date."""
    global _engine, _factory
    url = url or config.db_url()
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"timeout": 30} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args)
    if _engine.dialect.name == "sqlite":
        event.listen(_engine, "connect", _sqlite_pragmas)
    _factory = sessionmaker(_engine, expire_on_commit=False)
    if migrate:
        run_migrations(_engine)
    return _engine


def run_migrations(engine: Engine) -> None:
    """Upgrade to the head revision, backing up an existing SQLite database first (see
    ``backup``)."""
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    saved = None
    if current is not None and current != head:
        if (db := backup.db_file(engine)) is None:
            logger.info("Migrating %s -> %s: no backup (not a SQLite file)", current, head)
        else:
            saved = backup.before_migration(db, current, str(head))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    if saved is not None:
        backup.migration_done(saved)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on error."""
    if _factory is None:
        init_engine()
    assert _factory is not None
    session = _factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
