"""Folders within folders, and their settings (categories, citations): a folder's own, or
its parent's (all of them); the people's excerpts follow when they change. The migration
giving each folder its own."""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from helpers import make_person
from sqlalchemy import create_engine

from sci_report_analyzer import categories, folders, reports
from sci_report_analyzer.db import session as db_session

MIGRATIONS = Path(db_session.__file__).parent / "migrations"


def _cats(folder_id: int) -> list[str]:
    return [n.path for n in categories.tree(folder_id)]


def _filed(period_id: int) -> list[tuple[str, str]]:
    """(the path of its category, its text) of each excerpt of a person in a folder."""
    with db_session.session_scope() as s:
        from sci_report_analyzer.db.models import Period

        folder_id = s.get(Period, period_id).folder_id
    names = {n.id: n.path for n in categories.tree(folder_id)}
    return sorted((names[e.category_id], e.text) for e in categories.excerpts(period_id))


def test_a_subfolder_uses_its_parents_settings():
    top = folders.save_folder(None, "Hiring")
    sub = folders.save_folder(None, "Hiring 2026", parent_id=top)
    assert folders.own_settings(top) and not folders.own_settings(sub)
    categories.add(top, "Research")
    categories.add(sub, "Teaching")  # (the parent's: shared)
    reports.save_skeleton(sub, "# Notes")
    assert _cats(top) == _cats(sub) == ["Research", "Teaching"]
    assert reports.skeleton(top) == "# Notes\n"
    assert folders.sharing(sub) == ["Hiring"]
    [fv] = [f for f in folders.folders() if f.id == sub]
    assert (fv.parent_id, fv.path) == (top, "Hiring › Hiring 2026")
    assert [(n.name, n.depth, n.own) for n in folders.tree()] == [
        ("Hiring", 0, True),
        ("Hiring 2026", 1, False),
    ]
    with pytest.raises(ValueError):
        folders.set_parent(top, sub)  # (never within itself)


def test_own_settings_start_from_a_copy_and_excerpts_follow():
    ann = make_person("Ann Example")
    top = folders.save_folder(None, "Prize")
    sub = folders.save_folder(None, "Prize 2026", parent_id=top)
    research = categories.add(top, "Research")
    projects = categories.add(top, "Projects", research)
    categories.add(top, "Projects", research)  # (two of the same name: told apart)
    reports.save_skeleton(top, "# Start")
    period = folders.add_person(sub, ann)
    categories.add_excerpt(projects, period, "Led a project.", 1, [])
    folders.use_own_settings(sub)
    assert folders.own_settings(sub) and folders.sharing(sub) == []
    assert _cats(sub) == _cats(top) and reports.skeleton(sub) == "# Start\n"
    [e] = categories.excerpts(period)
    assert e.category_id != projects and _filed(period) == [
        ("Research › Projects", "Led a project.")
    ]
    # Its own settings now: changing them leaves the parent's alone.
    categories.add(sub, "Teaching")
    reports.save_skeleton(sub, "# Mine")
    assert "Teaching" not in _cats(top) and reports.skeleton(top) == "# Start\n"
    # Back to the parent's: the excerpts go to its categories (the missing ones added).
    teaching = next(n.id for n in categories.tree(sub) if n.name == "Teaching")
    categories.add_excerpt(teaching, period, "Taught a course.", 2, [])
    assert folders.use_parent_settings(sub) == 1
    assert not folders.own_settings(sub) and reports.skeleton(sub) == "# Start\n"
    assert _cats(top) == ["Research", "Research › Projects", "Research › Projects", "Teaching"]
    assert _filed(period) == [
        ("Research › Projects", "Led a project."),
        ("Teaching", "Taught a course."),
    ]
    assert categories.excerpts(period)[0].category_id == projects


