"""User annotations on publications: tags (global or per period, stars included), notes,
forced venue matches, kinds and tracks set by hand."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .db.app_settings import get_setting, set_setting
from .db.models import (
    STARRED,
    PeriodNote,
    PeriodTag,
    Publication,
    PublicationTag,
    Tag,
)
from .db.session import session_scope
from .folders import delete_period, periods, save_period  # noqa: F401  (re-exported)

# ---- tags and notes -------------------------------------------------------------------------


def all_tags() -> list[Tag]:
    """Every tag: the global ones, then the per-period ones (by name)."""
    with session_scope() as s:
        return list(s.scalars(select(Tag).order_by(Tag.per_period, Tag.name)))


def starred_tag_id() -> int:
    with session_scope() as s:
        return s.scalar(select(Tag.id).where(Tag.key == STARRED))


def save_tag(
    name: str, colour: str | None = None, per_period: bool = False, tag_id: int | None = None
) -> int:
    """Create a tag (or rename / recolour one; a tag keeps its kind). An existing name is
    reused."""
    name = name.strip()
    with session_scope() as s:
        t = s.get(Tag, tag_id) if tag_id else s.scalar(select(Tag).where(Tag.name == name))
        if t is None:
            t = Tag(name=name, per_period=per_period, colour=colour or "#0969da")
            s.add(t)
        t.name = name
        if colour:
            t.colour = colour
        s.flush()
        return t.id


def delete_tag(tag_id: int) -> None:
    """Delete a tag (from every paper); the built-in ones are kept."""
    with session_scope() as s:
        if (t := s.get(Tag, tag_id)) and t.key is None:
            s.delete(t)


def toggle_tag(pub_id: int, tag_id: int, period_id: int | None = None) -> bool:
    """Put a tag on a paper (or take it off): a per-period tag within ``period_id``."""
    with session_scope() as s:
        if s.get(Tag, tag_id).per_period:
            if period_id is None:
                raise ValueError("a per-period tag needs a period")
            row = s.get(PeriodTag, (period_id, pub_id, tag_id))
            new = PeriodTag(period_id=period_id, publication_id=pub_id, tag_id=tag_id)
        else:
            row = s.get(PublicationTag, (pub_id, tag_id))
            new = PublicationTag(publication_id=pub_id, tag_id=tag_id)
        if row:
            s.delete(row)
            return False
        s.add(new)
        return True


def tag_numbered(tag_id: int, numbers: dict[int, int | None], period_id: int | None = None) -> None:
    """Put a tag on papers (pub id -> their number in a list), keeping the tag where it is
    and setting its number."""
    with session_scope() as s:
        per_period = s.get(Tag, tag_id).per_period
        if per_period and period_id is None:
            raise ValueError("a per-period tag needs a period")
        for pub_id, number in numbers.items():
            if per_period:
                key = (period_id, pub_id, tag_id)
                row = s.get(PeriodTag, key) or PeriodTag(
                    period_id=period_id, publication_id=pub_id, tag_id=tag_id
                )
            else:
                row = s.get(PublicationTag, (pub_id, tag_id)) or PublicationTag(
                    publication_id=pub_id, tag_id=tag_id
                )
            row.number = number
            s.add(row)


def set_note(pub_id: int, text: str | None, period_id: int | None = None) -> None:
    """The paper's note (Markdown), or its note within a period; empty: none."""
    text = (text or "").strip() or None
    with session_scope() as s:
        if period_id is None:
            s.get(Publication, pub_id).note = text
            return
        row = s.get(PeriodNote, (period_id, pub_id))
        if text is None:
            if row:
                s.delete(row)
        elif row:
            row.text = text
        else:
            s.add(PeriodNote(period_id=period_id, publication_id=pub_id, text=text))


def set_rank_override(
    pub_id: int, override: dict[str, Any] | None, note: str | None = None
) -> None:
    """Rank of one publication set by hand: a level ``{"type", "rank", "name"}`` or a
    ranking record ``{"record_key"}`` (None: back to its venue's rank)."""
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        pub.rank_override = override
        pub.rank_note = (note or None) if override else None


def set_venue_source(pub_id: int, source: str | None) -> None:
    """Use this source's venue for the publication (when the sources disagree)."""
    with session_scope() as s:
        s.get(Publication, pub_id).venue_source = source


_OVERRIDES = ("year_override", "author_pos_override", "note")


def set_overrides(pub_id: int, **values: Any) -> None:
    """Year / author position set by hand (None: from the sources), and a note."""
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        for k, v in values.items():
            if k not in _OVERRIDES:
                raise ValueError(k)
            setattr(pub, k, v if v not in ("", None) else None)


def set_doi(pub_id: int, doi: str | None) -> int:
    """Give a publication its DOI by hand (None: from the sources). Returns its person."""
    from .sources.base import normalize_doi

    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        pub.doi_manual = normalize_doi(doi)
        return pub.person_id


def set_hidden(pub_id: int, hidden: bool) -> None:
    with session_scope() as s:
        s.get(Publication, pub_id).hidden = hidden


# (the UI's names)
ui_state, save_ui_state = get_setting, set_setting


def panel_state(person_id: int) -> dict[str, Any]:
    return dict(get_setting(f"ui.person.{person_id}", {}))


def save_panel_state(person_id: int, state: dict[str, Any]) -> None:
    set_setting(f"ui.person.{person_id}", state)


