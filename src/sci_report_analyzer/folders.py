"""Folders: named, dated groups of people; each member has their own period in the folder.
Folders can be within others; each uses named settings (its categories, how its notes cite
papers…) that it chose, else the default ones."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import EllipsisType

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .db.models import (
    Category,
    Excerpt,
    Folder,
    FolderSettings,
    Period,
    PeriodDocument,
    PeriodNote,
    PeriodTag,
    Person,
    Publication,
    PublicationPdf,
    SectionText,
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
    documents: int = 0  # (attached to their period)


@dataclass
class FolderView:
    id: int
    name: str
    date: date | None
    hidden: bool
    notes: str | None
    members: list[Member] = field(default_factory=list)
    parent_id: int | None = None
    path: str = ""  # (with the folders it is in: "Hiring › 2026")
    settings_id: int | None = None  # (those it chose; none: the default ones)


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
        documents = dict(
            s.execute(
                select(PeriodDocument.period_id, func.count()).group_by(PeriodDocument.period_id)
            ).all()
        )
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
                            documents.get(p.id, 0),
                        )
                        for p in f.periods
                    ),
                    key=lambda m: m.name.lower(),
                ),
                f.parent_id,
                paths.get(f.id, f.name),
                f.settings_id,
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
    *,
    parent_id: int | None = None,
) -> int:
    """Create or update a folder; ``notes``: its own short text (``...``: left as it is, e.g.
    not edited in a dialog showing a stale copy). A new one: in ``parent_id`` (using the
    settings it chose), else at the top level (using the default ones)."""
    with session_scope() as s:
        f = s.get(Folder, folder_id) if folder_id else None
        if f is None:
            parent = s.get(Folder, parent_id or 0)
            f = Folder(
                name=name,
                parent_id=parent.id if parent else None,
                settings_id=parent.settings_id if parent else None,
            )
            s.add(f)
            s.flush()
        f.name, f.date, f.hidden = name, day, hidden
        if notes is not ...:
            f.notes = notes or None
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


@dataclass
class Contents:
    people: int
    folders: int  # (directly within it)
    notes: bool

    @property
    def empty(self) -> bool:
        return not (self.people or self.folders or self.notes)


def contents(folder_id: int) -> Contents:
    """What a folder holds (what deleting it loses, or moves up: see delete_folder)."""
    with session_scope() as s:
        f = s.get(Folder, folder_id)
        return Contents(
            s.scalar(select(func.count()).where(Period.folder_id == folder_id)) or 0,
            s.scalar(select(func.count()).where(Folder.parent_id == folder_id)) or 0,
            bool(f is not None and (f.notes or "").strip()),
        )


def delete_folder(folder_id: int) -> None:
    """Delete a folder with its periods (and their tags and notes on papers); people are kept.
    The folders within it go up (keeping their settings)."""
    with session_scope() as s:
        if f := s.get(Folder, folder_id):
            for child in s.scalars(select(Folder).where(Folder.parent_id == f.id)):
                child.parent_id = f.parent_id
            s.flush()
            s.delete(f)


# ---- The settings of folders: named, one of them the default -------------------------------


class SettingsRef(int):
    """Settings given where a folder is expected (e.g. to their editors, in the Parameters):
    those settings themselves, not a folder's."""


def default_id(s) -> int:
    """The default settings (made if there are none)."""
    sid = s.scalar(select(FolderSettings.id).where(FolderSettings.is_default))
    if sid is None:
        new = FolderSettings(name=_("Default"), is_default=True)
        s.add(new)
        s.flush()
        sid = new.id
    return sid


def default_settings() -> int:
    """The default settings (see default_id)."""
    with session_scope() as s:
        return default_id(s)


def settings_id(s, folder_id: int) -> int:
    """The settings a folder uses: those it chose, else the default ones (a SettingsRef:
    those)."""
    if isinstance(folder_id, SettingsRef):
        return int(folder_id)
    f = s.get(Folder, folder_id)
    return (f.settings_id if f is not None else None) or default_id(s)


def used_settings(s, folder_id: int) -> FolderSettings | None:
    """The settings a folder uses (see settings_id); none for an unknown folder."""
    if not isinstance(folder_id, SettingsRef) and s.get(Folder, folder_id) is None:
        return None
    return s.get(FolderSettings, settings_id(s, folder_id))


def primary_source(folder_id: int) -> str | None:
    """The primary source of a folder's settings (none: the default; see source_settings)."""
    with session_scope() as s:
        found = used_settings(s, folder_id)
        return found.primary_source if found is not None else None


