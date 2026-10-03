"""ORCID public API (pub.orcid.org)."""

from __future__ import annotations

import re
from typing import Any

from ..authors import name_key
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    get_json,
    is_repository_url,
    normalize_doi,
    to_year,
)

API = "https://pub.orcid.org/v3.0"
_ORCID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")
HEADERS = {"Accept": "application/json"}


def _v(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def summary_to_pub(s: dict[str, Any]) -> FetchedPub:
    ext_ids = _v(s, "external-ids", "external-id") or []
    ids = {e.get("external-id-type"): e.get("external-id-value") for e in ext_ids}
    wtype = s.get("type")
    arxiv = ids.get("arxiv")
    url = _v(s, "url", "value")
    return FetchedPub(
        external_key=str(s.get("put-code")),
        title=_v(s, "title", "title", "value"),
        year=to_year(_v(s, "publication-date", "year", "value")),
        venue=_v(s, "journal-title", "value"),
        venue_type="conference"
        if wtype == "conference-paper"
        else ("journal" if wtype == "journal-article" else None),
        doi=normalize_doi(ids.get("doi")),
        url=url or (f"https://doi.org/{ids['doi']}" if ids.get("doi") else None),
        doc_type=wtype,
        pdf_url=f"https://arxiv.org/pdf/{arxiv}" if arxiv else None,
        archival=wtype == "preprint",
        raw={
            "source": _v(s, "source", "source-name", "value"),
            "external_ids": ids,
            "publisher_url": url if url and not is_repository_url(url) else None,
        },
    )


class OrcidAdapter(SourceAdapter):
    name = "orcid"
    label = "ORCID"

    def profile_url(self, external_id: str) -> str:
        return f"https://orcid.org/{external_id}"

    def parse_url(self, url: str) -> str | None:
        m = _ORCID_RE.search(url)
        return m.group(1) if m else None

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        surname, _ = name_key(name)
        parts = name.split()
        given = " ".join(parts[:-1])
        q = f"family-name:{parts[-1] if parts else surname}"
        if given:
            q += f" AND given-names:{given}"
        data = await get_json(
            f"{API}/expanded-search/", params={"q": q, "rows": 10}, headers=HEADERS
        )
        out = []
        for r in data.get("expanded-result") or []:
            oid = r.get("orcid-id")
            full = " ".join(x for x in (r.get("given-names"), r.get("family-names")) if x)
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=oid,
                    display_name=r.get("credit-name") or full or oid,
                    url=self.profile_url(oid),
                    affiliation=", ".join(r.get("institution-name") or []) or None,
                    orcid=oid,
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        data = await get_json(f"{API}/{external_id}/works", headers=HEADERS)
        pubs = []
        for g in data.get("group") or []:
            summaries = g.get("work-summary") or []
            if summaries:
                # The first summary is the preferred one (display index).
                pubs.append(summary_to_pub(summaries[0]))
        return FetchResult(publications=pubs)
