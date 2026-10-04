"""DOI records: the metadata registered with a DOI, the main source of a paper that has one.

Crossref (most publishers) is asked first, then doi.org content negotiation (CSL JSON), which
also covers DataCite (arXiv, Zenodo, HAL) and the other registries. Answers are cached
forever (``DoiRecord``); unknown DOIs are asked again after ``NOT_FOUND_TTL``.

Crossref's polite pool allows 10 requests/s and 3 concurrent ones (5/s and 1 without a
contact address), DataCite about 3000 per 5 minutes: requests are throttled below that.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select

from ..db.models import DoiRecord, utcnow
from ..db.session import session_scope
from ..i18n import _
from .base import (
    FetchedPub,
    FetchResult,
    SourceAdapter,
    SourceError,
    client,
    contact_email,
    normalize_doi,
)

logger = logging.getLogger(__name__)

CROSSREF = "https://api.crossref.org/works/"
CSL = "application/vnd.citationstyles.csl+json"
NOT_FOUND_TTL = timedelta(days=30)
CONCURRENCY = 3
MIN_INTERVAL = 0.15  # seconds between two request starts (< 7 requests/s)
# Crossref's list queries (several DOIs at once) are limited to 3 requests/s.
BATCH = 50
BATCH_INTERVAL = 0.4
RETRIES = 4


class _Throttle:
    def __init__(self, concurrency: int = CONCURRENCY, interval: float = MIN_INTERVAL) -> None:
        self._sem: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._next = 0.0
        self.concurrency, self.interval = concurrency, interval

    async def __aenter__(self) -> None:
        if self._sem is None:  # created in the running loop
            self._sem, self._lock = asyncio.Semaphore(self.concurrency), asyncio.Lock()
        await self._sem.acquire()
        async with self._lock:
            wait = self._next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next = time.monotonic() + self.interval

    async def __aexit__(self, *exc) -> None:
        self._sem.release()

    def pause(self, seconds: float) -> None:
        """Hold every request (after a 429)."""
        self._next = max(self._next, time.monotonic() + seconds)


_throttle = _Throttle()
_batch_throttle = _Throttle(1, BATCH_INTERVAL)


async def _get(url: str, *, throttle: _Throttle | None = None, **kw: Any) -> httpx.Response | None:
    """GET with throttling and backoff; None when the DOI is unknown there (404)."""
    delay = 2.0
    for attempt in range(RETRIES + 1):
        async with throttle or _throttle:
            try:
                res = await client().get(url, **kw)
            except httpx.TransportError as e:
                if attempt == RETRIES:
                    raise SourceError(_("network error: {error}").format(error=e)) from e
                res = None
        if res is not None:
            if res.status_code in (404, 410):
                return None
            if res.status_code == 429 or res.status_code >= 500:
                if attempt == RETRIES:
                    raise SourceError(
                        _("HTTP {status} from {url}").format(status=res.status_code, url=url)
                    )
                wait = float(res.headers.get("Retry-After") or delay)
                (throttle or _throttle).pause(min(wait, 60))
                logger.info("HTTP %s from %s, retrying in %.1fs", res.status_code, url, wait)
            else:
                res.raise_for_status()
                return res
        await asyncio.sleep(delay)
        delay *= 2
    raise SourceError(_("giving up on {url}").format(url=url))


# ---- parsing -------------------------------------------------------------------------------


def _first(v: Any) -> Any:
    return (v[0] if v else None) if isinstance(v, list) else v


def _year(msg: dict[str, Any]) -> int | None:
    for k in ("issued", "published-print", "published-online", "published", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _acronym(event: dict[str, Any] | None) -> str | None:
    """The event's acronym without its year ("SIGIR '21" → "SIGIR")."""
    acr = (event or {}).get("acronym") or ""
    acr = re.sub(r"[\s'’]*(?:19|20)?\d{2}\s*$", "", acr).strip(" -'’")
    return acr or None


def _authors(people: list[dict[str, Any]] | None) -> list[str]:
    out = []
    for a in people or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name")
        if name:
            out.append(name.strip())
    return out