def set_primary_source(folder_id: int, source: str | None) -> None:
    """The primary source of a folder's settings (those sharing them too)."""
    with session_scope() as s:
        s.get(FolderSettings, settings_id(s, folder_id)).primary_source = source


def settings_of(folder_id: int) -> int:
    """The settings a folder uses (see settings_id)."""
    with session_scope() as s:
        return settings_id(s, folder_id)


def _users(s) -> dict[int, list[int]]:
    """The folders using each settings."""
    default = default_id(s)
    out: dict[int, list[int]] = {}
    for f, sid in s.execute(select(Folder.id, Folder.settings_id)):
        out.setdefault(sid or default, []).append(f)
    return out


def sharing(folder_id: int) -> list[str]:
    """The other folders using the same settings as a folder (their paths; a SettingsRef:
    all those using them)."""
    with session_scope() as s:
        users = _users(s).get(settings_id(s, folder_id), [])
        paths = _paths(s)
    return sorted(paths[f] for f in users if f != folder_id or isinstance(folder_id, SettingsRef))


@dataclass
class NamedSettings:
    id: int
    name: str
    default: bool
    folders: list[str]  # (the paths of those using them)
    filed: int  # (the excerpts and report texts in their categories)


def named_settings() -> list[NamedSettings]:
    """The settings of folders: the default ones, then the others by name."""
    with session_scope() as s:
        users, paths = _users(s), _paths(s)
        filed = _filed(s)
        out = [
            NamedSettings(
                x.id,
                x.name,
                x.is_default,
                sorted(paths[f] for f in users.get(x.id, [])),
                filed.get(x.id, 0),
            )
            for x in s.scalars(select(FolderSettings))
        ]
    return sorted(out, key=lambda x: (not x.default, x.name.lower(), x.id))


def _filed(s) -> dict[int, int]:
    """How many excerpts and report texts are in the categories of each settings."""
    out: dict[int, int] = {}
    for model in (Excerpt, SectionText):
        q = (
            select(Category.settings_id, func.count())
            .join(model, model.category_id == Category.id)
            .group_by(Category.settings_id)
        )
        for sid, n in s.execute(q):
            out[sid] = out.get(sid, 0) + n
    return out


def _named(s, name: str, settings_id: int | None = None) -> str:
    """A name for settings, trimmed. Refused (``ValueError``): blank, or that of others."""
    name = name.strip()
    if not name:
        raise ValueError(_("A name is needed"))
    taken = select(FolderSettings.id).where(
        func.lower(FolderSettings.name) == name.lower(), FolderSettings.id != (settings_id or 0)
    )
    if s.scalar(taken) is not None:
        raise ValueError(_("Settings of this name exist already"))
    return name


def create_settings(name: str, source_id: int | None = None) -> int:
    """New settings: empty, or a copy of others (``source_id``: their citations, primary
    source, report templates, categories and star lists). Refused (``ValueError``): see _named."""
    from . import annotations, categories

    with session_scope() as s:
        new = FolderSettings(name=_named(s, name))
        s.add(new)
        s.flush()
        if (source := s.get(FolderSettings, source_id or 0)) is not None:
            new.citations = dict(source.citations or {}) or None
            new.primary_source = source.primary_source
            new.report_templates = source.report_templates
            categories.copy_settings(s, source.id, new.id)
            annotations.copy_lists(s, source.id, new.id)
        return new.id


def rename_settings(settings_id: int, name: str) -> None:
    """Refused (``ValueError``): see _named."""
    with session_scope() as s:
        if (x := s.get(FolderSettings, settings_id)) is not None:
            x.name = _named(s, name, settings_id)


def set_default_settings(settings_id: int) -> None:
    """The settings used by the folders that chose none."""
    with session_scope() as s:
        if s.get(FolderSettings, settings_id) is None:
            return
        for x in s.scalars(select(FolderSettings).where(FolderSettings.is_default)):
            x.is_default = False
        s.flush()
        s.get(FolderSettings, settings_id).is_default = True


def delete_settings(settings_id: int) -> None:
    """Delete settings, with their categories and star lists. Refused (``ValueError``): the
    default ones, those a folder uses, those with excerpts or report texts in their
    categories (lost otherwise)."""
    with session_scope() as s:
        x = s.get(FolderSettings, settings_id)
        if x is None:
            return
        if x.is_default:
            raise ValueError(_("The default settings cannot be deleted"))
        if _users(s).get(x.id):
            raise ValueError(_("Folders use these settings"))
        if _filed(s).get(x.id):
            raise ValueError(_("Excerpts or report texts are filed in their categories"))
        s.delete(x)


