"""A person's timeline: their excerpts in a folder placed by their years (categories.timeline),
their career, supervised theses and papers (timeline), and its tab on the person's page."""

import asyncio
from dataclasses import replace
from datetime import date

import pytest
from helpers import PDF, add_source, make_person, pub, thesis
from nicegui.testing import User
from sqlalchemy import select

from sci_report_analyzer import career, categories, documents, folders, timeline
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


async def test_timeline_tab(user: User, fake_viewer) -> None:
    pid, period, cats, _paper = _seed()
    doc = documents.add(period, "Application.pdf", PDF)
    dated = categories.add_excerpt(
        cats["Research"], period, "Led a project.", 1, [], document_id=doc, start=2021
    )
    undated = categories.add_excerpt(cats["Teaching"], period, "Taught a course.", 1, [])
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline")
    await user.should_see(marker="timeline-row-excerpts")
    # In its year, the beginning of its text; the details below, until a year's are
    # clicked: a hint.
    await user.should_see("Led a project.", marker=f"timeline-span-{dated}")
    await user.should_see(marker="timeline-details-hint")
    user.find(marker="timeline-tick-2021").click()
    await user.should_see("1 excerpt in 2021", marker="timeline-details")
    await user.should_see("Led a project.", marker="timeline-details")
    # Without years: said so, apart (not hidden).
    await user.should_see("1 excerpt without years", marker="timeline-undated")
    await user.should_see("Taught a course.", marker="timeline-undated")
    # Clicked: its PDF beside the page (the timeline still shown).
    user.find(marker=f"timeline-excerpt-{dated}").click()
    await user.should_see(marker=f"dock-frame-d{doc}")
    tab = user.find(marker="tab-timeline").elements.pop()
    assert tab.parent_slot.parent.value == "timeline"
    # Its button: seen in the Excerpts tab.
    user.find(marker=f"timeline-select-{dated}").click()
    await user.should_see(marker="excerpts-side")
    assert tab.parent_slot.parent.value == "excerpts"
    assert undated


async def test_timeline_edit_excerpt(user: User) -> None:
    """An excerpt edited from the timeline (its dialog, as in the Excerpts tab): drawn again."""
    pid, period, cats, _paper = _seed()
    dated = categories.add_excerpt(cats["Research"], period, "Led a project.", 1, [], start=2021)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    user.find(marker="timeline-tick-2021").click()
    await user.should_see(marker=f"timeline-edit-{dated}")
    user.find(marker=f"timeline-edit-{dated}").click()
    await user.should_see(marker="excerpt-edit-text")
    user.find(marker="excerpt-edit-text").clear().type("Led a big project.")
    user.find(marker="excerpt-edit-save").click()
    await user.should_see("Led a big project.", marker=f"timeline-span-{dated}")
    assert next(e for e in categories.excerpts(period) if e.id == dated).text == (
        "Led a big project."
    )
    tab = user.find(marker="tab-timeline").elements.pop()
    assert tab.parent_slot.parent.value == "timeline"


