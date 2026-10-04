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
    old = {  # the general rule they replace, unchanged: dropped
        "id": "ordinals",
        "name": "Ordinals",
        "description": "Remove ordinal numbers such as 45th, 1st, 17e, 22èmes.",
        "pattern": r"\b\d+(?:st|nd|rd|th|e|er|eme|ème)s?\b",
        "replacement": " ",
        "ignore_case": True,
        "enabled": True,
        "sources": [],
        "example": "45th Annual Meeting",
    }
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO app_setting (key, value) VALUES ('matching', ?)",
            (json.dumps({"min_score": 0.7, "norm_rules": [mine, old]}),),
        )
    _migrate(db)
    _migrate(db)
    with sqlite3.connect(db) as c:
        value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
    ids = [r["id"] for r in value["norm_rules"]]
    assert ids == ["proceedingsOf", "leadingThe", "ordinalsEn", "ordinalsFr", "custom1"]
    assert value["min_score"] == 0.7


def test_parentheses_rule_dropped_from_saved_rules(tmp_path):
    """The "Parentheses" rule (it removed tracks: "(Demonstrations)") leaves the saved rules
    when not edited; an edited one stays."""
    import json

    db = tmp_path / "db.sqlite"
    cfg = _cfg()
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "a9c3e5f17d28")
    old = {
        "id": "parentheses",
        "name": "Parentheses",
        "description": "Remove parenthesised text (one level of nesting).",
        "pattern": r"\((?:[^()]|\([^()]*\))*\)",
        "replacement": " ",
        "ignore_case": False,
        "enabled": True,
        "sources": [],
        "example": "Neural Information Processing Systems (NeurIPS)",
    }
    acronym = {"id": "parenAcronym", "name": "Parenthesised acronym", "pattern": "x"}
    for rules, kept in (
        ([old, acronym], ["parenAcronym"]),
        ([{**old, "enabled": False}], ["parentheses"]),
    ):
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT OR REPLACE INTO app_setting (key, value) VALUES ('matching', ?)",
                (json.dumps({"norm_rules": rules}),),
            )
            c.execute("UPDATE alembic_version SET version_num = 'a9c3e5f17d28'")
        _migrate(db)
        with sqlite3.connect(db) as c:
            value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
        ids = [r["id"] for r in value["norm_rules"]]
        assert [i for i in ids if i not in ("proceedingsOf", "leadingThe")] == kept


def test_ordinal_marks_in_saved_rules(tmp_path):
    """Saved ordinal rules replace by "Zth" / "Zème" (not when their replacement was
    edited)."""
    import json

    db = tmp_path / "db.sqlite"
    cfg = _cfg()
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "c4f8a2d6e913")
    en = {"id": "ordinalsEn", "name": "Ordinals", "pattern": "x", "replacement": " "}
    fr = {"id": "ordinalsFr", "name": "Ordinals", "pattern": "y", "replacement": "#"}
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO app_setting (key, value) VALUES ('matching', ?)",
            (json.dumps({"norm_rules": [en, fr]}),),
        )
    _migrate(db)
    with sqlite3.connect(db) as c:
        value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
    rules = value["norm_rules"]
    assert [r["replacement"] for r in rules if r["id"].startswith("ordinals")] == ["Zth", "#"]
    # (Later: the rules for the front matter DOI texts keep, "Proceedings of the", first.)
    assert [r["id"] for r in rules][:2] == ["proceedingsOf", "leadingThe"]


def test_migrations_keep_the_venues_variants_and_links(tmp_path):
    """Migrations run without foreign keys: a table copied (SQLite's batch mode) and its old
    one dropped must not cascade (a venue's variants, its papers' links)."""
    from sqlalchemy import event

    db = tmp_path / "db.sqlite"
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg = _cfg()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "d2b7e4c91a56")
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO venue (id, name, kind_manual, created_at) VALUES (1, 'W', 0, '')")
        c.execute("INSERT INTO venue_key (key, venue_id, manual) VALUES ('w', 1, 1)")
    engine = create_engine(f"sqlite:///{db}")
    event.listen(engine, "connect", db_session._sqlite_pragmas)  # (foreign keys on)
    db_session.run_migrations(engine)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT venue_id FROM venue_key").fetchall() == [(1,)]


