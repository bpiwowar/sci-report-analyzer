"""A person's timeline: what is dated about them, by year. Their career and the theses they
supervised (spans of years, one above the other when they overlap), the theses they were on the
jury of (a moment: their defence), their excerpts in a folder (see categories.timeline) and
their papers (by year)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from . import career, persons, theses
from .db.models import Publication
from .db.session import session_scope
from .sources.base import to_year


@dataclass
class CareerSpan:
    """A line of the CV on the timeline: from its first year to its last (a current
    position: this year), and more precisely from ``begin`` to ``stop`` (in years: its
    months, when known); ``lane``: its row, 0 the top one (overlapping lines one above the
    other)."""

    entry: career.Entry
    first: int
    last: int
    lane: int = 0
    begin: float = 0.0
    stop: float = 0.0

    def __post_init__(self) -> None:
        self.begin = self.begin or float(self.first)
        self.stop = self.stop or float(self.last + 1)


def _month(v: str | None) -> int | None:
    """The month of a date (“2024-09-30”, “2024-10”), none if only its year."""
    m = re.search(r"\b(?:1[89]|20)\d{2}-(\d{1,2})\b", v or "")
    return int(m[1]) if m and 1 <= int(m[1]) <= 12 else None


def career_spans(
    person_id: int, today: date | None = None
) -> tuple[list[CareerSpan], list[career.Entry]]:
    """The person's CV lines placed on the timeline (the most recent first), and those
    without a date."""
    this_year = (today or date.today()).year
    spans, undated = [], []
    for e in career.entries(person_id):
        first, last = to_year(e.start), to_year(e.end)
        if first is None and last is None:
            undated.append(e)
            continue
        if e.current:
            last = max(this_year, first)
        first, last = first or last, last or first
        first, last = min(first, last), max(first, last)
        begin = first + ((_month(e.start) or 1) - 1) / 12 if to_year(e.start) == first else first
        stop = last + 1.0
        if not e.current and to_year(e.end) == last and (m := _month(e.end)):
            stop = last + m / 12
        spans.append(CareerSpan(e, first, last, begin=begin, stop=max(stop, begin + 1 / 12)))
    lanes(spans)
    return spans, undated


def bounds(sp) -> tuple[float, float]:
    """Where a span begins and stops, in years (its ``begin``, ``stop`` if it has them, else
    from the start of its first year to the end of its last one)."""
    return getattr(sp, "begin", sp.first), getattr(sp, "stop", sp.last + 1)


def lanes(spans: list, start: int = 0, room: int = 1) -> int:
    """Each span (its ``first``, ``last`` years; see bounds) in the first lane free over its
    time (from the most recent down), from ``start`` on, taking at least ``room`` years
    (e.g. its label's); returns the lanes taken, ``start`` included."""
    lowest: list[float] = []  # (each lane: where its last span begins)
    for sp in sorted(spans, key=lambda x: tuple(-b for b in reversed(bounds(x)))):
        begin, stop = bounds(sp)
        end = max(stop, begin + room)
        lane = next((i for i, low in enumerate(lowest) if end <= low + 1e-9), len(lowest))
        if lane == len(lowest):
            lowest.append(begin)
        lowest[lane] = begin
        sp.lane = start + lane
    return start + len(lowest)


@dataclass
class Supervised:
    """A thesis the person directed: from its start to its defence (in progress: to this
    year), as a span; ``year``: its last, none without a date; ``lane``: its row (see
    lanes)."""

    id: int
    year: int | None
    student: str
    title: str
    years: str  # e.g. “2019–2022”, “since 2023”
    in_progress: bool
    first: int = 0
    last: int = 0
    lane: int = 0


def supervised(person_id: int, today: date | None = None) -> list[Supervised]:
    """The theses the person directed, the most recent first (those without a date last)."""
    this_year = (today or date.today()).year
    found = []
    for t in persons.theses(person_id, "director"):
        sp = theses.span(t)
        first, last = sp.start_year, sp.end_year
        if last is not None:
            years = f"{first}–{last}" if first and first != last else str(last)
        else:
            years = str(first or "")
            last = max(this_year, first) if first else None
        found.append(
            Supervised(
                t.id,
                last or first,
                t.student or "",
                t.title or "",
                years,
                sp.end_year is None,
                min(first or last or 0, last or first or 0),
                max(first or last or 0, last or first or 0),
            )
        )
    return sorted(found, key=lambda x: x.year or 0, reverse=True)


@dataclass
class Jury:
    """A thesis the person was on the jury of (``role``: see theses.JURY_ROLES): a moment, its
    defence (``date`` as known; ``at``: where, in years, to the month if known, else the
    middle of its year); ``year``: none without a date; ``lane``: its row (see lanes)."""

    id: int
    role: str
    student: str
    title: str
    date: str
    year: int | None
    at: float = 0.0
    first: int = 0
    last: int = 0
    lane: int = 0


def juries(person_id: int) -> list[Jury]:
    """The theses the person was on the jury of (not hidden), the most recent first (those
    without a date last)."""
    found = []
    for t in persons.theses(person_id):
        if t.role not in theses.JURY_ROLES:
            continue
        sp = theses.span(t)
        when = sp.end or (sp.start if not sp.start_estimated else None) or ""
        year = to_year(when)
        month = re.match(r"\d{4}-(\d{2})", when)
        at = year + ((int(month[1]) - 0.5) / 12 if month else 0.5) if year else 0.0
        found.append(
            Jury(t.id, t.role, t.student or "", t.title or "", when, year, at, year or 0, year or 0)
        )
    return sorted(found, key=lambda x: x.at, reverse=True)


def papers_by_year(person_id: int) -> dict[int, list[str]]:
    """The titles of the person's papers (not hidden, nor missing) by year."""
    year = func.coalesce(Publication.year_override, Publication.year)
    with session_scope() as s:
        rows = s.execute(
            select(year, Publication.title)
            .where(
                Publication.person_id == person_id,
                Publication.hidden.is_(False),
                Publication.missing.is_(False),
                year.is_not(None),
            )
            .order_by(Publication.title)
        )
        found: dict[int, list[str]] = {}
        for y, title in rows:
            found.setdefault(y, []).append(title or "")
        return found