async def test_timeline_tab_ranges(user: User) -> None:
    """An excerpt along several years: a bar along them, in the details clicked (and in
    those of each of its years)."""
    pid, period, cats, _paper = _seed()
    add = categories.add_excerpt
    project = add(cats["Research"], period, "Led a project.", 1, [], start=2019, end=2022)
    taught = add(cats["Teaching"], period, "Taught a course.", 1, [], start=2020, end=2020)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker=f"timeline-span-{project}")
    await user.should_see("Taught a course.", marker=f"timeline-span-{taught}")
    # A row for each main category (named after it).
    await user.should_see("Research", marker=f"timeline-row-main-{cats['Research']}")
    await user.should_see("Teaching", marker=f"timeline-row-main-{cats['Teaching']}")
    await user.should_see(marker="timeline-papers-ranked")  # (drawn again, once ranked)
    chart = user.find(marker="timeline-chart").elements.pop()
    user.find(marker=f"timeline-span-{project}").click()
    await user.should_see("An excerpt, 2019–2022", marker="timeline-details")
    await user.should_see("Led a project.", marker="timeline-details")
    # Picked: marked on the chart, the chart kept as it is (its scroll too).
    span = user.find(marker=f"timeline-span-{project}").elements.pop()
    assert "picked" in span.classes
    assert user.find(marker="timeline-chart").elements.pop() is chart
    # A year clicked: its excerpts, those along it too.
    user.find(marker="timeline-tick-2020").click()
    await user.should_see("2 excerpts in 2020", marker="timeline-details")
    await user.should_see("Taught a course.", marker="timeline-details")
    await user.should_see("Led a project.", marker="timeline-details")
    assert "picked" not in span.classes and not chart.is_deleted
    # Closed: the hint again.
    user.find(marker="timeline-details-close").click()
    await user.should_see(marker="timeline-details-hint")


async def test_timeline_tab_without_excerpts(user: User) -> None:
    pid, period, _cats, _paper = _seed()
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-empty")
    # Nor career, nor theses: said so (the paper of 2018 still on it).
    await user.should_see(marker="timeline-none-career")
    await user.should_see(marker="timeline-none-theses")
    await user.should_see(marker="timeline-row-papers")
    await user.should_see("1 paper in 2018", marker="timeline-papers-2018")
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


def test_career_spans_by_month() -> None:
    """A position ending in September and the next one starting in October: in one lane
    (their months, not only their years)."""
    pid = make_person("Ed Placeholder")
    for e in (
        Entry("position", "Researcher", "2018-10-01", "2024-09-30"),
        Entry("position", "Senior researcher", "2024-10"),
    ):
        career.save(pid, e)
    spans, _undated = timeline.career_spans(pid, today=date(2026, 5, 1))
    got = [(s.entry.title, round(s.begin, 2), round(s.stop, 2), s.lane) for s in spans]
    assert got == [("Senior researcher", 2024.75, 2027.0, 0), ("Researcher", 2018.75, 2024.75, 0)]


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
    (directed,) = timeline.supervised(pid)
    categories.add_excerpt(cats["Research"], period, "Led a project.", 1, [], start=2021)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-row-career")
    await user.should_see(marker="timeline-row-theses")
    await user.should_see("Lecturer", marker=f"timeline-career-{lead}")
    # A PhD: its student's name on its bar, its title hovered.
    await user.should_see("A Student", marker=f"timeline-thesis-{directed.id}")
    await user.should_see("On things", marker=f"timeline-thesis-{directed.id}")
    await user.should_see(marker="timeline-career-undated")
    # Every year a column, even without anything (the gaps seen); the folder's period shaded.
    await user.should_see(marker="timeline-tick-2017")
    await user.should_see(marker="timeline-period")
    # Clicked: all about it below, to edit (its dialog), or to see in its tab.
    user.find(marker=f"timeline-career-{lead}").click()
    await user.should_see(marker="timeline-line-edit")
    user.find(marker="timeline-line-edit").click()
    await user.should_see("Edit the line")
    user.find(marker="career-title").clear().type("Senior lecturer")
    user.find("Save").click()
    await user.should_see("Senior lecturer", marker=f"timeline-career-{lead}")  # (drawn again)
    user.find(marker=f"timeline-thesis-{directed.id}").click()
    await user.should_see(marker=f"timeline-line-thesis-{directed.id}")
    user.find(marker="timeline-line-edit").click()
    await user.should_see("Edit the PhD student")
    user.find("Cancel").click()
    user.find(marker="timeline-line-tab").click()
    tab = user.find(marker="tab-theses").elements.pop()
    assert tab.parent_slot.parent.value == "theses"
    user.find(marker="tab-timeline").click()
    await user.should_see(marker=f"timeline-career-{lead}")
    # Zoomed in at first (the years twice as wide as fitting the width: scrolled); the
    # slider released, out to fitting it (no further), kept once drawn again.
    slider = user.find(marker="timeline-zoom").elements.pop()
    assert slider.value == 2
    assert _zoom(user) == 2
    user.find(marker="timeline-zoom").trigger("change", 0.5)
    for _i in range(50):  # (drawn again: the room for the labels changed)
        if _zoom(user) == 1:
            break
        await asyncio.sleep(0.02)
    assert _zoom(user) == 1
    # A kind hidden: off the timeline.
    user.find(marker="timeline-show-career").click()
    await user.should_not_see(marker=f"timeline-career-{lead}")
    await user.should_not_see(marker="timeline-row-career")
    await user.should_see(marker="timeline-row-excerpts")
    assert _zoom(user) == 1


