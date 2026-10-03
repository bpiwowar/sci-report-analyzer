"""PhD theses: start / end and periods."""

from dataclasses import replace

from helpers import add_source, make_person, thesis
from sqlalchemy import select

from sci_report_analyzer import sync, theses
from sci_report_analyzer.db.models import Thesis
from sci_report_analyzer.db.session import session_scope
from sci_report_analyzer.sources.base import FetchResult


def _t(role="director", start=None, defence=None, status=None) -> Thesis:
    return Thesis(role=role, start_date=start, defence_date=defence, status=status)


def test_span() -> None:
    sp = theses.span(_t(start="2021-10-01", defence="2024-12-12", status="soutenue"))
    assert (sp.start, sp.end, sp.start_estimated) == ("2021-10-01", "2024-12-12", False)
    # theses.fr does not give the start of a defended thesis: about 3 years before.
    sp = theses.span(_t(defence="2024-12-12", status="soutenue"))
    assert (sp.start, sp.start_year, sp.end_year, sp.start_estimated) == ("2021", 2021, 2024, True)
    sp = theses.span(_t(start="2023-10-01", status="enCours"))
    assert (sp.start_year, sp.end) == (2023, None)
    assert theses.span(_t()).start is None


def test_in_period() -> None:
    supervised = _t(start="2018-10-01", defence="2022-06-01", status="soutenue")
    assert theses.in_period(supervised, 2020, 2021)  # overlaps
    assert theses.in_period(supervised, 2022, None)
    assert not theses.in_period(supervised, 2023, 2026)
    assert not theses.in_period(supervised, None, 2017)
    # A jury: the defence only.
    jury = _t("rapporteur", start="2018-10-01", defence="2022-06-01", status="soutenue")
    assert not theses.in_period(jury, 2020, 2021)
    assert theses.in_period(jury, 2022, 2022)
    # In progress: up to now.
    running = _t(start="2020-10-01", status="enCours")
    assert theses.in_period(running, 2025, None)
    assert not theses.in_period(running, None, 2019)
    assert theses.in_period(_t(), 2000, 2001)  # undated: kept


def test_start_kept_once_defended() -> None:
    pid = make_person()
    started = replace(thesis("s123", "director", "T"), start_date="2021-10-01", status="enCours")
    link = add_source(pid, "thesesfr", "123456789", theses=[started])
    # Defended: a new id (NNT), and no start any more.
    defended = replace(
        thesis("2024PA000001", "director", "T"), defence_date="2024-12-12", status="soutenue"
    )
    other = replace(thesis("2024PA000002", "director", "U"), student="B Student")
    sync.finish_link(link, FetchResult(theses=[defended, other]))
    with session_scope() as s:
        got = {t.thesis_id: t.start_date for t in s.scalars(select(Thesis))}
    assert got == {"2024PA000001": "2021-10-01", "2024PA000002": None}
    # Kept on the next syncs too.
    sync.finish_link(link, FetchResult(theses=[defended]))
    with session_scope() as s:
        assert [t.start_date for t in s.scalars(select(Thesis))] == ["2021-10-01"]


def test_search_links() -> None:
    links = theses.search_links("Alice Martin, Bob Li")
    assert [(lk["label"], lk["name"]) for lk in links][:3] == [
        ("LinkedIn", "Alice Martin"),
        ("Google", "Alice Martin"),
        ("Scholar", "Alice Martin"),
    ]
    assert len(links) == 6
    assert links[1]["url"] == "https://www.google.com/search?q=%22Alice+Martin%22"
