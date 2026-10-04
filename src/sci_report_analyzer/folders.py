"""Folders: named, dated groups of people; each member has their own period in the folder.
Folders can be within others; a folder uses its own settings (its categories, how its notes
cite papers), or its parent's (all of them)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import EllipsisType

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from .db.models import (
    Category,
    Excerpt,
    Folder,
    FolderSettings,
    FolderSettingsUse,
    Period,
    Person,
)
from .db.session import session_scope
from .i18n import _


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
    parent_id: int | None = None
    path: str = ""  # (with the folders it is in: "Hiring › 2026")


def _paths(s) -> dict[int, str]:
    """Each folder's name, after those of the folders it is in ("Hiring › 2026")."""
    rows = {i: (n, p) for i, n, p in s.execute(select(Folder.id, Folder.name, Folder.parent_id))}
    out: dict[int, str] = {}
    for i in rows:
        names, seen, g = [], set(), i
        while g in rows and g not in seen:
            seen.add(g)
            names.append(rows[g][0])
            g = rows[g][1]
        out[i] = " › ".join(reversed(names))
    return out


def folders(*, include_hidden: bool = True) -> list[FolderView]:
    """Folders, most recent first (undated last)."""
    with session_scope() as s:
        q = select(Folder).options(
            selectinload(Folder.periods).selectinload(Period.person),
            selectinload(Folder.periods).selectinload(Period.paper_tags),
        )
        if not include_hidden:
            q = q.where(Folder.hidden.is_(False))
        paths = _paths(s)
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
                f.parent_id,
                paths.get(f.id, f.name),
            )
            for f in s.scalars(q)
        ]
    return sorted(out, key=_recent_first)


def _recent_first(f) -> tuple:
    return (f.date is None, -(f.date or date.min).toordinal(), f.name)


def save_folder(
    folder_id: int | None,
    name: str,
    day: date | None = None,
    hidden: bool = False,
    notes: str | EllipsisType | None = ...,
    primary_source: str | None = None,
    *,
    parent_id: int | None = None,
) -> int:
    """Create or update a folder; ``notes``: its own short text (``...``: left as it is, e.g.
    not edited in a dialog showing a stale copy). A new one: in ``parent_id`` (using its
    settings), else at the top level (with new settings of its own)."""
    with session_scope() as s:
        f = s.get(Folder, folder_id) if folder_id else None
        if f is None:
            f = Folder(name=name, parent_id=parent_id if s.get(Folder, parent_id or 0) else None)
            s.add(f)
            s.flush()
            if f.parent_id is None:
                _own_new(s, f.id)
        f.name, f.date, f.hidden = name, day, hidden
        if notes is not ...:
            f.notes = notes or None
        f.primary_source = primary_source
        for p in f.periods:
            p.name = name  # a folder period is named after its folder
        s.flush()
        return f.id


def rename(folder_id: int, name: str) -> None:
    """A folder's name (its periods are named after it); a blank one is ignored."""
    with session_scope() as s:
        if (f := s.get(Folder, folder_id)) is not None and name.strip():
            f.name = name.strip()
            for p in f.periods:
                p.name = f.name


def set_hidden(folder_id: int, hidden: bool) -> None:
    with session_scope() as s:
        s.get(Folder, folder_id).hidden = hidden


def delete_folder(folder_id: int) -> None:
    """Delete a folder with its periods (and their tags and notes on papers); people are kept.
    The folders within it go up, keeping the settings they used (those of the deleted folder:
    then used by them)."""
    with session_scope() as s:
        if f := s.get(Folder, folder_id):
            used = settings_id(s, f.id)
            for child in s.scalars(select(Folder).where(Folder.parent_id == f.id)):
                if s.get(FolderSettingsUse, child.id) is None:
                    s.add(FolderSettingsUse(folder_id=child.id, settings_id=used))
                child.parent_id = f.parent_id
            s.flush()
            s.delete(f)
            s.flush()
            _drop_unused(s)


# ---- The tree of folders, and their settings ------------------------------------------------


def _own_new(s, folder_id: int, citations: dict | None = None) -> int:
    """New settings, used by the folder (instead of those it used); returns their id."""
    new = FolderSettings(citations=citations)
    s.add(new)
    s.flush()
    if (use := s.get(FolderSettingsUse, folder_id)) is not None:
        use.settings_id = new.id
    else:
        s.add(FolderSettingsUse(folder_id=folder_id, settings_id=new.id))
    s.flush()
    return new.id


def settings_id(s, folder_id: int) -> int:
    """The settings a folder uses: its own, else its parent's (those of the first folder
    above it having some; none: new ones, of the top folder)."""
    f, seen = s.get(Folder, folder_id), set()
    while True:
        if (use := s.get(FolderSettingsUse, f.id)) is not None:
            return use.settings_id
        seen.add(f.id)
        if f.parent_id is None or f.parent_id in seen:
            return _own_new(s, f.id)
        f = s.get(Folder, f.parent_id)


def settings_of(folder_id: int) -> int:
    """The settings a folder uses (see settings_id)."""
    with session_scope() as s:
        return settings_id(s, folder_id)


