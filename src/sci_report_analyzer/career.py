"""A person's career (their CV): the positions they held and their diplomas (a PhD with its
thesis…), dated; the current position (that without an end), and the CV in a report
(the block ``[[cv]]``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from .db.models import CareerEntry, SourceLink, Thesis
from .db.session import session_scope
from .i18n import N_, _
from .source_settings import active_links

KINDS = {"position": N_("Position"), "diploma": N_("Diploma")}
# A date as typed: a year, YYYY-MM or YYYY-MM-DD.
_DATE = re.compile(r"(1[89]|20)\d{2}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?")


@dataclass
class Entry:
    kind: str  # position | diploma
    title: str
    start: str | None = None
    end: str | None = None
    institution: str | None = None
    details: str | None = None
    thesis_id: str | None = None
    id: int | None = None
    elapsed: bool = False  # (shown with the years since: “+12”)

    @property
    def current(self) -> bool:
        """A position held (started, not ended)."""
        return self.kind == "position" and self.end is None and self.start is not None

    def when(self) -> str:
        """Its dates, e.g. “2015–2019”, “since 2020”, “2014” (a diploma's)."""
        if self.current:
            return _("since {start}").format(start=self.start)
        if self.start and self.end and self.start != self.end:
            return f"{self.start}–{self.end}"
        return self.end or self.start or ""

    def since(self) -> str | None:
        """When it counts from: a diploma, when obtained; a position, when started."""
        return (self.end or self.start) if self.kind == "diploma" else self.start

    def years(self, today: date | None = None) -> int | None:
        """The years since it (shown as “+12”; none: not asked for, or no date)."""
        if not self.elapsed or not (since := self.since()):
            return None
        return max(0, (today or date.today()).year - int(since[:4]))

    def plus(self, today: date | None = None) -> str:
        """“+12” (the years since it), or nothing."""
        n = self.years(today)
        return "" if n is None else f"+{n}"

    def line(self) -> str:
        """“Associate professor, Sorbonne University”."""
        return ", ".join(x for x in (self.title, self.institution) if x)


def valid_date(value: str | None) -> bool:
    """Whether a date is empty or as expected (a year, YYYY-MM, YYYY-MM-DD)."""
    return not (value or "").strip() or _DATE.fullmatch(value.strip()) is not None


def _sort_key(e: Entry) -> tuple:
    # (the most recent first: current positions, then by end, else start, date)
    return (e.current, e.end or e.start or "", e.start or "")


def entries(person_id: int) -> list[Entry]:
    """The person's CV, the most recent first."""
    with session_scope() as s:
        rows = s.scalars(select(CareerEntry).where(CareerEntry.person_id == person_id))
        found = [
            Entry(
                r.kind,
                r.title,
                r.start,
                r.end,
                r.institution,
                r.details,
                r.thesis_id,
                id=r.id,
                elapsed=r.elapsed,
            )
            for r in rows
        ]
    return sorted(found, key=_sort_key, reverse=True)


def current(person_id: int) -> Entry | None:
    """The person's current position (the latest started, if several)."""
    return next((e for e in entries(person_id) if e.current), None)


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def save(person_id: int, entry: Entry) -> int:
    """Add (no id) or update a line of the CV."""
    with session_scope() as s:
        row = s.get(CareerEntry, entry.id) if entry.id else None
        if row is None:
            row = CareerEntry(person_id=person_id)
            s.add(row)
        row.kind, row.title = entry.kind, entry.title.strip()
        row.start, row.end = _clean(entry.start), _clean(entry.end)
        row.institution, row.details = _clean(entry.institution), _clean(entry.details)
        row.thesis_id, row.elapsed = entry.thesis_id, entry.elapsed
        s.flush()
        return row.id


def delete(entry_id: int) -> None:
    with session_scope() as s:
        if row := s.get(CareerEntry, entry_id):
            s.delete(row)


def own_theses(person_id: int) -> list[Entry]:
    """The person's own theses (on their validated theses.fr profile) not yet in their CV:
    as PhD diplomas to add."""
    with session_scope() as s:
        theses = list(
            s.scalars(
                select(Thesis)
                .join(SourceLink)
                .where(SourceLink.person_id == person_id, active_links(), Thesis.role == "author")
            )
        )
        known = set(
            s.scalars(select(CareerEntry.thesis_id).where(CareerEntry.person_id == person_id))
        )
        return [
            Entry(
                "diploma",
                _("PhD"),
                start=(t.start_date or "")[:10] or None,
                end=(t.defence_date or "")[:10] or None,
                institution=t.institution,
                details=t.title,
                thesis_id=t.thesis_id,
                elapsed=True,
            )
            for t in theses
            if t.thesis_id not in known
        ]


def markdown(person_id: int) -> str:
    """The CV in a report: a list, the most recent first (in the current language); the
    years since a line, if asked for, after its dates (“**2014 (+12)**: PhD”)."""
    lines = []
    for e in entries(person_id):
        when = " ".join(x for x in (e.when(), e.plus() and f"({e.plus()})") if x)
        text = f"**{when}**: {e.line()}" if when else e.line()
        if e.details:
            text += f" — *{e.details}*" if e.kind == "diploma" else f" ({e.details})"
        lines.append(f"- {text}")
    return "\n".join(lines)
