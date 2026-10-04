"""Venues: variants, venue rules, manual kind / level / record / search text, links."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import venue_match
from .db.models import AppSetting, Publication, SourceLink, SourcePub, Venue, VenueKey, utcnow
from .db.session import session_scope
from .i18n import _
from .ranking.badge import (
    TRACK_ORDER,
    Badge,
    category_of,
    category_order,
    core_rank_at,
    detect_track,
    edition_year,
)
from .ranking.kinds import (
    KINDS,
    VENUE_KINDS,
    WORKSHOP_KINDS,
    WORKSHOP_RE,
    KindEvidence,
    detect_kind,
    host_text,
    is_edited_volume,
)
from .ranking.normalize import normalize, tokenize
from .ranking.service import (
    VenuePattern,
    paren_acronym,
    service,
)
from .source_settings import active_links

CONFERENCE_KINDS = ("intl_conference", "natl_conference")
JOURNAL_KINDS = ("intl_journal", "natl_journal")


_ACRONYM = re.compile(r"[A-Z][A-Za-z0-9&+-]{1,11}")


def dropped_from_core(badge: Badge | None, year: int | None = None) -> tuple[str, int] | None:
    """(rank, edition year) of a CORE record its edition in force (in ``year``: this one by
    default) no longer lists ("CORE A until 2008"): a venue's rank is not its last one."""
    if not badge or badge.manual or not badge.coreRank or not badge.coreHistory:
        return None
    _edition, rank = core_rank_at(badge.coreHistory, year or utcnow().year)
    if rank is not None:
        return None
    last = max(badge.coreHistory, key=edition_year)
    return badge.coreHistory[last], edition_year(last)


def auto_short_name(
    badge: Badge | None, texts: Iterable[str | None] = (), *, workshop: bool = False
) -> str | None:
    """A venue acronym: from its ranking record (CORE aliases), else from a venue text.

    A workshop's ranking record is its main conference's, and its texts name that
    conference after "@" or "co-located with": only its own acronym is taken, if any."""
    if badge and not workshop:
        for a in badge.extra.get("aliases") or []:
            if a and _ACRONYM.fullmatch(a) and sum(c.isupper() for c in a) >= 2:
                return a
    for text in texts:
        if workshop and text and (m := _HOST_PART.search(text)):
            text = text[: m.start()]
        if a := paren_acronym(text):
            return a
    return None


# Where a workshop's text names its main conference.
_HOST_PART = re.compile(r"\s@\s?|@\s|\bco-located\b|\bin conjunction with\b", re.I)


def ensure_venue(session: Session, raw: str, source: str | None = None) -> Venue | None:
    """The venue whose variant is this raw text (created, with the variant, if none)."""
    key = service.key(raw, source)
    if not key:
        return None
    vk = session.get(VenueKey, key)
    if vk is not None:
        return vk.venue
    venue = Venue(name=service.clean(raw, source) or raw)
    session.add(venue)
    session.flush()
    session.add(VenueKey(key=key, venue_id=venue.id, example=raw, source=source))
    session.flush()
    return venue


def normalize_url(text: str | None) -> str | None:
    """A website as typed (``https://`` added when there is no scheme)."""
    text = (text or "").strip()
    if not text:
        return None
    return text if re.match(r"[a-z][a-z0-9+.-]*://", text, re.I) else f"https://{text}"


def add_venue(
    name: str, kind: str, short_name: str | None = None, url: str | None = None
) -> tuple[int, bool]:
    """A venue created by hand, matching its name (``(id, False)``: a venue already has
    this name as one of its texts)."""
    with session_scope() as s:
        key = service.key(name)
        if key and (vk := s.get(VenueKey, key)) is not None:
            return vk.venue_id, False
        v = Venue(
            name=name.strip(),
            kind=kind,
            kind_manual=True,
            short_name=short_name or None,
            short_manual=bool(short_name),
            url=normalize_url(url),
        )
        s.add(v)
        s.flush()
        if key:
            s.add(VenueKey(key=key, venue_id=v.id, manual=True, example=name.strip()))
        vid = v.id
    _changed(rematch=True)  # texts with the same name now belong to it
    return vid, True


def link_venues(pending: dict[int, int | None]) -> None:
    """Set the automatic venue of publications (``pub id -> venue id``, None: no venue)."""
    with session_scope() as s:
        for pub_id, venue_id in pending.items():
            pub = s.get(Publication, pub_id)
            if pub is not None and not pub.venue_manual:
                pub.venue_id = venue_id


def _venue_for_raw(session: Session, raw: str) -> Venue:
    venue = ensure_venue(session, raw)
    if venue is None:
        raise ValueError(f"empty venue text: {raw!r}")
    return venue


def _changed(*, rematch: bool = False) -> None:
    """After a venue change; ``rematch`` re-matches the venue texts (the variants or venue
    rules changed). The cached matches are kept: they are those of venue texts (a venue's
    "search rankings as" is its own text), its decisions applied after (``venue_badge``)."""
    service.invalidate()
    if rematch:
        venue_match.refresh(reassign=True)


# ---- manual venue-level decisions ----------------------------------------------------------


def set_correction(raw: str, text: str | None) -> None:
    """Match every variant of this venue as ``text`` (None clears)."""
    with session_scope() as s:
        _venue_for_raw(s, raw).match_text = text or None
    _changed()


def set_level(raw: str, type_: str | None, rank: str | None) -> None:
    with session_scope() as s:
        v = _venue_for_raw(s, raw)
        v.level_type, v.level_rank = (type_, rank) if rank else (None, None)
    _changed()


def set_kind(raw: str, kind: str | None) -> None:
    with session_scope() as s:
        v = _venue_for_raw(s, raw)
        v.kind, v.kind_manual = kind, bool(kind)
    _changed()


def set_record(raw: str, record_key: str | None) -> None:
    with session_scope() as s:
        _venue_for_raw(s, raw).record_key = record_key
    _changed()


