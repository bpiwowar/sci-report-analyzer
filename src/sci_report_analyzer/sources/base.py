"""Source adapter protocol and shared HTTP helpers."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import __version__
from ..keys import get_key

logger = logging.getLogger(__name__)


@dataclass
class AuthorCandidate:
    source: str
    external_id: str
    display_name: str
    url: str | None = None
    affiliation: str | None = None
    works_count: int | None = None
    orcid: str | None = None
    sample_titles: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def evidence(self) -> dict[str, Any]:
        return {
            "affiliation": self.affiliation,
            "works_count": self.works_count,
            "orcid": self.orcid,
            "sample_titles": self.sample_titles[:5],
            **self.extra,
        }


@dataclass
class FetchedPub:
    external_key: str
    title: str | None
    year: int | None = None
    venue: str | None = None
    authors: list[str] = field(default_factory=list)
    author_pos: int | None = None
    num_authors: int | None = None
    issn: str | None = None
    venue_type: str | None = None  # "journal" | "conference" hint
    doi: str | None = None
    url: str | None = None
    doc_type: str | None = None
    pdf_url: str | None = None
    # A preprint / archival deposit (arXiv, HAL preprint...) rather than the published version.
    archival: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedThesis:
    thesis_id: str
    role: str
    title: str | None
    student: str | None = None
    supervisors: list[str] = field(default_factory=list)
    status: str | None = None
    defence_date: str | None = None
    start_date: str | None = None
    discipline: str | None = None
    institution: str | None = None
    url: str | None = None


@dataclass
class FetchResult:
    publications: list[FetchedPub] = field(default_factory=list)
    theses: list[FetchedThesis] = field(default_factory=list)
    display_name: str | None = None


class SourceError(Exception):
    """A user-facing fetch failure (blocked, not found, ...)."""


class SourceAdapter(ABC):
    name: str
    label: str
    provides_theses: bool = False
    provides_publications: bool = True
    # Whether people are searched on it / linked by hand (not the automatic DOI source).
    linkable: bool = True
    # Sources that report the owner's position exactly (by author id).
    exact_position: bool = False

    @abstractmethod
    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]: ...

    @abstractmethod
    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult: ...

    @abstractmethod
    def profile_url(self, external_id: str) -> str | None: ...

    def parse_url(self, url: str) -> str | None:
        """Extract an external id from a pasted profile URL (or bare id)."""
        return None


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^(?:https?://)?(?:dx\.|www\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower() or None


_REPOSITORY_HOSTS = re.compile(
    r"^https?://(?:[\w-]+\.)*(?:hal\.science|archives-ouvertes\.fr|arxiv\.org|dblp\.org|"
    r"openalex\.org|semanticscholar\.org|scholar\.google\.[a-z.]+|orcid\.org|theses\.fr|"
    r"researchgate\.net|zenodo\.org)(?:[/:?#]|$)",
    re.I,
)


def is_repository_url(url: str) -> bool:
    """Whether a link points to a repository / index rather than the publisher's page."""
    return bool(_REPOSITORY_HOSTS.match(url))


def to_int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_year(v: Any) -> int | None:
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(v)) if v not in (None, "") else None
    return int(m.group(1)) if m else None


class NoContactEmail(SourceError):
    def __init__(self) -> None:
        super().__init__("set your email in Settings → API keys first")


def contact_email() -> str:
    """The user's email, sent to the APIs' "polite pools": no request goes out without it."""
    if email := get_key("email"):
        return email
    raise NoContactEmail


def user_agent() -> str:
    return f"sci-report-analyzer/{__version__} (mailto:{contact_email()})"


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), follow_redirects=True)
    _client.headers["User-Agent"] = user_agent()
    return _client


async def close_client() -> None:
    if _client is not None:
        await _client.aclose()


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    retries: int = 4,
) -> Any:
    """GET/POST JSON with backoff on 429 / 5xx."""
    delay = 1.5
    for attempt in range(retries + 1):
        try:
            res = await client().request(method, url, params=params, headers=headers, data=data)
        except httpx.TransportError as e:
            if attempt == retries:
                raise SourceError(f"network error: {e}") from e
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if res.status_code == 429 or res.status_code >= 500:
            if attempt == retries:
                raise SourceError(f"HTTP {res.status_code} from {url}")
            wait = float(res.headers.get("Retry-After", delay)) if res.status_code == 429 else delay
            if wait > 120:
                try:
                    message = res.json().get("message")
                except ValueError:
                    message = None
                raise SourceError(message or f"rate limited by {url} (retry in {wait:.0f}s)")
            logger.info("HTTP %s from %s, retrying in %.1fs", res.status_code, url, wait)
            await asyncio.sleep(min(wait, 60))
            delay *= 2
            continue
        if res.status_code == 404:
            raise SourceError(f"not found: {url}")
        res.raise_for_status()
        return res.json()
    raise SourceError(f"giving up on {url}")