_CONFERENCE_TYPES = {"proceedings-article", "paper-conference"}
_JOURNAL_TYPES = {"journal-article", "article-journal"}
_CHAPTER_TYPES = {"book-chapter", "chapter"}
_PREPRINT_PUBLISHERS = re.compile(r"\b(arxiv|biorxiv|medrxiv|ssrn|zenodo|preprints?)\b", re.I)


def _clean(text: str | None) -> str | None:
    return re.sub(r"\s+", " ", text).strip() or None if text else None


def venue_text(container: str | None) -> str | None:
    """A DOI container title as a venue text: as the registry gives it (but its spaces).
    "Proceedings of the 2018 …", its year, its ordinal and the part of the proceedings it
    names (": System Demonstrations": its track) are left to the cleaning and detection
    rules (Settings)."""
    text = _clean(container)
    return text[:1].upper() + text[1:] if text else None


# ACL Anthology DOIs name the event: 10.18653/v1/2022.acl-long.583, 10.18653/v1/P17-2035.
_ANTHOLOGY_NEW = re.compile(r"^10\.18653/v1/\d{4}\.([a-z]+)-", re.I)
_ANTHOLOGY_OLD = re.compile(r"^10\.18653/v1/([a-z])\d{2}-", re.I)
_ANTHOLOGY_LETTERS = {
    "p": "ACL",
    "d": "EMNLP",
    "n": "NAACL",
    "e": "EACL",
    "c": "COLING",
    "k": "CoNLL",
    "q": "TACL",
    "j": "CL",
    "s": "SemEval",
}
_ANTHOLOGY_NAMES = {
    "acl": "ACL",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "eacl": "EACL",
    "coling": "COLING",
    "conll": "CoNLL",
    "aacl": "AACL",
    "ijcnlp": "IJCNLP",
    "lrec": "LREC",
    "semeval": "SemEval",
    "wmt": "WMT",
    "tacl": "TACL",
    "cl": "CL",
}


def anthology_acronym(doi: str) -> str | None:
    """The venue acronym in an ACL Anthology DOI (not for workshops, whose ids vary)."""
    if m := _ANTHOLOGY_NEW.match(doi):
        return _ANTHOLOGY_NAMES.get(m.group(1).lower())
    if m := _ANTHOLOGY_OLD.match(doi):
        return _ANTHOLOGY_LETTERS.get(m.group(1).lower())
    return None


# Preprint and data repositories (DataCite): arXiv, Zenodo, bioRxiv / medRxiv, SSRN, OSF,
# figshare.
_ARCHIVAL_PREFIXES = ("10.48550/", "10.5281/", "10.1101/", "10.2139/", "10.31219/", "10.6084/")


def _assertion_event(msg: dict[str, Any]) -> dict[str, str] | None:
    """The conference in Springer's "assertion" entries (its books have no ``event``):
    "Included in the following conference series" on the chapter's page."""
    info = {
        a.get("name"): a.get("value")
        for a in msg.get("assertion") or []
        if (a.get("group") or {}).get("name") == "ConferenceInfo" and a.get("value")
    }
    if not info.get("conference_name"):
        return None
    return {
        "name": info["conference_name"],
        **({"acronym": info["conference_acronym"]} if info.get("conference_acronym") else {}),
    }