def update_venue(venue_id: int, **values: Any) -> None:
    rematch = False
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        if values.get("name") and values["name"] != v.name:
            # The name was usually derived from a venue text: keep matching that text.
            key = service.key(v.name)
            if key and s.get(VenueKey, key) is None:
                s.add(VenueKey(key=key, venue_id=v.id, manual=True, example=v.name))
                rematch = True
        for k, val in values.items():
            setattr(v, k, val)
        if "short_name" in values and "short_manual" not in values:
            v.short_manual = bool(values["short_name"])  # (none: inferred again)
        if "kind" in values:
            v.kind_manual = bool(values["kind"])
    _changed(rematch=rematch)


def merge_venues(
    target_id: int,
    other_ids: list[int],
    name: str | None = None,
    short_name: str | None = None,
    kind: str | None = None,
) -> None:
    """Move the variants (with their tracks), venue rules, ISSNs and publications of other
    venues into the target (manual); ``name``: the target's new name (its former one stays
    a variant), ``short_name``: its acronym and ``kind`` its kind, set by hand."""
    with session_scope() as s:
        target = s.get(Venue, target_id)
        for oid in other_ids:
            if oid == target_id:
                continue
            other = s.get(Venue, oid)
            for vk in list(other.keys):
                vk.venue_id, vk.manual = target.id, True
            for pub in s.scalars(select(Publication).where(Publication.venue_id == oid)):
                pub.venue_id = target.id
            # Keep manual decisions the target doesn't have.
            for attr in (
                "level_type",
                "level_rank",
                "record_key",
                "match_text",
                "url",
            ):
                if getattr(target, attr) is None:
                    setattr(target, attr, getattr(other, attr))
            if other.short_manual and not target.short_manual:
                target.short_name, target.short_manual = other.short_name, True
            if not target.kind_manual and other.kind_manual:
                target.kind, target.kind_manual = other.kind, True
            if other.patterns:
                target.patterns = [*(target.patterns or []), *other.patterns]
            if other.hosts:
                target.hosts = [*(target.hosts or []), *other.hosts]
            if other.joint and not target.joint:
                target.joint = other.joint
            if other.identifiers:
                ids = dict(target.identifiers or {})
                for k, vals in other.identifiers.items():
                    ids[k] = list(dict.fromkeys([*(ids.get(k) or []), *vals]))
                target.identifiers = ids
            s.flush()
            s.expire(other, ["keys"])
            s.delete(other)
        # Workshops hosted by a merged venue are now hosted by the target.
        merged = set(other_ids) - {target_id}
        for w in s.scalars(select(Venue).where(Venue.hosts.is_not(None))):
            if any(h.get("venue_id") in merged for h in w.hosts):
                w.hosts = [
                    {**h, "venue_id": target_id} if h.get("venue_id") in merged else h
                    for h in w.hosts
                    if w.id != target_id or h.get("venue_id") not in (target_id, *merged)
                ] or None
        # Joint venues made of a merged venue are now made of the target.
        for j in s.scalars(select(Venue).where(Venue.joint.is_not(None))):
            j.joint = _remap_joint(j.id, j.joint, merged, target_id)
    values = {
        "name": (name or "").strip(),
        "short_name": (short_name or "").strip(),
        "kind": kind,
    }
    if values := {k: v for k, v in values.items() if v}:
        update_venue(target_id, **values)
    _changed(rematch=True)


def _remap_joint(
    venue_id: int, joint: dict[str, Any], merged: set[int], target_id: int
) -> dict[str, Any] | None:
    def remap(i: int | None) -> int | None:
        i = target_id if i in merged else i
        return None if i == venue_id else i

    parts = list(dict.fromkeys(p for p in map(remap, joint.get("parts") or []) if p))
    use = remap(joint.get("use"))
    out = {**joint, "parts": parts, "use": use if use in parts else None}
    return out if parts or out.get("manual") else None


def split_key(key: str) -> None:
    """Detach a variant into its own venue."""
    with session_scope() as s:
        vk = s.get(VenueKey, key)
        venue = Venue(name=(vk.example and service.clean(vk.example, vk.source)) or key)
        s.add(venue)
        s.flush()
        vk.venue_id, vk.manual = venue.id, True
    _changed(rematch=True)  # automatic publication links are recomputed when displayed


def add_variant(venue_id: int, raw: str, source: str | None = None) -> None:
    """Assign a raw venue text to a venue by hand (a manual variant)."""
    key = service.key(raw, source)
    if not key:
        raise ValueError(f"empty venue text: {raw!r}")
    with session_scope() as s:
        vk = s.get(VenueKey, key)
        if vk is None:
            s.add(VenueKey(key=key, venue_id=venue_id, manual=True, example=raw, source=source))
        else:
            vk.venue_id, vk.manual = venue_id, True
    _changed(rematch=True)


def set_variant_track(key: str, track: str | None) -> None:
    with session_scope() as s:
        s.get(VenueKey, key).track = track or None
    _changed(rematch=True)


def link_publication(pub_id: int, venue_id: int | None) -> None:
    """Link a publication to a venue by hand (None: back to the automatic venue)."""
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        pub.venue_id, pub.venue_manual = venue_id, venue_id is not None


@dataclass
class VenueHit:
    """A search result: a known venue, or a ranking record without a venue yet."""

    label: str  # the name
    venue_id: int | None = None
    record_key: str | None = None
    badge: Badge | None = None
    detail: str = ""
    short: str | None = None  # acronym
    kind: str | None = None  # kind of venue (e.g. "International conference")


def find_venues(query: str, limit: int = 10) -> list[VenueHit]:
    """Venues whose name, short name or variant texts contain ``query``, then the ranking
    records matching it (those not pinned to one of these venues)."""
    q = normalize(query)
    if len(q) < 2:
        return []
    hits: list[VenueHit] = []
    pinned: set[str] = set()
    with session_scope() as s:
        venues = s.scalars(select(Venue).options(selectinload(Venue.keys)).order_by(Venue.name))
        for v in venues:
            if v.record_key:
                pinned.add(v.record_key)
            texts = [v.name, v.short_name or "", *(k.example or k.key for k in v.keys)]
            if not any(q in normalize(t) for t in texts):
                continue
            hits.append(
                VenueHit(
                    v.name,
                    venue_id=v.id,
                    detail=_("venue"),
                    short=v.short_name or (None if v.short_manual else paren_acronym(v.name)),
                    kind=KINDS.get(v.kind or ""),
                )
            )
    hits = hits[:limit]
    for b in service.search(query, limit):
        if b.recordKey in pinned:
            continue
        hits.append(
            VenueHit(
                b.name or "?",
                record_key=b.recordKey,
                badge=b,
                detail=_("{source} record · {score}%").format(
                    source=b.source, score=round(b.score * 100)
                ),
                short=auto_short_name(b),
                kind={"conference": _("Conference"), "journal": _("Journal")}.get(
                    b.type or "", b.type
                ),
            )
        )
    return hits


