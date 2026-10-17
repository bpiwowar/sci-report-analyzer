"""A person's timeline: their excerpts in a folder placed by their years (categories.timeline),
and its tab on the person's page."""

import pytest
from helpers import add_source, make_person, pub
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import categories, folders
from sci_report_analyzer.db.models import Publication
from sci_report_analyzer.db.session import session_scope

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _seed() -> tuple[int, int, dict[str, int], int]:
    """A person in a folder, with two categories and a paper of 2018: (person, period,
    categories by name, the paper)."""
    pid = make_person("Ann Example")
    add_source(pid, "dblp", "x/1", [pub("a", "A paper on things", 2018, "Some Journal")])
    with session_scope() as s:
        paper = s.scalar(select(Publication.id).where(Publication.person_id == pid))
    fid = folders.save_folder(None, "Hiring committee")
    period = folders.add_person(fid, pid, 2020, 2024)
    cats = {name: categories.add(fid, name) for name in ("Research", "Teaching")}
    return pid, period, cats, paper


def test_timeline_by_year() -> None:
    _pid, period, cats, paper = _seed()
    add = categories.add_excerpt
    taught = add(cats["Teaching"], period, "Taught a course.", 1, [], start=2021, end=2021)
    project = add(cats["Research"], period, "Led a project.", 1, [], start=2021, end=2023)
    prize = add(cats["Research"], period, "A prize.", 1, [], end=2022)  # (its last year only)
    of_paper = add(cats["Research"], period, "A result.", 2, [], publication_id=paper)
    undated = add(cats["Research"], period, "A committee.", 1, [])
    merged = add(cats["Research"], period, "Then renewed.", 1, [], start=2015, end=2015)
    categories.merge_excerpts(merged, project)  # (a group: placed by its lead's years)

    dated, without = categories.timeline(period)
    # The most recent first; within a year, in the order of the categories.
    assert [(year, [d.excerpt.id for d in items]) for year, items in dated] == [
        (2022, [prize]),
        (2021, [project, taught]),
        (2018, [of_paper]),
    ]
    assert [d.paper_year for _y, items in dated for d in items] == [False, False, False, True]
    assert [e.id for e in without] == [undated]
    # (the group: its lead, with the other)
    assert [x.id for x in dated[1][1][0].excerpt.members] == [merged]


def test_timeline_empty() -> None:
    _pid, period, _cats, _paper = _seed()
    assert categories.timeline(period) == ([], [])


async def test_timeline_tab(user: User) -> None:
    pid, period, cats, _paper = _seed()
    dated = categories.add_excerpt(cats["Research"], period, "Led a project.", 1, [], start=2021)
    undated = categories.add_excerpt(cats["Teaching"], period, "Taught a course.", 1, [])
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline")
    await user.should_see("Led a project.", marker="timeline-2021")
    # Without years: said so, apart (not hidden).
    await user.should_see("1 excerpt without years", marker="timeline-undated")
    await user.should_see("Taught a course.", marker="timeline-undated")
    # Clicked: shown in the Excerpts tab.
    user.find(marker=f"timeline-excerpt-{dated}").click()
    await user.should_see(marker="excerpts-side")
    tab = user.find(marker="tab-excerpts").elements.pop()
    assert tab.parent_slot.parent.value == "excerpts"
    assert undated


async def test_timeline_tab_without_excerpts(user: User) -> None:
    pid, period, _cats, _paper = _seed()
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-empty")
    # Not within a folder: said so.
    other = make_person("Bob Sample")
    await user.open(f"/person/{other}?tab=timeline")
    await user.should_see(marker="documents-no-period")
