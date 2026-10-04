"""The people: listed, created, edited; their name variants, PhD students and theses."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .db.models import Period, Person, Publication, SourceLink, Thesis
from .db.session import session_scope
from .source_settings import active_links


def load(person_id: int) -> Person | None:
    """A person, with their source links."""
    with session_scope() as s:
        return s.scalar(
            select(Person).where(Person.id == person_id).options(selectinload(Person.links))
        )


def people_with_counts() -> list[tuple[Person, int]]:
    """Everyone (with their source links, by name) and their number of publications."""
    with session_scope() as s:
        counts = dict(
            s.execute(
                select(Publication.person_id, func.count())
                .where(Publication.missing.is_(False))
                .group_by(Publication.person_id)
            ).all()
        )
        people = list(
            s.scalars(select(Person).options(selectinload(Person.links)).order_by(Person.name))
        )
        return [(p, counts.get(p.id, 0)) for p in people]


def create(name: str, affiliation: str | None = None) -> int:
    with session_scope() as s:
        person = Person(name=name.strip(), affiliation=(affiliation or "").strip() or None)
        s.add(person)
        s.flush()
        return person.id


def update(
    person_id: int, *, name: str, affiliation: str | None, aliases: list[str], notes: str | None
) -> None:
    """A person's name, affiliation, name aliases and notes (their ORCID: sync.set_orcid)."""
    with session_scope() as s:
        p = s.get(Person, person_id)
        p.name, p.affiliation = name.strip(), (affiliation or "").strip() or None
        p.aliases = [a.strip() for a in aliases if a.strip()]
        p.notes = notes or None


def last_merged_at(person_id: int) -> dt.datetime | None:
    """When the person's papers were last merged (by a sync)."""
    with session_scope() as s:
        return s.get(Person, person_id).last_merged_at


def link_id(person_id: int, source: str, external_id: str) -> int | None:
    """A person's link to a profile of a source."""
    with session_scope() as s:
        return s.scalar(
            select(SourceLink.id).where(
                SourceLink.person_id == person_id,
                SourceLink.source == source,
                SourceLink.external_id == external_id,
            )
        )


def of_period(period_id: int) -> tuple[int, str] | None:
    """(id, name) of the person of a folder's period."""
    with session_scope() as s:
        p = s.get(Period, period_id)
        return (p.person_id, p.person.name) if p is not None and p.folder_id else None


def paper(pub_id: int) -> tuple[str | None, int] | None:
    """(title, person id) of a publication."""
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        return (pub.title, pub.person_id) if pub else None


# ---- PhD students and theses ----------------------------------------------------------------


def theses(person_id: int, role: str | None = None) -> list[Thesis]:
    """The person's theses (of their validated, used sources); ``role``: in that role."""
    query = select(Thesis).join(SourceLink).where(SourceLink.person_id == person_id, active_links())
    if role is not None:
        query = query.where(Thesis.role == role)
    with session_scope() as s:
        return list(s.scalars(query))


def students(person_id: int) -> list[str]:
    """The person's PhD students (as named in their theses), sorted."""
    return sorted(
        {n for t in theses(person_id, "director") for n in (t.student or "").split(", ") if n}
    )


def student_aliases(person_id: int) -> dict[str, list[str]]:
    with session_scope() as s:
        return dict(s.get(Person, person_id).student_aliases or {})


def save_student_aliases(person_id: int, aliases: dict[str, list[str]]) -> None:
    """Other spellings of the PhD students' names (those with some)."""
    with session_scope() as s:
        s.get(Person, person_id).student_aliases = {n: v for n, v in aliases.items() if v}


def student_outcomes(person_id: int) -> dict:
    with session_scope() as s:
        return dict(s.get(Person, person_id).student_outcomes or {})


@dataclass
class Names:
    """Who's who in a person's author lists: their name and aliases, their PhD students
    (and their aliases), the co-authors in each of their categories."""

    name: str
    aliases: set[str] = field(default_factory=set)
    students: list[str] = field(default_factory=list)
    student_aliases: dict[str, set[str]] = field(default_factory=dict)
    categories: dict[int, set[str]] = field(default_factory=dict)


def names(person_id: int) -> Names:
    with session_scope() as s:
        p = s.get(Person, person_id)
        found = Names(
            p.name,
            set(p.aliases or []),
            student_aliases={k: set(v) for k, v in (p.student_aliases or {}).items()},
            categories={int(k): set(v) for k, v in (p.author_categories or {}).items()},
        )
    found.students = students(person_id)
    return found