def venue_for_record(record_key: str) -> int:
    """The venue pinned to a ranking record (created, with the record pinned, if none)."""
    with session_scope() as s:
        v = s.scalar(select(Venue).where(Venue.record_key == record_key))
        if v is not None:
            return v.id
        b = service.badge_for_record(record_key)
        if b is None:
            raise ValueError(f"unknown ranking record: {record_key}")
        v = Venue(name=b.name or record_key, short_name=auto_short_name(b), record_key=record_key)
        s.add(v)
        s.flush()
        vid = v.id
    _changed()
    return vid


def use_venue(
    pub_id: int, target_id: int, merge_ids: Iterable[int] = (), paper_venues: Iterable[int] = ()
) -> None:
    """Use ``target_id`` for a paper, after merging the ``merge_ids`` venues into it. When
    all the venues of the paper's sources (``paper_venues``) end up in the target, the link
    stays automatic; otherwise the paper is linked by hand."""
    merge_ids = [i for i in dict.fromkeys(merge_ids) if i != target_id]
    if merge_ids:
        merge_venues(target_id, merge_ids)
    rest = set(paper_venues) - {target_id, *merge_ids}
    if rest or not set(paper_venues):
        link_publication(pub_id, target_id)
    else:
        link_publication(pub_id, None)
        link_venues({pub_id: target_id})


def clear_manual(venue_id: int) -> None:
    update_venue(
        venue_id,
        kind=None,
        hosts=None,
        joint=None,
        level_type=None,
        level_rank=None,
        record_key=None,
        match_text=None,
        short_name=None,
    )


# ---- workshops -------------------------------------------------------------------------------


def save_hosts(venue_id: int, hosts: list[dict[str, Any]]) -> None:
    """Set a workshop's main conferences ({"venue_id", "from", "to"}, years included)."""
    clean = [
        {"venue_id": int(h["venue_id"]), "from": h.get("from") or None, "to": h.get("to") or None}
        for h in hosts
        if h.get("venue_id") and int(h["venue_id"]) != venue_id
    ]
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        v.hosts = clean or None
        if clean and v.kind not in WORKSHOP_KINDS:
            v.kind = "natl_workshop" if (v.kind or "").startswith("natl") else "intl_workshop"
            v.kind_manual = True
    _changed()


def suggest_host(session: Session, texts: Iterable[str | None]) -> Venue | None:
    """The main conference a workshop's texts name ("X @ SIGIR", "co-located with ECIR"):
    the venue with that text as a variant, else with that short name."""
    for text in texts:
        host = host_text(text)
        if not host:
            continue
        key = service.key(host)
        vk = session.get(VenueKey, key) if key else None
        if vk is not None:
            return vk.venue
        acronym = re.sub(r"[\s'’]*\d{2,4}\b.*$", "", host).strip()
        for cand in (host, acronym):
            v = session.scalar(
                select(Venue).where(func.lower(Venue.short_name) == cand.lower()).limit(1)
            )
            if v is not None:
                return v
        # A venue named like the text (e.g. its acronym as automatic name), or with a
        # DBLP-style "... (ACRONYM)" variant.
        for cand in (acronym, host):
            v = session.scalar(select(Venue).where(func.lower(Venue.name) == cand.lower()).limit(1))
            if v is not None:
                return v
        if acronym:
            vk = session.scalar(
                select(VenueKey).where(VenueKey.example.like(f"%({acronym})")).limit(1)
            )
            if vk is not None:
                return vk.venue
    return None


def host_suggestion(venue_id: int) -> tuple[int, str] | None:
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        host = suggest_host(s, [v.name, *(k.example for k in v.keys)])
        return (host.id, host.name) if host is not None and host.id != venue_id else None


def workshop_variants(venue_id: int) -> list[tuple[str, str]]:
    """Variants of a (non-workshop) venue that look like workshops: (key, raw text)."""
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        if v is None or v.kind in WORKSHOP_KINDS or WORKSHOP_RE.search(v.name or ""):
            return []
        return [(k.key, k.example) for k in v.keys if k.example and WORKSHOP_RE.search(k.example)]


def split_as_workshop(key: str) -> int:
    """Detach a variant into a new workshop venue hosted by its venue. Returns its id."""
    with session_scope() as s:
        vk = s.get(VenueKey, key)
        host = vk.venue
        text = vk.example or key
        scope = "natl" if (host.kind or "").startswith("natl") else "intl"
        venue = Venue(
            name=service.clean(text, vk.source) or text,
            kind=f"{scope}_workshop",
            kind_manual=True,
            hosts=[{"venue_id": host.id, "from": None, "to": None}],
        )
        s.add(venue)
        s.flush()
        vk.venue_id, vk.manual = venue.id, True
        for pub in s.scalars(
            select(Publication).where(
                Publication.venue_id == host.id, Publication.venue_manual.is_(False)
            )
        ):
            pub.venue_id = None  # relinked when displayed
        new_id = venue.id
    _changed(rematch=True)
    return new_id


# ---- joint venues -----------------------------------------------------------------------------


def set_joint_parts(venue_id: int, parts: list[int] | None) -> None:
    """A joint venue's parts, set by hand (an empty list: not a joint venue); None goes back
    to the parts found automatically."""
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        if parts is None:
            use = (v.joint or {}).get("use")
            v.joint = {"parts": [], "manual": False, "use": use} if use else None
        else:
            parts = list(dict.fromkeys(int(p) for p in parts if p and int(p) != venue_id))
            use = (v.joint or {}).get("use")
            v.joint = {"parts": parts, "manual": True, "use": use if use in parts else None}
    _changed()


def set_joint_use(venue_id: int, part_id: int | None) -> None:
    """The part whose level a joint venue takes (None: the level its parts share)."""
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        v.joint = {**(v.joint or {"parts": []}), "use": part_id}
    _changed()


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).upper()


