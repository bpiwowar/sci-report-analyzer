"""Venue texts → venues.

A source's venue text is cleaned by the normalization rules (settings), then matched to a
venue, by priority:

1. a manual variant (a raw text assigned to the venue by hand, matched by its key);
2. a venue rule (a regex on the raw text, optionally restricted to sources; SQLite
   ``REGEXP``), source-restricted rules first;
3. an automatic variant (same key), else a new automatic venue.

Records carrying one of a venue's identifiers (ISSN) belong to it whatever their text.
The result is stored in ``venue_text`` and recomputed when the rules, variants or venue
rules change.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .db.models import (
    AppSetting,
    Publication,
    SourceLink,
    SourcePub,
    Venue,
    VenueKey,
    VenueText,
)
from .db.session import session_scope
from .ranking.normalize import is_non_venue
from .ranking.service import VenuePattern, service

logger = logging.getLogger(__name__)

# AppSetting: hash of the normalization rules the variant keys were computed with.
KEYS_SETTING = "venue_keys"


@dataclass(frozen=True)
class TextMatch:
    source: str
    raw: str
    clean: str
    key: str
    venue_id: int | None
    venue_name: str | None
    via: str  # variant | pattern | auto | archival | identifier
    track: str | None = None
    pattern_index: int | None = None
    conflicts: tuple[int, ...] = field(default=())


def norm_issn(issn: str | None) -> str | None:
    return issn.replace("-", "").strip().upper() or None if issn else None


# ---- variants ------------------------------------------------------------------------------


def rekey(s: Session) -> None:
    """Recompute the variant keys from their raw texts (after a change of the rules).

    Two variants getting the same key: the manual one wins, else the one of a venue with
    manual decisions or variants; venues left without variant and without manual decision
    are dropped."""
    rows = list(s.scalars(select(VenueKey)))
    kept_venues = {k.venue_id for k in rows if k.manual}
    kept_venues |= {v.id for v in s.scalars(select(Venue)) if v.has_manual}
    rows.sort(key=lambda k: (not k.manual, k.venue_id not in kept_venues, k.venue_id, k.key))
    new: dict[str, tuple[int, bool, str | None, str | None, str | None]] = {}
    merged: dict[int, int] = {}  # venue whose variant collided -> the venue keeping the key
    for vk in rows:
        key = service.key(vk.example, vk.source) if vk.example else vk.key
        if not key:
            continue
        if key not in new:
            new[key] = (vk.venue_id, vk.manual, vk.example, vk.source, vk.track)
        elif not vk.manual:
            merged.setdefault(vk.venue_id, new[key][0])
    s.expunge_all()
    s.execute(delete(VenueKey))
    for key, (vid, manual, example, source, track) in new.items():
        s.add(
            VenueKey(
                key=key, venue_id=vid, manual=manual, example=example, source=source, track=track
            )
        )
    s.flush()
    kept = {vid for vid, *_ in new.values()}
    for v in s.scalars(select(Venue)):
        if v.id in kept or v.has_manual:
            continue
        # Merged into another venue by the rules (papers linked by hand follow it).
        if target := merged.get(v.id):
            for pub in s.scalars(select(Publication).where(Publication.venue_id == v.id)):
                pub.venue_id = target
        elif s.scalar(
            select(Publication.id)
            .where(Publication.venue_id == v.id, Publication.venue_manual)
            .limit(1)
        ):
            continue
        s.delete(v)
    s.flush()


def _new_venue(s: Session, t: VenueText) -> VenueKey:
    """A new automatic venue for a text (and its variant)."""
    venue = Venue(name=t.clean or t.raw)
    s.add(venue)
    s.flush()
    vk = VenueKey(key=t.key, venue_id=venue.id, example=t.raw, source=t.source)
    s.add(vk)
    return vk


# ---- venue texts ---------------------------------------------------------------------------


def _pattern_hits(
    s: Session, ids: set[int] | None
) -> dict[int, list[tuple[int, int, VenuePattern]]]:
    """Text id → the venue rules matching it (venue id, rule index, rule), by priority."""
    rules = []
    for v in s.scalars(select(Venue).where(Venue.patterns.is_not(None))):
        for i, d in enumerate(v.patterns or []):
            rule = VenuePattern.model_validate(d)
            if rule.compiled() is None:
                logger.warning("Ignoring invalid venue rule %r", rule.pattern)
                continue
            rules.append((v.id, i, rule))
    rules.sort(key=lambda r: not r[2].sources)
    hits: dict[int, list[tuple[int, int, VenuePattern]]] = defaultdict(list)
    for vid, i, rule in rules:
        q = select(VenueText.id).where(VenueText.raw.regexp_match(rule.regex))
        if rule.sources:
            q = q.where(VenueText.source.in_(rule.sources))
        for tid in s.scalars(q):
            if ids is None or tid in ids:
                hits[tid].append((vid, i, rule))
    return hits


def _assign(s: Session, texts: list[VenueText], *, all_texts: bool) -> None:
    hits = _pattern_hits(s, None if all_texts else {t.id for t in texts})
    variants = {vk.key: vk for vk in s.scalars(select(VenueKey))}
    for t in texts:
        vk = variants.get(t.key)
        found = hits.get(t.id, [])
        t.pattern_index, t.track = None, None
        if vk is not None and vk.manual:
            t.venue_id, t.via, t.track = vk.venue_id, "variant", vk.track
        elif found:
            vid, i, rule = found[0]
            t.venue_id, t.via, t.pattern_index, t.track = vid, "pattern", i, rule.track
        elif not t.key or is_non_venue(t.key):
            t.venue_id, t.via = None, "archival"
        elif vk is not None:
            t.venue_id, t.via, t.track = vk.venue_id, "auto", vk.track
        else:
            variants[t.key] = vk = _new_venue(s, t)
            t.venue_id, t.via = vk.venue_id, "auto"
        # A manual variant settles it: other venues' rules are then no conflict.
        t.conflicts = (
            [] if t.via == "variant" else sorted({vid for vid, _, _ in found} - {t.venue_id})
        )


def refresh(*, reassign: bool = False) -> int:
    """Bring ``venue_text`` up to date with the sources; returns the number of texts
    (re)matched. ``reassign`` re-matches every text (after a change of the variants or
    venue rules); a change of the normalization rules is detected and recomputes all."""
    st = service.settings
    with session_scope() as s:
        applied = s.get(AppSetting, KEYS_SETTING)
        if applied is None or applied.value.get("hash") != st.rules_hash:
            rekey(s)
            s.merge(AppSetting(key=KEYS_SETTING, value={"hash": st.rules_hash}))
            reassign = recompute = True
        else:
            recompute = False
        pairs = {
            (source, raw)
            for source, raw in s.execute(
                select(SourceLink.source, SourcePub.venue)
                .join(SourceLink, SourceLink.id == SourcePub.link_id)
                .where(SourcePub.venue.is_not(None), SourcePub.venue != "")
                .distinct()
            )
        }
        existing = {(t.source, t.raw): t for t in s.scalars(select(VenueText))}
        for pair, t in list(existing.items()):
            if pair not in pairs:
                s.delete(t)
                del existing[pair]
        new = []
        for source, raw in sorted(pairs - existing.keys()):
            clean = service.clean(raw, source)
            t = VenueText(source=source, raw=raw, clean=clean, key=service.key(raw, source))
            s.add(t)
            new.append(t)
        if recompute:
            for t in existing.values():
                t.clean = service.clean(t.raw, t.source)
                t.key = service.key(t.raw, t.source)
        s.flush()
        targets = [*existing.values(), *new] if reassign else new
        if targets:
            _assign(s, targets, all_texts=reassign)
        return len(targets)


def reset_automatic() -> dict[str, int]:
    """Clear out everything computed automatically, for all papers: automatic venues and
    variants, the venue texts' matches, automatic paper venues and kinds, the cached
    ranking matches. Manual decisions (on venues and papers) are kept. Then re-match."""
    with session_scope() as s:
        s.execute(
            update(Publication).where(Publication.venue_manual.is_(False)).values(venue_id=None)
        )
        s.execute(delete(VenueText))
        s.execute(delete(VenueKey).where(VenueKey.manual.is_(False)))
        s.execute(update(Venue).where(Venue.kind_manual.is_(False)).values(kind=None))
        for v in s.scalars(select(Venue).where(Venue.joint.is_not(None))):
            if not v.joint.get("manual"):  # parts found automatically (the part used stays)
                v.joint = {**v.joint, "parts": []} if v.joint.get("use") else None
        s.execute(delete(AppSetting).where(AppSetting.key == KEYS_SETTING))
        keep = set(s.scalars(select(VenueKey.venue_id)))
        keep |= set(s.scalars(select(Publication.venue_id).where(Publication.venue_manual)))
        dropped = 0
        for v in s.scalars(select(Venue)):
            if v.id not in keep and not v.has_manual:
                s.delete(v)
                dropped += 1
    service.invalidate(clear_cache=True)
    return {"venues": dropped, "texts": refresh(reassign=True)}


def manual_counts() -> dict[str, int]:
    """How many venues / variants / papers carry manual decisions."""
    with session_scope() as s:
        return {
            "venues": sum(v.has_manual for v in s.scalars(select(Venue))),
            "variants": len(list(s.scalars(select(VenueKey).where(VenueKey.manual)))),
            "papers": sum(p.has_overrides for p in s.scalars(select(Publication))),
        }


def clear_manual(*, venues: bool, papers: bool) -> dict[str, int]:
    """Erase the manual decisions on the venues (``Venue.MANUAL_FIELDS`` and the variants
    set by hand) and / or on the papers (``Publication.MANUAL_FIELDS``), then recompute
    everything automatically. Tags, stars, hidden papers and manual merges are kept."""
    counts = manual_counts()
    with session_scope() as s:
        if venues:
            s.execute(update(Venue).values(**Venue.MANUAL_FIELDS))
            s.execute(update(VenueKey).values(manual=False, track=None))
        if papers:
            s.execute(update(Publication).values(**Publication.MANUAL_FIELDS))
    reset_automatic()
    return {
        "venues": counts["venues"] + counts["variants"] if venues else 0,
        "papers": counts["papers"] if papers else 0,
    }


@dataclass
class Explanation:
    steps: list[tuple[str, str]]  # (normalization rule, text after it)
    clean: str
    key: str
    via: str | None
    venue_name: str | None
    detail: str | None  # the venue rule's regex, or the variant's raw text


def explain(source: str, raw: str) -> Explanation:
    """How a source's venue text is cleaned and matched (for display)."""
    steps = []
    text = raw.strip()
    for r in service.settings.norm_rules:
        if r.applies_to(source) and (after := r.apply(text)) != text:
            steps.append((r.name, after.strip()))
            text = after
    m = matches().get((source, raw))
    detail = None
    if m is not None and m.venue_id is not None:
        with session_scope() as s:
            v = s.get(Venue, m.venue_id)
            if m.via == "pattern" and m.pattern_index is not None and v.patterns:
                detail = VenuePattern.model_validate(v.patterns[m.pattern_index]).pattern
            elif m.via in ("variant", "auto") and (vk := s.get(VenueKey, m.key)):
                detail = vk.example
    return Explanation(
        steps,
        service.clean(raw, source),
        service.key(raw, source),
        m.via if m else None,
        m.venue_name if m else None,
        detail,
    )


