"""Per-publication view model: resolved badge, track, authorship, provenance."""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import contribution, i18n, pdfs, venue_match
from .authors import fold_name, same_author
from .db.models import (
    AppSetting,
    AuthorCategory,
    Period,
    Person,
    Publication,
    PublicationTag,
    SourceLink,
    SourcePub,
    Thesis,
    Venue,
)
from .db.session import session_scope
from .i18n import N_, Labels, _, language
from .merge import main_members
from .ranking.badge import (
    BASE_CATEGORIES,
    KIND_ORDER,
    Badge,
    Category,
    badge_from_level,
    category_label,
    category_of,
    category_order,
    detect_track,
)
from .ranking.kinds import (
    CONFERENCE_LIKE,
    KIND_SHORT,
    NO_VENUE_KINDS,
    UNRANKED_KINDS,
    VENUE_KINDS,
    WORKSHOP_KINDS,
    KindEvidence,
    data_kind,
    detect_kind,
)
from .ranking.normalize import is_non_venue, normalize
from .ranking.service import is_ranked, service
from .source_settings import active_links
from .sources import ADAPTERS, PRIORITY
from .sources.base import to_year
from .venues import auto_short_name, link_venues, venue_badge


@dataclass
class MemberView:
    id: int
    source: str
    external_key: str
    title: str | None
    venue: str | None
    year: int | None
    url: str | None
    pdf_url: str | None
    doi: str | None
    archival: bool
    badge: Badge | None = None
    publisher_url: str | None = None
    profile_url: str | None = None  # the person's profile on the source
    venue_id: int | None = None  # the venue its venue text (or ISSN) belongs to
    venue_name: str | None = None
    via: str | None = None  # how: variant | pattern | auto | archival | identifier
    track: str | None = None  # set by the variant / venue rule
    detected_track: str | None = None  # found in its venue text (workshop, findings...)
    conflicts: tuple[int, ...] = ()  # other venues whose rules match its text
    # False for a DOI record naming no real venue (a book chapter without an event).
    venue_reliable: bool = True
    # Its venue is the main conference of another record's (a workshop's): that one is
    # more precise, the difference is minor (not a disagreement).
    minor: bool = False

    @property
    def eff_track(self) -> str | None:
        """Its track: set by its variant / venue rule, else found in its text."""
        return self.track or self.detected_track


@dataclass
class PubStat:
    id: int
    title: str | None
    year: int | None
    badge: Badge | None
    track: str | None
    category: Category
    num_authors: int | None
    author_pos: int | None
    authors: list[str]
    # Per author: "owner", "student", "former" (a former PhD student) or None (for
    # highlighting).
    author_marks: list[str | None]
    # By author index: (tooltip, PhD student name or None).
    author_notes: dict[int, tuple[str, str | None]]
    venue: str | None
    url: str | None
    doi: str | None
    pdf_urls: list[str]
    sources: list[str]
    members: list[MemberView]
    flags: list[tuple[int, str, str]]  # (id, name, colour)
    venue_id: int | None
    venue_manual: bool
    venue_source: str | None  # source validated by hand for the venue
    rank_override: dict | None
    rank_note: str | None
    kind: str
    kind_source: str  # forced | venue | detected
    missing: bool
    hidden: bool
    archival_only: bool
    disagree: bool
    # The sources give different tracks (e.g. demo and short), not settled by hand.
    track_conflict: bool = False
    tags: set[int] = field(default_factory=set)  # global tags (ids)
    period_tags: dict[int, set[int]] = field(default_factory=dict)  # period id -> tag ids
    period_notes: dict[int, str] = field(default_factory=dict)  # period id -> note
    # The paper's number in the list a tag was put from: global tag id -> number, and
    # period id -> per-period tag id -> number.
    numbers: dict[int, int] = field(default_factory=dict)
    period_numbers: dict[int, dict[int, int]] = field(default_factory=dict)
    publisher_url: str | None = None  # the publisher's page (a link from a source, or the DOI)
    venue_short: str | None = None  # acronym of the venue (e.g. "ICLR")
    venue_raw: str | None = None  # the venue text of the source used (before matching)
    year_manual: bool = False
    doi_manual: str | None = None
    author_pos_manual: bool = False
    note: str | None = None  # Markdown
    # A workshop's main conference (in the paper's year), which gives its rank.
    host_id: int | None = None
    host_name: str | None = None
    # The person's role (a contribution Role's key), from their position.
    contribution: str | None = None
    # Its PDF stored in the data directory: None, "stored", or "edited" (annotated).
    pdf: str | None = None

    def tags_in(self, period_id: int | None) -> set[int]:
        """Its tags: the global ones, and those within the period."""
        return self.tags | self.period_tags.get(period_id, set()) if period_id else set(self.tags)

    def number_of(self, tag_id: int, period_id: int | None) -> int | None:
        if tag_id in self.numbers:
            return self.numbers[tag_id]
        return self.period_numbers.get(period_id, {}).get(tag_id) if period_id else None

    def has_notes(self, period_id: int | None) -> bool:
        """Whether it has a note of its own, or one within the period."""
        return bool(self.note or (period_id and self.period_notes.get(period_id)))

    @property
    def overridden(self) -> bool:
        """Whether the venue or rank was set by hand."""
        return bool(self.rank_override or self.venue_manual or self.venue_source)

    @property
    def decided_by_hand(self) -> bool:
        """Whether a matching decision was made by hand for this paper (venue picked or
        validated, rank or kind set)."""
        return self.overridden or self.kind_source == "forced"

    @property
    def problems(self) -> list[str]:
        """Issues that need a look (shown with a warning icon)."""
        out = []
        if self.author_pos_manual:
            pass  # the person's position is set by hand: the list does not matter
        elif not self.authors:
            out.append(_("no author list in the sources"))
        elif "owner" not in self.author_marks:
            if "owner?" in self.author_marks:
                out.append(self.author_notes[self.author_marks.index("owner?")][0])
            else:
                out.append(_("the person's name is not found among the authors — add an alias"))
        for i, m in enumerate(self.author_marks):
            if m == "student?":
                out.append(self.author_notes[i][0])
        if (
            not self.venue
            and not self.archival_only
            and self.kind
            not in ("book", "chapter", "proceedings", "software", "dataset", "thesis", "other")
        ):
            out.append(_("no venue"))
        if self.year is None:
            out.append(_("no year"))
        if self.disagree and not self.overridden:
            out.append(_("sources give different venues — pick one in the details"))
        if self.track_conflict:
            out.append(_("sources give different tracks — pick one in the details or flag it"))
        if self.missing:
            out.append(_("no longer in any source"))
        return out


