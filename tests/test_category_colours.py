"""The colours of the categories (a migration): each takes the colour most of its excerpts
had, else the next one of the palette; the excerpts' own colours are dropped."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from sci_report_analyzer import categories
from sci_report_analyzer.db import session as db_session

MIGRATIONS = Path(db_session.__file__).parent / "migrations"


def test_migration_colours_from_the_excerpts(tmp_path):
    db = tmp_path / "db.sqlite"
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "e7a3c5b9d1f2")
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO folder (id, name, hidden) VALUES (1, 'Hiring', 0)")
        c.execute(
            "INSERT INTO period (id, person_id, folder_id, name, tags) VALUES (10, 1, 1, 'x', '[]')"
        )
        for cid in (1, 2, 3):
            c.execute(
                "INSERT INTO category (id, folder_id, name, position, influence)"
                " VALUES (?, 1, 'Cat', ?, 0)",
                (cid, cid),
            )
        # Category 1: mostly pink; 2: no colour chosen; 3: no excerpt.
        for cid, colour in ((1, "#e91e63"), (1, "#e91e63"), (1, "#2196f3"), (2, None)):
            c.execute(
                "INSERT INTO excerpt (category_id, period_id, text, rects, influence, colour,"
                " position, ref_only, created_at)"
                " VALUES (?, 10, 'Text', '[]', 0, ?, 0, 0, '2026-01-01')",
                (cid, colour),
            )
    db_session.run_migrations(create_engine(f"sqlite:///{db}"))
    with sqlite3.connect(db) as c:
        colours = dict(c.execute("SELECT id, colour FROM category").fetchall())
        columns = {r[1] for r in c.execute("PRAGMA table_info(excerpt)")}
        (kept,) = c.execute("SELECT count(*) FROM excerpt").fetchone()
    assert colours == {1: "#e91e63", 2: categories.PALETTE[1], 3: categories.PALETTE[2]}
    assert "colour" not in columns and kept == 4
