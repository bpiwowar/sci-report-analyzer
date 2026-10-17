"""Folders within folders, and their settings (categories, citations…): named, a folder
using those it chose, else the default ones; the people's excerpts follow when they change."""

import pytest
from helpers import make_person, own_settings

from sci_report_analyzer import annotations, categories, folders, reports
from sci_report_analyzer.db import session as db_session


def _cats(folder_id: int) -> list[str]:
    return [n.path for n in categories.tree(folder_id)]


def _filed(period_id: int) -> list[tuple[str, str]]:
    """(the path of its category, its text) of each excerpt of a person in a folder."""
    with db_session.session_scope() as s:
        from sci_report_analyzer.db.models import Period

        folder_id = s.get(Period, period_id).folder_id
    names = {n.id: n.path for n in categories.tree(folder_id)}
    return sorted((names[e.category_id], e.text) for e in categories.excerpts(period_id))


def _named() -> dict[str, tuple[bool, list[str]]]:
    return {x.name: (x.default, x.folders) for x in folders.named_settings()}


def test_folders_use_the_default_settings_unless_chosen():
    top = folders.save_folder(None, "Hiring")
    sub = folders.save_folder(None, "Hiring 2026", parent_id=top)
    other = folders.save_folder(None, "Prize")
    categories.add(top, "Research")
    categories.add(sub, "Teaching")  # (the same settings: shared)
    reports.save_skeleton(sub, "# Notes")
    assert _cats(top) == _cats(other) == ["Research", "Teaching"]
    assert reports.skeleton(other) == "# Notes\n"
    assert folders.sharing(sub) == ["Hiring", "Prize"]
    default = folders.default_settings()
    assert _named() == {"Default": (True, ["Hiring", "Hiring › Hiring 2026", "Prize"])}
    assert [(n.name, n.depth, n.settings_id, n.default_settings) for n in folders.tree()] == [
        ("Hiring", 0, default, True),
        ("Hiring 2026", 1, default, True),
        ("Prize", 0, default, True),
    ]
    # Others chosen: a subfolder made then uses its parent's choice.
    mine = folders.create_settings("Committees")
    folders.use_settings(top, mine)
    within = folders.save_folder(None, "Session", parent_id=top)
    assert folders.settings_of(within) == mine and folders.settings_of(sub) == default
    assert _cats(within) == [] and _cats(sub) == ["Research", "Teaching"]
    # Moved: its settings stay those it uses.
    folders.set_parent(within, other)
    assert folders.settings_of(within) == mine
    with pytest.raises(ValueError):
        folders.set_parent(other, within)  # (never within itself)
    # The default ones changed: for the folders that chose none.
    folders.set_default_settings(mine)
    assert folders.settings_of(sub) == mine and folders.settings_of(top) == mine
    assert [x.name for x in folders.named_settings()] == ["Committees", "Default"]


def test_settings_created_renamed_copied_and_deleted():
    ann = make_person("Ann Example")
    a = folders.save_folder(None, "A")
    research = categories.add(a, "Research")
    categories.add(a, "Projects", research)
    reports.save_skeleton(a, "# Start")
    folders.set_primary_source(a, "hal")
    annotations.save_list(folders.settings_of(a), "shortlist")
    default = folders.default_settings()
    # A copy: its categories, citations, primary source and star lists.
    copy = folders.create_settings("Copy", default)
    ref = folders.SettingsRef(copy)
    assert _cats(ref) == ["Research", "Research › Projects"]
    assert reports.skeleton(ref) == "# Start\n" and folders.primary_source(ref) == "hal"
    assert [t.name for t in annotations.star_lists(settings_id=copy)][-1] == "shortlist"
    categories.add(ref, "Teaching")  # (apart from the source's)
    assert "Teaching" not in _cats(a)
    # Empty ones; names: unique, not blank.
    empty = folders.create_settings(" Empty ")
    assert _cats(folders.SettingsRef(empty)) == [] and "Empty" in _named()
    for taken in ("copy", "  "):
        with pytest.raises(ValueError):
            folders.create_settings(taken)
    with pytest.raises(ValueError):
        folders.rename_settings(empty, "Copy")
    folders.rename_settings(empty, "Blank")
    assert set(_named()) == {"Default", "Copy", "Blank"}
    # Deleted: not the default ones, nor those used, nor those with excerpts filed.
    with pytest.raises(ValueError):
        folders.delete_settings(default)
    folders.use_settings(a, copy)
    with pytest.raises(ValueError):
        folders.delete_settings(copy)
    period = folders.add_person(a, ann)
    teaching = next(n.id for n in categories.tree(a) if n.name == "Teaching")
    categories.add_excerpt(teaching, period, "A course.", 1, [])
    folders.remove_person(a, ann, keep=True)  # (its excerpts kept, filed there)
    folders.use_settings(a, None)
    assert [x.filed for x in folders.named_settings() if x.id == copy] == [1]
    with pytest.raises(ValueError):
        folders.delete_settings(copy)
    folders.delete_settings(empty)
    assert set(_named()) == {"Default", "Copy"}