def _badge_rank(b: Badge | None, source: str) -> tuple:
    """Sort key: best badge first (manual, ranked exact, score, source priority)."""
    if b is None:
        return (9, 0.0, 99)
    ranked = bool(b.quartile or b.coreRank)
    tier = 0 if b.manual else 1 if ranked and b.exact else 2 if ranked else 3
    if b.archival:
        tier = 5
    return (tier, -b.score, PRIORITY.index(source) if source in PRIORITY else 99)


def publication_kind(
    pub: Publication,
    badge: Badge | None,
    venue: Venue | None,
    views: list[MemberView],
    raw: dict[int, SourcePub],
) -> tuple[str, str]:
    """First-level classification: (kind, where it comes from)."""
    if pub.kind_override:
        return pub.kind_override, "forced"
    if venue is not None and venue.kind_manual and venue.kind in VENUE_KINDS:
        return venue.kind, "venue"
    kind, source = _detected_kind(badge, venue, views, raw, pub.title)
    if venue is not None and venue.hosts and kind not in (*WORKSHOP_KINDS, "shared_task"):
        # A venue with a main conference is a workshop.
        kind = "natl_workshop" if kind.startswith("natl") else "intl_workshop"
    return kind, source


def _detected_kind(
    badge: Badge | None,
    venue: Venue | None,
    views: list[MemberView],
    raw: dict[int, SourcePub],
    title: str | None = None,
) -> tuple[str, str]:
    # Software and datasets, from any record (on Zenodo, an archive, they are not preprints).
    if kind := data_kind(" ".join({raw[m.id].doc_type or "" for m in views})):
        return kind, "detected"
    published = [mv for mv in views if not mv.archival]
    if not published:
        return "preprint", "detected"
    best = next((mv for mv in published if venue and mv.venue_id == venue.id), published[0])
    sp = raw[best.id]
    st = service.settings
    # Hints from every published member: any conference/journal type and document type.
    vtype = sp.venue_type or next(
        (raw[m.id].venue_type for m in published if raw[m.id].venue_type), None
    )
    # A DOI chapter without an event (a proceedings volume in a series) says nothing of
    # the kind: its "book-chapter" is left out.
    doc_types = " ".join(
        t for t in {raw[m.id].doc_type for m in published if m.venue_reliable} if t
    )
    ev = KindEvidence(
        venue=(venue.name if venue else None) or sp.venue,
        venue_type=vtype,
        doc_type=doc_types,
        title=title,
    )
    return detect_kind(
        badge,
        ev,
        national_keywords=st.national_keywords,
        international_keywords=st.international_keywords,
        unknown_scope=st.unknown_scope,
    ), "detected"


_FINDINGS = re.compile(r"\bfindings\b", re.I)

# Sources whose venue text is only a fallback (ORCID: a free "journal title" declared by
# hand or copied from elsewhere, often missing or wrong for conference papers).
UNRELIABLE_VENUE = frozenset({"orcid"})


def venue_members(views: list[MemberView]) -> list[MemberView]:
    """Members whose venue ranks the paper: published versions, reliable sources first."""
    published = [mv for mv in views if not mv.archival] or views
    reliable = [
        mv
        for mv in published
        if mv.source not in UNRELIABLE_VENUE and mv.venue and mv.venue_reliable
    ]
    return reliable or published


def same_venue(view: MemberView) -> tuple:
    """What makes two records' venues the same one: their ranking record (e.g. "Findings of
    ACL" and "ACL", two venues ranked as ACL), else their venue."""
    if view.badge and view.badge.recordKey:
        return ("record", view.badge.recordKey)
    return ("venue", view.venue_id)


def paper_tracks(views: list[MemberView]) -> set[str]:
    """The tracks (demo, short, findings, workshop...) the records give, whatever their
    venue (a source may not resolve the venue, e.g. a HAL text, but they are one paper)."""
    return {v.eff_track for v in views if v.eff_track}


def track_of(view: MemberView, views: list[MemberView]) -> str | None:
    """A record's track, or the one the other records give: a track wins over none (a demo
    paper whose other records name only the conference); None when they give different
    tracks (a conflict, to be resolved by hand)."""
    if view.eff_track:
        return view.eff_track
    tracks = paper_tracks(views)
    return next(iter(tracks)) if len(tracks) == 1 else None