def _effective(s) -> dict[int, int | None]:
    """The settings each folder uses (none: neither it nor those above it have some)."""
    return _effective_of(*_uses(s))


def _uses(s) -> tuple[dict[int, int | None], dict[int, int]]:
    """(each folder's parent, the settings of those having their own)."""
    parent = dict(s.execute(select(Folder.id, Folder.parent_id)).all())
    own = dict(s.execute(select(FolderSettingsUse.folder_id, FolderSettingsUse.settings_id)).all())
    return parent, own


def _effective_of(parent: dict[int, int | None], own: dict[int, int]) -> dict[int, int | None]:
    """The settings each folder uses, given its parent and the settings of those having their
    own (see _effective)."""
    out: dict[int, int | None] = {}
    for f in parent:
        seen, g = set(), f
        while g is not None and g not in own and g not in seen:
            seen.add(g)
            g = parent.get(g)
        out[f] = own.get(g) if g is not None else None
    return out


def _resettle(s, before: dict[int, int | None]) -> int:
    """After the settings of folders changed (``before``: those they used), file their
    people's excerpts in the categories of the new ones (see categories.remap); then drops
    the settings no folder uses any more. Returns how many categories were added."""
    from . import categories

    s.flush()
    added = 0
    for f, now in _effective(s).items():
        if (was := before.get(f)) is not None and now is not None and was != now:
            periods = list(s.scalars(select(Period.id).where(Period.folder_id == f)))
            added += categories.remap(s, periods, was, now)
    _drop_unused(s)
    return added


def _drop_unused(s) -> None:
    """Delete the settings no folder uses (and no excerpt is filed in)."""
    used = set(s.scalars(select(FolderSettingsUse.settings_id)))
    filed = set(
        s.scalars(select(Category.settings_id).join(Excerpt, Excerpt.category_id == Category.id))
    )
    if unused := [i for i in s.scalars(select(FolderSettings.id)) if i not in used | filed]:
        s.execute(delete(Category).where(Category.settings_id.in_(unused)))
        s.execute(delete(FolderSettings).where(FolderSettings.id.in_(unused)))


def set_parent(folder_id: int, parent_id: int | None) -> int:
    """Put a folder (with those within it) in another one (``None``: at the top level),
    never within itself; one using its parent's settings then uses its new parent's (at the
    top level: still those it used, as its own). Its people's excerpts follow (see
    categories.remap); returns how many categories were added to the new settings for
    them. Refused (``ValueError``) within itself."""
    with session_scope() as s:
        f = s.get(Folder, folder_id)
        if f is None:
            return 0
        p = s.get(Folder, parent_id) if parent_id else None
        while p is not None:
            if p.id == f.id:
                raise ValueError(_("A folder cannot go within itself"))
            p = s.get(Folder, p.parent_id) if p.parent_id else None
        before = _effective(s)
        if parent_id is None and s.get(FolderSettingsUse, f.id) is None:
            s.add(FolderSettingsUse(folder_id=f.id, settings_id=settings_id(s, f.id)))
        f.parent_id = parent_id or None
        return _resettle(s, before)


def own_settings(folder_id: int) -> bool:
    """Whether a folder uses settings of its own (else: its parent's)."""
    with session_scope() as s:
        return s.get(FolderSettingsUse, folder_id) is not None


def use_own_settings(folder_id: int) -> None:
    """A folder gets settings of its own: a copy of those it used (its people's excerpts
    filed in the copies of their categories)."""
    from . import categories

    with session_scope() as s:
        before = _effective(s)
        was = settings_id(s, folder_id)
        new = _own_new(s, folder_id, dict(s.get(FolderSettings, was).citations or {}) or None)
        categories.copy_settings(s, was, new)
        _resettle(s, before)


def use_parent_settings(folder_id: int) -> int:
    """A folder within another one uses its parent's settings (its own ones are dropped,
    unless others use them): its people's excerpts are filed in their categories (see
    categories.remap). Returns how many categories were added there for them."""
    with session_scope() as s:
        f = s.get(Folder, folder_id)
        if f is None or f.parent_id is None:
            return 0
        before = _effective(s)
        if (use := s.get(FolderSettingsUse, folder_id)) is not None:
            s.delete(use)
        return _resettle(s, before)


def _above(parent: dict[int, int | None], folder_id: int) -> list[int]:
    """The folders a folder is in, from its parent up."""
    out, g = [], parent.get(folder_id)
    while g is not None and g not in out and g != folder_id:
        out.append(g)
        g = parent.get(g)
    return out


_NEW = -1  # (the settings a copy would make, before they are)


