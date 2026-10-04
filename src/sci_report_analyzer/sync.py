"""On-demand synchronisation of sources, and auto-matching of source profiles."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable

from sqlalchemy import delete, func, select, update

from .authors import name_similarity, natural_order
from .db.models import (
    PeriodNote,
    PeriodTag,
    Person,
    Publication,
    PublicationTag,
    SourceLink,
    SourcePub,
    Thesis,
    utcnow,
)
from .db.session import session_scope
from .merge import cluster, merge_person
from .ranking.kinds import is_edited_volume
from .ranking.normalize import normalize
from .source_settings import enabled
from .sources import ADAPTERS
from .sources import doi as doi_source
from .sources.base import AuthorCandidate, FetchResult, SourceError, normalize_doi

logger = logging.getLogger(__name__)

_running: dict[int, asyncio.Task] = {}


def reset_running_states() -> None:
    """Links left 'running' by a previous process are not running any more."""
    with session_scope() as s:
        s.execute(
            update(SourceLink).where(SourceLink.sync_state == "running").values(sync_state="idle")
        )


def owner_names(person: Person, link: SourceLink) -> list[str]:
    names = [person.name, link.display_name or "", *(person.aliases or [])]
    return [n for n in dict.fromkeys(names) if n]


# ---- storing fetched data ------------------------------------------------------------------


def store_result(link_id: int, result: FetchResult) -> int:
    """Upsert a fetch result for a link (keeps SourcePub ids stable). Returns #records."""
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        assert link is not None
        if result.display_name and not link.display_name:
            link.display_name = result.display_name
        existing = {sp.external_key: sp for sp in link.source_pubs}
        seen: set[str] = set()
        for fp in result.publications:
            if fp.external_key in seen:
                continue
            seen.add(fp.external_key)
            sp = existing.get(fp.external_key)
            if sp is None:
                sp = SourcePub(link_id=link.id, external_key=fp.external_key)
                s.add(sp)
            for field in (
                "title",
                "year",
                "venue",
                "authors",
                "author_pos",
                "num_authors",
                "issn",
                "venue_type",
                "doi",
                "url",
                "doc_type",
                "pdf_url",
                "archival",
                "raw",
            ):
                setattr(sp, field, getattr(fp, field))
            if not sp.venue and is_edited_volume(sp.doc_type):
                sp.venue = sp.title  # proceedings without a venue: their title names it
        for key, sp in existing.items():
            if key not in seen:
                s.delete(sp)

        if ADAPTERS[link.source].provides_theses:
            old = {(t.thesis_id, t.role): t for t in link.theses}
            keep: set[tuple[str, str]] = set()
            kept: list[Thesis] = []
            for ft in result.theses:
                k = (ft.thesis_id, ft.role)
                if k in keep:
                    continue
                keep.add(k)
                t = old.get(k) or Thesis(link_id=link.id, thesis_id=ft.thesis_id, role=ft.role)
                if k not in old:
                    s.add(t)
                kept.append(t)
                for field in (
                    "title",
                    "student",
                    "supervisors",
                    "status",
                    "defence_date",
                    "institution",
                    "url",
                ):
                    setattr(t, field, getattr(ft, field))
                t.start_date = ft.start_date or t.start_date  # (no longer given once defended)
            gone = [t for k, t in old.items() if k not in keep]
            _carry_start_dates(kept, gone)
            for t in gone:
                s.delete(t)
        return len(seen) + len(result.theses)


def _carry_start_dates(theses: list[Thesis], gone: list[Thesis]) -> None:
    """Keep the start of a thesis once defended: theses.fr then gives it another id (its
    NNT instead of its STEP subject), and no longer its start."""
    left = [g for g in gone if g.start_date and g.student]
    for t in theses:
        if t.start_date or not t.student:
            continue
        before = next((g for g in left if g.role == t.role and g.student == t.student), None)
        if before is not None:
            t.start_date = before.start_date
            left.remove(before)


def _set_state(link_id: int, **values) -> None:
    with session_scope() as s:
        s.execute(update(SourceLink).where(SourceLink.id == link_id).values(**values))


def merge(person_id: int) -> dict[str, int]:
    with session_scope() as s:
        person = s.get(Person, person_id)
        assert person is not None
        return merge_person(s, person)


def finish_link(link_id: int, result: FetchResult) -> None:
    """Store a result obtained out-of-band (e.g. an uploaded Scholar page) and re-merge."""
    count = store_result(link_id, result)
    _set_state(
        link_id, sync_state="ok", last_synced_at=utcnow(), last_error=None, record_count=count
    )
    with session_scope() as s:
        person_id = s.get(SourceLink, link_id).person_id
    merge(person_id)


