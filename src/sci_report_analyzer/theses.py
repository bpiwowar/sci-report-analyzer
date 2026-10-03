"""PhD theses: their start and end (defence), whether they fall in a period, and where to
look for what became of the students."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from urllib.parse import quote_plus

from .db.models import Thesis
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
    if t.start_date:
        return Span(t.start_date, t.defence_date)
    if year := to_year(t.defence_date):
        return Span(str(year - TYPICAL_YEARS), t.defence_date, start_estimated=True)
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
