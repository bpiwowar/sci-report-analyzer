"""Cross-source clustering of a person's SourcePubs into Publications.

Records are linked when they share a DOI, or when their normalized titles match (exactly or
with a high token overlap) and their years are close. Archival versions (arXiv, HAL
preprints...) fold into the published version regardless of year. Publication ids are
kept stable across re-merges so that tags, notes and forced matches survive a re-sync.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import Person, Publication, SourcePub, utcnow
from .ranking.normalize import is_non_venue, normalize
from .ranking.service import service
from .sources import PRIORITY
from .sources.base import normalize_doi

_MIN_TITLE_TOKENS = 3


def is_archival(sp: SourcePub) -> bool:
    return bool(sp.archival) or is_non_venue(normalize(sp.venue))


def title_tokens(title: str | None) -> frozenset[str]:
    return frozenset(t for t in normalize(title).split() if len(t) > 1)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _years_compatible(a: SourcePub, b: SourcePub) -> bool:
    if is_archival(a) or is_archival(b):
        return True  # a preprint can precede the published version by years
    if a.year is None or b.year is None:
        return True
    return abs(a.year - b.year) <= service.settings.merge_year_slack


def cluster(pubs: Sequence[SourcePub]) -> list[list[int]]:
    """Group indices of ``pubs`` referring to the same work."""
    uf = _UnionFind(len(pubs))
    by_doi: dict[str, int] = {}
    by_title: dict[str, list[int]] = defaultdict(list)
    token_index: dict[str, list[int]] = defaultdict(list)
    tokens = [title_tokens(p.title) for p in pubs]
    jaccard = service.settings.merge_title_jaccard

    for i, p in enumerate(pubs):
        if p.doi:
            if p.doi in by_doi:
                uf.union(i, by_doi[p.doi])
            else:
                by_doi[p.doi] = i
        norm = normalize(p.title)
        if norm:
            by_title[norm].append(i)
        for t in tokens[i]:
            token_index[t].append(i)

    for idxs in by_title.values():
        for j in idxs[1:]:
            if _years_compatible(pubs[idxs[0]], pubs[j]):
                uf.union(idxs[0], j)

    # Fuzzy titles: compare against records sharing the rarest token.
    for i, toks in enumerate(tokens):
        if len(toks) < _MIN_TITLE_TOKENS:
            continue
        rarest = min(toks, key=lambda t: len(token_index[t]))
        for j in token_index[rarest]:
            if j <= i or uf.find(i) == uf.find(j) or len(tokens[j]) < _MIN_TITLE_TOKENS:
                continue
            inter = len(toks & tokens[j])
            if inter / len(toks | tokens[j]) >= jaccard and _years_compatible(pubs[i], pubs[j]):
                uf.union(i, j)

    # Two different DOIs of non-archival records are distinct works (e.g. a conference paper
    # and its journal extension with the same title): we still merge them since the title
    # rule says so; users can split manually.
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pubs)):
        groups[uf.find(i)].append(i)
    return list(groups.values())


def _priority(sp: SourcePub) -> int:
    src = sp.link.source
    return PRIORITY.index(src) if src in PRIORITY else len(PRIORITY)


def main_members(members: Sequence[SourcePub]) -> list[SourcePub]:
    """Non-archival members first (by source priority); archival ones last."""
    return sorted(members, key=lambda m: (is_archival(m), _priority(m), m.id or 0))


def canonical(pub: Publication, members: Sequence[SourcePub]) -> None:
    ordered = main_members(members)
    published = [m for m in ordered if not is_archival(m)] or ordered
    pub.title = next((m.title for m in ordered if m.title), pub.title)
    years = Counter(m.year for m in published if m.year)
    # The DOI record (the publisher's) gives the year, else the majority does.
    doi_year = next(
        (m.year for m in published if m.link.source == "doi" and m.year and not is_archival(m)),
        None,
    )
    pub.year = doi_year or (years.most_common(1)[0][0] if years else None)
    pub.doi = next(
        (m.doi for m in published if m.doi and not m.doi.startswith("10.48550/")),
        next((m.doi for m in ordered if m.doi), None),
    )


def merge_person(session: Session, person: Person) -> dict[str, int]:
    """(Re)build the person's Publications from the SourcePubs of validated links."""
    links = [ln for ln in person.links if ln.active]
    link_ids = [ln.id for ln in links]
    pubs = (
        list(session.scalars(select(SourcePub).where(SourcePub.link_id.in_(link_ids))))
        if link_ids
        else []
    )
    existing = {
        p.id: p
        for p in session.scalars(select(Publication).where(Publication.person_id == person.id))
    }

    locked_ids = {p.id for p in existing.values() if p.merge_locked}
    # DOI records of a DOI given by hand belong to that publication.
    pinned = {
        sp.id: pid
        for sp in pubs
        if (pid := (sp.raw or {}).get("publication_id")) in existing
        and normalize_doi(existing[pid].doi_manual) == sp.external_key
    }
    locked = [sp for sp in pubs if sp.publication_id in locked_ids or sp.id in pinned]
    free = [sp for sp in pubs if sp.publication_id not in locked_ids and sp.id not in pinned]
    for sp in pubs:
        if sp.id in pinned:
            sp.publication_id = pinned[sp.id]

    used: set[int] = set(locked_ids)
    # Publications whose records went into another one -> that one.
    absorbed: dict[int, Publication] = {}
    created = 0
    for group in cluster(free):
        members = [free[i] for i in group]
        # Reuse the publication most members already belong to (stable ids).
        votes = Counter(
            m.publication_id
            for m in members
            if m.publication_id in existing and m.publication_id not in used
        )
        if votes:
            pub = existing[votes.most_common(1)[0][0]]
        else:
            pub = Publication(person_id=person.id)
            session.add(pub)
            session.flush()
            created += 1
        used.add(pub.id)
        pub.missing = False
        for old in votes:
            if old != pub.id:
                absorbed.setdefault(old, pub)
        for m in members:
            m.publication_id = pub.id
        canonical(pub, members)

    for pub_id in locked_ids:
        members = [sp for sp in locked if sp.publication_id == pub_id]
        pub = existing[pub_id]
        pub.missing = not members
        if members:
            canonical(pub, members)
    for pub_id in set(pinned.values()) - locked_ids:
        pub = existing[pub_id]
        if pub_id not in used:
            used.add(pub_id)
            pub.missing = False
        canonical(pub, [sp for sp in pubs if sp.publication_id == pub_id])

    missing = 0
    for pub_id, pub in existing.items():
        if pub_id in used:
            continue
        if pub_id in absorbed:
            # The same paper as another one now (e.g. a source joined them): its tags,
            # notes and track go with it.
            move_annotations(session, pub, absorbed[pub_id])
        if _has_user_data(session, pub):
            pub.missing = True  # keep annotations; shown as "missing from sources"
            missing += 1
        else:
            session.delete(pub)

    person.last_merged_at = utcnow()
    session.flush()
    return {"records": len(pubs), "created": created, "missing": missing}


