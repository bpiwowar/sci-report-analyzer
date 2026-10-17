"""A person's timeline: what is dated about them, by year. Their career (its lines as spans
of years, side by side when they overlap), their excerpts in a folder (see
categories.timeline), the theses they supervised and their papers (by year)."""

from __future__ import annotations

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
    position: this year); ``lane``: its column, 0 the nearest the axis (overlapping lines
    side by side)."""

    entry: career.Entry
    first: int
    last: int
    lane: int = 0


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
        spans.append(CareerSpan(e, min(first, last), max(first, last)))
    _lanes(spans)
    return spans, undated


def _lanes(spans: list[CareerSpan]) -> None:
    """Each span in the first lane free over its years (from the most recent down)."""
    lowest: list[int] = []  # (each lane: the first year of its last span)
    for sp in sorted(spans, key=lambda x: (-x.last, -x.first)):
        lane = next((i for i, low in enumerate(lowest) if sp.last < low), len(lowest))
        if lane == len(lowest):
            lowest.append(sp.first)
        lowest[lane] = sp.first
        sp.lane = lane


@dataclass
class Supervised:
    """A thesis the person directed (theses.fr): at its defence, else (in progress) at its
    start."""

    id: int
    year: int | None
    student: str
    title: str
    years: str  # e.g. “2019–2022”, “since 2023”
    in_progress: bool


def supervised(person_id: int) -> list[Supervised]:
    """The theses the person directed, the most recent first (those without a date last)."""
    found = []
    for t in persons.theses(person_id, "director"):
        sp = theses.span(t)
        first, last = sp.start_year, sp.end_year
        if last is not None:
            years = f"{first}–{last}" if first and first != last else str(last)
        else:
            years = str(first or "")
        found.append(
            Supervised(t.id, last or first, t.student or "", t.title or "", years, last is None)
        )
    return sorted(found, key=lambda x: x.year or 0, reverse=True)


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