_WORD = re.compile(r"[^\W\d_][\w&+]*")


def _acronyms(text: str | None) -> set[str]:
    """The words of a text that look like acronyms (two capitals or more), folded."""
    return {_fold(w) for w in _WORD.findall(text or "") if sum(c.isupper() for c in w) >= 2}


def detect_parts(rows: list[VenueRow]) -> dict[int, list[int]]:
    """Joint venues found from their texts: those naming the acronyms of two conferences or
    more ("CORIA-TALN 2023", "… (CORIA) … (TALN)"). Venue id → its parts (other venues).

    A venue's own acronym counts unless set by hand (an automatic one may come from one of
    the parts); venues that look joint are not parts of others."""
    known: dict[str, list[VenueRow]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (-r.publications, r.id)):
        if r.kind in CONFERENCE_KINDS and not r.hosts and r.short_name:
            known[_fold(r.short_name)].append(r)
    named: dict[int, set[str]] = {}
    for r in rows:
        if r.kind in WORKSHOP_KINDS or r.hosts:
            continue
        texts = [r.name, *(ex for _k, ex, *_rest in r.variants), *r.raw_examples]
        found = set().union(*map(_acronyms, texts)) & known.keys()
        if r.short_manual and r.short_name:
            found.discard(_fold(r.short_name))
        if len(found) >= 2:
            named[r.id] = found
    out = {}
    for vid, acronyms in named.items():
        parts = []
        for a in sorted(acronyms):
            part = next((p for p in known[a] if p.id != vid and p.id not in named), None)
            if part is not None and part.id not in parts:
                parts.append(part.id)
        if len(parts) >= 2:
            out[vid] = parts
    return out


def _store_parts(venues: list[Venue], found: dict[int, list[int]]) -> bool:
    """Store the parts found automatically (not over manual ones); True if any changed.
    ``venues`` (detached) are updated too."""
    changed = {}
    for v in venues:
        j = v.joint or {}
        if j.get("manual"):
            continue
        parts = found.get(v.id, [])
        if parts != (j.get("parts") or []):
            use = j.get("use")
            changed[v.id] = {"parts": parts, "manual": False, "use": use} if parts or use else None
    if changed:
        with session_scope() as s:
            for vid, joint in changed.items():
                if (v := s.get(Venue, vid)) is not None and not (v.joint or {}).get("manual"):
                    v.joint = joint
        for v in venues:
            if v.id in changed:
                v.joint = changed[v.id]
        service.invalidate()
    return bool(changed)


def _level_key(b: Badge | None) -> str:
    return category_of(b, None).base_key


async def part_badges(
    v: Venue, venues: dict[int, Venue] | None = None
) -> list[tuple[Venue, Badge | None]]:
    """A joint venue's parts with their levels (their own: parts of parts are ignored)."""
    ids = [i for i in v.parts if i != v.id]
    if not ids:
        return []
    if venues is None:
        with session_scope() as s:
            venues = {
                p.id: p
                for p in s.scalars(
                    select(Venue).where(Venue.id.in_(ids)).options(selectinload(Venue.keys))
                )
            }
            s.expunge_all()
    parts = [venues[i] for i in ids if i in venues]
    return [(p, await venue_badge(p, venues=venues, joint=False)) for p in parts]


def joint_differ(parts: list[tuple[Venue, Badge | None]]) -> bool:
    """The parts of a joint venue have different levels."""
    return len({_level_key(b) for _p, b in parts}) > 1


def _joint_badge(v: Venue, parts: list[tuple[Venue, Badge | None]]) -> Badge | None:
    """The level of the part chosen by hand, else the one the parts share; when they differ,
    the lowest (until a part is chosen)."""
    names = [p.name for p, _b in parts]
    use = (v.joint or {}).get("use")
    chosen = next((b for p, b in parts if p.id == use), None)
    if chosen is not None:
        return chosen.copy(manual=True, extra={**chosen.extra, "joint": names})
    if any(p.id == use for p, _b in parts):
        return None  # the chosen part is not ranked
    differ = joint_differ(parts)
    if differ:
        b = max((b for _p, b in parts), key=lambda b: category_order(category_of(b, None)))
    else:
        b = min((b for _p, b in parts if b is not None), key=_quality, default=None)
    if b is None:
        return None
    return b.copy(manual=False, extra={**b.extra, "joint": names, "joint_differ": differ})


# ---- resolution through a venue ------------------------------------------------------------


async def venue_badge(
    v: Venue,
    samples: Iterable[tuple[str, str | None, str | None]] | None = None,
    *,
    venues: dict[int, Venue] | None = None,
    joint: bool = True,
) -> Badge | None:
    """Badge of a venue from its manual decisions, else that of its parts for a joint venue
    (``venues``: where to find them, else loaded; ``joint``: False ignores the parts), else
    by matching its texts.

    ``samples`` are (text, ISSN, conference/journal hint) of its source records, most
    common first; they are matched like the papers' venues (with the acronym and type
    hints: "Conference on Neural Information Processing Systems (NeurIPS)" is CORE
    NeurIPS, while the bare name would match a Scimago journal), and the best is kept.
    """
    from .ranking.badge import badge_from_level

    if v.level:
        return badge_from_level(v.level)
    if v.record_key and (b := service.badge_for_record(v.record_key)):
        return b.copy(manual=True)
    if v.match_text:
        return await service.resolve(v.match_text, venue_override=v.match_text)
    if joint and v.parts and (parts := await part_badges(v, venues)):
        return _joint_badge(v, parts)
    tried = (
        list(dict.fromkeys(samples or ()))[:5]
        or [(k.example, None, None) for k in v.keys if k.example][:5]
    )
    best: Badge | None = None
    for text, issn, hint in tried:
        b = await service.resolve(text, issn, hint)
        if b is not None and (best is None or _quality(b) < _quality(best)):
            best = b
    return best or await service.resolve(v.name)


def _quality(b: Badge) -> tuple:
    ranked = bool(b.quartile or b.coreRank)
    return (0 if ranked and b.exact else 1 if ranked else 2, -b.score)


# ---- venue lists ---------------------------------------------------------------------------