def test_prefix_rules_for_every_source(tmp_path):
    """The DOI-only "Proceedings of" / "The" rules saved become the general ones (not when
    edited)."""
    import json

    from sci_report_analyzer.db.migrations.versions import (
        b3e9d7a2c5f4_prefix_rules_every_source as m,
    )

    db = tmp_path / "db.sqlite"
    cfg = _cfg()
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "f8b2c6d4a3e1")
    edited = {**m.OLD["leadingThe"], "pattern": r"^\s*the\s+(?=journal)"}
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO app_setting (key, value) VALUES ('matching', ?)",
            (json.dumps({"norm_rules": [m.OLD["proceedingsOf"], edited]}),),
        )
    _migrate(db)
    with sqlite3.connect(db) as c:
        value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
    assert value["norm_rules"] == [m.RULES["proceedingsOf"], edited]


def test_flags_become_tracks_set_by_hand_and_tags(tmp_path):
    """A flag with a track becomes its papers' track set by hand (its colour the track's);
    one without becomes a tag on the same papers; the flag tables go, nothing else."""
    import json

    from sqlalchemy import event

    db = tmp_path / "db.sqlite"
    cfg = _cfg()
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "d7f3a9c2e186")
    edited = {"id": "track_demo", "pattern": r"\bshowcase\b", "ignore_case": True}
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO venue (id, name, kind_manual, created_at) VALUES (1, 'W', 0, '')")
        c.execute("INSERT INTO venue_key (key, venue_id, manual) VALUES ('w', 1, 1)")
        c.execute(
            "INSERT INTO person (id, name, aliases, student_aliases, student_outcomes, "
            "name_rejects, author_categories, created_at) "
            "VALUES (1, 'Zorba Quill', '[]', '{}', '{}', '{}', '[]', '')"
        )
        for i in (1, 2, 3):
            c.execute(
                "INSERT INTO publication (id, person_id, title, venue_id, venue_manual, "
                "merge_locked, missing, hidden, created_at) VALUES (?, 1, ?, 1, 0, 0, 0, 0, '')",
                (i, f"Paper {i}"),
            )
        c.executemany(
            "INSERT INTO flag (id, name, colour, track) VALUES (?, ?, ?, ?)",
            [
                (1, "short", "#d4a72c", "short"),
                (2, "demo", "#ff00ff", "demo"),
                (3, "to check", "#cf222e", None),
            ],
        )
        c.executemany(
            "INSERT INTO publication_flag (publication_id, flag_id) VALUES (?, ?)",
            [(1, 1), (2, 2), (2, 3), (3, 3)],
        )
        c.execute(
            "INSERT INTO app_setting (key, value) VALUES ('matching', ?)",
            (json.dumps({"min_score": 0.7, "detection_rules": [edited]}),),
        )
    engine = create_engine(f"sqlite:///{db}")
    event.listen(engine, "connect", db_session._sqlite_pragmas)  # (foreign keys on)
    db_session.run_migrations(engine)
    with sqlite3.connect(db) as c:
        overrides = c.execute("SELECT id, track_override FROM publication ORDER BY id").fetchall()
        assert overrides == [(1, "short"), (2, "demo"), (3, None)]
        (tag_id, colour) = c.execute(
            "SELECT id, colour FROM tag WHERE name = 'to check' AND per_period = 0"
        ).fetchone()
        assert colour == "#cf222e"
        tagged = c.execute("SELECT publication_id FROM publication_tag WHERE tag_id = ?", (tag_id,))
        assert sorted(r[0] for r in tagged) == [2, 3]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"flag", "publication_flag"} & tables
        # The rest is intact (no cascade).
        assert c.execute("SELECT venue_id FROM venue_key").fetchall() == [(1,)]
        assert c.execute("SELECT count(*) FROM publication WHERE venue_id = 1").fetchone() == (3,)
        assert c.execute("SELECT name FROM venue").fetchall() == [("W",)]
        value = json.loads(c.execute("SELECT value FROM app_setting").fetchone()[0])
    # The tracks in the settings: the flags' colours, the track rule edited moved in.
    defs = {t["id"]: t for t in value["tracks"]}
    assert list(defs) == ["findings", "tutorial", "demo", "short"]
    assert defs["demo"]["colour"] == "#ff00ff" and defs["short"]["colour"] == "#d4a72c"
    assert defs["demo"]["rules"][0]["pattern"] == r"\bshowcase\b"
    assert not any(r["id"].startswith("track_") for r in value["detection_rules"])
    assert value["min_score"] == 0.7