def parse(msg: dict[str, Any], registry: str) -> dict[str, Any]:
    """Normalized fields of a Crossref message or a CSL JSON record."""
    kind = msg.get("type") or ""
    event = msg.get("event") or _assertion_event(msg)
    if isinstance(event, str):  # CSL: the event name
        event = {"name": event}
    if isinstance(event, dict) and event.get("name"):
        event = {**event, "name": _clean(event["name"])}
    containers = msg.get("container-title") or []
    if isinstance(containers, str):
        containers = [containers]
    containers = [c for c in (_clean(c) for c in containers) if c]
    # A series and its volume ("Lecture Notes in Computer Science", "<book title>"): the
    # most specific one, the last.
    container = containers[-1] if containers else None
    event_name = (event or {}).get("name")
    # A chapter of a conference's book (LNCS...): the conference, not the book's title
    # ("Perception, Representations, Image, Sound, Music" is CMMR 2019's).
    chapter_of_event = kind in _CHAPTER_TYPES and bool(event_name)
    venue = venue_text(
        (event_name if chapter_of_event else None)
        or container
        or event_name
        or msg.get("event-title")
    )
    doi = normalize_doi(msg.get("DOI")) or ""
    acronym = _acronym(event) or anthology_acronym(doi)
    # (Not a workshop's main conference: "Clinical NLP @ LREC 2026".)
    if (
        venue
        and acronym
        and not re.search(rf"\({re.escape(acronym)}\b", venue, re.I)
        and not re.search(rf"@\s*{re.escape(acronym)}\b", venue, re.I)
    ):
        venue = f"{venue} ({acronym})"  # like DBLP: matched through the acronym as well
    publisher = msg.get("publisher") or ""
    archival = (
        kind == "posted-content"
        or msg.get("subtype") == "preprint"
        or doi.startswith(_ARCHIVAL_PREFIXES)
        or (kind in ("article", "") and bool(_PREPRINT_PUBLISHERS.search(publisher)))
    )
    url = ((msg.get("resource") or {}).get("primary") or {}).get("URL") or msg.get("URL")
    journal = kind in _JOURNAL_TYPES
    return {
        "title": _clean(_first(msg.get("title"))),
        "year": _year(msg),
        "venue": venue or (publisher if archival else None),
        # A chapter of a book in a series, without an event, names no venue (a volume title
        # such as "Caring is Sharing"): the other sources' venue is used.
        "venue_reliable": not (kind in _CHAPTER_TYPES and not event),
        "container": container,
        "series": containers[0] if len(containers) > 1 else None,
        "venue_type": "conference"
        if kind in _CONFERENCE_TYPES or chapter_of_event
        else "journal"
        if journal
        else None,
        # A conference paper (not a book chapter) when its book is a conference's.
        "doc_type": "proceedings-article" if chapter_of_event else kind or None,
        "authors": _authors(msg.get("author")),
        # A series' ISSN (LNCS...) would match the series as a journal.
        "issn": _first(msg.get("ISSN")) if journal else None,
        "url": url,
        "publisher": publisher or None,
        "archival": archival,
        "event": event or None,
        "registry": registry,
    }


# ---- fetching and cache --------------------------------------------------------------------


async def fetch_crossref_batch(dois: list[str]) -> dict[str, dict]:
    """Crossref's records of several DOIs in one request ({doi: message}; the DOIs it does
    not know are missing)."""
    res = await _get(
        CROSSREF.rstrip("/"),
        throttle=_batch_throttle,
        params={
            "filter": ",".join(f"doi:{d}" for d in dois),
            "rows": len(dois),
            "mailto": contact_email(),
        },
    )
    items = ((res.json().get("message") or {}).get("items") or []) if res is not None else []
    return {d: it for it in items if (d := normalize_doi(it.get("DOI")))}


async def fetch_record(
    doi: str, *, crossref: bool = True
) -> tuple[str, str | None, dict | None, dict | None]:
    """(status, registry, fields, raw) of a DOI, asking the registries (``crossref``: False
    when it is known not to have it)."""
    if crossref:
        res = await _get(CROSSREF + quote(doi, safe="/"), params={"mailto": contact_email()})
        if res is not None:
            msg = res.json().get("message") or {}
            return "ok", "crossref", parse(msg, "crossref"), msg
    res = await _get(f"https://doi.org/{quote(doi, safe='/')}", headers={"Accept": CSL})
    if res is None:
        return "not_found", None, None, None
    try:
        msg = res.json()
    except ValueError:
        return "not_found", None, None, None  # no metadata (a landing page)
    return "ok", "csl", parse(msg, "csl"), msg


def cached(dois: list[str]) -> dict[str, DoiRecord]:
    with session_scope() as s:
        rows = list(s.scalars(select(DoiRecord).where(DoiRecord.doi.in_(dois)))) if dois else []
        s.expunge_all()
    return {r.doi: r for r in rows}


def _stale(r: DoiRecord | None) -> bool:
    return r is None or (r.status != "ok" and utcnow() - r.fetched_at > NOT_FOUND_TTL)


