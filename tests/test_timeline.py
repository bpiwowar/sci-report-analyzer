"""A person's timeline: their excerpts in a folder placed by their years (categories.timeline),
their career, supervised theses and papers (timeline), and its tab on the person's page."""

from dataclasses import replace
from datetime import date

import pytest
from helpers import add_source, make_person, pub, thesis
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import career, categories, folders, timeline
from sci_report_analyzer.career import Entry
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
    # Nor career, nor theses: said so (the paper of 2018 still on it).
    await user.should_see(marker="timeline-none-career")
    await user.should_see(marker="timeline-none-theses")
    await user.should_see("1 paper", marker="timeline-papers-2018")
    # Not within a folder: said so.
    other = make_person("Bob Sample")
    await user.open(f"/person/{other}?tab=timeline")
    await user.should_see(marker="documents-no-period")


def test_career_spans() -> None:
    pid = make_person("Cy Placeholder")
    for e in (
        Entry("diploma", "PhD", "2010", "2013-09-30"),
        Entry("position", "Postdoc", "2013", "2015"),
        Entry("position", "Lecturer", "2015-09"),  # (current: until this year)
        Entry("position", "Visiting", "2018", "2018"),  # (overlapping: beside it)
        Entry("position", "Consultant"),  # (no date)
    ):
        career.save(pid, e)
    spans, undated = timeline.career_spans(pid, today=date(2026, 5, 1))
    assert [(s.entry.title, s.first, s.last, s.lane) for s in spans] == [
        ("Lecturer", 2015, 2026, 0),
        ("Visiting", 2018, 2018, 1),
        ("Postdoc", 2013, 2015, 1),  # (2015: the Lecturer's too)
        ("PhD", 2010, 2013, 0),  # (2013: the Postdoc's, in the other lane)
    ]
    assert [e.title for e in undated] == ["Consultant"]


def test_supervised_and_papers() -> None:
    pid = make_person("Dee Placeholder")
    done = replace(
        thesis("t1", "director", "On things"),
        start_date="2017-10-01",
        defence_date="2020-12-01",
        status="soutenue",
    )
    running = replace(
        thesis("t2", "director", "On more"), student="B Student", start_date="2023-10-01"
    )
    jury = replace(thesis("t3", "rapporteur", "Elsewhere"), defence_date="2021-01-01")
    add_source(pid, "thesesfr", "1", theses=[done, running, jury])
    got = [
        (t.first, t.last, t.student, t.years, t.in_progress)
        for t in timeline.supervised(pid, today=date(2025, 3, 1))
    ]  # (a span: from its start to its defence; in progress, to this year)
    assert got == [
        (2023, 2025, "B Student", "2023", True),
        (2017, 2020, "A Student", "2017–2020", False),
    ]
    add_source(pid, "dblp", "x/2", [pub("a", "Alpha", 2019), pub("b", "Beta", 2019)])
    assert timeline.papers_by_year(pid) == {2019: ["Alpha", "Beta"]}


async def test_timeline_tab_career_and_theses(user: User) -> None:
    pid, period, cats, _paper = _seed()
    lead = career.save(pid, Entry("position", "Lecturer", "2015"))
    career.save(pid, Entry("position", "Mentor"))  # (no date: listed apart)
    done = replace(thesis("t1", "director", "On things"), defence_date="2016-06-01")
    add_source(pid, "thesesfr", "1", theses=[done])
    categories.add_excerpt(cats["Research"], period, "Led a project.", 1, [], start=2021)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see("Lecturer", marker=f"timeline-career-{lead}")
    await user.should_see("On things", marker="timeline-2016")
    await user.should_see(marker="timeline-career-undated")
    # Every year a tick, even without anything (the gaps seen); the folder's period shaded.
    await user.should_see(marker="timeline-tick-2017")
    await user.should_see(marker="timeline-period")
    # A kind hidden: off the timeline.
    user.find(marker="timeline-show-career").click()
    await user.should_not_see(marker=f"timeline-career-{lead}")
    await user.should_see("Led a project.", marker="timeline-2021")


def test_lanes_from_a_start() -> None:
    """Spans side by side when they overlap, in lanes from ``start`` on (the theses: beyond
    the career's)."""
    spans = [
        timeline.Supervised(1, 2020, "A", "", "", False, 2017, 2020),
        timeline.Supervised(2, 2022, "B", "", "", False, 2019, 2022),
        timeline.Supervised(3, 2016, "C", "", "", False, 2013, 2016),
    ]
    assert timeline.lanes(spans, start=2) == 4
    assert [t.lane for t in spans] == [3, 2, 2]