@dataclass
class VenueRow:
    id: int
    name: str
    kind: str
    kind_manual: bool
    badge: Badge | None
    manual: bool
    records: int = 0
    publications: int = 0
    people: set[int] = field(default_factory=set)
    # (key, raw text, records, manual, track)
    variants: list[tuple[str, str | None, int, bool, str | None]] = field(default_factory=list)
    raw_examples: Counter = field(default_factory=Counter)
    short_name: str | None = None  # set by hand, or found automatically
    url: str | None = None  # its website
    short_manual: bool = False
    source_texts: Counter = field(default_factory=Counter)  # (source, raw text) -> records
    pub_ids: set[int] = field(default_factory=set)  # its publications
    # A workshop's main conferences: (venue id, name, from, to).
    hosts: list[tuple[int, str, int | None, int | None]] = field(default_factory=list)
    # A joint venue's parts: (venue id, name, level).
    parts: list[tuple[int, str, Badge | None]] = field(default_factory=list)
    parts_manual: bool = False
    joint_use: int | None = None  # the part whose level it takes, chosen by hand
    joint_differ: bool = False  # its parts have different levels


async def venue_rows(only: set[int] | None = None, *, detect: bool = True) -> list[VenueRow]:
    """Every venue seen in the sources, with its variants and usage. With ``only``, the
    badges and kinds of the other venues are not resolved (their last known kind).

    The joint venues' parts are found from the result (``detect``); when they change, the
    rows are computed again."""
    # 1. Match every venue text to its venue.
    venue_match.refresh()
    texts = venue_match.matches()
    by_issn = venue_match.issn_venues()
    by_venue: dict[int, list[SourcePub]] = defaultdict(list)
    key_count: Counter = Counter()
    with session_scope() as s:
        pubs = list(
            s.scalars(
                select(SourcePub)
                .where(SourcePub.venue.is_not(None))
                .options(selectinload(SourcePub.link))
            )
        )
        for sp in pubs:
            if sp.archival or not sp.link.active:
                continue
            m = texts.get((sp.link.source, sp.venue))
            vid = by_issn.get(venue_match.norm_issn(sp.issn) or "") or (m and m.venue_id)
            if vid:
                by_venue[vid].append(sp)
            if m:
                key_count[m.key] += 1
        s.expunge_all()

    # 2. Read-only: badges and kinds (resolutions write the cache meanwhile).
    auto_kinds: dict[int, str] = {}
    auto_shorts: dict[int, str | None] = {}
    out = []
    with session_scope() as s:
        manual_pubs: dict[int, set[int]] = defaultdict(set)
        for pid, vid in s.execute(
            select(Publication.id, Publication.venue_id).where(Publication.venue_manual.is_(True))
        ):
            manual_pubs[vid].add(pid)
        manual_links = Counter({vid: len(ids) for vid, ids in manual_pubs.items()})
        venues = list(s.scalars(select(Venue).options(selectinload(Venue.keys))))
        s.expunge_all()
    st = service.settings
    names = {x.id: x.name for x in venues}
    by_id = {x.id: x for x in venues}
    for v in venues:
        members = by_venue.get(v.id, [])
        if not members and not v.has_manual and not manual_links.get(v.id):
            continue
        samples = Counter((m.venue, m.issn, m.venue_type) for m in members)
        light = only is not None and v.id not in only
        badge = (
            None
            if light
            else await venue_badge(v, [s for s, _n in samples.most_common()], venues=by_id)
        )
        if light:
            kind = v.kind or "other"
        elif v.kind_manual and v.kind in VENUE_KINDS:
            kind = v.kind
        else:
            # Its edited volumes (proceedings) say nothing of the venue's kind.
            papers = [m for m in members if not is_edited_volume(m.doc_type)] or members
            types = Counter(m.venue_type for m in papers if m.venue_type)
            docs = " ".join({m.doc_type for m in papers if m.doc_type})
            kind = detect_kind(
                badge,
                KindEvidence(
                    v.match_text or v.name, types.most_common(1)[0][0] if types else None, docs
                ),
                national_keywords=st.national_keywords,
                international_keywords=st.international_keywords,
                unknown_scope=st.unknown_scope,
            )
            if kind not in VENUE_KINDS:  # (a thesis: not a venue's kind)
                kind = "other"
            if v.hosts and kind not in (*WORKSHOP_KINDS, "shared_task"):
                kind = "natl_workshop" if kind.startswith("natl") else "intl_workshop"
            if kind != v.kind:
                auto_kinds[v.id] = kind
        row = VenueRow(
            v.id,
            v.name,
            kind,
            v.kind_manual,
            badge,
            v.has_manual,
            records=len(members),
            publications=len({m.publication_id for m in members if m.publication_id})
            + manual_links.get(v.id, 0),
            people={m.link.person_id for m in members},
        )
        row.hosts = [
            (h["venue_id"], names.get(h["venue_id"], "?"), h.get("from"), h.get("to"))
            for h in v.hosts or []
        ]
        if v.parts and not light:
            parts = await part_badges(v, by_id)
            row.parts = [(p.id, p.name, b) for p, b in parts]
            row.joint_differ = joint_differ(parts)
        row.parts_manual = bool((v.joint or {}).get("manual"))
        row.joint_use = (v.joint or {}).get("use")
        row.pub_ids = {m.publication_id for m in members if m.publication_id} | manual_pubs.get(
            v.id, set()
        )
        for vk in v.keys:
            row.variants.append((vk.key, vk.example, key_count.get(vk.key, 0), vk.manual, vk.track))
        row.raw_examples.update(m.venue for m in members)  # as in the sources
        row.source_texts.update((m.link.source, m.venue) for m in members)
        row.short_manual = v.short_manual
        row.url = v.url
        if v.short_manual or light:
            row.short_name = v.short_name
        else:
            row.short_name = auto_short_name(
                badge,
                [*(t for t, _n in row.raw_examples.most_common()), v.name],
                workshop=row.kind in WORKSHOP_KINDS,
            )
            if row.short_name != v.short_name:
                auto_shorts[v.id] = row.short_name
        out.append(row)

    # 3. Cache the automatic kinds and acronyms ("inferred").
    if auto_kinds or auto_shorts:
        with session_scope() as s:
            for vid, kind in auto_kinds.items():
                if (v := s.get(Venue, vid)) is not None and not v.kind_manual:
                    v.kind = kind
            for vid, short in auto_shorts.items():
                if (v := s.get(Venue, vid)) is not None and not v.short_manual:
                    v.short_name = short
    if detect and only is None and _store_parts(venues, detect_parts(out)):
        return await venue_rows(only, detect=False)
    return out