def doi_member(views: list[MemberView]) -> MemberView | None:
    """The paper's DOI record, when it gives a (published) venue."""
    return next(
        (
            mv
            for mv in views
            if mv.source == "doi" and mv.venue and not mv.archival and mv.venue_reliable
        ),
        None,
    )


def pick_venue_member(views: list[MemberView]) -> MemberView | None:
    """The record whose venue ranks the paper in automatic mode (badges resolved): a
    workshop's rather than its main conference's, then the DOI record (registered by the
    publisher), else the best ranked one."""
    if (mv := doi_member(views)) is not None and not mv.minor:
        return mv
    pool = [mv for mv in venue_members(views) if not mv.minor] or venue_members(views)
    return min(pool, key=lambda mv: _badge_rank(mv.badge, mv.source), default=None)


def mark_minor(views: list[MemberView], venues: dict[int, Venue]) -> None:
    """Mark the records whose venue is the main conference of another record's venue."""
    hosts = {
        h["venue_id"]
        for v in views
        if v.venue_id in venues and not v.archival
        for h in venues[v.venue_id].hosts or []
    }
    for v in views:
        v.minor = v.venue_id is not None and v.venue_id in hosts


def pick_reason(best: MemberView, views: list[MemberView]) -> str:
    """Why ``best`` was picked (for the details' Venue matching tab)."""
    b = best.badge
    ranked = bool(b and (b.quartile or b.coreRank))
    if any(v.minor for v in views) and not best.minor:
        names = ", ".join(dict.fromkeys(v.venue_name or "?" for v in views if v.minor))
        return _("the workshop rather than its main conference ({names})").format(names=names)
    if best.source == "doi":
        return _("the DOI record (registered by the publisher) is the main source")
    if b and b.manual:
        why = _("its venue has a rank set by hand")
    elif ranked and b.exact:
        why = _("its venue matches a ranking record exactly")
    elif ranked:
        why = _("its venue has the best ranked match ({score}%)").format(score=round(b.score * 100))
    else:
        why = _("no source venue is ranked")
    # Ties that matter: equally good records with another venue text.
    ties = [
        v
        for v in venue_members(views)
        if v is not best
        and v.venue_id != best.venue_id
        and _badge_rank(v.badge, v.source)[:2] == _badge_rank(b, best.source)[:2]
    ]
    candidates = venue_members(views)
    # Only the sources that took part (the DOI record may not have).
    order = " > ".join(
        ADAPTERS[s].label for s in PRIORITY if any(v.source == s for v in candidates)
    )
    if (ties or not ranked) and len({v.source for v in candidates}) > 1:
        why += _("; ties are broken by source priority ({order})").format(order=order)
    skipped = [v for v in views if v not in candidates]
    if any(v.source == "doi" and not v.archival and not v.venue_reliable for v in skipped):
        why += _(
            "; the DOI record is not used: a book chapter without an event (e.g. in a volume "
            "of a series such as LNCS) names no real venue"
        )
    if any(v.archival or v.source in UNRELIABLE_VENUE for v in skipped):
        why += _("; preprints and ORCID venues are only used when nothing else is available")
    return why


def member_view(
    m: SourcePub,
    texts: dict[tuple[str, str], venue_match.TextMatch],
    by_issn: dict[str, int],
    names: dict[int, str],
) -> MemberView:
    tm = texts.get((m.link.source, m.venue)) if m.venue else None
    vid = by_issn.get(venue_match.norm_issn(m.issn) or "")
    via = "identifier" if vid else (tm.via if tm else None)
    vid = vid or (tm.venue_id if tm else None)
    archival = bool(m.archival) or (tm is not None and tm.via == "archival")
    return MemberView(
        m.id,
        m.link.source,
        m.external_key,
        m.title,
        m.venue,
        m.year,
        m.url,
        m.pdf_url,
        m.doi,
        archival or is_non_venue(normalize(m.venue)),
        publisher_url=(m.raw or {}).get("publisher_url"),
        profile_url=m.link.url or ADAPTERS[m.link.source].profile_url(m.link.external_id),
        venue_id=vid,
        venue_name=names.get(vid) if vid else None,
        via=via,
        track=tm.track if tm else None,
        detected_track=_text_track(m.venue) or _text_track((m.raw or {}).get("part")),
        conflicts=tm.conflicts if tm else (),
        venue_reliable=(m.raw or {}).get("venue_reliable", True),
    )


def dated_badge(b: Badge | None, year: int | None) -> Badge | None:
    """The CORE rank of the edition in force in ``year``, or the journal's quartile that
    year (unless the latest ones are used)."""
    if b is None or service.settings.core_edition == "latest":
        return b
    return b.at_year(year)


async def workshop_badge(
    venue: Venue | None, year: int | None, venues: dict[int, Venue]
) -> tuple[Badge | None, Venue | None]:
    """A workshop paper's rank: that of the workshop's main conference in its year."""
    host = venues.get(venue.host_at(year)) if venue is not None else None
    if host is None:
        return None, None
    b = dated_badge(await venue_badge(host, venues=venues), year)
    if b is not None:
        b = b.copy(extra={**b.extra, "host": host.name})
    return b, host


def _text_track(text: str | None) -> str | None:
    return detect_track(text) or ("findings" if text and _FINDINGS.search(text) else None)