async def ensure(dois: list[str], *, refresh: bool = False) -> dict[str, int]:
    """Fetch the DOIs missing from the cache (all of them with ``refresh``)."""
    dois = list(dict.fromkeys(d for d in (normalize_doi(x) for x in dois) if d))
    have = cached(dois)
    todo = [d for d in dois if refresh or _stale(have.get(d))]
    counts = {"fetched": 0, "not_found": 0, "errors": 0}

    def store(doi: str, status: str, registry: str | None, data, raw) -> None:
        with session_scope() as s:
            s.merge(
                DoiRecord(
                    doi=doi,
                    status=status,
                    origin=registry,
                    data=data,
                    raw=raw,
                    fetched_at=utcnow(),
                )
            )
        counts["fetched" if status == "ok" else "not_found"] += 1

    # Crossref first, by batches (a filter on several DOIs: far fewer requests).
    missing: list[tuple[str, bool]] = []  # (doi, whether Crossref is still to be asked)
    batchable = [d for d in todo if "," not in d]
    for i in range(0, len(batchable), BATCH):
        chunk = batchable[i : i + BATCH]
        try:
            found = await fetch_crossref_batch(chunk)
        except (SourceError, httpx.HTTPError, ValueError) as e:
            logger.info("Crossref batch: %s", e)
            found = None  # asked one by one
        for d in chunk:
            if found is not None and d in found:
                store(d, "ok", "crossref", parse(found[d], "crossref"), found[d])
            else:
                missing.append((d, found is None))
    missing += [(d, True) for d in todo if "," in d]

    async def one(doi: str, crossref: bool) -> None:
        try:
            status, registry, data, raw = await fetch_record(doi, crossref=crossref)
        except (SourceError, httpx.HTTPError, ValueError) as e:
            logger.info("DOI %s: %s", doi, e)
            counts["errors"] += 1
            return  # not cached: asked again next time
        store(doi, status, registry, data, raw)

    # The others (DataCite, mEDRA...) through doi.org.
    await asyncio.gather(*(one(d, crossref) for d, crossref in missing))
    return counts


def fetched_pub(doi: str, data: dict[str, Any], publication_id: int | None = None) -> FetchedPub:
    raw: dict[str, Any] = {
        "registry": data.get("registry"),
        "publisher": data.get("publisher"),
        "container": data.get("container"),
        "venue_reliable": data.get("venue_reliable", True),
    }
    if data.get("url"):
        raw["publisher_url"] = data["url"]
    if data.get("event"):
        raw["event"] = data["event"]
    if publication_id is not None:
        raw["publication_id"] = publication_id  # a DOI given by hand for this publication
    return FetchedPub(
        external_key=doi,
        title=data.get("title"),
        year=data.get("year"),
        venue=data.get("venue"),
        authors=data.get("authors") or [],
        num_authors=len(data.get("authors") or []) or None,
        issn=data.get("issn"),
        venue_type=data.get("venue_type"),
        doi=doi,
        url=f"https://doi.org/{doi}",
        doc_type=data.get("doc_type"),
        archival=bool(data.get("archival")),
        raw=raw,
    )


def result_for(dois: dict[str, int | None]) -> FetchResult:
    """The DOI source's records of a person, from the cache ({doi: publication given by
    hand or None})."""
    records = cached(list(dois))
    pubs = [
        # Parsed again from the registry's answer: parsing improvements apply to the cache.
        fetched_pub(doi, parse(r.raw, r.origin or "crossref") if r.raw else r.data, dois[doi])
        for doi, r in records.items()
        if r.status == "ok" and (r.raw or r.data)
    ]
    return FetchResult(publications=pubs)


class DoiAdapter(SourceAdapter):
    """Not searched: every person gets a DOI source, filled from their papers' DOIs."""

    name = "doi"
    label = "DOI"
    linkable = False

    async def search(self, name: str, affiliation: str | None = None) -> list:
        return []

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        raise SourceError(_("DOI records are fetched from the person's publications"))

    def profile_url(self, external_id: str) -> str | None:
        return None
