"""Semantic Scholar Graph API."""

from __future__ import annotations

import re
from typing import Any

from ..keys import get_key
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    get_json,
    normalize_doi,
    to_year,
)

API = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = (
    "title,year,venue,publicationVenue,externalIds,authors,publicationTypes,url,openAccessPdf,"
    "journal"
)


def _headers() -> dict[str, str]:
    key = get_key("semanticscholar")
    return {"x-api-key": key} if key else {}


def paper_to_pub(p: dict[str, Any], owner_id: str) -> FetchedPub:
    authors = p.get("authors") or []
    names = [a.get("name") or "" for a in authors]
    ids = [str(a.get("authorId")) for a in authors]
    pv = p.get("publicationVenue") or {}
    ext = p.get("externalIds") or {}
    types = p.get("publicationTypes") or []
    vtype = pv.get("type")
    venue_type = (
        "conference"
        if vtype == "conference" or "Conference" in types
        else ("journal" if vtype == "journal" or "JournalArticle" in types else None)
    )
    venue = pv.get("name") or p.get("venue") or (p.get("journal") or {}).get("name")
    arxiv = ext.get("ArXiv")
    pdf = (p.get("openAccessPdf") or {}).get("url") or (
        f"https://arxiv.org/pdf/{arxiv}" if arxiv else None
    )
    return FetchedPub(
        external_key=p["paperId"],
        title=p.get("title"),
        year=to_year(p.get("year")),
        venue=venue,
        authors=names,
        author_pos=ids.index(owner_id) + 1 if owner_id in ids else None,
        num_authors=len(names) or None,
        issn=pv.get("issn"),
        venue_type=venue_type,
        doi=normalize_doi(ext.get("DOI")),
        url=p.get("url"),
        doc_type=",".join(types) or None,
        pdf_url=pdf or None,
        archival=bool(venue and re.fullmatch(r"(?i)arxiv(\.org)?", venue.strip())),
        raw={"arxiv": arxiv},
    )


class SemanticScholarAdapter(SourceAdapter):
    name = "semanticscholar"
    label = "Semantic Scholar"
    exact_position = True

    def profile_url(self, external_id: str) -> str:
        return f"https://www.semanticscholar.org/author/{external_id}"

    def parse_url(self, url: str) -> str | None:
        url = url.strip()
        if m := re.search(r"semanticscholar\.org/author/(?:[^/]+/)?(\d+)", url):
            return m.group(1)
        return url if url.isdigit() else None

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        data = await get_json(
            f"{API}/author/search",
            params={
                "query": name,
                "fields": "name,affiliations,paperCount,externalIds,url",
                "limit": 10,
            },
            headers=_headers(),
        )
        out = []
        for a in data.get("data", []):
            ext = str(a["authorId"])
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=ext,
                    display_name=a.get("name") or ext,
                    url=a.get("url") or self.profile_url(ext),
                    affiliation=", ".join(a.get("affiliations") or []) or None,
                    works_count=a.get("paperCount"),
                    orcid=(a.get("externalIds") or {}).get("ORCID"),
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        pubs: list[FetchedPub] = []
        offset = 0
        while True:
            data = await get_json(
                f"{API}/author/{external_id}/papers",
                params={"fields": PAPER_FIELDS, "limit": 500, "offset": offset},
                headers=_headers(),
            )
            batch = data.get("data") or []
            pubs.extend(paper_to_pub(p, external_id) for p in batch if p.get("paperId"))
            nxt = data.get("next")
            if not batch or nxt is None:
                break
            offset = nxt
        return FetchResult(publications=pubs)