def _moved(parent, own, folder_id: int, target_id: int, *, copy: bool) -> dict[int, int]:
    """The settings of the folders having their own, once those a folder uses are moved
    (else, with ``copy``, copied) to another one (see move_settings, copy_settings_to).
    Refused (``ValueError``): moved to a folder it is not in, copied to itself."""
    if folder_id not in parent or target_id not in parent:
        raise ValueError(_("Unknown folder"))
    if copy:
        if target_id == folder_id:
            raise ValueError(_("A folder's settings cannot be copied to itself"))
        return {**own, target_id: _NEW}
    above = _above(parent, folder_id)
    if target_id not in above:
        raise ValueError(_("Settings move only to a folder it is in"))
    out = {**own, target_id: _effective_of(parent, own)[folder_id]}
    for f in [folder_id, *above[: above.index(target_id)]]:  # (they use the target's)
        out.pop(f, None)
    return out


def affected(folder_id: int, target_id: int, *, copy: bool = False) -> list[str]:
    """The folders whose settings change when those of a folder are moved (or copied) to
    another one (their paths; see move_settings, copy_settings_to)."""
    with session_scope() as s:
        settings_id(s, folder_id)  # (made if none)
        parent, own = _uses(s)
        before = _effective_of(parent, own)
        after = _effective_of(parent, _moved(parent, own, folder_id, target_id, copy=copy))
        paths = _paths(s)
    return sorted(paths[f] for f in parent if before.get(f) != after.get(f))


def move_settings(folder_id: int, target_id: int) -> int:
    """The settings a folder uses become those of a folder it is in (``target_id``, e.g. its
    parent): the folder, and those between them, then use the target's (their own ones
    dropped, unless others use them); so do the folders that used the target's (its previous
    ones dropped, unless others use them). Everyone's excerpts follow (see categories.remap);
    returns how many categories were added for them. Refused (``ValueError``): to a folder
    it is not in."""
    with session_scope() as s:
        settings_id(s, folder_id)  # (made if none)
        parent, own = _uses(s)
        before = _effective_of(parent, own)
        after = _moved(parent, own, folder_id, target_id, copy=False)
        for f in set(own) | set(after):
            use = s.get(FolderSettingsUse, f)
            if f not in after:
                s.delete(use)
            elif use is None:
                s.add(FolderSettingsUse(folder_id=f, settings_id=after[f]))
            else:
                use.settings_id = after[f]
        return _resettle(s, before)


def copy_settings_to(folder_id: int, target_id: int) -> int:
    """Another folder (``target_id``) gets its own copy of the settings a folder uses (their
    citations and categories); its people's excerpts, and those of the folders using its
    settings, are filed in the copy (see categories.remap: the missing categories added).
    Returns how many were added. Refused (``ValueError``): to itself."""
    from . import categories

    with session_scope() as s:
        parent, own = _uses(s)
        _moved(parent, own, folder_id, target_id, copy=True)  # (checked)
        before = _effective_of(parent, own)
        source = settings_id(s, folder_id)
        citations = dict(s.get(FolderSettings, source).citations or {}) or None
        categories.copy_settings(s, source, _own_new(s, target_id, citations))
        return _resettle(s, before)


def sharing(folder_id: int) -> list[str]:
    """The other folders using the same settings as a folder (their paths)."""
    with session_scope() as s:
        effective = _effective(s)
        mine = effective.get(folder_id) or settings_id(s, folder_id)
        paths = _paths(s)
    return sorted(paths[f] for f, sid in effective.items() if sid == mine and f != folder_id)


@dataclass
class FolderNode:
    id: int
    name: str
    parent_id: int | None
    depth: int
    path: str
    hidden: bool
    own: bool  # (its own settings; else its parent's)
    shared_with: list[str] = field(default_factory=list)  # (others using its settings)
    children: list[FolderNode] = field(default_factory=list)


def tree() -> list[FolderNode]:
    """The folders, depth first (a folder, then those within it; most recent first)."""
    with session_scope() as s:
        rows = list(s.scalars(select(Folder)))
        own = set(s.scalars(select(FolderSettingsUse.folder_id)))
        effective = _effective(s)
        paths = _paths(s)
    users: dict[int | None, list[str]] = {}
    for f, sid in effective.items():
        users.setdefault(sid, []).append(paths[f])
    ids = {f.id for f in rows}
    kids: dict[int | None, list[Folder]] = {}
    for f in sorted(rows, key=_recent_first):
        kids.setdefault(f.parent_id if f.parent_id in ids else None, []).append(f)
    out: list[FolderNode] = []

    def walk(parent: int | None, depth: int) -> list[FolderNode]:
        nodes = []
        for f in kids.get(parent, []):
            n = FolderNode(
                f.id,
                f.name,
                f.parent_id,
                depth,
                paths[f.id],
                f.hidden,
                f.id in own,
                sorted(p for p in users.get(effective[f.id], []) if p != paths[f.id]),
            )
            out.append(n)
            n.children = walk(f.id, depth + 1)
            nodes.append(n)
        return nodes

    walk(None, 0)
    return out


def within(folder_id: int) -> list[int]:
    """A folder and those within it."""
    nodes = {n.id: n for n in tree()}
    out: list[int] = []

    def walk(n: FolderNode) -> None:
        out.append(n.id)
        for c in n.children:
            walk(c)

    if folder_id in nodes:
        walk(nodes[folder_id])
    return out


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