def _has_decision(v: Venue | None) -> bool:
    """A level set by hand on the venue, or taken from its parts (a joint venue)."""
    return bool(v and (v.level or v.record_key or v.match_text or v.parts))


async def resolve_member(m: SourcePub, mv: MemberView, venues: dict[int, Venue]) -> Badge | None:
    """Badge of a source record: its venue's rank set by hand, else its text through the
    rankings (the venue name first when the text was assigned to the venue by hand)."""
    v = venues.get(mv.venue_id) if mv.venue_id else None
    if _has_decision(v):
        return await venue_badge(v, venues=venues)
    if v is not None and mv.via in ("variant", "pattern", "identifier"):
        b = await service.resolve(v.name, m.issn, m.venue_type)
        if is_ranked(b):
            return b
    if not (m.venue or m.issn):
        return None
    return await service.resolve(m.venue, m.issn, m.venue_type, source=m.link.source)


def override_badge(pub: Publication) -> Badge | None:
    """The rank set by hand for a publication (a level or a ranking record)."""
    o = pub.rank_override
    if not o:
        return None
    if o.get("record_key"):
        b = service.badge_for_record(o["record_key"])
        return b.copy(forced=True) if b else None
    return badge_from_level(o).copy(forced=True) if o.get("rank") else None


async def resolve_publication(
    pub: Publication,
    members: list[MemberView],
    raw: dict[int, SourcePub],
    venues: dict[int, Venue],
) -> tuple[Badge | None, MemberView | None, Venue | None]:
    """Badge of a publication, the member whose venue ranks it, and its venue.

    CORE ranks are those of the edition in force at the paper's year (unless the settings
    say to use the latest edition).
    """
    year = pub.year_override or pub.year

    def dated(b: Badge | None) -> Badge | None:
        return dated_badge(b, year)

    for mv in members:
        mv.badge = dated(await resolve_member(raw[mv.id], mv, venues))
    best = None
    if pub.venue_source:
        best = next((mv for mv in venue_members(members) if mv.source == pub.venue_source), None)
    best = best or pick_venue_member(members)
    if pub.venue_manual and pub.venue is not None:
        venue = venues.get(pub.venue.id) or pub.venue
        badge = await venue_badge(
            venue,
            [(m.venue, raw[m.id].issn, raw[m.id].venue_type) for m in members if m.venue],
            venues=venues,
        )
    else:
        venue = venues.get(best.venue_id) if best and best.venue_id else None
        badge = best.badge if best else None
    if (ob := override_badge(pub)) is not None:
        badge = ob
    return dated(badge), best, venue


def _authorship(
    members: list[SourcePub],
) -> tuple[int | None, int | None, list[str], bool]:
    exact = [m for m in members if ADAPTERS[m.link.source].exact_position and m.author_pos]
    pool = exact or [m for m in members if m.author_pos]
    pos = pool[0].author_pos if pool else None
    pos_exact = bool(exact)
    # The DOI record's author list first (the publisher's), then exact-position sources;
    # not a shorter one (registries can be incomplete: a single DataCite creator).
    most = max((len(m.authors) for m in members if m.authors), default=0)
    with_authors = sorted(
        (m for m in members if m.authors),
        key=lambda m: (
            m.link.source != "doi" or m.archival or len(m.authors) < most,
            not ADAPTERS[m.link.source].exact_position,
            -len(m.authors),
        ),
    )
    authors = list(with_authors[0].authors) if with_authors else []
    counts = [m.num_authors for m in main_members(members) if m.num_authors]
    num = max([*counts, len(authors)]) or None
    return pos, num, authors, pos_exact


OWNER = "owner"
# A PhD student on a paper more than this many years after their defence: a former student.
FORMER_AFTER = 2


@dataclass
class StudentNames:
    name: str  # as on theses.fr
    exact: set[str]  # folded name + aliases
    variants: list[str]  # name + aliases (for fuzzy matching)
    rejects: set[str]
    note: str
    defence_year: int | None = None