def use_settings(folder_id: int, chosen: int | None) -> int:
    """The settings a folder uses (``chosen``; ``None``: the default ones): its people's
    excerpts are filed in their categories (see categories.remap), their starred papers in
    their star lists (see annotations.remap_lists). Returns how many categories were added
    there for them."""
    from . import annotations, categories

    with session_scope() as s:
        f = s.get(Folder, folder_id)
        if f is None:
            return 0
        was = settings_id(s, folder_id)
        f.settings_id = chosen if s.get(FolderSettings, chosen or 0) else None
        s.flush()
        now = settings_id(s, folder_id)
        if was == now:
            return 0
        periods = list(s.scalars(select(Period.id).where(Period.folder_id == folder_id)))
        annotations.remap_lists(s, periods, was, now)
        return categories.remap(s, periods, was, now)


# ---- The tree of folders ------------------------------------------------------------------


def set_parent(folder_id: int, parent_id: int | None) -> None:
    """Put a folder (with those within it) in another one (``None``: at the top level),
    never within itself (refused: ``ValueError``); its settings stay those it uses."""
    with session_scope() as s:
        f = s.get(Folder, folder_id)
        if f is None:
            return
        p = s.get(Folder, parent_id) if parent_id else None
        while p is not None:
            if p.id == f.id:
                raise ValueError(_("A folder cannot go within itself"))
            p = s.get(Folder, p.parent_id) if p.parent_id else None
        f.parent_id = parent_id or None


@dataclass
class FolderNode:
    id: int
    name: str
    parent_id: int | None
    depth: int
    path: str
    hidden: bool
    settings_id: int  # (those it uses)
    settings: str  # (their name)
    default_settings: bool  # (whether they are the default ones)
    children: list[FolderNode] = field(default_factory=list)


