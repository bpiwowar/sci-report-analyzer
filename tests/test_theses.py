"""PhD theses: start / end and periods."""

from dataclasses import replace

from helpers import add_source, make_person, thesis
from sqlalchemy import select

from sci_report_analyzer import persons, sync, theses
from sci_report_analyzer.db.models import Person, Thesis
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


# ---- edited by hand -------------------------------------------------------------------------


def _supervised(tid: str, student: str, **kw):
    return replace(thesis(tid, "director", f"Thesis of {student}"), student=student, **kw)


def _row(pid: int, student: str) -> Thesis:
    return next(t for t in persons.theses(pid, hidden=True) if t.student == student)


def test_span_set_by_hand() -> None:
    t = _t(start="2021-10-01", defence="2024-12-12", status="soutenue")
    t.start_override, t.end_override = "2021-09", "2025-01-15"
    assert theses.span(t) == theses.Span("2021-09", "2025-01-15")
    # An end set over a thesis in progress: within the periods up to then only.
    running = _t(start="2020-10-01", status="enCours")
    running.end_override = "2023-06-30"
    assert not theses.in_period(running, 2024, None)


def test_dates_kept_on_updates() -> None:
    pid = make_person()
    ongoing = _supervised("s1", "Carl Sample", start_date="2021-10-01", status="enCours")
    link = add_source(pid, "thesesfr", "123456789", theses=[ongoing])
    theses.set_dates(_row(pid, "Carl Sample").id, "2021-11", "")
    t = _row(pid, "Carl Sample")
    assert (t.start_override, t.end_override, t.start_date) == ("2021-11", None, "2021-10-01")
    sync.finish_link(link, FetchResult(theses=[ongoing]))
    assert theses.span(_row(pid, "Carl Sample")).start == "2021-11"
    # Defended: a new id on theses.fr, the dates set by hand carried over.
    defended = _supervised("2024PA000009", "Carl Sample", defence_date="2024-12-12")
    sync.finish_link(link, FetchResult(theses=[defended]))
    t = _row(pid, "Carl Sample")
    assert (t.thesis_id, t.start_override) == ("2024PA000009", "2021-11")
    assert theses.span(t) == theses.Span("2021-11", "2024-12-12")
    # The source's own dates: back to them.
    theses.set_dates(t.id, "2021-10-01", "2024-12-12")
    t = _row(pid, "Carl Sample")
    assert (t.start_override, t.end_override) == (None, None)


def test_hidden_kept_and_not_counted() -> None:
    from sci_report_analyzer.pubview import PeopleIndex

    pid = make_person()
    rows = [_supervised("s1", "Carl Sample"), _supervised("s2", "Dana Placeholder")]
    link = add_source(pid, "thesesfr", "123456789", theses=rows)
    assert theses.remove(_row(pid, "Dana Placeholder").id, " Not a student of hers ") is False
    sync.finish_link(link, FetchResult(theses=rows))  # (fetched again: still hidden)
    t = _row(pid, "Dana Placeholder")
    assert (t.hidden, t.comment) == (True, "Not a student of hers")
    assert persons.students(pid) == ["Carl Sample"]
    assert [t.student for t in persons.theses(pid)] == ["Carl Sample"]
    with session_scope() as s:
        people = PeopleIndex.for_person(s, s.get(Person, pid))
        assert [st.name for st in people.students] == ["Carl Sample"]
    theses.set_comment(t.id, "Co-supervised elsewhere")
    assert _row(pid, "Dana Placeholder").comment == "Co-supervised elsewhere"
    theses.restore(t.id)
    t = _row(pid, "Dana Placeholder")
    assert (t.hidden, t.comment) == (False, None)
    assert persons.students(pid) == ["Carl Sample", "Dana Placeholder"]


def test_student_added_by_hand() -> None:
    pid = make_person()
    add_source(pid, "thesesfr", "123456789", theses=[_supervised("s1", "Carl Sample")])
    row_id = theses.save_student(
        pid,
        theses.Student(
            " Eve Madeup ", "On made-up things", "2019-10", "", [" Jane Doe", " Al Other", ""]
        ),
    )
    assert persons.students(pid) == ["Carl Sample", "Eve Madeup"]
    t = _row(pid, "Eve Madeup")
    assert (t.added, t.role, t.status, t.supervisors) == (
        True,
        "director",
        "enCours",
        ["Jane Doe", "Al Other"],
    )
    theses.set_dates(row_id, "2019-10", "2023-03-01")
    t = _row(pid, "Eve Madeup")
    assert (t.start_date, t.defence_date, t.status, t.end_override) == (
        "2019-10",
        "2023-03-01",
        "soutenue",
        None,
    )
    theses.save_student(pid, theses.Student("Eve Madeup", "Renamed", "2019", "2023"), row_id)
    assert _row(pid, "Eve Madeup").title == "Renamed"
    # Removed: deleted (not hidden).
    assert theses.remove(row_id) is True
    assert persons.students(pid) == ["Carl Sample"]
    assert len(persons.theses(pid, hidden=True)) == 1
    # Without any theses.fr profile too; gone with the person.
    other = make_person("Ann Example")
    theses.save_student(other, theses.Student("Finn Fictive"))
    assert persons.students(other) == ["Finn Fictive"]
    with session_scope() as s:
        s.delete(s.get(Person, other))
    with session_scope() as s:
        assert s.scalars(select(Thesis).where(Thesis.person_id == other)).all() == []