def test_moving_and_deleting_folders():
    ann = make_person("Ann Example")
    a = folders.save_folder(None, "A")
    b = folders.save_folder(None, "B")
    categories.add(a, "Research")
    categories.add(b, "Activities")
    sub = folders.save_folder(None, "Sub", parent_id=a)
    period = folders.add_person(sub, ann)
    research = categories.tree(sub)[0].id
    categories.add_excerpt(research, period, "A grant.", 1, [])
    # Moved into B (using its parent's): B's settings, the excerpts there too.
    assert folders.set_parent(sub, b) == 1
    assert _cats(sub) == ["Activities", "Research"] and _filed(period) == [("Research", "A grant.")]
    # At the top level: still the settings it used, as its own (shared with B).
    assert folders.set_parent(sub, None) == 0
    assert folders.own_settings(sub) and folders.sharing(sub) == ["B"]
    folders.set_parent(sub, a)
    folders.rename(sub, "Within A")
    assert [(n.path, n.own) for n in folders.tree() if n.id == sub] == [("A › Within A", True)]
    # Deleting A: the folders within it go up, keeping the settings they used.
    deep = folders.save_folder(None, "Deep", parent_id=a)
    folders.delete_folder(a)
    assert {n.name: (n.depth, n.own) for n in folders.tree()} == {
        "B": (0, True),
        "Within A": (0, True),
        "Deep": (0, True),
    }
    assert _cats(deep) == ["Research"]  # (A's: kept for it)
    assert _filed(period) == [("Research", "A grant.")]


def test_migration_each_folder_its_own_settings(tmp_path):
    db = tmp_path / "db.sqlite"
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "a1d6f3c8e925")
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO folder (id, name, hidden, citations) VALUES (1, 'Hiring', 0, ?)",
            (json.dumps({"tag_id": 3, "skeleton": "# Notes\n"}),),
        )
        c.execute("INSERT INTO folder (id, name, hidden) VALUES (2, 'Prize', 0)")
        c.execute(
            "INSERT INTO period (id, person_id, folder_id, name, tags) VALUES (10, 1, 1, 'x', '[]')"
        )
        for cid, fid, parent in ((1, 1, None), (2, 1, 1), (3, 2, None)):
            c.execute(
                "INSERT INTO category (id, folder_id, parent_id, name, position, influence,"
                " colour) VALUES (?, ?, ?, 'Cat', 1, 0, '#4caf50')",
                (cid, fid, parent),
            )
        c.execute(
            "INSERT INTO excerpt (category_id, period_id, text, rects, influence, position,"
            " ref_only, created_at) VALUES (2, 10, 'Text', '[]', 0, 0, 0, '2026-01-01')"
        )
    db_session.run_migrations(create_engine(f"sqlite:///{db}"))
    with sqlite3.connect(db) as c:
        settings = dict(c.execute("SELECT id, citations FROM folder_settings").fetchall())
        uses = c.execute("SELECT folder_id, settings_id FROM folder_settings_use").fetchall()
        cats = c.execute("SELECT id, settings_id, parent_id FROM category").fetchall()
        parents = c.execute("SELECT parent_id FROM folder").fetchall()
        (kept,) = c.execute("SELECT category_id FROM excerpt").fetchone()
        columns = {r[1] for r in c.execute("PRAGMA table_info(folder)")}
    assert json.loads(settings[1]) == {"tag_id": 3, "skeleton": "# Notes\n"} and settings[2] is None
    assert sorted(uses) == [(1, 1), (2, 2)]
    assert sorted(cats) == [(1, 1, None), (2, 1, 1), (3, 2, None)]
    assert parents == [(None,), (None,)] and kept == 2 and "citations" not in columns
    # Down again: a folder sharing settings with another gets a copy, with its excerpts.
    with sqlite3.connect(db) as c:
        c.execute("UPDATE folder SET parent_id = 1 WHERE id = 2")
        c.execute("DELETE FROM folder_settings_use WHERE folder_id = 2")
        c.execute(
            "INSERT INTO period (id, person_id, folder_id, name, tags) VALUES (20, 1, 2, 'x', '[]')"
        )
        c.execute(
            "INSERT INTO excerpt (category_id, period_id, text, rects, influence, position,"
            " ref_only, created_at) VALUES (2, 20, 'Other', '[]', 0, 0, 0, '2026-01-01')"
        )
    with create_engine(f"sqlite:///{db}").begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "a1d6f3c8e925")
    with sqlite3.connect(db) as c:
        citations = dict(c.execute("SELECT id, citations FROM folder").fetchall())
        cats = c.execute("SELECT id, folder_id, parent_id FROM category ORDER BY id").fetchall()
        filed = dict(c.execute("SELECT text, category_id FROM excerpt").fetchall())
    assert (
        json.loads(citations[1])
        == json.loads(citations[2])
        == {"tag_id": 3, "skeleton": "# Notes\n"}
    )
    assert cats[:2] == [(1, 1, None), (2, 1, 1)] and len(cats) == 4  # (3: unused, dropped)
    copy = {i: (f, p) for i, f, p in cats[2:]}
    assert filed["Text"] == 2 and copy[filed["Other"]][0] == 2
    assert copy[copy[filed["Other"]][1]] == (2, None)
