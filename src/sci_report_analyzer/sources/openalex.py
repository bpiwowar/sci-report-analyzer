"""OpenAlex (api.openalex.org)."""

from __future__ import annotations

import re
from typing import Any

from ..keys import get_key
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    contact_email,
    get_json,
    normalize_doi,
    to_year,
)

API = "https://api.openalex.org"
SELECT = "id,doi,display_name,publication_year,type,primary_location,best_oa_location,authorships"


def oa_id(url: str | None) -> str:
    return (url or "").rsplit("/", 1)[-1].lower()


def venue_type(work_type: str | None, source_type: str | None) -> str | None:
    if work_type == "conference-paper" or source_type == "conference":
        return "conference"
    if source_type == "journal":
        return "journal"
    return None


def work_to_pub(w: dict[str, Any], owner_id: str) -> FetchedPub:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    authorships = w.get("authorships") or []
    authors = [(a.get("author") or {}).get("display_name") or "" for a in authorships]
    ids = [oa_id((a.get("author") or {}).get("id")) for a in authorships]
    pos = ids.index(owner_id) + 1 if owner_id in ids else None
    issn = src.get("issn_l") or (src.get("issn") or [None])[0]
    best = w.get("best_oa_location") or {}
    return FetchedPub(
        external_key=oa_id(w.get("id")).upper(),
        title=w.get("display_name"),
        year=to_year(w.get("publication_year")),
        venue=src.get("display_name") or loc.get("raw_source_name"),
        authors=authors,
        author_pos=pos,
        num_authors=len(authors) or None,
        issn=issn,
        venue_type=venue_type(w.get("type"), src.get("type")),
        doi=normalize_doi(w.get("doi")),
        url=w.get("id"),
        doc_type=w.get("type"),
        pdf_url=best.get("pdf_url") or loc.get("pdf_url"),
        archival=w.get("type") == "preprint" or src.get("type") == "repository",
        raw={
            "publisher_url": loc.get("landing_page_url")
            if src.get("type") != "repository"
            else None
        },
    )


def params(**kw) -> dict:
    kw["mailto"] = contact_email()
    if key := get_key("openalex"):
        kw["api_key"] = key
    return kw


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    label = "OpenAlex"
    exact_position = True

    def profile_url(self, external_id: str) -> str:
        return f"https://openalex.org/authors/{external_id}"

    def parse_url(self, url: str) -> str | None:
        m = re.search(r"(?:^|/|author\.id:)(a\d{5,})\b", url.strip(), re.I)
        return m.group(1).upper() if m else None

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        data = await get_json(
            f"{API}/authors",
            params=params(search=name, **{"per-page": 10}),
        )
        out = []
        for a in data.get("results", []):
            inst = a.get("last_known_institutions") or []
            topics = [t.get("display_name") for t in (a.get("topics") or [])[:3]]
            ext = oa_id(a.get("id")).upper()
            orcid = a.get("orcid")
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=ext,
                    display_name=a.get("display_name") or ext,
                    url=self.profile_url(ext),
                    affiliation=", ".join(i.get("display_name", "") for i in inst) or None,
                    works_count=a.get("works_count"),
                    orcid=orcid.removeprefix("https://orcid.org/") if orcid else None,
                    extra={"topics": topics},
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        owner = external_id.lower()
        pubs: list[FetchedPub] = []
        cursor = "*"
        while cursor:
            data = await get_json(
                f"{API}/works",
                params=params(
                    filter=f"author.id:{external_id}",
                    cursor=cursor,
                    select=SELECT,
                    **{"per-page": 200},
                ),
            )
            results = data.get("results") or []
            pubs.extend(work_to_pub(w, owner) for w in results)
            cursor = (data.get("meta") or {}).get("next_cursor") if results else None
        return FetchResult(publications=pubs)
