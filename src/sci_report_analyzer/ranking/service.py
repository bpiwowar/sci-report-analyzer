"""Venue resolution service: matchers, settings, corrections, levels and the badge cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from ..db.models import AppSetting, JcrRecord, VenueCache, utcnow
from ..db.session import session_scope
from ..sources.base import SourceError, user_agent
from ..sources.openalex import params as openalex_params
from . import datasets, detection
from .badge import (
    FINDINGS_RE,
    TOGGLABLE_SOURCES,
    Badge,
    badge_archival,
    badge_from_record,
)
from .detection import DetectionRule, default_detection_rules
from .kinds import DEFAULT_INTERNATIONAL_KEYWORDS, DEFAULT_NATIONAL_KEYWORDS
from .matcher import Matcher
from .normalize import (
    NormRule,
    apply_rules,
    default_rules,
    is_non_venue,
    normalize,
    tokenize,
    tokens_match,
    without_ordinal_marks,
)

logger = logging.getLogger(__name__)

MATCHING_KEY = "matching"
LONG_TTL = timedelta(days=90)
SHORT_TTL = timedelta(days=14)  # OpenAlex fallback and "not ranked"
_ACRONYM_RE = re.compile(r"[A-Z]{3,}")
_FINDINGS_PREFIX = re.compile(r"^\s*findings\s+(?:of\s+(?:the\s+)?)?", re.I)
# A parenthesised acronym, as in DBLP stream titles: "AAAI Conference on AI (AAAI)".
_PAREN_ACRONYM = re.compile(r"\(\s*([A-Za-z][A-Za-z0-9&+-]*[A-Z][A-Za-z0-9&+-]*)\s*\)")
OPENALEX_BACKOFF = timedelta(minutes=15)


# Bumped when the matching logic changes, so that cached matches are recomputed.
MATCH_VERSION = "m8"
# Words saying a venue text is a conference (not a journal).
_CONFERENCE_CUE = re.compile(
    r"\b(?:conf(?:erence|\.)?|conférence|symposium|workshops?|congress|colloque|"
    r"proceedings|proc\.)(?=\W|$)",
    re.I,
)
# Words naming a journal ("Journal of Neuroscience"), and a society or an event instead
# ("Society for Neuroscience", its annual meeting).
_JOURNAL_FORM = re.compile(
    r"\b(?:journal|transactions|letters|review|annals|bulletin|magazine|quarterly|revue)\b",
    re.I,
)
_NOT_A_JOURNAL = re.compile(
    r"\b(?:society|soci[ée]t[ée]|association|meeting|congress|forum|summit)\b", re.I
)
# Minimum score of a conference record replacing a fuzzy journal match.
CONFERENCE_ALT_SCORE = 0.75


# Parenthesised text, kept in the cleaned texts (it can name a track: "(Demonstrations)")
# but not in the rankings: "ACL (Findings)", "... Linguistics (Volume 1: Long Papers)".
_PARENS = re.compile(r"\((?:[^()]|\([^()]*\))*\)")


def for_rankings(text: str) -> str:
    """A cleaned venue text as the rankings name it: without its parenthesised text, nor
    its ordinal marks ("Zth")."""
    return re.sub(r"\s{2,}", " ", _PARENS.sub(" ", without_ordinal_marks(text))).strip()


def paren_acronym(raw: str | None) -> str | None:
    """Last parenthesised token with ≥2 capitals (a venue acronym), if any."""
    found = [m for m in _PAREN_ACRONYM.findall(raw or "") if len(re.findall(r"[A-Z]", m)) >= 2]
    return found[-1] if found else None


# Words any conference name can have: they say nothing of which one it is.
_GENERIC_WORDS = frozenset(
    [
        "international",
        "national",
        "annual",
        "conference",
        "conferences",
        "symposium",
        "workshop",
        "proceedings",
        "meeting",
        "joint",
        "acm",
        "ieee",
        "ifip",
    ]
)


def _name_overlap(text: str, name: str | None) -> bool:
    """Whether a venue text and a ranking record's name substantially overlap: at least half
    of the distinctive words of the shorter one are shared, a word matching the other's
    word, a prefix of it ("comput" ↔ "computing") or the initials of consecutive words
    ("AI" ↔ "Artificial Intelligence")."""
    a = [w for w in tokenize(_PAREN_ACRONYM.sub(" ", text)) if w not in _GENERIC_WORDS]
    b = [w for w in tokenize(name) if w not in _GENERIC_WORDS]
    if not a or not b:
        return False

    def found(word: str, words: list[str]) -> bool:
        if any(tokens_match(word, w) for w in words):
            return True
        return len(word) >= 2 and any(
            "".join(w[0] for w in words[i : i + len(word)]) == word for i in range(len(words))
        )

    shared = max(sum(found(w, b) for w in a), sum(found(w, a) for w in b))
    return shared >= max(1, min(len(a), len(b)) / 2)


def is_ranked(b: Badge | None) -> bool:
    return bool(b and (b.coreRank or (b.quartile and b.quartile.startswith("Q")) or b.manual))


class VenuePattern(BaseModel):
    """A venue's regex rule: source venue texts it matches belong to the venue.

    Stored on the venue (``Venue.patterns``) and matched against the raw venue texts of the
    sources (SQLite ``REGEXP``, i.e. Python's ``re.search``).
    """

    pattern: str
    ignore_case: bool = True
    # Sources whose venue texts the rule applies to (empty: every source).
    sources: list[str] = Field(default_factory=list)
    # Track (demo, findings, short...) of the publications whose venue text matches.
    track: str | None = None
    note: str | None = None

    @property
    def regex(self) -> str:
        """The pattern with its flags inlined (as given to SQLite's REGEXP)."""
        return f"(?i){self.pattern}" if self.ignore_case else self.pattern

    def compiled(self) -> re.Pattern | None:
        try:
            return re.compile(self.pattern, re.I if self.ignore_case else 0)
        except re.error:
            return None

    def applies(self, source: str | None, raw: str | None) -> bool:
        if not raw or (self.sources and source not in self.sources):
            return False
        rx = self.compiled()
        return bool(rx and rx.search(raw))


def _default_sources() -> dict[str, bool]:
    # OpenAlex needs an API key nowadays: off unless one is configured.
    return {s: s != "openalex" for s in TOGGLABLE_SOURCES}


class MatchSettings(BaseModel):
    """Matching settings: ranking sources, threshold, normalization rules and venue kinds."""

    sources: dict[str, bool] = Field(default_factory=_default_sources)
    min_score: float = 0.8
    # Ordered regex substitutions cleaning the venue texts of the sources.
    norm_rules: list[NormRule] = Field(default_factory=default_rules)
    # Venue kinds (first level): keywords deciding national vs international, the scope
    # of venues that give no clue, and an optional default level per kind (e.g. every
    # national conference counts as "C").
    national_keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_NATIONAL_KEYWORDS))
    international_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_INTERNATIONAL_KEYWORDS)
    )
    unknown_scope: str = "international"
    kind_levels: dict[str, str] = Field(default_factory=dict)
    # The regexes classifying venues and papers (kinds, workshops, tracks, joint
    # conferences), each by id; one missing takes its default (``ranking.detection``).
    detection_rules: list[DetectionRule] = Field(default_factory=default_detection_rules)
    # CORE rank of a paper: the edition in force when it was published, or the latest.
    core_edition: Literal["publication", "latest"] = "publication"

    @field_validator("detection_rules")
    @classmethod
    def _every_detection_rule(cls, rules: list[DetectionRule]) -> list[DetectionRule]:
        return detection.completed(rules)

    def source_on(self, source: str) -> bool:
        # manual / archival are always on
        return self.sources.get(source, _default_sources().get(source, True))

    @property
    def rules_hash(self) -> str:
        """Changes when the normalization rules do (the venue keys are then recomputed)."""
        dump = json.dumps([r.model_dump() for r in self.norm_rules], sort_keys=True)
        return hashlib.sha1(dump.encode()).hexdigest()[:16]