def _zoom(user: User) -> float:
    """That of the chart shown (its years' width, see timeline.ZOOM_MIN)."""
    chart = user.find(marker="timeline-chart").elements.pop()
    grid = next(c for c in chart.descendants() if "vr-tl" in c.classes)
    return float(grid.style["--tl-zoom"])


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


def test_lanes_with_room() -> None:
    """Each span taking at least ``room`` years (its label's): the next one in its lane not
    before."""
    spans = [
        timeline.Supervised(1, 2020, "A", "", "", False, 2020, 2020),
        timeline.Supervised(2, 2018, "B", "", "", False, 2018, 2018),
        timeline.Supervised(3, 2015, "C", "", "", False, 2015, 2015),
    ]
    assert timeline.lanes(spans) == 1
    assert timeline.lanes(spans, room=3) == 2
    assert [t.lane for t in spans] == [0, 1, 0]


async def test_timeline_excerpt_left_out(user: User) -> None:
    """An excerpt's switch off: not on the timeline, listed apart (put back from there); its
    button in the Excerpts tab too."""
    pid, period, cats, _paper = _seed()
    add = categories.add_excerpt
    kept = add(cats["Research"], period, "Led a project.", 1, [], start=2021)
    out = add(cats["Teaching"], period, "Taught a course.", 1, [], start=2022)
    categories.set_in_timeline(out, False)
    left: list[categories.ExcerptView] = []
    dated, _undated = categories.timeline(period, left)
    assert [d.excerpt.id for _y, items in dated for d in items] == [kept]
    assert [e.id for e in left] == [out]

    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker=f"timeline-span-{kept}")
    await user.should_not_see(marker=f"timeline-span-{out}")
    await user.should_see("1 excerpt left out of the timeline", marker="timeline-left-out")
    user.find(marker=f"timeline-switch-{out}").elements.pop().set_value(True)
    await user.should_see(marker=f"timeline-span-{out}")
    await user.should_not_see(marker="timeline-left-out")
    # Off from the details of its year.
    user.find(marker="timeline-tick-2021").click()
    await user.should_see(marker=f"timeline-switch-{kept}")
    user.find(marker=f"timeline-switch-{kept}").elements.pop().set_value(False)
    await user.should_see(marker="timeline-left-out")
    await user.should_not_see(marker=f"timeline-span-{kept}")
    # Off when shown alone in the details: they closed.
    user.find(marker=f"timeline-switch-{kept}").elements.pop().set_value(True)
    await user.should_see(marker=f"timeline-span-{kept}")
    user.find(marker="timeline-details-close").click()
    await user.should_see(marker="timeline-details-hint")
    user.find(marker=f"timeline-span-{kept}").click()
    await user.should_not_see(marker="timeline-details-hint")
    user.find(marker=f"timeline-switch-{kept}").elements.pop().set_value(False)
    await user.should_see(marker="timeline-details-hint")
    assert not next(e for e in categories.excerpts(period) if e.id == kept).in_timeline
    # From the Excerpts tab: back on it.
    await user.open(f"/person/{pid}/{period}?tab=excerpts")
    user.find(marker=f"excerpt-timeline-{kept}").click()
    await user.should_see(marker="excerpts-side")
    assert next(e for e in categories.excerpts(period) if e.id == kept).in_timeline