def tree() -> list[FolderNode]:
    """The folders, depth first (a folder, then those within it; most recent first)."""
    with session_scope() as s:
        rows = list(s.scalars(select(Folder)))
        default = default_id(s)
        names = dict(s.execute(select(FolderSettings.id, FolderSettings.name)).all())
        paths = _paths(s)
    ids = {f.id for f in rows}
    kids: dict[int | None, list[Folder]] = {}
    for f in sorted(rows, key=_recent_first):
        kids.setdefault(f.parent_id if f.parent_id in ids else None, []).append(f)
    out: list[FolderNode] = []

    def walk(parent: int | None, depth: int) -> list[FolderNode]:
        nodes = []
        for f in kids.get(parent, []):
            sid = f.settings_id or default
            n = FolderNode(
                f.id,
                f.name,
                f.parent_id,
                depth,
                paths[f.id],
                f.hidden,
                sid,
                names.get(sid, ""),
                sid == default,
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


def period_in(folder_id: int, person_id: int) -> int | None:
    """The id of the person's period in the folder, if they are in it."""
    with session_scope() as s:
        return s.scalar(
            select(Period.id).where(Period.folder_id == folder_id, Period.person_id == person_id)
        )


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


def move_person(person_id: int, from_id: int, to_id: int) -> int:
    """Move a person from a folder to another, with their period in it (its years, tags,
    notes, documents, starred papers and report): their stars go to the star lists of the
    other folder's settings, their sections' texts to its categories (see categories.remap).
    Refused (``ValueError``) if they are in it already, or if they have excerpts and its
    settings differ (they could be misfiled there). Returns how many categories were added
    for them."""
    from . import annotations, categories

    with session_scope() as s:
        p = s.scalar(
            select(Period).where(Period.folder_id == from_id, Period.person_id == person_id)
        )
        if p is None or from_id == to_id:
            return 0
        if s.scalar(
            select(Period.id).where(Period.folder_id == to_id, Period.person_id == person_id)
        ):
            raise ValueError(_("Already in this folder"))
        was, now = settings_id(s, from_id), settings_id(s, to_id)
        if was != now and s.scalar(select(Excerpt.id).where(Excerpt.period_id == p.id).limit(1)):
            raise ValueError(
                _("They have excerpts, and this folder's settings (its categories) differ")
            )
        target = s.get(Folder, to_id)
        if p.name == p.folder.name:  # (named after the folder)
            p.name = target.name
        p.folder_id = to_id
        s.flush()
        if was == now:
            return 0
        annotations.remap_lists(s, [p.id], was, now)
        return categories.remap(s, [p.id], was, now)


def set_period(period_id: int, start: int | None, end: int | None) -> None:
    with session_scope() as s:
        p = s.get(Period, period_id)
        p.start_year, p.end_year = start, end


def set_period_primary(period_id: int, source: str | None) -> None:
    """The person's primary source in the folder (None: the folder's; see source_settings)."""
    with session_scope() as s:
        s.get(Period, period_id).primary_source = source


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


# What a deletion loses that is the user's own work (asked for explicitly before deleting).
PRECIOUS = ("notes", "annotated_pdfs", "documents", "excerpts", "report_texts")


def deletion_counts(
    *, person_ids: list[int] | None = None, period_ids: list[int] | None = None
) -> dict[str, int]:
    """What deleting these people (with their papers, PDFs and periods), or these periods
    (with their documents, notes, excerpts and report), loses; ``precious``: how much of it
    is the user's own work (see PRECIOUS)."""

    def n(q) -> int:
        return s.scalar(select(func.count()).select_from(q.subquery())) or 0

    def filled(column):
        return column.is_not(None) & (func.trim(column) != "")

    with session_scope() as s:
        if person_ids is not None:
            periods = select(Period.id).where(Period.person_id.in_(person_ids))
            pubs = select(Publication.id).where(Publication.person_id.in_(person_ids))
            pdfs = select(PublicationPdf).where(PublicationPdf.publication_id.in_(pubs))
            counts = {
                "people": len(person_ids),
                "papers": n(pubs),
                "pdfs": n(pdfs),
                "annotated_pdfs": n(pdfs.where(PublicationPdf.edited_at.is_not(None))),
                "paper_notes": n(pubs.where(filled(Publication.note))),
            }
        else:
            periods = select(Period.id).where(Period.id.in_(period_ids or []))
            counts = {"paper_notes": 0}
        docs = select(PeriodDocument).where(PeriodDocument.period_id.in_(periods))
        counts |= {
            "periods": n(periods),
            "documents": n(docs),
            "annotated_documents": n(docs.where(PeriodDocument.edited_at.is_not(None))),
            "starred": n(select(PeriodTag).where(PeriodTag.period_id.in_(periods))),
            "excerpts": n(select(Excerpt).where(Excerpt.period_id.in_(periods))),
            "report_texts": n(select(SectionText).where(SectionText.period_id.in_(periods))),
        }
        counts["notes"] = (
            counts.pop("paper_notes")
            + n(select(PeriodNote).where(PeriodNote.period_id.in_(periods)))
            + n(select(Period.id).where(Period.id.in_(periods), filled(Period.notes)))
            + n(docs.where(filled(PeriodDocument.note)))
        )
        counts.setdefault("annotated_pdfs", 0)
        counts["precious"] = sum(counts[k] for k in PRECIOUS)
        return counts


def delete_people(person_ids: list[int]) -> None:
    with session_scope() as s:
        for pid in person_ids:
            if p := s.get(Person, pid):
                s.delete(p)


# ---- A person's periods ---------------------------------------------------------------------


def periods(person_id: int, *, include_hidden_folders: bool = False) -> list[Period]:
    """The person's own periods (by start), then their folder periods (latest folder first)."""
    q = (
        select(Period)
        .outerjoin(Folder, Period.folder_id == Folder.id)
        .where(Period.person_id == person_id)
        .options(selectinload(Period.folder))
    )
    if not include_hidden_folders:
        q = q.where(Period.folder_id.is_(None) | Folder.hidden.is_(False))
    with session_scope() as s:
        items = list(s.scalars(q))

    def key(p: Period) -> tuple:
        if p.folder is None:
            return (0, p.start_year or 0, p.id)
        day = p.folder.date.toordinal() if p.folder.date else 0
        return (1, -day, p.id)

    return sorted(items, key=key)


def save_period(
    person_id: int, name: str, start: int | None, end: int | None, period_id: int | None = None
) -> int:
    with session_scope() as s:
        p = s.get(Period, period_id) if period_id else Period(person_id=person_id)
        if period_id is None:
            s.add(p)
        p.name, p.start_year, p.end_year = name, start, end
        s.flush()
        return p.id


def delete_period(period_id: int) -> None:
    with session_scope() as s:
        if p := s.get(Period, period_id):
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