@dataclass
class VenuePaper:
    id: int
    title: str | None
    year: int | None
    person_id: int
    person: str
    hidden: bool


def venue_papers(pub_ids: Iterable[int]) -> list[VenuePaper]:
    """Publications (with their person), by person then most recent first."""
    from .db.models import Person

    ids = list(pub_ids)
    if not ids:
        return []
    with session_scope() as s:
        rows = s.execute(
            select(Publication, Person.name)
            .join(Person, Person.id == Publication.person_id)
            .where(Publication.id.in_(ids))
        ).all()
        out = [
            VenuePaper(p.id, p.title, p.year_override or p.year, p.person_id, name, bool(p.hidden))
            for p, name in rows
        ]
    return sorted(out, key=lambda p: (p.person.lower(), -(p.year or 0), (p.title or "").lower()))


def _row_tokens(r: VenueRow) -> set[str]:
    out = set(tokenize(r.name))
    for _k, example, *_rest in r.variants:
        out |= set(tokenize(example))
    return out


def similar_venues(
    row: VenueRow, rows: list[VenueRow], query: str = "", limit: int = 15
) -> list[tuple[VenueRow, float]]:
    """Other venues to merge with ``row``: those matching ``query`` (name, short name or
    variant texts), or without a query the most similar ones (shared words, same acronym)."""
    q = normalize(query)
    mine = _row_tokens(row)
    out = []
    for r in rows:
        if r.id == row.id:
            continue
        score = _similarity(row, mine, r, _row_tokens(r))
        if q:
            texts = [r.name, r.short_name or "", *(ex or k for k, ex, *_rest in r.variants)]
            if not any(q in normalize(t) for t in texts):
                continue
        elif score < 0.3:
            continue
        out.append((r, score))
    out.sort(key=lambda t: (-t[1], -t[0].publications))
    return out[:limit]


def _similarity(a: VenueRow, a_toks: set[str], b: VenueRow, b_toks: set[str]) -> float:
    """Shared words (Jaccard), plus 0.5 for the same acronym."""
    score = len(a_toks & b_toks) / len(a_toks | b_toks) if a_toks | b_toks else 0.0
    same_short = bool(a.short_name and b.short_name) and (
        a.short_name.lower() == b.short_name.lower()
    )
    return score + 0.5 * same_short


def _family(kind: str) -> str:
    """Venues of different families are never proposed for a merge."""
    if kind in WORKSHOP_KINDS:
        return "workshop"
    if kind in CONFERENCE_KINDS:
        return "conference"
    if kind in JOURNAL_KINDS:
        return "journal"
    return "other"


NOT_SAME_KEY = "venues.not_same"
PROPOSAL_SCORE = 0.5


def not_same_pairs() -> set[tuple[int, int]]:
    """Pairs of venues said not to be the same (smallest id first)."""
    with session_scope() as s:
        row = s.get(AppSetting, NOT_SAME_KEY)
        return {(a, b) for a, b in (row.value if row else [])}


def set_not_same(a: int, b: int) -> None:
    """Never propose to merge these two venues again."""
    pairs = not_same_pairs() | {(min(a, b), max(a, b))}
    with session_scope() as s:
        s.merge(AppSetting(key=NOT_SAME_KEY, value=sorted([a, b] for a, b in pairs)))


def merge_proposals(rows: list[VenueRow]) -> list[tuple[VenueRow, VenueRow, float]]:
    """Pairs of venues of the same family that look alike (as for ``similar_venues``), most
    alike first; in each, the venue to keep (most papers) comes first. Pairs said not to be
    the same, or already related (see ``related``), are left out."""
    toks = {r.id: _row_tokens(r) for r in rows}
    # Only venues sharing a word or their acronym are compared.
    index: dict[tuple[str, str], list[VenueRow]] = defaultdict(list)
    for r in rows:
        keys = {*toks[r.id], *([f"={r.short_name.lower()}"] if r.short_name else [])}
        for k in keys:
            index[_family(r.kind), k].append(r)
    not_same = not_same_pairs()
    seen: set[tuple[int, int]] = set()
    out = []
    for bucket in index.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                pair = (min(a.id, b.id), max(a.id, b.id))
                if pair in seen or pair in not_same:
                    continue
                seen.add(pair)
                if related(a, b):
                    continue
                score = _similarity(a, toks[a.id], b, toks[b.id])
                if score >= PROPOSAL_SCORE:
                    keep, other = sorted((a, b), key=lambda r: (-r.publications, r.id))
                    out.append((keep, other, score))
    out.sort(key=lambda t: (-t[2], -(t[0].publications + t[1].publications)))
    return out


# ---- related venues ---------------------------------------------------------------------------
#
# How another venue relates to one: the same venue, one of its tracks, a joint conference
# including it or one of its workshops. "~" reverses it: the venue is the other's track…

RELATION_KINDS = ("same", "track", "joint", "workshop")
_FINDINGS = re.compile(r"\bfindings\b", re.I)
# Two conferences joined by "and" ("… Conference on X and the International Joint Conference
# on Y"); IJCAI, a single "International Joint Conference", is not one.
_JOINT = re.compile(
    r"\b(?:conference|symposium|meeting|workshop)\b.*\band\b(?:\s+the)?\b.*"
    r"\b(?:conference|symposium|meeting|workshop)\b",
    re.I,
)


def _texts(r: VenueRow) -> list[str]:
    return [r.name, *(ex for _k, ex, *_rest in r.variants if ex)]


def _track_word(text: str) -> str | None:
    if _FINDINGS.search(text):
        return "findings"
    return detect_track(text)


def venue_track(r: VenueRow) -> str | None:
    """The track a venue looks like (Findings, demo…): named by its name or all its texts."""
    if t := _track_word(r.name):
        return t
    tracks = {_track_word(t) for t in _texts(r)}
    return tracks.pop() if len(tracks) == 1 else None


