"""The pages' addresses, readable: a person's from their name, within their folder's path
(``/person/hiring/2026/jane-doe``); a folder's on the Reports page (``/?folder=hiring/2026``).

Slugs are computed from the names (lowercase ASCII, the rest as ``-``): of two people, or two
folders in the same one, with the same slug, the oldest keeps it, the others get their id
appended (``jane-doe-12``). The former numeric addresses (``/person/3/7``) are still
understood (see resolve_person)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlencode

from sqlalchemy import select

from .db.models import Folder, Period, Person
from .db.session import session_scope
from .text import ascii_fold


def slugify(text: str) -> str:
    """Lowercase ASCII letters and digits, the rest as single ``-`` ("Émile Zoé": "emile-zoe")."""
    return re.sub(r"[^a-z0-9]+", "-", ascii_fold(text).lower()).strip("-")


def _unique(items: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Each (id, name)'s slug, unique: the lowest id keeps it, the others get ``-<id>``; a
    name without a slug: its id."""
    items = sorted((i, slugify(n) or str(i)) for i, n in items)
    taken = {s: i for i, s in reversed(items)}
    out = {}
    for i, s in items:
        if taken[s] != i:
            while s in taken:
                s = f"{s}-{i}"
            taken[s] = i
        out[i] = s
    return out


@dataclass
class Slugs:
    """The slugs of the people and folders (a snapshot: see load)."""

    persons: dict[int, str]
    folders: dict[int, str]
    parents: dict[int, int | None]

    @classmethod
    def load(cls) -> Slugs:
        with session_scope() as s:
            persons = _unique(s.execute(select(Person.id, Person.name)))
            rows = s.execute(select(Folder.id, Folder.name, Folder.parent_id)).all()
        parents = {i: p for i, _n, p in rows}
        folders: dict[int, str] = {}
        for parent in set(parents.values()):
            folders |= _unique((i, n) for i, n, p in rows if p == parent)
        return cls(persons, folders, parents)

    def folder_path(self, folder_id: int) -> list[str]:
        """The slugs of a folder, after those of the folders it is in."""
        out, seen, f = [], set(), folder_id
        while f in self.folders and f not in seen:
            seen.add(f)
            out.append(self.folders[f])
            f = self.parents[f]
        return out[::-1]

    def find_folder(self, path: list[str]) -> int | None:
        """The folder at a path of slugs (from the top)."""
        f = None
        for slug in path:
            f = next(
                (i for i, p in self.parents.items() if p == f and self.folders[i] == slug), None
            )
            if f is None:
                return None
        return f

    def find_person(self, slug: str) -> int | None:
        return next((i for i, s in self.persons.items() if s == slug), None)


def _query(params: dict) -> str:
    params = {k: v for k, v in params.items() if v is not None}
    return f"?{urlencode(params, safe='/,')}" if params else ""


def person_url(
    person_id: int, period_id: int | None = None, *, slugs: Slugs | None = None, **params
) -> str:
    """A person's page (within a period: its folder's path; a period in no folder: as
    ``period``), with these query parameters (``tab="report"``…)."""
    slugs = slugs or Slugs.load()
    folder = None
    if period_id:
        with session_scope() as s:
            folder = s.scalar(select(Period.folder_id).where(Period.id == period_id))
        if folder is None:
            params = {"period": period_id} | params
    path = [*(slugs.folder_path(folder) if folder else []), slugs.persons.get(person_id)]
    if path[-1] is None:  # (unknown: as before)
        path = [str(person_id), *([str(period_id)] if period_id else [])]
    return "/person/" + "/".join(path) + _query(params)


def resolve_person(path: str) -> tuple[int, int | None] | None:
    """(person id, period id) of a person's page path (what follows ``/person/``): slugs (see
    person_url), else ids (``3``, ``3/7``: the former addresses); its period: that of the
    person in the folder (none: no longer in it, or the folder not found)."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    slugs = Slugs.load()
    person = slugs.find_person(parts[-1])
    folder = slugs.find_folder(parts[:-1]) if len(parts) > 1 else None
    if person is None or (len(parts) > 1 and folder is None):
        if len(parts) <= 2 and all(p.isdigit() for p in parts):
            return _by_ids(*map(int, parts))
        if person is None:
            return None
    if folder is None:
        return person, None
    with session_scope() as s:
        period = s.scalar(
            select(Period.id).where(Period.person_id == person, Period.folder_id == folder)
        )
    return person, period


def _by_ids(person_id: int, period_id: int | None = None) -> tuple[int, int | None] | None:
    with session_scope() as s:
        if s.get(Person, person_id) is None:
            return None
        p = s.get(Period, period_id) if period_id else None
        return person_id, (p.id if p is not None and p.person_id == person_id else None)


def folder_url(folder_id: int, *, slugs: Slugs | None = None) -> str:
    """A folder on the Reports page (0: All people)."""
    path = (slugs or Slugs.load()).folder_path(folder_id) if folder_id else []
    return "/?folder=" + ("/".join(path) or str(folder_id))


def resolve_folder(value: str) -> int | None:
    """The folder of a Reports page's ``folder`` parameter: a path of slugs, else an id."""
    parts = [p for p in value.split("/") if p]
    found = Slugs.load().find_folder(parts) if parts else None
    if found is None and value.strip().isdigit():
        return int(value)
    return found