def test_excerpts_follow_the_settings_chosen():
    ann = make_person("Ann Example")
    top = folders.save_folder(None, "Prize")
    sub = folders.save_folder(None, "Prize 2026", parent_id=top)
    research = categories.add(top, "Research")
    projects = categories.add(top, "Projects", research)
    categories.add(top, "Projects", research)  # (two of the same name: told apart)
    reports.save_skeleton(top, "# Start")
    period = folders.add_person(sub, ann)
    categories.add_excerpt(projects, period, "Led a project.", 1, [])
    categories.set_section_text(period, research, "Some research.")  # (without excerpts)
    # A copy chosen: the excerpts filed in the copies of their categories.
    own = own_settings(sub, copy=True)
    assert folders.sharing(sub) == [] and _cats(sub) == _cats(top)
    assert reports.skeleton(sub) == "# Start\n"
    [e] = categories.excerpts(period)
    assert e.category_id != projects and _filed(period) == [
        ("Research › Projects", "Led a project.")
    ]
    [mine] = [n.id for n in categories.tree(sub) if n.path == "Research"]
    assert categories.section_texts(period) == {mine: "Some research."}  # (its text follows)
    categories.add(sub, "Teaching")
    teaching = next(n.id for n in categories.tree(sub) if n.name == "Teaching")
    categories.add_excerpt(teaching, period, "Taught a course.", 2, [])
    # Back to the default ones: the excerpts there (the missing categories added).
    assert folders.use_settings(sub, None) == 1
    assert _cats(top) == ["Research", "Research › Projects", "Research › Projects", "Teaching"]
    assert _filed(period) == [
        ("Research › Projects", "Led a project."),
        ("Teaching", "Taught a course."),
    ]
    assert categories.excerpts(period)[0].category_id == projects
    assert categories.section_texts(period) == {research: "Some research."}
    assert folders.use_settings(sub, own) == 0  # (all there)


def test_deleting_a_folder():
    ann = make_person("Ann Example")
    a = folders.save_folder(None, "A")
    sub = folders.save_folder(None, "Within A", parent_id=a)
    deep = folders.save_folder(None, "Deep", parent_id=sub)
    own = own_settings(sub)
    categories.add(sub, "Research")
    period = folders.add_person(deep, ann)
    folders.delete_folder(a)
    assert {n.name: (n.depth, n.settings_id) for n in folders.tree()} == {
        "Within A": (0, own),
        "Deep": (1, folders.default_settings()),
    }
    assert _filed(period) == []
    folders.delete_folder(sub)
    assert [n.name for n in folders.tree()] == ["Deep"]
    assert [x.folders for x in folders.named_settings() if x.id == own] == [[]]  # (kept)


def test_moving_a_person_to_another_folder():
    from helpers import add_source, pub, toggle_star

    from sci_report_analyzer.db.models import Publication

    ann = make_person("Ann Example")
    add_source(ann, "dblp", "x/1", [pub("a", "A paper", 2021, "Some Venue")])
    with db_session.session_scope() as s:
        paper = s.query(Publication.id).scalar()
    a = folders.save_folder(None, "Committee A")
    b = folders.save_folder(None, "Committee B")
    own_settings(b)
    categories.add(a, "Research")
    categories.add(b, "Activities")
    period = folders.add_person(a, ann, 2020, 2024)
    folders.set_tags(period, ["shortlisted"])
    categories.add_excerpt(categories.tree(a)[0].id, period, "A grant.", 1, [])
    toggle_star(period, paper)
    # Moved with their period (years, tags, stars), their excerpts filed in B's categories.
    assert folders.move_person(ann, a, b) == 1
    [fa] = [f for f in folders.folders() if f.id == a]
    [fb] = [f for f in folders.folders() if f.id == b]
    assert not fa.members
    [m] = fb.members
    assert (m.period_id, m.start_year, m.end_year, m.tags, m.stars) == (
        period,
        2020,
        2024,
        ["shortlisted"],
        1,
    )
    assert _cats(b) == ["Activities", "Research"] and _filed(period) == [("Research", "A grant.")]
    # Not into a folder they are in already.
    folders.add_person(a, ann)
    with pytest.raises(ValueError):
        folders.move_person(ann, a, b)