# ---- syncing -------------------------------------------------------------------------------


def doi_link(s, person: Person) -> SourceLink:
    link = next((ln for ln in person.links if ln.source == "doi"), None)
    if link is None:
        link = SourceLink(
            person_id=person.id,
            source="doi",
            external_id="doi",
            display_name="DOI records",
            status="validated",
            validated_at=utcnow(),
        )
        s.add(link)
        s.flush()
    return link


def added_dois(link: SourceLink) -> list[str]:
    """The DOIs of the papers added by hand (kept on the person's DOI source)."""
    return list((link.evidence or {}).get("added") or [])


def person_dois(person_id: int) -> dict[str, int | None]:
    """DOIs of a person's papers: {doi: publication it was given to by hand, or None}."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        out: dict[str, int | None] = {}
        for pub in s.scalars(select(Publication).where(Publication.person_id == person_id)):
            if d := normalize_doi(pub.doi_manual):
                out[d] = pub.id
        for ln in person.links:
            if ln.source == "doi":
                for d in added_dois(ln):
                    out.setdefault(d, None)
        link_ids = [ln.id for ln in person.links if ln.active and ln.source != "doi"]
        if link_ids:
            for raw in s.scalars(
                select(SourcePub.doi).where(
                    SourcePub.link_id.in_(link_ids), SourcePub.doi.is_not(None)
                )
            ):
                if d := normalize_doi(raw):
                    out.setdefault(d, None)
    return out


async def sync_dois(person_id: int, *, refresh: bool = False, remerge: bool = True) -> None:
    """Fetch the DOI records of a person's papers (cached forever) into their DOI source."""
    if not enabled("doi"):
        return
    dois = person_dois(person_id)
    with session_scope() as s:
        person = s.get(Person, person_id)
        if person is None:
            return
        link = next((ln for ln in person.links if ln.source == "doi"), None)
        if not dois and link is None:
            return
        link_id = doi_link(s, person).id
    _set_state(link_id, sync_state="running", last_error=None)
    try:
        counts = await doi_source.ensure(list(dois), refresh=refresh)
        count = store_result(link_id, doi_source.result_for(dois))
    except Exception as e:
        logger.exception("DOI records of person %s failed", person_id)
        _set_state(link_id, sync_state="error", last_error=str(e) or e.__class__.__name__)
        return
    error = f"{counts['errors']} DOI(s) could not be fetched" if counts["errors"] else None
    _set_state(
        link_id, sync_state="ok", last_synced_at=utcnow(), record_count=count, last_error=error
    )
    if remerge:
        merge(person_id)


async def sync_link(link_id: int, *, remerge: bool = True) -> None:
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        if link is None or not link.active:
            return
        person_id = link.person_id
        if link.source != "doi":
            adapter = ADAPTERS[link.source]
            ext, names = link.external_id, owner_names(link.person, link)
    if link.source == "doi":
        await sync_dois(person_id, remerge=remerge)
        return
    _set_state(link_id, sync_state="running", last_error=None)
    try:
        result = await adapter.fetch(ext, names)
        count = store_result(link_id, result)
    except (SourceError, Exception) as e:
        logger.exception("Sync of link %s failed", link_id)
        _set_state(link_id, sync_state="error", last_error=str(e) or e.__class__.__name__)
        return
    _set_state(link_id, sync_state="ok", last_synced_at=utcnow(), record_count=count)
    if remerge:
        merge(person_id)


async def sync_person(person_id: int, *, only_stale: bool = False) -> None:
    with session_scope() as s:
        person = s.get(Person, person_id)
        if person is None:
            return
        todo = [
            (ln.id, ln.source)
            for ln in person.links
            if ln.active and (not only_stale or ln.is_stale or ln.sync_state == "error")
        ]
    others = [i for i, source in todo if source != "doi"]
    await asyncio.gather(*(sync_link(i, remerge=False) for i in others))
    merge(person_id)
    # Then the DOI records of the papers (their DOIs come from the other sources).
    if not only_stale or todo:
        await sync_dois(person_id)


def start_sync(person_id: int, *, only_stale: bool = False) -> bool:
    """Start a background sync of a person (no-op if one is already running)."""
    task = _running.get(person_id)
    if task and not task.done():
        return False
    _running[person_id] = asyncio.create_task(sync_person(person_id, only_stale=only_stale))
    return True