def _has_user_data(session: Session, pub: Publication) -> bool:
    from .db.models import PeriodNote, PeriodTag, PublicationPdf

    if pub.tags or pub.has_overrides or pub.merge_locked or pub.hidden:
        return True
    pdf = session.get(PublicationPdf, pub.id)
    if pdf is not None and (pdf.origin is None or pdf.edited_at is not None):
        return True  # a PDF uploaded or annotated by hand (a downloaded one goes)
    return any(
        session.scalar(select(m.period_id).where(m.publication_id == pub.id).limit(1)) is not None
        for m in (PeriodTag, PeriodNote)
    )


def split_member(session: Session, sp: SourcePub) -> Publication:
    """Detach one source record into its own (locked) publication."""
    old = sp.publication
    pub = Publication(person_id=sp.link.person_id, merge_locked=True)
    session.add(pub)
    session.flush()
    sp.publication_id = pub.id
    canonical(pub, [sp])
    if old is not None:
        old.merge_locked = True
        session.flush()
        session.refresh(old)
        rest = [m for m in old.members if m.id != sp.id]
        if rest:
            canonical(old, rest)
    return pub


def move_annotations(session: Session, source: Publication, target: Publication) -> None:
    """Move the tags, notes (also those within periods), track set by hand and PDF of
    ``source`` to ``target``; notes are put together."""
    from . import categories, pdfs
    from .db.models import PeriodNote, PeriodTag

    for tag in source.tags:
        if tag not in target.tags:
            target.tags.append(tag)
    source.tags = []
    if source.track_override and not target.track_override:
        target.track_override = source.track_override
    source.track_override = None
    for pt in session.scalars(select(PeriodTag).where(PeriodTag.publication_id == source.id)):
        if not session.get(PeriodTag, (pt.period_id, target.id, pt.tag_id)):
            session.add(
                PeriodTag(
                    period_id=pt.period_id,
                    publication_id=target.id,
                    tag_id=pt.tag_id,
                    number=pt.number,
                )
            )
        session.delete(pt)
    if source.note and source.note not in (target.note or ""):
        target.note = f"{target.note}\n\n{source.note}" if target.note else source.note
    source.note = None
    for pn in session.scalars(select(PeriodNote).where(PeriodNote.publication_id == source.id)):
        mine = session.get(PeriodNote, (pn.period_id, target.id))
        if mine is None:
            session.add(PeriodNote(period_id=pn.period_id, publication_id=target.id, text=pn.text))
        elif pn.text not in mine.text:
            mine.text = f"{mine.text}\n\n{pn.text}"
        session.delete(pn)
    pdfs.move(session, source.id, target.id)
    categories.move_paper(session, source.id, target.id)
    session.flush()


def join_publications(session: Session, target: Publication, others: Sequence[Publication]) -> None:
    """Merge ``others`` into ``target`` (locked), moving members and user data."""
    target.merge_locked = True
    for o in others:
        for m in list(o.members):
            m.publication = target  # keeps both collections in sync (no FK nulling on delete)
        move_annotations(session, o, target)
        session.delete(o)
    session.flush()
    session.refresh(target)
    canonical(target, target.members)


def split_out(source_pub_id: int) -> None:
    """A source's record out of its publication (not the same paper): one of its own."""
    from .db.session import session_scope

    with session_scope() as s:
        if (sp := s.get(SourcePub, source_pub_id)) is not None:
            split_member(s, sp)


def join(pub_id: int, other_ids: Sequence[int]) -> None:
    """Publications merged into ``pub_id`` (the same paper)."""
    from .db.session import session_scope

    with session_scope() as s:
        target = s.get(Publication, pub_id)
        others = [p for i in other_ids if (p := s.get(Publication, i)) is not None]
        if target is not None and others:
            join_publications(s, target, others)


def source_record(source_pub_id: int) -> dict | None:
    """What a source says about a record (its fields), and the profile it comes from
    (``link_url``, ``external_id``)."""
    from .db.session import session_scope

    with session_scope() as s:
        sp = s.get(SourcePub, source_pub_id)
        if sp is None:
            return None
        fields = (
            "title",
            "venue",
            "year",
            "authors",
            "doc_type",
            "doi",
            "issn",
            "venue_type",
            "external_key",
            "pdf_url",
            "archival",
            "url",
            "raw",
        )
        return {
            **{k: getattr(sp, k) for k in fields},
            "link_url": sp.link.url,
            "external_id": sp.link.external_id,
        }