def looks_joint(r: VenueRow) -> bool:
    """A venue that looks like a joint conference (its parts known, or its name joins two);
    a workshop is not one (its texts name its main conference)."""
    if r.kind in WORKSHOP_KINDS or r.hosts:
        return False
    return bool(r.parts) or any(_JOINT.search(t) for t in _texts(r))


def _looks_workshop(r: VenueRow) -> bool:
    return r.kind in WORKSHOP_KINDS or bool(r.hosts) or bool(WORKSHOP_RE.search(r.name))


def guess_relation(row: VenueRow, other: VenueRow) -> str:
    """How ``other`` relates to ``row``: "same", "track:<track>", "joint" (other is a joint
    conference including row), "workshop" (other is a workshop of row), or "~" + one of the
    last three when it is the other way round."""
    track, other_track = venue_track(row), venue_track(other)
    if other_track and not track:
        return f"track:{other_track}"
    if track and not other_track:
        return f"~track:{track}"
    joint, other_joint = looks_joint(row), looks_joint(other)
    if other_joint and not joint:
        return "joint"
    if joint and not other_joint:
        return "~joint"
    workshop, other_workshop = _looks_workshop(row), _looks_workshop(other)
    if other_workshop and not workshop:
        return "workshop"
    if workshop and not other_workshop:
        return "~workshop"
    return "same"


def relation_choices(guess: str, *, both: bool = False) -> list[str]:
    """The relations to offer: the guess, the same venue, and each relation both ways (the
    tracks only in the guessed direction, unless ``both``)."""
    rev = "~" if guess.startswith("~") else ""
    tracks = [f"{rev}track:{t}" for t in TRACK_ORDER]
    if both:
        tracks += [f"{'' if rev else '~'}track:{t}" for t in TRACK_ORDER]
    out = ["same", *tracks, "joint", "~joint", "workshop", "~workshop"]
    return [guess, *(r for r in out if r != guess)]


def related(row: VenueRow, other: VenueRow) -> bool:
    """``other`` is already a part, a joint venue, a host or a workshop of ``row``."""
    return (
        any(other.id == p for p, *_ in row.parts)
        or any(row.id == p for p, *_ in other.parts)
        or any(other.id == h for h, *_ in row.hosts)
        or any(row.id == h for h, *_ in other.hosts)
    )


def suggest_related(
    row: VenueRow, rows: list[VenueRow], limit: int = 4
) -> list[tuple[list[VenueRow], str]]:
    """Venues that look related to ``row`` (those said not to be or already related left
    out), with their guessed relation; those that look like the same venue as each other
    with the same relation are grouped (most papers first), to be merged together first."""
    not_same = not_same_pairs()
    found = [
        r
        for r, _score in similar_venues(row, rows, limit=limit * 2)
        if (min(r.id, row.id), max(r.id, row.id)) not in not_same and not related(row, r)
    ][:limit]
    groups: list[tuple[list[VenueRow], str]] = []
    for r in found:
        rel = guess_relation(row, r)
        toks = _row_tokens(r)
        group = next(
            (
                g
                for g, grel in groups
                if grel == rel
                and rel != "same"
                and all(_similarity(r, toks, o, _row_tokens(o)) >= PROPOSAL_SCORE for o in g)
            ),
            None,
        )
        if group is None:
            groups.append(([r], rel))
        else:
            group.append(r)
            group.sort(key=lambda o: (-o.publications, o.id))
    return groups


def relate_venues(row_id: int, other_ids: list[int], relation: str) -> int:
    """Record how other venues relate to a venue (see ``guess_relation``); several others
    are merged together first (into the first). Returns the venue left for ``row_id``
    (another one when it was merged)."""
    other, *rest = other_ids
    if relation == "same":
        merge_venues(row_id, other_ids)
        return row_id
    if rest:
        merge_venues(other, rest)
    reverse = relation.startswith("~")
    kind, _sep, track = relation.lstrip("~").partition(":")
    sat, main = (row_id, other) if reverse else (other, row_id)  # the satellite and its venue
    if kind == "track":
        with session_scope() as s:
            for vk in s.scalars(select(VenueKey).where(VenueKey.venue_id == sat)):
                vk.track = vk.track or track
        merge_venues(main, [sat])
        return main
    if kind == "joint":
        with session_scope() as s:
            j = s.get(Venue, sat).joint or {}
        set_joint_parts(sat, [*(j.get("parts") or []), main])
    elif kind == "workshop":
        with session_scope() as s:
            hosts = s.get(Venue, sat).hosts or []
        save_hosts(sat, [*hosts, {"venue_id": main, "from": None, "to": None}])
    else:
        raise ValueError(f"unknown relation: {relation!r}")
    return row_id


# A track part of a venue name: "(Demonstration)", "System Demonstrations", "Findings of"…
_TRACK_WORDS = r"(?:findings|tutorials?|demos?|demonstrations?|short\s+papers?)"
_TRACK_PARTS = (
    re.compile(rf"[(\[][^)\]]*\b{_TRACK_WORDS}\b[^)\]]*[)\]]", re.I),
    re.compile(r"\bfindings\s+of(?:\s+the)?\b", re.I),
    re.compile(rf"\b(?:system\s+)?{_TRACK_WORDS}(?:\s+(?:track|session|papers?))?\b", re.I),
)


def track_free_name(name: str) -> str:
    """A venue's name without its track part, for the conference it is a track of
    ("WIDG (Demonstration) Conference on Widgets (WIDG)" → "Conference on Widgets (WIDG)")."""
    out = name
    for rx in _TRACK_PARTS:
        out = rx.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" :-–—,;/")
    out = re.sub(r"^(?:of|at)(?:\s+the)?\s+", "", out, flags=re.I)  # ("Tutorials of …")
    # An acronym in front of the track ("WIDG (Demonstration) Conference on …"): at the end,
    # as in the conference's usual name ("Conference on … (WIDG)").
    front = re.match(rf"(\S+)\s*[(\[][^)\]]*\b{_TRACK_WORDS}\b", name, re.I)
    if front and _ACRONYM.fullmatch(front[1]) and out.startswith(f"{front[1]} "):
        out = out[len(front[1]) + 1 :]
        if f"({front[1]})" not in out:
            out = f"{out} ({front[1]})"
    out = re.sub(r"\s+([:,;)\]])", r"\1", re.sub(r"([(\[])\s+", r"\1", out))
    return out or name