def purge_counts(person_ids: Iterable[int]) -> dict[str, int]:
    """What a purge of these people removes."""
    ids = list(person_ids)
    with session_scope() as s:
        pubs = select(Publication.id).where(Publication.person_id.in_(ids))
        annotated = {
            p.id
            for p in s.scalars(select(Publication).where(Publication.person_id.in_(ids)))
            if p.has_overrides or p.hidden or p.merge_locked
        }
        for m in (PublicationTag, PeriodTag, PeriodNote):
            annotated |= set(s.scalars(select(m.publication_id).where(m.publication_id.in_(pubs))))
        return {
            "people": len(ids),
            "papers": s.scalar(select(func.count()).select_from(pubs.subquery())),
            "records": s.scalar(
                select(func.count())
                .select_from(SourcePub)
                .join(SourceLink, SourceLink.id == SourcePub.link_id)
                .where(SourceLink.person_id.in_(ids))
            ),
            "annotated": len(annotated),
        }


def purge(person_ids: Iterable[int]) -> dict[str, int]:
    """Remove every paper of these people (with their tags, notes and per-paper decisions)
    and every source record, so that a re-sync (``start_sync``) starts from scratch. Sources,
    periods, aliases and venue decisions are kept."""
    ids = list(person_ids)
    counts = purge_counts(ids)
    with session_scope() as s:
        links = select(SourceLink.id).where(SourceLink.person_id.in_(ids))
        s.execute(delete(SourcePub).where(SourcePub.link_id.in_(links)))
        s.execute(delete(Publication).where(Publication.person_id.in_(ids)))
        s.execute(
            update(SourceLink)
            .where(SourceLink.person_id.in_(ids))
            .values(last_synced_at=None, record_count=None, last_error=None)
        )
    return counts


def is_syncing(person_id: int) -> bool:
    task = _running.get(person_id)
    return bool(task and not task.done())


def any_running() -> bool:
    """Whether a sync is running (of anyone)."""
    return any(not t.done() for t in _running.values())


# ---- auto-matching -------------------------------------------------------------------------


def _known_titles(person: Person) -> set[str]:
    return {normalize(p.title) for p in person.publications if p.title}


def score_candidate(
    person: Person, c: AuthorCandidate | SourceLink, orcids: set[str], titles: set[str]
) -> float:
    display = c.display_name or ""
    evidence = c.evidence() if isinstance(c, AuthorCandidate) else (c.evidence or {})
    score = name_similarity(person.name, display.replace(" (name form, no idHAL)", ""))
    orcid = evidence.get("orcid") or (c.external_id if c.source == "orcid" else None)
    if orcid and orcid in orcids:
        score += 0.3
    elif orcid and person.orcid:
        score -= 0.3  # someone else (the person's ORCID is known)
    aff = (evidence.get("affiliation") or "").lower()
    if (
        person.affiliation
        and aff
        and any(w in aff for w in normalize(person.affiliation).split() if len(w) > 3)
    ):
        score += 0.1
    if titles and any(normalize(t) in titles for t in evidence.get("sample_titles") or []):
        score += 0.2
    if evidence.get("works_count") == 0:
        score -= 0.3
    if ov := evidence.get("overlap"):
        share = ov["matched"] / ov["total"] if ov["total"] else 0
        if share >= 0.3:
            score += 0.3
        elif ov["matched"] == 0 and ov["total"] >= 5:
            score -= 0.3  # none of its papers is known: probably someone else
    return round(max(0.0, min(score, 1.5)), 3)


def normalize_orcid(text: str | None) -> str | None:
    """``0000-0002-1825-0097`` from an ORCID or its URL (None if it is not one)."""
    m = re.search(r"(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dXx])", text or "")
    return "-".join(m.groups()).upper() if m else None


def suggested_orcid(person: Person) -> str | None:
    """The ORCID of the person's validated profiles, when they have one (and agree)."""
    found = {
        o
        for ln in person.links
        if ln.status == "validated" and (o := normalize_orcid((ln.evidence or {}).get("orcid")))
    }
    return found.pop() if len(found) == 1 else None


def set_orcid(person_id: int, orcid: str | None) -> None:
    with session_scope() as s:
        s.get(Person, person_id).orcid = normalize_orcid(orcid)
    rescore(person_id)


def _validated_orcids(person: Person) -> set[str]:
    out = {person.orcid} if person.orcid else set()
    for ln in person.links:
        if ln.status != "validated":
            continue
        if ln.source == "orcid":
            out.add(ln.external_id)
        if o := (ln.evidence or {}).get("orcid"):
            out.add(o)
    return out


