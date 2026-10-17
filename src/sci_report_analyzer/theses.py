"""PhD theses: their start and end (defence), whether they fall in a period, and where to
look for what became of the students; the PhD students edited by hand (their dates, added,
hidden)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import Select, and_, or_, select

from .db.models import SourceLink, Thesis
from .db.session import session_scope
from .source_settings import active_links
from .sources.base import to_year

# A French PhD lasts about three years: the start of a defended thesis, when unknown.
TYPICAL_YEARS = 3
# Roles exercised at the defence only (the jury), rather than during the whole thesis.
JURY_ROLES = {"rapporteur", "examiner", "president"}


@dataclass
class Span:
    start: str | None  # a date (YYYY-MM-DD) or, when estimated, a year
    end: str | None  # the defence date; None while in progress
    start_estimated: bool = False

    @property
    def start_year(self) -> int | None:
        return to_year(self.start)

    @property
    def end_year(self) -> int | None:
        return to_year(self.end)


def span(t: Thesis) -> Span:
    """Its dates (those set by hand first)."""
    start, end = t.start_override or t.start_date, t.end_override or t.defence_date
    if start:
        return Span(start, end)
    if year := to_year(end):
        return Span(str(year - TYPICAL_YEARS), end, start_estimated=True)
    return Span(None, None)


def in_period(t: Thesis, lo: int | None, hi: int | None) -> bool:
    """Whether the thesis falls in the years [lo, hi]: its defence for a jury role, else
    any of its years (still running when in progress)."""
    sp = span(t)
    if t.role in JURY_ROLES:
        first = last = sp.end_year or sp.start_year
    else:
        first = sp.start_year or sp.end_year
        last = sp.end_year or (dt.date.today().year if t.status != "soutenue" else first)
    if first is None:
        return True  # (undated: kept)
    return (lo is None or last >= lo) and (hi is None or first <= hi)


def search_links(student: str) -> list[dict[str, str]]:
    """Web searches to find out what became of a PhD student (one set per name, when the
    thesis has several students)."""
    links = []
    for name in (n for n in student.split(", ") if n):
        q = quote_plus(name)
        links += [
            {
                "label": "LinkedIn",
                "name": name,
                "url": f"https://www.linkedin.com/search/results/people/?keywords={q}",
            },
            {
                "label": "Google",
                "name": name,
                "url": "https://www.google.com/search?q=" + quote_plus(f'"{name}"'),
            },
            {
                "label": "Scholar",
                "name": name,
                "url": f"https://scholar.google.com/citations?view_op=search_authors&mauthors={q}",
            },
        ]
    return links


# ---- edited by hand -------------------------------------------------------------------------
# The theses of a source keep what is set by hand over its updates (their dates, hidden); a
# PhD student added by hand is a thesis without a source, of the person (``Thesis.added``).


def of_person(person_id: int, hidden: bool = False) -> Select:
    """Query of the person's theses: of their validated, used sources, and added by hand;
    ``hidden``: those removed by hand too."""
    query = (
        select(Thesis)
        .outerjoin(SourceLink)
        .where(
            or_(
                and_(SourceLink.person_id == person_id, active_links()),
                Thesis.person_id == person_id,
            )
        )
    )
    return query if hidden else query.where(~Thesis.hidden)


@dataclass
class Student:
    """A supervised PhD student as typed (dates: a year, YYYY-MM or YYYY-MM-DD)."""

    student: str
    title: str | None = None
    start: str | None = None
    end: str | None = None
    supervisors: list[str] | None = None
    institution: str | None = None


def save_student(person_id: int, st: Student, row_id: int | None = None) -> int:
    """Add a PhD student by hand (``row_id``: edit one added by hand); its id."""
    with session_scope() as s:
        t = s.get(Thesis, row_id) if row_id else None
        if t is None:
            t = Thesis(
                person_id=person_id, thesis_id=f"manual:{uuid.uuid4().hex[:12]}", role="director"
            )
            s.add(t)
        t.student = st.student.strip()
        t.title = (st.title or "").strip() or None
        t.start_date = (st.start or "").strip() or None
        t.defence_date = (st.end or "").strip() or None
        t.status = "soutenue" if t.defence_date else "enCours"
        t.supervisors = [n.strip() for n in st.supervisors or [] if n.strip()]
        t.institution = (st.institution or "").strip() or None
        s.flush()
        return t.id


def set_dates(row_id: int, start: str | None, end: str | None) -> None:
    """The start and end of a thesis: of one added by hand, else set over its source's
    (empty, or the source's: back to it)."""
    start, end = (start or "").strip() or None, (end or "").strip() or None
    with session_scope() as s:
        t = s.get(Thesis, row_id)
        if t.added:
            t.start_date, t.defence_date = start, end
            t.status = "soutenue" if end else "enCours"
        else:
            t.start_override = start if start != t.start_date else None
            t.end_override = end if end != t.defence_date else None


def remove(row_id: int, comment: str | None = None) -> bool:
    """Remove a PhD student: one added by hand is deleted (True), a source's is hidden
    (with why, ``comment``)."""
    with session_scope() as s:
        t = s.get(Thesis, row_id)
        if t.added:
            s.delete(t)
            return True
        t.hidden = True
        t.comment = (comment or "").strip() or None
        return False


def restore(row_id: int) -> None:
    """Show (and count) a hidden thesis again."""
    with session_scope() as s:
        t = s.get(Thesis, row_id)
        t.hidden = False
        t.comment = None


def set_comment(row_id: int, comment: str) -> None:
    """Why a thesis is hidden."""
    with session_scope() as s:
        s.get(Thesis, row_id).comment = comment.strip() or None