def _ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th… 11th, 12th, 13th, 21st… (in French: 1er, 2e, 3e…)"""
    if language() == "fr":
        return f"{n}er" if n == 1 else f"{n}e"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class PeopleIndex:
    """Names to highlight in author lists: the person and their PhD students.

    Marks: ``owner`` / ``student`` for confirmed names (exact name or alias, or an author
    position given by an id-based source), ``owner?`` / ``student?`` for potential matches
    (same surname and initial) waiting for a manual validation, ``former`` for a confirmed
    student on a paper more than ``FORMER_AFTER`` years after their defence.
    """

    owner_exact: set[str]
    owner_variants: list[str]
    owner_rejects: set[str]
    students: list[StudentNames]
    # Author categories: (id, name, folded names) — exact names only.
    categories: list[tuple[int, str, set[str]]] = field(default_factory=list)

    @classmethod
    def for_person(cls, session, person: Person) -> PeopleIndex:
        aliases = person.student_aliases or {}
        rejects = person.name_rejects or {}
        students: dict[str, StudentNames] = {}
        rows = session.scalars(
            select(Thesis)
            .join(SourceLink)
            .where(
                SourceLink.person_id == person.id,
                active_links(),
                Thesis.role == "director",
            )
        )
        for t in rows:
            for name in (t.student or "").split(", "):
                if name and name not in students:
                    when = (t.defence_date or "")[:4] or _("in progress")
                    names = [name, *aliases.get(name, [])]
                    students[name] = StudentNames(
                        name,
                        {fold_name(n) for n in names},
                        names,
                        {fold_name(n) for n in rejects.get(name, [])},
                        _("PhD student {name} ({when})").format(name=name, when=when),
                        to_year(t.defence_date),
                    )
        owner = [person.name, *(person.aliases or [])]
        names = {c.id: c.name for c in session.scalars(select(AuthorCategory))}
        categories = [
            (int(cid), names[int(cid)], {fold_name(n) for n in members})
            for cid, members in (person.author_categories or {}).items()
            if members and int(cid) in names
        ]
        return cls(
            {fold_name(n) for n in owner},
            owner,
            {fold_name(n) for n in rejects.get(OWNER, [])},
            list(students.values()),
            categories,
        )

    def marks(
        self,
        authors: list[str],
        pos: int | None,
        pos_exact: bool = False,
        year: int | None = None,
    ) -> tuple[list[str | None], dict[int, tuple[str, str | None]]]:
        marks: list[str | None] = []
        notes: dict[int, tuple[str, str | None]] = {}
        owner_found = False
        for i, a in enumerate(authors):
            f = fold_name(a)
            if not owner_found and (f in self.owner_exact or (pos_exact and pos == i + 1)):
                marks.append("owner")
                owner_found = True
                continue
            similar = any(same_author(a, n) for n in self.owner_variants)
            if not owner_found and f not in self.owner_rejects and (pos == i + 1 or similar):
                marks.append("owner?")
                name = self.owner_variants[0] if self.owner_variants else _("this person")
                why = (
                    _("similar name")
                    if similar
                    else _("{ordinal} author for a source").format(ordinal=_ordinal(i + 1))
                )
                notes[i] = (
                    _("Is the author “{author}” {name}? ({why})").format(
                        author=a, name=name, why=why
                    ),
                    None,
                )
                owner_found = True
                continue
            mark = None
            for st in self.students:
                if f in st.exact:
                    if year and st.defence_year and year - st.defence_year > FORMER_AFTER:
                        former = _("former {note}").format(note=st.note)
                        mark, notes[i] = "former", (former, st.name)
                    else:
                        mark, notes[i] = "student", (st.note, st.name)
                    break
                if f not in st.rejects and any(same_author(a, n) for n in st.variants):
                    mark, notes[i] = (
                        "student?",
                        (
                            _("Is “{author}” the PhD student {name}? (similar name)").format(
                                author=a, name=st.name
                            ),
                            st.name,
                        ),
                    )
            if mark is None:
                for cid, cname, members in self.categories:
                    if f in members:
                        mark, notes[i] = f"cat:{cid}", (cname, None)
                        break
            marks.append(mark)
        return marks, notes


async def load_stats(person_id: int) -> list[PubStat]:
    venue_match.refresh()  # venue texts of newly synced records
    texts = venue_match.matches()
    by_issn = venue_match.issn_venues()
    stored_pdfs = pdfs.stored(person_id)
    with session_scope() as s:
        pubs = list(
            s.scalars(
                select(Publication)
                .where(Publication.person_id == person_id)
                .options(
                    selectinload(Publication.members).selectinload(SourcePub.link),
                    selectinload(Publication.venue),
                )
            )
        )
        venues = {v.id: v for v in s.scalars(select(Venue))}
        names = {vid: v.name for vid, v in venues.items()}
        person = s.get(Person, person_id)
        people = PeopleIndex.for_person(s, person)
        pending_links: dict[int, int] = {}
        period_tags: dict[int, dict[int, set[int]]] = {}
        period_notes: dict[int, dict[int, str]] = {}
        period_numbers: dict[int, dict[int, dict[int, int]]] = {}
        numbers: dict[int, dict[int, int]] = {}
        for pt in s.scalars(
            select(PublicationTag)
            .join(Publication)
            .where(Publication.person_id == person_id, PublicationTag.number.is_not(None))
        ):
            numbers.setdefault(pt.publication_id, {})[pt.tag_id] = pt.number
        for period in s.scalars(
            select(Period)
            .where(Period.person_id == person_id)
            .options(selectinload(Period.paper_notes))
        ):
            for pt in period.paper_tags:
                period_tags.setdefault(pt.publication_id, {}).setdefault(period.id, set()).add(
                    pt.tag_id
                )
                if pt.number is not None:
                    period_numbers.setdefault(pt.publication_id, {}).setdefault(period.id, {})[
                        pt.tag_id
                    ] = pt.number
            for pn in period.paper_notes:
                period_notes.setdefault(pn.publication_id, {})[period.id] = pn.text
        out: list[PubStat] = []
        for i, pub in enumerate(pubs):
            if i % 50 == 49:
                await asyncio.sleep(0)  # keep the UI responsive on big lists
            members = [m for m in main_members(pub.members) if m.link.active]
            if not members and not pub.missing:
                continue
            raw = {m.id: m for m in members}
            views = [member_view(m, texts, by_issn, names) for m in members]
            mark_minor(views, venues)
            badge, best, venue = await resolve_publication(pub, views, raw, venues)
            archival_only = bool(views) and all(v.archival for v in views)
            kind, kind_source = publication_kind(pub, badge, venue, views, raw)
            # A book or a chapter has no venue (unless linked to one by hand).
            no_venue = kind in NO_VENUE_KINDS and not pub.venue_manual
            if no_venue:
                venue = None
                if not pub.rank_override:
                    badge = None
            if (
                not pub.venue_manual
                and not archival_only
                and (venue is not None or no_venue)
                and pub.venue_id != (venue.id if venue else None)
            ):
                # Linked after the loop, in a short write transaction (SQLite has a single
                # writer; resolutions write the cache meanwhile).
                pending_links[pub.id] = venue.id if venue else None
            host = None
            if kind in UNRANKED_KINDS and not pub.rank_override:
                badge = None  # an edited volume: not a paper of its venue
            if kind in WORKSHOP_KINDS and not pub.rank_override and not _has_decision(venue):
                badge, host = await workshop_badge(venue, pub.year_override or pub.year, venues)
            if (
                not pub.rank_override
                and not is_ranked(badge)
                and (level := service.settings.kind_levels.get(kind))
            ):
                # Default level of the kind (e.g. every national conference is "C").
                vtype = "conference" if kind in CONFERENCE_LIKE else "journal"
                name = venue.name if venue else None
                badge = badge_from_level({"type": vtype, "rank": level, "name": name})
                badge.extra["kind_default"] = kind
            flag_track = next((f.track for f in pub.flags if f.track), None)
            text = best.venue if best else None
            # The track of the source whose venue is used (a track wins over none, unless
            # the source without is validated by hand; different tracks: a disagreement).
            validated = best is not None and best.source == pub.venue_source
            track = (
                flag_track
                or (
                    (best.eff_track if validated else track_of(best, venue_members(views)))
                    if best
                    else None
                )
                or ("findings" if badge and badge.findings else None)
            )
            pos, num, authors, pos_exact = _authorship(members)
            if pub.author_pos_override is not None:  # (0: the order does not matter)
                pos, pos_exact = pub.author_pos_override, True
            marks, notes = people.marks(authors, pos, pos_exact, pub.year_override or pub.year)
            if pub.author_pos_override is None:
                for m in ("owner", "owner?"):
                    if m in marks:
                        pos = marks.index(m) + 1  # position found through a name / alias
                        break
            venue_ids = {
                same_venue(v)
                for v in venue_members(views)
                if not v.archival and v.venue_id and not v.minor
            }
            # Different tracks: to be settled by hand (a flag with a track, or a source
            # validated), even when a DOI record gives the venue.
            track_conflict = (
                len(paper_tracks(venue_members(views))) > 1
                and not flag_track
                and not validated
                and not pub.rank_override
                and not no_venue
            )
            dm = doi_member(views)
            pdf_links = list(dict.fromkeys(v.pdf_url for v in views if v.pdf_url))
            url = next((v.url for v in views if not v.archival and v.url), None) or next(
                (v.url for v in views if v.url), None
            )
            out.append(
                PubStat(
                    id=pub.id,
                    title=pub.title,
                    year=pub.year_override or pub.year,
                    badge=badge,
                    track=track,
                    category=category_of(badge, track, kind),
                    kind=kind,
                    kind_source=kind_source,
                    venue_id=None if no_venue else venue.id if venue else pub.venue_id,
                    venue_manual=pub.venue_manual,
                    venue_source=pub.venue_source,
                    rank_override=pub.rank_override,
                    rank_note=pub.rank_note,
                    num_authors=num,
                    author_pos=pos,
                    authors=authors,
                    author_marks=marks,
                    author_notes=notes,
                    venue=(venue.name if venue else None)
                    or text
                    or next((v.venue for v in views if v.venue), None),
                    url=url,
                    doi=pub.doi,
                    pdf_urls=pdf_links,
                    sources=list(dict.fromkeys(v.source for v in views)),
                    members=views,
                    flags=[(f.id, f.name, f.colour) for f in pub.flags],
                    missing=pub.missing,
                    hidden=pub.hidden,
                    archival_only=archival_only,
                    # With a DOI record, its venue is the paper's: no disagreement.
                    disagree=len(venue_ids) > 1 and (dm is None or dm.minor) and not no_venue,
                    track_conflict=track_conflict,
                    tags={tg.id for tg in pub.tags},
                    period_tags=period_tags.get(pub.id, {}),
                    period_notes=period_notes.get(pub.id, {}),
                    numbers=numbers.get(pub.id, {}),
                    pdf=None
                    if pub.id not in stored_pdfs
                    else "edited"
                    if stored_pdfs[pub.id]
                    else "stored",
                    period_numbers=period_numbers.get(pub.id, {}),
                    venue_short=None
                    if archival_only or no_venue
                    else venue.short_name
                    if venue and venue.short_manual
                    else auto_short_name(
                        badge,
                        [v.venue for v in venue_members(views)],
                        workshop=kind in WORKSHOP_KINDS,
                    ),
                    publisher_url=next(
                        (v.publisher_url for v in views if v.publisher_url and not v.archival),
                        None,
                    )
                    or (f"https://doi.org/{pub.doi}" if pub.doi else None),
                    venue_raw=text or next((v.venue for v in views if v.venue), None),
                    year_manual=bool(pub.year_override),
                    doi_manual=pub.doi_manual,
                    author_pos_manual=pub.author_pos_override is not None,
                    note=pub.note,
                    host_id=host.id if host else None,
                    host_name=host.name if host else None,
                )
            )
    if pending_links:
        link_venues(pending_links)
    roles = contribution.load_config()
    for st in out:
        st.contribution = contribution.role(st, roles)
    _save_problem_years(person_id, out)
    return out


# ---- problems, cached for the People page --------------------------------------------------

PROBLEMS_KEY = "problems.{}"  # AppSetting: years of a person's papers with problems


def _save_problem_years(person_id: int, stats: list[PubStat]) -> None:
    years = sorted((s.year for s in stats if s.problems and not s.hidden), key=lambda y: y or 0)
    with session_scope() as s:
        s.merge(AppSetting(key=PROBLEMS_KEY.format(person_id), value={"years": years}))


def problem_years(person_ids: Iterable[int]) -> dict[int, list[int | None]]:
    """Years of the (shown) papers with problems, by person, as of their last computation
    (people never computed are left out)."""
    keys = {PROBLEMS_KEY.format(pid): pid for pid in person_ids}
    with session_scope() as s:
        rows = s.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))
        return {keys[r.key]: r.value["years"] for r in rows}


def count_in_period(years: list[int | None], start: int | None, end: int | None) -> int:
    """Papers in a period (as the panel: a paper without a year is always in)."""
    return sum(
        1
        for y in years
        if y is None or ((start is None or y >= start) and (end is None or y <= end))
    )


# ---- panel logic ---------------------------------------------------------------------------

HIST_CAP = 12
YEAR_BINS = 10


@dataclass(frozen=True)
class Sel:
    """A cross-filter selection (port of StatSel)."""

    facet: str  # year | coauthors | category | contribution | phd | authorcat
    label: str
    lo: int = 0
    hi: int = 0
    value: int = 0
    key: str = ""


def match_sel(s: PubStat, sel: Sel) -> bool:
    if sel.facet == "year":
        return s.year is not None and sel.lo <= s.year <= sel.hi
    if sel.facet == "coauthors":
        return s.num_authors is not None and (
            s.num_authors >= HIST_CAP if sel.value == HIST_CAP else s.num_authors == sel.value
        )
    if sel.facet == "category":
        if sel.key == "predatory":
            return bool(s.badge and s.badge.predatory)
        return s.category.key == sel.key
    if sel.facet == "phd":
        return "student" in s.author_marks
    if sel.facet == "authorcat":
        return f"cat:{sel.key}" in s.author_marks
    if sel.facet == "contribution":
        # (lo / hi: within a range of years, when picked on the chart by year)
        return s.contribution == sel.key and (
            not sel.hi or (s.year is not None and sel.lo <= s.year <= sel.hi)
        )
    return False


def year_bin_defs(years: list[int]) -> list[tuple[str, int, int]]:
    if not years:
        return []
    lo, hi = min(years), max(years)
    width = math.ceil((hi - lo + 1) / YEAR_BINS)
    out = []
    for start in range(lo, hi + 1, width):
        end = min(start + width - 1, hi)
        out.append((str(start) if start == end else f"{start}–{end}", start, end))
    return out


def category_list(rows: list[PubStat]) -> list[Category]:
    cats: dict[str, Category] = {}
    for r in rows:
        cats.setdefault(r.category.key, r.category)
    return sorted(cats.values(), key=category_order)


SUMMARY_KEY = "summary"  # the summary dialog's last settings (an AppSetting)
# Languages of the summary (English: the app's labels).
SUMMARY_LANGUAGES = {"en": "English", "fr": "Français"}
_FR_KINDS = {
    "intl_conference": "Conf. int.",
    "intl_workshop": "Atelier int.",
    "intl_journal": "Revue int.",
    "natl_conference": "Conf. nat.",
    "natl_workshop": "Atelier nat.",
    "natl_journal": "Revue nat.",
    "shared_task": "Campagne d'éval.",
    "preprint": "Prépublication",
    "book": "Livre",
    "chapter": "Chapitre",
    "proceedings": "Actes (éd.)",
    "software": "Logiciel",
    "dataset": "Jeu de données",
    "thesis": "Thèse",
    "other": "Autre",
}
_FR_TRACKS = {"findings": "Findings", "short": "Court", "demo": "Démo", "tutorial": "Tutoriel"}


def _fr_category(cat: Category, kind: str | None, n: int) -> str:
    """A category's label in French ("Atelier CORE A*", "Court CORE A", "non classés"; in
    the workshops' line, "dans une conf. CORE A")."""
    unranked = "non classé" + ("s" if n > 1 else "")
    base = cat.base_key
    if base.startswith("k_"):
        label = unranked if base == f"k_{kind}" else _FR_KINDS.get(base[2:], base[2:])
    elif base in ("other", "unranked"):
        label = "autre" if base == "other" else unranked
    else:  # Q1, CORE A*: the same
        label = next((lab for k, lab, _c in BASE_CATEGORIES if k == base), base)
        if cat.workshop and kind in WORKSHOP_KINDS:  # (ranked by its main conference)
            label = f"dans une conf. {label}"
    if cat.workshop and kind not in WORKSHOP_KINDS:
        label = f"Atelier {label}"
    if cat.edited and kind != "proceedings":
        label = f"Actes (éd.) {label}"
    if cat.track:
        label = f"{_FR_TRACKS.get(cat.track, cat.track)} {label}"
    return label