async def test_timeline_full_width(user: User, fake_viewer) -> None:
    """The timeline of the full width: the PDF shown set aside, shown again after."""
    pid, period, _cats, _paper = _seed()
    doc = documents.add(period, "Application.pdf", PDF)
    await user.open(f"/person/{pid}/{period}?tab=report&pdfs=d{doc}&pdf=d{doc}")
    await user.should_see(marker=f"dock-frame-d{doc}")
    user.find(marker="tab-timeline").click()
    await user.should_see(marker="timeline")
    await user.should_not_see(marker=f"dock-frame-d{doc}")
    await user.should_see(marker=f"dock-tab-d{doc}")  # (still in the strip)
    user.find(marker="tab-report").click()
    await user.should_see(marker=f"dock-frame-d{doc}")
    # Opened there on the timeline: set aside too.
    await user.open(f"/person/{pid}/{period}?tab=timeline&pdfs=d{doc}&pdf=d{doc}")
    await user.should_see(marker="timeline")
    await user.should_not_see(marker=f"dock-frame-d{doc}")


async def test_timeline_filters(user: User) -> None:
    """The excerpts filtered by markers and levels (several of a kind: one of them; both
    kinds: both); how many are hidden said, cleared at once."""
    from sci_report_analyzer import reports

    pid, period, cats, _paper = _seed()
    fid = folders.folders()[0].id
    reports.save_levels(fid, [reports.Level(k, k) for k in reports.DEFAULT_LEVELS], "national")
    add = categories.add_excerpt
    talk = add(cats["Research"], period, "Gave a talk.", 1, [], start=2021, influence=True)
    prize = add(
        cats["Research"],
        period,
        "Won a prize.",
        1,
        [],
        start=2022,
        salient=True,
        level="international",
    )
    course = add(cats["Teaching"], period, "Taught a course.", 1, [], start=2021, level="local")
    undated = add(cats["Teaching"], period, "A committee.", 1, [], salient=True)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker=f"timeline-span-{course}")
    await user.should_see(marker="timeline-filters")
    await user.should_not_see(marker="timeline-filter-hidden")
    # Salient or influence: the course hidden (said so).
    user.find(marker="timeline-filter-marker-salient").trigger("update:selected", True)
    user.find(marker="timeline-filter-marker-influence").trigger("update:selected", True)
    await user.should_not_see(marker=f"timeline-span-{course}")
    await user.should_see(marker=f"timeline-span-{talk}")
    await user.should_see(marker=f"timeline-span-{prize}")
    await user.should_see("A committee.", marker="timeline-undated")
    await user.should_see("1 excerpt hidden by the filters", marker="timeline-filter-hidden")
    # And national (the talk: without a level, the folder's default one).
    user.find(marker="timeline-filter-level-national").trigger("update:selected", True)
    await user.should_not_see(marker=f"timeline-span-{prize}")
    await user.should_see(marker=f"timeline-span-{talk}")
    await user.should_see("2 excerpts hidden by the filters", marker="timeline-filter-hidden")
    # (the committee, without a level too: national)
    await user.should_see("A committee.", marker="timeline-undated")
    # Kept once drawn again (a kind hidden, shown again).
    user.find(marker="timeline-show-papers").click()
    await user.should_not_see(marker=f"timeline-span-{prize}")
    user.find(marker="timeline-filter-clear").click()
    await user.should_see(marker=f"timeline-span-{prize}")
    await user.should_see(marker=f"timeline-span-{course}")
    await user.should_not_see(marker="timeline-filter-hidden")
    assert undated
    # A folder without levels: said so.
    reports.save_levels(fid, [])
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-filter-no-levels")


