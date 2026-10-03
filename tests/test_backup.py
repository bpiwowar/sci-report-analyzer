import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from sci_report_analyzer.db import backup
from sci_report_analyzer.db import session as db_session

MIGRATIONS = Path(db_session.__file__).parent / "migrations"


def _cfg() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return cfg


def _old_db(path: Path) -> tuple[str, str]:
    """A database one migration behind; returns (its revision, head)."""
    cfg = _cfg()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    prev = ScriptDirectory.from_config(cfg).get_revision(head).down_revision
    with create_engine(f"sqlite:///{path}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, prev)
    return prev, head


def _revision(path: Path) -> str:
    with sqlite3.connect(path) as c:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _migrate(path: Path) -> None:
    db_session.run_migrations(create_engine(f"sqlite:///{path}"))


def test_backup_before_migration(tmp_path):
    db = tmp_path / "db.sqlite"
    prev, head = _old_db(db)
    _migrate(db)
    (saved,) = (tmp_path / "backups").glob("pre-migration-*.sqlite")
    assert f"-{prev}-to-{head}" in saved.name
    assert _revision(saved) == prev and _revision(db) == head
    assert not list((tmp_path / "backups").glob("*.pending"))
    _migrate(db)  # up to date: no other backup
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_no_backup_for_new_db(tmp_path):
    db = tmp_path / "db.sqlite"
    _migrate(db)
    assert _revision(db) == ScriptDirectory.from_config(_cfg()).get_current_head()
    assert not (tmp_path / "backups").exists()


def test_failed_migration_backup_kept(tmp_path, monkeypatch, caplog):
    db = tmp_path / "db.sqlite"
    prev, _ = _old_db(db)

    def boom(*a, **kw):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(command, "upgrade", boom)
    with pytest.raises(RuntimeError):
        _migrate(db)
    (failed,) = backup.failed_migrations(db)
    content = failed.read_bytes()
    with sqlite3.connect(db) as c:  # changes after the failure: not in the kept backup
        c.execute("CREATE TABLE later (x)")
    monkeypatch.undo()
    with caplog.at_level(logging.WARNING, logger=backup.__name__):
        _migrate(db)
    assert str(failed) in caplog.text
    assert backup.failed_migrations(db) == [failed]
    assert failed.read_bytes() == content
    (retry,) = set((tmp_path / "backups").glob("pre-migration-*.sqlite")) - {failed}
    assert _revision(retry) == prev
    with sqlite3.connect(retry) as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE name='later'").fetchone()


def test_successful_migration_backups_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "MIGRATION_KEEP", 2)
    db = tmp_path / "db.sqlite"
    _old_db(db)
    failed = backup.before_migration(db, "a", "b")  # never finished
    done = [backup.before_migration(db, "a", "b") for _ in range(3)]
    for b in done:
        backup.migration_done(b)
    left = set((tmp_path / "backups").glob("pre-migration-*.sqlite"))
    assert left == {failed, *done[1:]}


def test_daily_rotation(tmp_path):
    db = tmp_path / "db.sqlite"
    assert backup.daily(db) is None  # no database yet
    _old_db(db)
    start = datetime(2026, 1, 1)
    for i in range(10):
        assert backup.daily(db, start + timedelta(days=i))
    assert backup.daily(db, start + timedelta(days=9)) is None  # once a day
    days = sorted(p.name for p in (tmp_path / "backups").glob("daily-*.sqlite"))
    assert days == [f"daily-2026-01-{d:02}.sqlite" for d in range(4, 11)]


def test_language_cleaning_rules_added_to_saved_rules(tmp_path):
    """Saved cleaning rules get the spelled-ordinal ones (once; not the default settings)."""
    import json

    db = tmp_path / "db.sqlite"
    cfg = _cfg()
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "b7e2f4a91c03")
    mine = {"id": "custom1", "name": "Mine", "pattern": "^Proc\\. "}
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO app_setting (key, value) VALUES ('matching', ?)",
            (json.dumps({"min_score": 0.7, "norm_rules": [mine]}),),
        )
    _migrate(db)
    _migrate(db)
    with sqlite3.connect(db) as c:
        value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
    ids = [r["id"] for r in value["norm_rules"]]
    assert ids == ["ordinalWordsEn", "ordinalWordsFr", "custom1"]
    assert value["min_score"] == 0.7
