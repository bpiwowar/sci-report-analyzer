"""Folders: named, dated groups of people; each member has their own period in the folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .db.models import Folder, Period, Person
from .db.session import session_scope


@dataclass
class Member:
    person_id: int
    name: str
    period_id: int
    start_year: int | None
    end_year: int | None
    stars: int
    tags: list[str] = field(default_factory=list)


@dataclass
class FolderView:
    id: int
    name: str
    date: date | None
    hidden: bool
    notes: str | None
    members: list[Member] = field(default_factory=list)
    primary_source: str | None = None  # (see source_settings.folder_primary)


def folders(*, include_hidden: bool = True) -> list[FolderView]:
    """Folders, most recent first (undated last)."""
    with session_scope() as s:
        q = select(Folder).options(
            selectinload(Folder.periods).selectinload(Period.person),
            selectinload(Folder.periods).selectinload(Period.paper_tags),
        )
        if not include_hidden:
            q = q.where(Folder.hidden.is_(False))
        out = [
            FolderView(
                f.id,
                f.name,
                f.date,
                f.hidden,
                f.notes,
                sorted(
                    (
                        Member(
                            p.person_id,
                            p.person.name,
                            p.id,
                            p.start_year,
                            p.end_year,
                            len(p.stars),
                            list(p.tags or []),
                        )
                        for p in f.periods
                    ),
                    key=lambda m: m.name.lower(),
                ),
                f.primary_source,
            )
            for f in s.scalars(q)
        ]
    return sorted(out, key=lambda f: (f.date is None, -(f.date or date.min).toordinal(), f.name))


def save_folder(
    folder_id: int | None,
    name: str,
    day: date | None = None,
    hidden: bool = False,
    notes: str | EllipsisType | None = ...,
    primary_source: str | None = None,
) -> int:
    """Create or update a folder; ``notes``: its own short text (``...``: left as it is, e.g.
    not edited in a dialog showing a stale copy)."""
    with session_scope() as s:
        f = s.get(Folder, folder_id) if folder_id else None
        if f is None:
            f = Folder(name=name)
            s.add(f)
        f.name, f.date, f.hidden = name, day, hidden
        if notes is not ...:
            f.notes = notes or None
        f.primary_source = primary_source
        for p in f.periods:
            p.name = name  # a folder period is named after its folder
        s.flush()
        return f.id


def set_hidden(folder_id: int, hidden: bool) -> None:
    with session_scope() as s:
        s.get(Folder, folder_id).hidden = hidden


def delete_folder(folder_id: int) -> None:
    """Delete a folder with its periods (and their tags and notes on papers); people are kept."""
    with session_scope() as s:
        if f := s.get(Folder, folder_id):
            s.delete(f)


def add_person(
    folder_id: int, person_id: int, start: int | None = None, end: int | None = None
) -> int:
    """Put a person in a folder (creates their period in it); returns the period id."""
    with session_scope() as s:
        p = s.scalar(
            select(Period).where(Period.folder_id == folder_id, Period.person_id == person_id)
        )
        if p is None:
            p = Period(
                person_id=person_id,
                folder_id=folder_id,
                name=s.get(Folder, folder_id).name,
                start_year=start,
                end_year=end,
            )
            s.add(p)
            s.flush()
        return p.id


def remove_person(folder_id: int, person_id: int, *, keep: bool = False) -> None:
    """Take a person out of a folder: their period in it, with its paper tags, notes,
    documents and report, is deleted; or, with ``keep``, becomes one of their own periods
    (still named after the folder)."""
    with session_scope() as s:
        for p in s.scalars(
            select(Period).where(Period.folder_id == folder_id, Period.person_id == person_id)
        ):
            if keep:
                p.folder_id = None
            else:
                s.delete(p)


def set_period(period_id: int, start: int | None, end: int | None) -> None:
    with session_scope() as s:
        p = s.get(Period, period_id)
        p.start_year, p.end_year = start, end


def set_tags(period_id: int, tags: list[str]) -> None:
    """Set the person's tags in the folder (trimmed, without duplicates, sorted)."""
    with session_scope() as s:
        s.get(Period, period_id).tags = sorted({t.strip() for t in tags if t.strip()})


def tags_of(f: FolderView) -> list[str]:
    """Every tag used in the folder."""
    return sorted({t for m in f.members for t in m.tags}, key=str.lower)


def all_people() -> dict[int, str]:
    with session_scope() as s:
        return {p.id: p.name for p in s.scalars(select(Person).order_by(Person.name))}


@dataclass
class PersonRow:
    id: int
    name: str
    affiliation: str | None
    folders: list[str]
    publications: int
    sources: int


def people_rows() -> list[PersonRow]:
    """Every person with the folders they are in (for the cleanup view)."""
    from sqlalchemy import func

    from .db.models import Publication

    with session_scope() as s:
        counts = dict(
            s.execute(
                select(Publication.person_id, func.count())
                .where(Publication.missing.is_(False))
                .group_by(Publication.person_id)
            ).all()
        )
        people = s.scalars(
            select(Person)
            .options(
                selectinload(Person.links),
                selectinload(Person.periods).selectinload(Period.folder),
            )
            .order_by(Person.name)
        )
        return [
            PersonRow(
                p.id,
                p.name,
                p.affiliation,
                sorted(pr.folder.name for pr in p.periods if pr.folder is not None),
                counts.get(p.id, 0),
                sum(ln.status == "validated" for ln in p.links),
            )
            for p in people
        ]


def delete_people(person_ids: list[int]) -> None:
    with session_scope() as s:
        for pid in person_ids:
            if p := s.get(Person, pid):
                s.delete(p)


def folder_of_period(period_id: int | None) -> tuple[int, str] | None:
    """(id, name) of the folder of a period, if it belongs to one."""
    if not period_id:
        return None
    with session_scope() as s:
        p = s.get(Period, period_id)
        return (p.folder.id, p.folder.name) if p is not None and p.folder is not None else None


def notes_of(period_id: int) -> str:
    """The notes of a person within a folder (Markdown; their period's)."""
    with session_scope() as s:
        p = s.get(Period, period_id)
        return (p.notes if p else None) or ""


def set_notes(period_id: int, text: str, *, base: str | None = None) -> bool:
    """Save the notes of a person within a folder; with ``base`` (the notes as the editor
    loaded them), only if they are still those (else refused: changed elsewhere since, e.g.
    in another window). Returns whether they were saved."""
    with session_scope() as s:
        p = s.get(Period, period_id)
        if p is None:
            return False
        if base is not None and (p.notes or "").strip() != base.strip():
            return False
        p.notes = text.strip() or None
        return True