async def test_timeline_open_ended_one_off_within_period(user: User) -> None:
    """An excerpt since a year: up to now (fading); a one-off event: a dot at its year;
    within the period: along it (its own years, if any), dotted; the period without years:
    listed without years, said so."""
    pid, period, cats, _paper = _seed()
    add = categories.add_excerpt
    since = add(cats["Research"], period, "Leads a team.", 1, [], start=2021)
    talk = add(cats["Research"], period, "Gave a talk.", 1, [], start=2022, punctual=True)
    sometime = add(cats["Teaching"], period, "Taught a course.", 1, [], in_period=True)
    # (both asked: one-off only)
    both = add(
        cats["Teaching"], period, "Chaired.", 1, [], start=2023, punctual=True, in_period=True
    )
    views = {e.id: e for e in categories.excerpts(period)}
    assert views[talk].punctual and not views[talk].in_period
    assert views[sometime].in_period and not views[both].in_period
    dated, undated = categories.timeline(period)
    placed = {d.excerpt.id: d for _y, items in dated for d in items}
    assert placed[sometime].period == (2020, 2024) and placed[sometime].year == 2020
    assert not undated

    await user.open(f"/person/{pid}/{period}?tab=timeline")
    span = user.find(marker=f"timeline-span-{since}").elements.pop()
    assert "now" in span.classes and "dot" not in span.classes
    dot = user.find(marker=f"timeline-span-{talk}").elements.pop()
    assert "dot" in dot.classes and "now" not in dot.classes
    approx = user.find(marker=f"timeline-span-{sometime}").elements.pop()
    assert "approx" in approx.classes
    await user.should_see(marker="timeline-legend-approx")
    user.find(marker=f"timeline-span-{since}").click()
    await user.should_see("An excerpt, since 2021", marker="timeline-details")
    user.find(marker=f"timeline-span-{sometime}").click()
    await user.should_see("within the period (2020–2024)", marker=f"timeline-years-{sometime}")
    user.find(marker=f"timeline-span-{talk}").click()
    await user.should_see(marker=f"timeline-punctual-{talk}")

    # Set from the excerpt's dialog: one on, the other off.
    user.find(marker=f"timeline-edit-{talk}").click()
    await user.should_see(marker="excerpt-in-period")
    user.find(marker="excerpt-in-period").click()
    assert not user.find(marker="excerpt-punctual").elements.pop().value
    user.find(marker="excerpt-edit-save").click()
    await user.should_see(marker=f"timeline-span-{talk}")
    talked = next(e for e in categories.excerpts(period) if e.id == talk)
    assert talked.in_period and not talked.punctual

    # A period without years: listed apart, said so.
    folders.set_period(period, None, None)
    dated, undated = categories.timeline(period)
    assert [e.id for e in undated] == [sometime]
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(
        "within the period (its years not set)", marker=f"timeline-years-{sometime}"
    )


def test_one_off_kept_by_the_next_lead() -> None:
    """The lead of a group removed: the next one takes its one-off / within-the-period flags."""
    _pid, period, cats, _paper = _seed()
    add = categories.add_excerpt
    lead = add(cats["Research"], period, "A talk.", 1, [], start=2021, punctual=True)
    other = add(cats["Research"], period, "Another talk.", 2, [])
    categories.merge_excerpts(other, lead)
    categories.remove_excerpt(lead)
    (left,) = categories.excerpts(period)
    assert left.id == other and left.punctual