def rescore(person_id: int) -> None:
    """Recompute candidate scores (e.g. after a validation brought new evidence)."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        orcids, titles = _validated_orcids(person), _known_titles(person)
        for ln in person.links:
            if ln.status == "candidate":
                ln.score = score_candidate(person, ln, orcids, titles)


async def discover(person_id: int, sources: Iterable[str] | None = None) -> dict[str, str]:
    """Search every source for the person; store new candidates. Returns per-source errors."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        name, aff = natural_order(person.name), person.affiliation
    names = list(sources or default_search_sources())

    async def one(src: str) -> list[AuthorCandidate]:
        return await ADAPTERS[src].search(name, aff)

    results = await asyncio.gather(*(one(src) for src in names), return_exceptions=True)
    errors: dict[str, str] = {}
    with session_scope() as s:
        person = s.get(Person, person_id)
        existing = {(ln.source, ln.external_id) for ln in person.links}
        # ORCIDs seen across candidates corroborate each other (DBLP, HAL, OpenAlex, S2...).
        all_cands = [c for r in results if isinstance(r, list) for c in r]
        orcid_votes: dict[str, int] = {}
        for c in all_cands:
            o = c.orcid or (c.external_id if c.source == "orcid" else None)
            if o and name_similarity(name, c.display_name) >= 0.75:
                orcid_votes[o] = orcid_votes.get(o, 0) + 1
        orcids = _validated_orcids(person) | {o for o, n in orcid_votes.items() if n >= 2}
        titles = _known_titles(person)
        for src, r in zip(names, results, strict=True):
            if isinstance(r, BaseException):
                errors[src] = str(r) or r.__class__.__name__
                continue
            scored = sorted(
                ((score_candidate(person, c, orcids, titles), c) for c in r),
                key=lambda t: -t[0],
            )
            for score, c in scored[:5]:
                if score < 0.5 or (c.source, c.external_id) in existing:
                    continue
                existing.add((c.source, c.external_id))
                s.add(
                    SourceLink(
                        person_id=person_id,
                        source=c.source,
                        external_id=c.external_id,
                        url=c.url,
                        display_name=c.display_name,
                        evidence=c.evidence(),
                        score=score,
                        status="candidate",
                    )
                )
    return errors


async def probe_overlap(link_id: int) -> dict[str, int] | None:
    """Fetch a candidate's records (without storing them) and count how many are papers the
    validated sources already have (same rules as the merge). Stored in its evidence as
    ``overlap``: {"matched", "total"}. None when there is nothing to compare with."""
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        adapter = ADAPTERS[link.source]
        if not adapter.provides_publications:
            return None
        link_ids = [ln.id for ln in link.person.links if ln.active]
        known = list(s.scalars(select(SourcePub).where(SourcePub.link_id.in_(link_ids))))
        if not known:
            return None
        ext, names = link.external_id, owner_names(link.person, link)
        s.expunge_all()  # (plain records for the clustering below)
    result = await adapter.fetch(ext, names)
    fetched = list({fp.external_key: fp for fp in result.publications}.values())
    for fp in fetched:
        fp.doi = normalize_doi(fp.doi)
    groups = cluster([*known, *fetched])
    n = len(known)
    matched = sum(sum(1 for i in g if i >= n) for g in groups if any(i < n for i in g))
    overlap = {"matched": matched, "total": len(fetched)}
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        link.evidence = {**(link.evidence or {}), "overlap": overlap}
        person_id = link.person_id
    rescore(person_id)
    return overlap


def default_search_sources() -> list[str]:
    """Sources searched when auto-matching (OpenAlex only with an API key)."""
    from .keys import get_key

    return [
        n
        for n, a in ADAPTERS.items()
        if a.linkable and enabled(n) and (n != "openalex" or get_key("openalex"))
    ]


def set_link_status(link_id: int, status: str) -> None:
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        was_validated = link.status == "validated"
        link.status = status
        link.validated_at = utcnow() if status == "validated" else None
        if status == "validated" and link.source == "orcid":
            link.evidence = {**(link.evidence or {}), "orcid": link.external_id}
        person_id = link.person_id
        if was_validated and status != "validated":
            # Its records no longer feed the merged list.
            for sp in list(link.source_pubs):
                s.delete(sp)
            link.last_synced_at = None
            link.sync_state = "idle"
            link.record_count = None
    if was_validated and status != "validated":
        merge(person_id)
    rescore(person_id)


def add_link(person_id: int, source: str, external_id: str, *, validated: bool = True) -> int:
    adapter = ADAPTERS[source]
    with session_scope() as s:
        link = s.scalar(
            select(SourceLink).where(
                SourceLink.person_id == person_id,
                SourceLink.source == source,
                SourceLink.external_id == external_id,
            )
        )
        if link is None:
            link = SourceLink(
                person_id=person_id,
                source=source,
                external_id=external_id,
                url=adapter.profile_url(external_id),
            )
            s.add(link)
        if validated:
            link.status = "validated"
            link.validated_at = utcnow()
            if source == "orcid":
                link.evidence = {**(link.evidence or {}), "orcid": external_id}
        s.flush()
        return link.id