def summary_settings() -> dict:
    with session_scope() as s:
        row = s.get(AppSetting, SUMMARY_KEY)
        return dict(row.value or {}) if row else {}


def save_summary_settings(value: dict) -> None:
    with session_scope() as s:
        s.merge(AppSetting(key=SUMMARY_KEY, value=value))


# How much a category says in the summary: off (only counted in its kind), its count, its
# venues, its venues with their years.
SUMMARY_DETAILS = Labels(
    {"off": N_("Off"), "count": N_("Count"), "list": N_("List"), "years": N_("List + years")}
)


def summary_lines(
    rows: list[PubStat],
    *,
    short: bool = False,
    by_kind: bool = False,
    years: bool = True,
    hidden: Iterable[str] = (),
    details: dict[str, str] | None = None,
    lang: str = "en",
    markdown: bool = False,
) -> list[str]:
    """One line per category, with the venues and years: "5 Q1 (2x Cognition 2025,
    Neuron 2023, …)"; ``by_kind``: one line per kind of venue, with its categories
    ("25 Intl. journal: 13 Q1 (…); 12 Q2 (…)"). ``short``: the venues' short names
    (acronyms) when they have one.

    ``details``: per category key, one of ``SUMMARY_DETAILS`` (by default, "years" or
    "list" following ``years``, and "off" for the ``hidden`` categories). Without the years,
    a venue's papers are counted together ("3x Acoustica"); "off" categories are only
    counted in their kind. ``lang``: one of ``SUMMARY_LANGUAGES``; ``markdown``: a
    Markdown list (one item per line)."""
    fr = lang == "fr"
    no_venue = "sans canal" if fr else "no venue"
    hidden = set(hidden)
    details = details or {}

    def detail(key: str) -> str:
        return details.get(key) or ("off" if key in hidden else "years" if years else "list")

    if not by_kind:  # no kind to count them in
        rows = [r for r in rows if detail(r.category.key) != "off"]

    def record(r: PubStat) -> str | None:
        return r.badge.name if r.badge and r.badge.name else None

    # A venue split in several (e.g. its old name) gets the short name of one of them.
    shorts: dict[str, str] = {}
    for r in rows:
        if r.venue_short:
            for k in (record(r), r.venue):
                if k:
                    shorts.setdefault(k, r.venue_short)

    def venue_name(r: PubStat) -> str:
        if short and (
            name := r.venue_short or shorts.get(record(r) or "") or shorts.get(r.venue or "")
        ):
            return name
        return r.venue or no_venue

    def category_items(papers: list[PubStat], kind: str | None = None) -> list[str]:
        out = []
        for cat in category_list(papers):
            level = detail(cat.key)
            if level == "off":
                continue
            members = [r for r in papers if r.category.key == cat.key]
            if fr:
                label = _fr_category(cat, kind, len(members))
            else:
                with i18n.using("en"):  # (the categories are labelled in the app's language)
                    label = category_label(cat)
                if kind is not None:  # within its kind: "unranked", "A*" for a workshop
                    label = (
                        "unranked" if cat.key == f"k_{kind}" else label.removeprefix("Workshop ")
                    )
            if level == "count":
                out.append(f"{len(members)} {label}")
                continue
            with_years = level == "years"
            counts = Counter((venue_name(r), r.year if with_years else None) for r in members)
            # Most recent first; without the years, the most frequent venues first.
            items = sorted(
                counts.items(),
                key=lambda t: (
                    (-(t[0][1] or 0), t[0][0].lower()) if with_years else (-t[1], t[0][0].lower())
                ),
            )
            venues = ", ".join(
                (f"{n}x " if n > 1 else "") + name + (f" {year}" if year else "")
                for (name, year), n in items
            )
            out.append(f"{len(members)} {label} ({venues})")
        return out

    if not by_kind:
        items = category_items(rows)
        return [f"- {i}" for i in items] if markdown else items
    lines = []
    for kind in (*KIND_ORDER, None):
        papers = [r for r in rows if (r.kind if r.kind in KIND_ORDER else None) == kind]
        if not papers:
            continue
        if fr:
            label = _FR_KINDS.get(kind or "other", "Other")
        else:
            with i18n.using("en"):
                label = KIND_SHORT.get(kind or "other", "Other")
        items = category_items(papers, kind)
        unranked = ("non classé" + ("s" if len(papers) > 1 else "")) if fr else "unranked"
        only = f"{len(papers)} {unranked}"
        head = f"{len(papers)} {label}"
        if len(items) == 1 and (items[0] == only or items[0].startswith(only + " ")):
            # "15 Preprint (arXiv …)"
            head, items = f"{head}{items[0].removeprefix(only)}", []
        line = f"{head}: " + "; ".join(items) if items else head
        lines.append(f"- {line}" if markdown else line)
    return lines


def tagged(rows: list[PubStat], tag_ids: Iterable[int], period_id: int | None) -> list[PubStat]:
    """The papers having one of the tags (global, or within the period); every paper
    without tags to look for."""
    wanted = set(tag_ids)
    return [r for r in rows if r.tags_in(period_id) & wanted] if wanted else list(rows)


def hashtag(name: str) -> str:
    """A tag as an Obsidian #tag: letters, digits, _, - and / (spaces become -); never
    digits only."""
    tag = re.sub(r"[^\w/-]+", "-", name.strip(), flags=re.UNICODE).strip("-") or "tag"
    return f"#{tag}" if not tag.isdigit() else f"#tag-{tag}"


def links_of(person_id: int) -> list[SourceLink]:
    with session_scope() as s:
        return list(s.scalars(select(SourceLink).where(SourceLink.person_id == person_id)))