async def test_timeline_juries(user: User) -> None:
    """The theses they were on the jury of: a dot at the defence (to the month), in their
    role's colour; clicked, in the details (to the Theses tab); hidden ones left out."""
    pid, period, _cats, _paper = _seed()
    rev = replace(thesis("j1", "rapporteur", "On graphs"), defence_date="2022-03-15")
    ex = replace(thesis("j2", "examiner", "On trees"), defence_date="2019")
    gone = replace(thesis("j3", "president", "On loops"), defence_date="2021-06-01")
    add_source(pid, "thesesfr", "1", theses=[rev, ex, gone])
    found = {t.title: t for t in timeline.juries(pid)}
    assert found["On graphs"].at == pytest.approx(2022 + 2.5 / 12)
    assert found["On trees"].at == 2019.5 and found["On trees"].role == "examiner"
    from sci_report_analyzer.db.models import Thesis

    with session_scope() as s:
        s.get(Thesis, found["On loops"].id).hidden = True
    assert {t.title for t in timeline.juries(pid)} == {"On graphs", "On trees"}
    jid = found["On graphs"].id

    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-row-juries")
    await user.should_see(marker="timeline-legend-jury")
    await user.should_see("A Student", marker=f"timeline-jury-{jid}")
    await user.should_not_see(marker=f"timeline-jury-{found['On loops'].id}")
    item = user.find(marker=f"timeline-jury-{jid}").elements.pop()
    assert "dot" in item.classes and "#cf222e" in item.style["--c"]
    user.find(marker=f"timeline-jury-{jid}").click()
    await user.should_see(marker=f"timeline-line-jury-{jid}")
    await user.should_see("On graphs", marker="timeline-line-details")
    await user.should_not_see(marker="timeline-line-edit")
    user.find(marker="timeline-line-tab").click()
    tab = user.find(marker="tab-theses").elements.pop()
    assert tab.parent_slot.parent.value == "theses"
    # None: said so.
    other = make_person("Bob Sample")
    fid = folders.folders()[0].id
    other_period = folders.add_person(fid, other)
    await user.open(f"/person/{other}/{other_period}?tab=timeline")
    await user.should_see(marker="timeline-none-juries")


async def test_timeline_papers_by_rank(user: User, fake_viewer) -> None:
    """A year's papers: by rank (hovered, stacked); clicked, listed below (the chart kept),
    a stored PDF opened beside the page."""
    from helpers import set_level

    from sci_report_analyzer import pdfs

    pid = make_person("Ann Example")
    papers = [
        pub("a", "On made-up things", 2022, "Journal of Made-up Studies"),
        pub("b", "On other made-up things", 2022, "Journal of Made-up Studies"),
        pub("c", "A small idea", 2022, "Imaginary Workshop"),
    ]
    add_source(pid, "dblp", "x/1", papers)
    set_level("Journal of Made-up Studies", "journal", "Q1")
    fid = folders.save_folder(None, "Hiring committee")
    period = folders.add_person(fid, pid, 2020, 2024)
    with session_scope() as s:
        ids = dict(s.execute(select(Publication.title, Publication.id)).all())
    pdfs.save(ids["On made-up things"], PDF, None)
    await user.open(f"/person/{pid}/{period}?tab=timeline")
    await user.should_see(marker="timeline-papers-ranked")
    cell = user.find(marker="timeline-papers-2022").elements.pop()
    assert (
        len(next(c for c in cell.descendants() if "vr-tl-hist" in c.classes).default_slot.children)
        == 2
    )
    chart = user.find(marker="timeline-chart").elements.pop()
    user.find(marker="timeline-papers-2022").click()
    # (an unranked workshop: its kind, as in the Publications tab)
    await user.should_see(
        "3 papers in 2022 — 2 Q1, 1 Intl. workshop", marker="timeline-papers-details"
    )
    assert "picked" in cell.classes and user.find(marker="timeline-chart").elements.pop() is chart
    await user.should_see(marker=f"timeline-paper-{ids['A small idea']}")
    # Stored: its PDF beside the page; else its links.
    user.find(marker=f"timeline-paper-{ids['On made-up things']}").click()
    await user.should_see(marker=f"dock-frame-p{ids['On made-up things']}")
    tab = user.find(marker="tab-timeline").elements.pop()
    assert tab.parent_slot.parent.value == "timeline"
    user.find(marker="timeline-papers-close").click()
    await user.should_not_see(marker="timeline-papers-details")