def matches() -> dict[tuple[str, str], TextMatch]:
    """Every venue text of the sources and its venue."""
    with session_scope() as s:
        names = {vid: name for vid, name in s.execute(select(Venue.id, Venue.name))}
        return {
            (t.source, t.raw): TextMatch(
                t.source,
                t.raw,
                t.clean,
                t.key,
                t.venue_id,
                names.get(t.venue_id) if t.venue_id else None,
                t.via,
                t.track,
                t.pattern_index,
                tuple(t.conflicts or ()),
            )
            for t in s.scalars(select(VenueText))
        }


def issn_venues() -> dict[str, int]:
    """ISSN → the venue it identifies (venue identifiers)."""
    out: dict[str, int] = {}
    with session_scope() as s:
        for v in s.scalars(select(Venue).where(Venue.identifiers.is_not(None))):
            for issn in (v.identifiers or {}).get("issn") or []:
                if n := norm_issn(issn):
                    out.setdefault(n, v.id)
    return out


def texts_of(venue_ids: Iterable[int]) -> dict[int, list[TextMatch]]:
    ids = set(venue_ids)
    out: dict[int, list[TextMatch]] = defaultdict(list)
    for m in matches().values():
        if m.venue_id in ids:
            out[m.venue_id].append(m)
    return out