def load_settings() -> MatchSettings:
    with session_scope() as s:
        row = s.get(AppSetting, MATCHING_KEY)
        return MatchSettings.model_validate(row.value) if row else MatchSettings()


def save_settings(settings: MatchSettings) -> None:
    with session_scope() as s:
        s.merge(AppSetting(key=MATCHING_KEY, value=settings.model_dump()))
    service.invalidate(clear_cache=True)


class RankingService:
    def __init__(self) -> None:
        self._matcher: Matcher | None = None
        self._sjr_years: set[int] = set()
        self._predatory: Matcher | None = None
        self._settings: MatchSettings | None = None
        self._cache: dict[str, tuple[Badge | None, Any]] | None = None
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._openalex_down_until = None

    # ---- state ---------------------------------------------------------------------------

    def invalidate(self, *, data: bool = False, clear_cache: bool = False) -> None:
        """Drop in-memory state after a settings change (optionally the badge cache)."""
        self._settings = None
        detection.reset()
        if data:
            self._matcher = self._predatory = None
        if clear_cache:
            self.clear_cache()

    @property
    def settings(self) -> MatchSettings:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    @property
    def matcher(self) -> Matcher:
        if self._matcher is None:
            m = Matcher()
            for name in datasets.ranking_files():
                if name == datasets.JOURNALS_FILE:
                    journals = datasets.load_journals()
                    self._sjr_years = {
                        *datasets.scimago_years(journals),
                        *datasets.imported_scimago_years(),
                    }
                    m.load(journals)
                else:
                    m.load(datasets.load_file(name))
            with session_scope() as s:
                m.load([r.data for r in s.scalars(select(JcrRecord))])
            self._matcher = m
        return self._matcher

    @property
    def scimago_years(self) -> set[int]:
        """The years of the Scimago ranking loaded (bundled, or imported)."""
        _ = self.matcher
        return self._sjr_years

    def scimago_missing(self, year: int | None) -> bool:
        """Whether the Scimago ranking of ``year`` could be imported (not loaded, published)."""
        return (
            year is not None
            and datasets.SCIMAGO_FIRST_YEAR <= year <= max(self.scimago_years, default=year)
            and year not in self.scimago_years
        )

    @property
    def predatory(self) -> Matcher:
        if self._predatory is None:
            self._predatory = Matcher().load(datasets.load_predatory())
        return self._predatory

    def clean(self, raw: str | None, source: str | None = None) -> str:
        """A venue text after the normalization rules (those applying to ``source``)."""
        return apply_rules(raw, self.settings.norm_rules, source)

    def key(self, raw: str | None, source: str | None = None) -> str:
        """Key of a venue text: its cleaned, normalized form (variants are matched by key)."""
        return normalize(self.clean(raw, source))

    # ---- cache ---------------------------------------------------------------------------

    def _load_cache(self) -> dict[str, tuple[Badge | None, Any]]:
        if self._cache is None:
            now = utcnow()
            cache: dict[str, tuple[Badge | None, Any]] = {}
            with session_scope() as s:
                for row in s.scalars(select(VenueCache)):
                    badge = Badge.from_dict(row.badge)
                    ttl = LONG_TTL if badge and badge.source != "openalex" else SHORT_TTL
                    if now - row.ts <= ttl:
                        cache[row.key] = (badge, row.ts)
            self._cache = cache
        return self._cache

    def _cache_set(self, key: str, badge: Badge | None) -> None:
        now = utcnow()
        self._load_cache()[key] = (badge, now)
        with session_scope() as s:
            s.merge(
                VenueCache(
                    key=key,
                    badge=badge.to_dict() if badge else None,
                    source=badge.source if badge else None,
                    ts=now,
                )
            )

    def clear_cache(self, source: str | None = None) -> int:
        with session_scope() as s:
            q = delete(VenueCache)
            if source == "unranked":
                q = q.where(VenueCache.source.is_(None))
            elif source:
                q = q.where(VenueCache.source == source)
            n = s.execute(q).rowcount
        self._cache = None
        return n or 0

    def cache_stats(self) -> dict[str, int]:
        with session_scope() as s:
            rows = s.execute(
                select(VenueCache.source, func.count()).group_by(VenueCache.source)
            ).all()
        return {(src or "unranked"): n for src, n in rows}

    # ---- resolution ----------------------------------------------------------------------

    async def _openalex(self, venue: str) -> Badge | None:
        if self._openalex_down_until and utcnow() < self._openalex_down_until:
            raise httpx.HTTPError("OpenAlex temporarily disabled after a rate-limit error")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        res = await self._client.get(
            "https://api.openalex.org/sources",
            params=openalex_params(search=venue, **{"per-page": 1}),
            headers={"User-Agent": user_agent()},
        )
        if res.status_code == 429:
            # Out of budget: stop hammering it for a while (results stay uncached).
            self._openalex_down_until = utcnow() + OPENALEX_BACKOFF
        res.raise_for_status()
        results = res.json().get("results") or []
        if not results:
            return None
        s = results[0]
        stats = s.get("summary_stats") or {}
        return Badge(
            name=s.get("display_name") or venue,
            source="openalex",
            type="conference" if s.get("type") == "conference" else "journal",
            hindex=stats.get("h_index"),
            twoYearMeanCitedness=stats.get("2yr_mean_citedness"),
            worksCount=s.get("works_count"),
            score=0.8,
            exact=False,
            url=s.get("homepage_url") or s.get("id"),
        )

    async def resolve(
        self,
        raw: str | None,
        issn: str | None = None,
        type_hint: str | None = None,
        *,
        venue_override: str | None = None,
        source: str | None = None,
    ) -> Badge | None:
        """Resolve a raw venue string to a badge through the rankings (the venue-level manual
        decisions are applied by ``venues.venue_badge``).

        ``venue_override`` is a corrected text to search the rankings with (a venue's
        "search rankings as"); ``source``: the source of the text (its rules apply).
        """
        st = self.settings
        if not self.key(raw, source) and not venue_override:
            return None
        corrected = venue_override
        venue = corrected if corrected is not None else self.clean(raw, source)
        norm_venue = normalize(venue)
        raw_s = raw or ""
        findings = corrected is None and bool(FINDINGS_RE.search(raw_s))
        host_raw = raw_s
        if findings:
            colon = raw_s.rfind(":")
            host_raw = (
                _FINDINGS_PREFIX.sub("", raw_s, count=1) if colon == -1 else raw_s[colon + 1 :]
            )
        acronym = paren_acronym(host_raw) if corrected is None and type_hint != "journal" else None
        key = f"{norm_venue}#{type_hint}" if type_hint else norm_venue
        if acronym:
            key += f"@{acronym}"
        key = f"{MATCH_VERSION}|{key}"  # cached matches of older matching code are redone

        if corrected is None and is_non_venue(norm_venue):
            return badge_archival(key)

        def with_corrected(b: Badge | None) -> Badge | None:
            return b.copy(corrected=True) if b and corrected is not None else b

        hit = self._load_cache().get(key)
        if hit is not None and (hit[0] is None or st.source_on(hit[0].source)):
            return with_corrected(hit[0])

        match_venue = (self.clean(host_raw, source) or venue) if findings else venue
        if corrected is None:
            match_venue = for_rankings(match_venue) or match_venue
        # Matching takes a few ms: let the other tasks run between the venues of a batch.
        await asyncio.sleep(0)

        badge: Badge | None = None
        cacheable = True
        m = self.matcher.match(match_venue, issn, type_hint)
        if m and m.score >= st.min_score and st.source_on(m.record["source"]):
            badge = badge_from_record(m.record, m.score, m.exact)
        if (
            m
            and badge is not None
            and badge.type == "journal"
            # A fuzzy journal match, or one without a real quartile (Scimago lists some
            # proceedings, e.g. ACL's, with none).
            and (not m.exact or badge.quartile not in ("Q1", "Q2", "Q3", "Q4"))
            and (type_hint == "conference" or _CONFERENCE_CUE.search(raw_s))
        ):
            # A conference text fuzzily matching a journal ("International Conference on
            # Language Resources and Evaluation" vs the journal "Language Resources and
            # Evaluation"): take a close enough conference record instead.
            alt = next(
                (
                    c
                    for c in self.matcher.candidates(match_venue, None, 10)
                    if c.record.get("type") == "conference"
                    and c.score >= CONFERENCE_ALT_SCORE
                    and st.source_on(c.record["source"])
                ),
                None,
            )
            if alt is not None:
                badge = badge_from_record(alt.record, alt.score, alt.exact)
        if (
            m
            and badge is not None
            and not m.exact
            and badge.name == m.record.get("name")
            and badge.type == "journal"
            and _JOURNAL_FORM.search(badge.name or "")
            and not _JOURNAL_FORM.search(raw_s)
            and (_NOT_A_JOURNAL.search(raw_s) or _CONFERENCE_CUE.search(raw_s))
        ):
            # The generic words differ: "Society for Neuroscience" is not the "Journal of
            # Neuroscience", even if both reduce to "neuroscience".
            badge = None
        if not is_ranked(badge) and acronym and st.source_on("core"):
            # DBLP-style "(ACRONYM)" suffixes resolve to CORE by alias,
            # e.g. "AAAI Conference on AI (AAAI)" whose CORE name differs. The names must
            # still overlap: "IC" is also "International Conference on Internet Computing".
            # A joint conference ("LREC-COLING") is tried with each of its acronyms.
            parts = (
                [acronym, *re.split(r"[-/+&]", acronym)]
                if re.search(r"[-/+&]", acronym)
                else [acronym]
            )
            for part in dict.fromkeys(p.strip() for p in parts if p.strip()):
                am = self.matcher.match(part, None, "conference")
                if (
                    am
                    and am.exact
                    and am.record["source"] == "core"
                    and _name_overlap(match_venue, am.record.get("name"))
                ):
                    badge = badge_from_record(am.record, am.score, am.exact)
                    break
        if badge is None and st.source_on("openalex") and match_venue:
            try:
                badge = await self._openalex(match_venue)
            except (httpx.HTTPError, ValueError, SourceError) as e:
                logger.info("OpenAlex venue lookup failed (not cached): %s", e)
                cacheable = False

        if badge and findings:
            badge.findings = True

        if st.source_on("predatory"):
            ph = self.predatory.match(match_venue, issn)
            if ph and (ph.exact or ph.score >= 0.9):
                if badge is None:
                    badge = Badge(
                        name=ph.record["name"],
                        source="predatory",
                        type="journal",
                        score=ph.score,
                        exact=ph.exact,
                    )
                badge.predatory = True
                badge.predatoryUrl = ph.record.get("url")

        if cacheable:
            self._cache_set(key, badge)
        return with_corrected(badge)

    def candidates(self, raw: str, issn: str | None = None, limit: int = 8) -> list[Badge]:
        """Candidate records for the manual picker (cleaned name + detected acronyms)."""
        st = self.settings
        cleaned = for_rankings(self.clean(raw))
        scored: list[tuple[Any, bool]] = [
            (r, False) for r in self.matcher.candidates(cleaned, issn)
        ]
        for acr in dict.fromkeys(_ACRONYM_RE.findall(raw or "")):
            scored.extend((r, True) for r in self.matcher.candidates(acr))
        scored.sort(key=lambda t: -t[0].score)
        seen: set[int] = set()
        out: list[Badge] = []
        for r, acronym in scored:
            if id(r.record) in seen or not st.source_on(r.record["source"]):
                continue
            seen.add(id(r.record))
            b = badge_from_record(r.record, r.score, r.exact)
            b.extra["acronym"] = acronym
            out.append(b)
        return out[:limit]

    def search(self, text: str, limit: int = 20) -> list[Badge]:
        return [
            badge_from_record(r.record, r.score, r.exact) for r in self.matcher.search(text, limit)
        ]

    def badge_for_record(self, record_key: str) -> Badge | None:
        rec = self.matcher.by_key(record_key)
        return badge_from_record(rec, 1.0, True) if rec else None


service = RankingService()
detection.use(lambda: service.settings.detection_rules)
