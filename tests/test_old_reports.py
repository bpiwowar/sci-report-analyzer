"""The old reports into the folders' notes: the folders' numbering (a migration), then
their texts and papers appended to the notes (once)."""

import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from helpers import add_source, make_person, pub
from sqlalchemy import create_engine

from sci_report_analyzer import annotations, folders, old_reports, pubview, reports
from sci_report_analyzer.db import session as db_session
from sci_report_analyzer.db.models import AppSetting, Report
from sci_report_analyzer.db.session import session_scope

MIGRATIONS = Path(db_session.__file__).parent / "migrations"


def test_migration_numbering_from_the_reports(tmp_path):
    db = tmp_path / "db.sqlite"
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "a4c7e2f9d316")
    with sqlite3.connect(db) as c:
        for fid, name in ((1, "Hiring"), (2, "Prize"), (3, "Empty")):
            c.execute(
                "INSERT INTO folder (id, name, hidden, notes) VALUES (?, ?, 0, ?)",
                (fid, name, "Mine."),
            )
        for pid, fid in ((10, 1), (20, 2), (30, 3)):
            c.execute(
                "INSERT INTO period (id, person_id, folder_id, name, tags) VALUES (?, 1, ?, 'x', '[]')",
                (pid, fid),
            )
        c.execute("INSERT INTO tag (id, name, colour, per_period) VALUES (2, 'discuss', '#000', 0)")
        c.execute("INSERT INTO tag (id, name, colour, per_period) VALUES (3, 'listed', '#000', 1)")
        # Folder 1: a report with two tags, the second one put from a list (numbered).
        c.execute(
            "INSERT INTO report (period_id, text, tag_ids, number_format, updated_at) "
            "VALUES (10, 'Text', '[2, 3]', '**#{n}**', '')"
        )
        c.execute(
            "INSERT INTO period_tag (period_id, publication_id, tag_id, number) VALUES (10, 5, 3, 4)"
        )
        # Folder 2: no report, papers starred (the built-in tag).
        c.execute("INSERT INTO period_tag (period_id, publication_id, tag_id) VALUES (20, 5, 1)")
    db_session.run_migrations(create_engine(f"sqlite:///{db}"))
    with sqlite3.connect(db) as c:
        rows = dict(c.execute("SELECT id, citations FROM folder").fetchall())
        notes = [n for (n,) in c.execute("SELECT notes FROM folder")]
        pending = c.execute("SELECT value FROM app_setting WHERE key = 'old_reports'").fetchone()
    assert json.loads(rows[1]) == {"tag_id": 3, "format": "**#{index}**"}
    assert json.loads(rows[2]) == {"tag_id": 1}
    assert rows[3] is None
    assert notes == ["Mine."] * 3  # (appended by the app, later)
    assert json.loads(pending[0]) == {"pending": True, "done": []}


def _pending() -> None:
    with session_scope() as s:
        s.merge(AppSetting(key=old_reports.PENDING_KEY, value={"pending": True, "done": []}))


async def test_old_reports_appended_once():
    pid = make_person("Jane Doe")
    add_source(
        pid,
        "dblp",
        "d/1",
        [
            pub("a", "Deep ranking for search", 2021, "SIGIR", authors=["Jane Doe"]),
            pub("b", "The neural retrieval", 2022, "ECIR", authors=["Ann Smith", "Jane Doe"]),
            pub("c", "Deep ranking again", 2021, "SIGIR", authors=["Jane Doe"]),
        ],
    )
    other = make_person("John Roe")
    add_source(other, "dblp", "r/1", [pub("z", "Sparse things", 2020, "ACL", authors=["John Roe"])])
    fid = folders.save_folder(None, "Hiring", notes="My own notes.")
    period = folders.add_person(fid, pid)
    period_other = folders.add_person(fid, other)
    empty = folders.save_folder(None, "Nothing", notes="Untouched.")
    folders.add_person(empty, other)
    stats = await pubview.load_stats(pid)
    keys = reports.citation_keys(stats)
    by = {s.title: s.id for s in stats}
    a, b = by["Deep ranking for search"], by["The neural retrieval"]
    tag = annotations.save_tag("discuss")
    annotations.toggle_tag(a, tag)
    annotations.toggle_tag(b, tag)
    annotations.set_note(a, "Strong *results*.\n\nSecond paragraph.")
    annotations.set_note(a, "In the folder: shortlist", period)
    with session_scope() as s:
        s.add(
            Report(
                period_id=period,
                text=f"Both strong: [@{keys[b]}].",
                tag_ids=[tag],
                number_format="**#{n}**",
            )
        )
    z = (await pubview.load_stats(other))[0]
    annotations.toggle_star(period_other, z.id)
    zkey = reports.citation_keys([z])[z.id]
    # Not asked: nothing done.
    assert not old_reports.pending()
    assert await old_reports.migrate() == 0
    assert folders.notes_of(fid) == "My own notes."
    # Asked (by the migration): appended, after the notes kept as they were.
    _pending()
    assert await old_reports.migrate() == 1
    text = folders.notes_of(fid)
    assert text.startswith("My own notes.\n\n## Starred papers\n\n### Jane Doe\n\nBoth strong: [@")
    assert f"- [@{keys[b]}]: **The neural retrieval** · *ECIR* · 2022" in text
    assert f"- [@{keys[a]}]: **Deep ranking for search** · *SIGIR* · 2021" in text
    assert "\n\n  Strong *results*.\n\n  Second paragraph.\n\n  In the folder: shortlist" in text
    assert "Deep ranking again" not in text  # (not one of the report's papers)
    assert f"### John Roe\n\n- [@{zkey}]: **Sparse things** · *ACL* · 2020" in text
    assert folders.notes_of(empty) == "Untouched."
    assert not old_reports.pending()
    # Never twice: asked again, the folders done are skipped.
    with session_scope() as s:
        row = s.get(AppSetting, old_reports.PENDING_KEY)
        row.value = {**row.value, "pending": True}
    assert await old_reports.migrate() == 0
    assert folders.notes_of(fid) == text
    # The citations render in the notes, numbered as the report did.
    ctx = reports.folder_context(stats, period)
    assert reports.render(f"[@{keys[b]}]", ctx).unknown == []