# ---- name aliases (the person and their PhD students) ---------------------------------------

OWNER = "owner"


def add_alias(person_id: int, name: str, student: str | None = None) -> None:
    """Confirm ``name`` as a spelling of the person (or of PhD student ``student``)."""
    from .db.models import Person

    with session_scope() as s:
        p = s.get(Person, person_id)
        if student is None:
            if name != p.name and name not in (p.aliases or []):
                p.aliases = [*(p.aliases or []), name]
        else:
            aliases = dict(p.student_aliases or {})
            if name != student and name not in aliases.get(student, []):
                aliases[student] = [*aliases.get(student, []), name]
            p.student_aliases = aliases
        _unreject(p, student or OWNER, name)


def reject_alias(person_id: int, name: str, student: str | None = None) -> None:
    """``name`` is *not* the person (or the student): stop suggesting it."""
    from .db.models import Person

    with session_scope() as s:
        p = s.get(Person, person_id)
        rejects = dict(p.name_rejects or {})
        key = student or OWNER
        if name not in rejects.get(key, []):
            rejects[key] = [*rejects.get(key, []), name]
        p.name_rejects = rejects


def _unreject(p, key: str, name: str) -> None:
    rejects = dict(p.name_rejects or {})
    if name in rejects.get(key, []):
        rejects[key] = [n for n in rejects[key] if n != name]
        p.name_rejects = rejects


def set_kind_override(pub_id: int, kind: str | None) -> None:
    with session_scope() as s:
        s.get(Publication, pub_id).kind_override = kind or None


def set_track_override(pub_id: int, track: str | None) -> None:
    """The paper's track set by hand: a track's id, ``tracks.MAIN`` (the main track), or
    None (automatic)."""
    with session_scope() as s:
        s.get(Publication, pub_id).track_override = track or None


def forget_tracks(track_ids: set[str]) -> int:
    """Remove the tracks deleted from where they are set (the papers' tracks set by hand,
    the variants', the venue rules'): those papers, variants and rules get theirs
    automatically again. Returns how many were changed."""
    from sqlalchemy import update

    from .db.models import Venue, VenueKey

    if not track_ids:
        return 0
    with session_scope() as s:
        n = s.execute(
            update(Publication)
            .where(Publication.track_override.in_(track_ids))
            .values(track_override=None)
        ).rowcount
        n += s.execute(
            update(VenueKey).where(VenueKey.track.in_(track_ids)).values(track=None)
        ).rowcount
        for v in s.scalars(select(Venue).where(Venue.patterns.is_not(None))):
            if any(p.get("track") in track_ids for p in v.patterns or []):
                v.patterns = [
                    {**p, "track": None} if p.get("track") in track_ids else p for p in v.patterns
                ]
                n += 1
        return n


def remove_alias(person_id: int, name: str, student: str | None = None) -> None:
    """Undo a confirmed spelling: ``name`` is no longer the person (or the student)."""
    from .db.models import Person

    with session_scope() as s:
        p = s.get(Person, person_id)
        if student is None:
            p.aliases = [a for a in (p.aliases or []) if a != name]
        else:
            aliases = dict(p.student_aliases or {})
            aliases[student] = [a for a in aliases.get(student, []) if a != name]
            p.student_aliases = aliases
    reject_alias(person_id, name, student)


def set_student_outcome(person_id: int, student: str, note: str) -> None:
    """Record what became of PhD student ``student`` after the PhD (empty: forget it)."""
    from .db.models import Person

    with session_scope() as s:
        p = s.get(Person, person_id)
        outcomes = dict(p.student_outcomes or {})
        if note.strip():
            outcomes[student] = note.strip()
        else:
            outcomes.pop(student, None)
        p.student_outcomes = outcomes


# ---- author categories (e.g. "Intl. collaborators") ----------------------------------------


def author_categories() -> list:
    from .db.models import AuthorCategory

    with session_scope() as s:
        return list(s.scalars(select(AuthorCategory).order_by(AuthorCategory.name)))


def save_author_category(name: str, colour: str, category_id: int | None = None) -> int:
    from .db.models import AuthorCategory

    with session_scope() as s:
        c = s.get(AuthorCategory, category_id) if category_id else None
        if c is None:
            c = s.scalar(select(AuthorCategory).where(AuthorCategory.name == name))
        if c is None:
            c = AuthorCategory(name=name)
            s.add(c)
        c.name, c.colour = name, colour
        s.flush()
        return c.id


def delete_author_category(category_id: int) -> None:
    from .db.models import AuthorCategory, Person

    with session_scope() as s:
        if c := s.get(AuthorCategory, category_id):
            s.delete(c)
        for p in s.scalars(select(Person)):
            if str(category_id) in (p.author_categories or {}):
                cats = dict(p.author_categories)
                del cats[str(category_id)]
                p.author_categories = cats


def set_author_category(person_id: int, name: str, category_id: int, on: bool = True) -> None:
    """Put co-author ``name`` in (or out of) a category, for this person."""
    from .db.models import Person

    with session_scope() as s:
        p = s.get(Person, person_id)
        cats = dict(p.author_categories or {})
        names = [n for n in cats.get(str(category_id), []) if n != name]
        if on:
            names.append(name)
        cats[str(category_id)] = names
        p.author_categories = cats