def mark_as_track(venue_id: int, track: str, name: str | None = None) -> None:
    """The venue is a track (demo…) of a conference with no venue of its own: all its texts
    are marked as that track, the venue standing for the conference (renamed to ``name``)."""
    with session_scope() as s:
        renamed = bool(name) and name != s.get(Venue, venue_id).name
    if renamed:
        update_venue(venue_id, name=name)  # (its former name stays a variant: marked too)
    with session_scope() as s:
        for vk in s.scalars(select(VenueKey).where(VenueKey.venue_id == venue_id)):
            vk.track = track
    _changed(rematch=True)


def venue_options() -> dict[int, str]:
    with session_scope() as s:
        return {v.id: v.name for v in s.scalars(select(Venue).order_by(Venue.name))}


def venue_choices() -> dict[int, str]:
    """Venue labels to pick from, their acronym first ("[EMNLP] Conference on …") so that
    typing it finds them: set by hand (or none), else inferred from its ranking record or
    its texts."""
    out: dict[int, str] = {}
    with session_scope() as s:
        for v in s.scalars(select(Venue).options(selectinload(Venue.keys))):
            short = (
                v.short_name
                if v.short_manual
                else v.short_name  # (inferred, cached)
                or auto_short_name(
                    service.badge_for_record(v.record_key) if v.record_key else None,
                    [v.name, *(k.example for k in v.keys)],
                    workshop=v.kind in WORKSHOP_KINDS,
                )
            )
            out[v.id] = f"[{short}] {v.name}" if short else v.name
    return dict(sorted(out.items(), key=lambda kv: kv[1].lstrip("[").lower()))


def conflicts() -> list[tuple[str, str, list[int]]]:
    """Venue texts that several venues match (source, raw text, venue ids: the one it belongs
    to first) — candidates for a merge."""
    venue_match.refresh()
    return [
        (m.source, m.raw, [m.venue_id, *m.conflicts])
        for m in venue_match.matches().values()
        if m.conflicts and m.venue_id
    ]


# ---- venue rules and identifiers ----------------------------------------------------------


def venue_texts() -> list[tuple[str, str, int]]:
    """Distinct (source, venue text, number of records) of the validated sources."""
    with session_scope() as s:
        rows = s.execute(
            select(SourceLink.source, SourcePub.venue, func.count())
            .join(SourceLink, SourceLink.id == SourcePub.link_id)
            .where(SourcePub.venue.is_not(None), active_links())
            .group_by(SourceLink.source, SourcePub.venue)
        ).all()
    return [(src, text, n) for src, text, n in rows]


def save_patterns(venue_id: int, patterns: list[VenuePattern]) -> None:
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        v.patterns = [p.model_dump(exclude_defaults=True) for p in patterns] or None
    _changed(rematch=True)


def venue_patterns(venue_id: int) -> list[VenuePattern]:
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        return [VenuePattern.model_validate(d) for d in (v.patterns or [])] if v else []


def save_issns(venue_id: int, issns: list[str]) -> None:
    """The ISSNs identifying a venue (records with one of them belong to it)."""
    clean = list(dict.fromkeys(i.strip() for i in issns if venue_match.norm_issn(i)))
    with session_scope() as s:
        v = s.get(Venue, venue_id)
        ids = {k: val for k, val in (v.identifiers or {}).items() if k != "issn"}
        if clean:
            ids["issn"] = clean
        v.identifiers = ids or None
    _changed()


@dataclass
class PatternEffect:
    source: str
    raw: str
    count: int
    before: str | None  # the venue the text belongs to now (None: none)
    after: str | None  # its venue with the rule (None: back to automatic matching)
    conflict: bool = False  # another venue's rule matches it too


def venue_names() -> dict[int, str]:
    with session_scope() as s:
        return {vid: name for vid, name in s.execute(select(Venue.id, Venue.name))}


def pattern_effects(
    venue_id: int, rule: VenuePattern, replaces: int | None = None
) -> list[PatternEffect]:
    """Venue texts whose venue changes when ``rule`` is added to the venue (or replaces its
    rule number ``replaces``)."""
    venue_match.refresh()
    texts = venue_match.matches()
    target = venue_names().get(venue_id)
    out = []
    for source, raw, n in venue_texts():
        m = texts.get((source, raw))
        if m is None:
            continue
        now = rule.applies(source, raw)
        was = m.via == "pattern" and m.venue_id == venue_id and m.pattern_index == replaces
        if now and m.venue_id != venue_id:
            if m.via == "variant":
                continue  # a manual variant wins over the rules
            conflict = m.via == "pattern"
            out.append(PatternEffect(source, raw, n, m.venue_name, target, conflict))
        elif was and not now and replaces is not None:
            out.append(PatternEffect(source, raw, n, m.venue_name, None))
    return sorted(out, key=lambda e: -e.count)


@dataclass
class PatternUse:
    index: int
    rule: VenuePattern
    examples: list[tuple[str, str, int]]  # (source, raw text, records) it captures
    lost: list[tuple[str, str, str | None]] = field(default_factory=list)  # to other venues


def pattern_uses(venue_id: int) -> list[PatternUse]:
    """The venue's rules, with the source texts each one captures (and those it matches but
    that belong to another venue: a manual variant or a more specific rule)."""
    venue_match.refresh()
    texts = venue_match.matches()
    counts = {(src, raw): n for src, raw, n in venue_texts()}
    out = []
    for i, r in enumerate(venue_patterns(venue_id)):
        ex, lost = [], []
        for (src, raw), m in texts.items():
            if (src, raw) not in counts:
                continue
            if m.venue_id == venue_id and m.via == "pattern" and m.pattern_index == i:
                ex.append((src, raw, counts[(src, raw)]))
            elif m.venue_id != venue_id and r.applies(src, raw):
                lost.append((src, raw, m.venue_name))
        out.append(PatternUse(i, r, sorted(ex, key=lambda e: -e[2]), lost))
    return out
