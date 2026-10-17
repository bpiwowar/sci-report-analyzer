"""A person's career (their CV): positions, diplomas, the current position, [[cv]]."""

import asyncio
from datetime import date

import pytest
from helpers import add_source, make_person
from nicegui.testing import User

from sci_report_analyzer import career, folders, pubview, reports
from sci_report_analyzer.sources.base import FetchedThesis

pytestmark = pytest.mark.nicegui_main_file("tests/app_main.py")


def _cv(pid: int) -> None:
    career.save(pid, career.Entry("position", "Postdoc", "2018-10", "2020", "Inria"))
    career.save(
        pid,
        career.Entry("diploma", "PhD", "2015", "2018-09", "Sorbonne University", "Neural ranking"),
    )
    career.save(pid, career.Entry("position", " Associate professor ", "2020-09", None, "ISIR"))


def test_entries_and_current():
    pid = make_person()
    assert career.current(pid) is None
    _cv(pid)
    rows = career.entries(pid)
    assert [e.title for e in rows] == ["Associate professor", "Postdoc", "PhD"]
    assert [e.when() for e in rows] == ["since 2020-09", "2018-10–2020", "2015–2018-09"]
    now = career.current(pid)
    assert now.line() == "Associate professor, ISIR"
    # Ended: no current position any more.
    now.end = "2024"
    career.save(pid, now)
    assert career.current(pid) is None
    career.delete(now.id)
    assert len(career.entries(pid)) == 2


def test_years_since():
    """The years since a line (“+12”), if asked for: a diploma's since obtained, a
    position's since started."""
    today = date(2026, 10, 17)
    phd = career.Entry("diploma", "PhD", "2011", "2014-06", elapsed=True)
    assert (phd.years(today), phd.plus(today)) == (12, "+12")
    grade = career.Entry("position", "Professor", "2020-09", None, elapsed=True)
    assert grade.plus(today) == "+6"
    assert career.Entry("position", "Postdoc", "2018", "2020").plus(today) == ""
    assert career.Entry("position", "Visitor", elapsed=True).plus(today) == ""  # (no date)
    pid = make_person()
    career.save(pid, phd)
    assert career.entries(pid)[0].elapsed


def test_valid_date():
    assert all(map(career.valid_date, ["", None, "2019", "2019-09", "2019-09-30"]))
    assert not any(map(career.valid_date, ["19", "2019-13", "Sept. 2019", "2019/09"]))


def test_own_thesis_suggested():
    pid = make_person()
    own = FetchedThesis(
        "t1", "author", "On ranking", defence_date="2018-09-12", institution="Paris 6"
    )
    add_source(pid, "thesesfr", "1", theses=[own])
    (t,) = career.own_theses(pid)
    assert (t.kind, t.end, t.institution, t.details) == (
        "diploma",
        "2018-09-12",
        "Paris 6",
        "On ranking",
    )
    assert t.elapsed  # (the years since the PhD, by default)
    career.save(pid, t)
    assert career.own_theses(pid) == []  # (once added)


def test_cv_block():
    pid = make_person()
    _cv(pid)
    fid = folders.save_folder(None, "Committee")
    period = folders.add_person(fid, pid)
    stats = asyncio.run(pubview.load_stats(pid))
    r = reports.render("## CV\n[[cv]]", reports.folder_context(stats, period))
    assert r.text == (
        "## CV\n"
        "- **since 2020-09**: Associate professor, ISIR\n"
        "- **2018-10–2020**: Postdoc, Inria\n"
        "- **2015–2018-09**: PhD, Sorbonne University — *Neural ranking*"
    )
    phd = career.entries(pid)[-1]
    phd.elapsed = True
    career.save(pid, phd)
    r = reports.render("[[cv]]", reports.folder_context(stats, period))
    since = date.today().year - 2018
    assert f"- **2015–2018-09 (+{since})**: PhD, Sorbonne University" in r.text


async def test_career_ui(user: User) -> None:
    pid = make_person()
    await user.open(f"/person/{pid}?tab=sources")
    await user.should_see("No position or diploma yet", retries=5)
    user.find(marker="career-add").click()
    user.find(marker="career-title").type("Associate professor")
    user.find(marker="career-institution").type("ISIR")
    user.find(marker="career-start").type("Sept 2020")
    user.find(marker="career-save").click()
    await user.should_see("Dates as 2019, 2019-09 or 2019-09-01")
    user.find(marker="career-start").clear().type("2020-09")
    user.find(marker="career-elapsed").click()
    user.find(marker="career-save").click()
    await user.should_see(marker="current-position")  # (the page reloaded: in the header)
    await user.should_see("since 2020-09")
    (e,) = career.entries(pid)
    assert (e.title, e.institution, e.current) == ("Associate professor", "ISIR", True)
    assert e.elapsed
    await user.should_see(f"+{date.today().year - 2020}")
    user.find(marker=f"career-edit-{e.id}").click()
    user.find(marker="career-delete").click()
    await user.should_not_see(marker="current-position")
    assert career.entries(pid) == []
