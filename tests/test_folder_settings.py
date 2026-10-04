"""Folders within folders, and their settings (categories, citations): a folder's own, or
its parent's (all of them); the people's excerpts follow when they change. The migration
giving each folder its own."""

from pathlib import Path

import pytest
from helpers import make_person

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


def test_settings_moved_to_the_parent_shared_by_the_subfolders():
    ann, bob = make_person("Ann Example"), make_person("Bob Sample")
    top = folders.save_folder(None, "Hiring")
    sub = folders.save_folder(None, "Session 1", parent_id=top)
    other = folders.save_folder(None, "Session 2", parent_id=top)
    deep = folders.save_folder(None, "Interviews", parent_id=sub)
    folders.use_own_settings(sub)
    categories.add(top, "Old")
    reports.save_skeleton(top, "# Old")
    research = categories.add(sub, "Research")
    reports.save_skeleton(sub, "# Mine")
    old = next(n.id for n in categories.tree(other) if n.name == "Old")
    period = folders.add_person(other, ann)
    categories.add_excerpt(old, period, "A prize.", 1, [])
    mine = folders.add_person(deep, bob)
    categories.add_excerpt(research, mine, "A grant.", 1, [])
    # Who changes: the parent and the folders using its settings (not those using the moved).
    assert folders.affected(sub, top) == ["Hiring", "Hiring › Session 2"]
    with pytest.raises(ValueError):
        folders.move_settings(top, sub)  # (only to a folder it is in)
    assert folders.move_settings(sub, top) == 1  # ("Old", for the excerpt filed in it)
    assert not folders.own_settings(sub) and folders.own_settings(top)
    assert reports.skeleton(top) == reports.skeleton(other) == "# Mine\n"
    assert _cats(top) == _cats(other) == _cats(deep) == ["Research", "Old"]
    assert _filed(period) == [("Old", "A prize.")] and _filed(mine) == [("Research", "A grant.")]
    assert sorted(folders.sharing(top)) == [
        "Hiring › Session 1",
        "Hiring › Session 1 › Interviews",
        "Hiring › Session 2",
    ]
    # To a folder further up: those between them use it too (their own dropped).
    folders.use_own_settings(sub)
    folders.use_own_settings(deep)
    categories.add(deep, "Deep")
    assert folders.affected(deep, top) == ["Hiring", "Hiring › Session 1", "Hiring › Session 2"]
    folders.move_settings(deep, top)
    assert [n.own for n in folders.tree()] == [True, False, False, False]
    assert "Deep" in _cats(other) and _filed(mine) == [("Research", "A grant.")]


def test_settings_copied_to_another_folder():
    ann = make_person("Ann Example")
    a = folders.save_folder(None, "A")
    b = folders.save_folder(None, "B")
    within = folders.save_folder(None, "Within B", parent_id=b)
    categories.add(a, "Research")
    reports.save_skeleton(a, "# From A")
    teaching = categories.add(b, "Teaching")
    period = folders.add_person(within, ann)
    categories.add_excerpt(teaching, period, "A course.", 1, [])
    with pytest.raises(ValueError):
        folders.copy_settings_to(a, a)
    assert folders.affected(a, b, copy=True) == ["B", "B › Within B"]
    assert folders.copy_settings_to(a, b) == 1  # (Teaching, for the excerpt)
    assert reports.skeleton(b) == "# From A\n" and _cats(within) == ["Research", "Teaching"]
    assert _filed(period) == [("Teaching", "A course.")]
    # A copy: apart from the source's.
    assert folders.sharing(b) == ["B › Within B"] and _cats(a) == ["Research"]
    categories.add(b, "Projects")
    assert "Projects" not in _cats(a)
