"""Migrations of the data (from a database at the revision before)."""

import json
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from sci_report_analyzer.db import session as db_session


def _upgrade(path, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(db_session.MIGRATIONS_DIR))
    with create_engine(f"sqlite:///{path}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def _person(c, pid: int, papers) -> None:
    """A person and their papers: (id, title, year, [(source, authors, archival)])."""
    c.execute(
        "INSERT INTO person (id, name, created_at, aliases, student_aliases, student_outcomes,"
        " name_rejects, author_categories)"
        " VALUES (?, 'Ann Example', '2026-01-01', '[]', '{}', '{}', '{}', '{}')",
        (pid,),
    )
    links = {}
    for source in ("doi", "dblp", "hal"):
        links[source] = c.execute(
            "INSERT INTO source_link (person_id, source, external_id, evidence, score, status,"
            " sync_state) VALUES (?, ?, 'x', '{}', 0, 'validated', 'idle')",
            (pid, source),
        ).lastrowid
    for pub_id, title, year, members in papers:
        c.execute(
            "INSERT INTO publication (id, person_id, title, year, merge_locked, missing, hidden,"
            " venue_manual, created_at) VALUES (?, ?, ?, ?, 0, 0, 0, 0, '2026-01-01')",
            (pub_id, pid, title, year),
        )
        for i, (source, authors, archival) in enumerate(members):
            c.execute(
                "INSERT INTO source_pub (link_id, external_key, authors, archival, raw,"
                " publication_id) VALUES (?, ?, ?, ?, '{}', ?)",
                (links[source], f"{pub_id}.{i}", json.dumps(authors), archival, pub_id),
            )


def test_cleanup_7c1e4a9b2d30(tmp_path):
    """Citation keys renamed in the texts, the reports kept in the notes, then dropped with
    the other data never read."""
    db = tmp_path / "db.sqlite"
    _upgrade(db, "f6b3d8a2c5e9")
    with sqlite3.connect(db) as c:
        _person(
            c,
            1,
            [
                # "Doe, Jane": jane2020neural, now doe2020neural (the DOI record's list first)
                (1, "Neural ranking", 2020, [("hal", ["Jane Doe"], 0), ("doi", ["Doe, Jane"], 0)]),
                # doe2020neural, now doe2020neurala (a later paper of the same key)
                (2, "Neural search", 2020, [("dblp", ["John Doe"], 0)]),
                (3, "The deep reader", 2021, [("hal", ["NEVEOL Aurélie", "Jo Roe"], 0)]),
                (4, "Same key", 2019, [("dblp", ["Jo Roe"], 0)]),  # roe2019same: unchanged
            ],
        )
        _person(c, 2, [(5, "Other", 2020, [("dblp", ["Doe, Jane"], 0)])])
        c.execute("INSERT INTO folder (id, name, hidden) VALUES (1, 'Hiring', 0)")
        c.execute(
            "INSERT INTO period (id, person_id, folder_id, name, tags) VALUES (1, 1, 1, 'p', '[]')"
        )
        c.execute(
            "UPDATE period SET notes = ? WHERE id = 1",
            (
                "[@jane2020neural; @doe2020neural] and @aurelie2021deep{.notes}, "
                "[-@roe2019same]; jane@jane2020neural.org, @jane2020neuralx, "
                "`@jane2020neural`.",
            ),
        )
        c.execute("UPDATE publication SET note = '[@jane2020neural]' WHERE id IN (2, 5)")
        c.execute(
            "INSERT INTO app_setting VALUES ('ui.reflist.1.0.1', '[]'), ('ui.x', '1'),"
            " ('old_reports', '{}')"
        )
        c.execute(
            "INSERT INTO report VALUES (1, 'Read [@jane2020neural].', '[]', '', ''),"
            " (2, '  ', '[]', '', '')"
        )
    _upgrade(db, "7c1e4a9b2d30")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT notes FROM period").fetchone()[0] == (
            "[@doe2020neural; @doe2020neurala] and @neveol2021deep{.notes}, "
            "[-@roe2019same]; jane@jane2020neural.org, @jane2020neuralx, "
            "`@doe2020neural`.\n\n## Report\n\nRead [@doe2020neural].\n"
        )
        notes = dict(c.execute("SELECT id, note FROM publication WHERE note IS NOT NULL"))
        # (person 2: their paper's key is doe2020other, no jane2020neural to rename)
        assert notes == {2: "[@doe2020neural]", 5: "[@jane2020neural]"}
        assert c.execute("SELECT key FROM app_setting").fetchall() == [("ui.x",)]
        tables = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "report" not in tables
        assert "sync_started_at" not in {r[1] for r in c.execute("PRAGMA table_info(source_link)")}
        assert "discipline" not in {r[1] for r in c.execute("PRAGMA table_info(thesis)")}
