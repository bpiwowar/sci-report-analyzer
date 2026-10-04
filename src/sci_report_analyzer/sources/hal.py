"""HAL (hal.science) via its Solr search API and author referential."""

from __future__ import annotations

import re
from typing import Any

from ..authors import author_position, name_key, name_similarity
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    first,
    get_json,
    normalize_doi,
    to_year,
)

API = "https://api.archives-ouvertes.fr"
PAGE = 1000
CAP = 5000
FL = (
    "halId_s,docType_s,journalTitle_s,conferenceTitle_s,bookTitle_s,producedDateY_i,"
    "issn_s,eissn_s,title_s,uri_s,authFullName_s,authIdHal_s,doiId_s,fileMain_s,arxivId_s,"
    "publisherLink_s"
)
# Preprints / working papers: archival deposits rather than published versions.
ARCHIVAL_TYPES = {"UNDEFINED", "PREPRINT", "WORKINGPAPER"}


def venue_type(doc_type: str | None) -> str | None:
    if doc_type in ("COMM", "POSTER", "PROCEEDINGS"):
        return "conference"
    if doc_type == "ART":
        return "journal"
    return None


# A document id ("hal-01234567", "tel-04012345", "inria-00123456"), without its version.
_DOC_ID = r"[a-z][a-z0-9]*-\d{6,}"


def document_id(text: str) -> str | None:
    """The HAL document id of a document's URL (or a bare id); None if it is not one."""
    text = text.strip()
    if m := re.fullmatch(rf"({_DOC_ID})(?:v\d+)?", text, re.I):
        return m.group(1).lower()
    m = re.match(rf"(?:https?://)?([\w.-]+)/({_DOC_ID})(?:v\d+)?(?:[/?#]|$)", text, re.I)
    # Any HAL portal: hal.science and its collections (inria.hal.science...), the older
    # archives-ouvertes.fr ones, and the institutions' (hal.inria.fr, hal.univ-lorraine.fr...).
    if m and re.search(r"(?:^|[.-])hal(?:[.-]|$)|archives-ouvertes\.fr$", m.group(1), re.I):
        return m.group(2).lower()
    return None


def is_document(external_id: str) -> bool:
    """A link to one document added by hand (``doc:<id>``), rather than to an author."""
    return external_id.startswith("doc:")


def _query(external_id: str) -> str:
    kind, _, value = external_id.partition(":")
    if kind == "doc":
        return f'halId_s:"{value}"'
    if kind == "idhal":
        return f'authIdHal_s:"{value}"'
    if kind == "person":
        return f"authIdPerson_i:{value}"
    return f'authFullName_s:"{value}"'


def _doi(d: dict[str, Any]) -> str | None:
    """The record's DOI, unless it is the arXiv DOI of another paper than the record's arXiv id.

    HAL sometimes attaches a wrong arXiv DOI (hal-03311766 carries the one of a later paper by
    the same authors), which would merge unrelated works: the record's own arXiv id wins.
    """
    doi = normalize_doi(d.get("doiId_s"))
    arxiv = d.get("arxivId_s")
    if doi and arxiv and doi.startswith("10.48550/arxiv."):
        own = "10.48550/arxiv." + re.sub(r"v\d+$", "", arxiv.strip()).lower()
        if doi != own:
            return own
    return doi


def meta_from_doc(d: dict[str, Any], owner_names: list[str]) -> FetchedPub:
    doc_type = d.get("docType_s")
    vt = venue_type(doc_type)
    fields = {
        "conference": "conferenceTitle_s",
        "journal": "journalTitle_s",
    }
    venue = None
    if vt and d.get(fields[vt]):
        venue = d[fields[vt]]
    venue = venue or d.get("conferenceTitle_s") or d.get("journalTitle_s") or d.get("bookTitle_s")
    authors = d.get("authFullName_s") or []
    arxiv = d.get("arxivId_s")
    pdf = d.get("fileMain_s") or (f"https://arxiv.org/pdf/{arxiv}" if arxiv else None)
    return FetchedPub(
        external_key=d["halId_s"],
        title=first(d.get("title_s")),
        year=to_year(d.get("producedDateY_i")),
        venue=venue,
        authors=authors,
        author_pos=author_position(authors, owner_names),
        num_authors=len(authors) or None,
        issn=first(d.get("issn_s")) or first(d.get("eissn_s")),
        venue_type=vt,
        doi=_doi(d),
        url=d.get("uri_s") or f"https://hal.science/{d['halId_s']}",
        doc_type=doc_type,
        pdf_url=pdf,
        archival=doc_type in ARCHIVAL_TYPES,
        raw={"arxiv": arxiv, "publisher_url": first(d.get("publisherLink_s"))},
    )


class HalAdapter(SourceAdapter):
    name = "hal"
    label = "HAL"

    def profile_url(self, external_id: str) -> str:
        kind, _, value = external_id.partition(":")
        if kind == "doc":
            return f"https://hal.science/{value}"
        if kind == "idhal":
            return f"https://cv.hal.science/{value}"
        return f"https://hal.science/search/index/?q={_query(external_id)}"

    def parse_url(self, url: str) -> str | None:
        url = url.strip()
        if m := re.search(r"cv\.(?:archives-ouvertes\.fr|hal\.science)/([a-z0-9-]+)", url):
            return f"idhal:{m.group(1)}"
        if doc := document_id(url):
            return f"doc:{doc}"
        if m := re.search(r"authIdHal_s(?:[:=/]|%3A)(?:%22|\")?([a-z0-9-]+)", url):
            return f"idhal:{m.group(1)}"
        if m := re.search(r"authIdPerson_i[:=/](\d+)", url):
            return f"person:{m.group(1)}"
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", url):
            return f"idhal:{url}"
        return None

    async def _count(self, external_id: str) -> tuple[int, list[str]]:
        data = await get_json(
            f"{API}/search/",
            params={
                "q": _query(external_id),
                "fl": "title_s",
                "rows": 3,
                "wt": "json",
                "sort": "producedDate_tdate desc",
            },
        )
        resp = data.get("response", {})
        return resp.get("numFound", 0), [first(d.get("title_s")) for d in resp.get("docs", [])]

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        surname, _ = name_key(name)
        if not surname:
            return []
        data = await get_json(
            f"{API}/ref/author/",
            params={
                "q": f'fullName_t:"{name}" OR lastName_t:"{surname}"',
                "fl": "docid,idHal_s,idHal_i,fullName_s,valid_s,orcidId_s",
                "rows": 100,
                "wt": "json",
            },
        )
        docs = data.get("response", {}).get("docs", [])
        seen: dict[str, AuthorCandidate] = {}
        for d in docs:
            full = d.get("fullName_s") or ""
            if name_similarity(name, full) < 0.5:
                continue
            ext = f"idhal:{d['idHal_s']}" if d.get("idHal_s") else f"name:{full}"
            if ext in seen:
                continue
            orcid = first(d.get("orcidId_s"))
            seen[ext] = AuthorCandidate(
                source=self.name,
                external_id=ext,
                display_name=full + ("" if d.get("idHal_s") else " (name form, no idHAL)"),
                url=self.profile_url(ext),
                orcid=orcid.removeprefix("https://orcid.org/") if orcid else None,
                extra={"idhal": d.get("idHal_s")},
            )
        # Documents by this name: which HAL authors (idHAL / person id) signed them. Finds
        # profiles the author referential misses (as looking up a paper's author would).
        for c in await self._from_documents(name):
            key = c.external_id
            if key.startswith("idhal:") and key in seen:
                continue
            seen.setdefault(key, c)
        # Profiles with an idHAL first, then person ids, then bare name forms.
        order = {"idhal": 0, "person": 1, "name": 2}
        ranked = sorted(seen.values(), key=lambda c: order[c.external_id.partition(":")[0]])
        out = ranked[:8]
        for c in out:
            c.works_count, c.sample_titles = await self._count(c.external_id)
        return [c for c in out if c.works_count]

    async def _from_documents(self, name: str) -> list[AuthorCandidate]:
        data = await get_json(
            f"{API}/search/",
            params={
                "q": f'authFullName_t:"{name}"',
                "rows": 0,
                "wt": "json",
                "facet": "true",
                "facet.field": "authFullNamePersonIDIDHal_fs",
                "facet.limit": 200,
                "facet.mincount": 1,
            },
        )
        facets = data.get("facet_counts", {}).get("facet_fields", {})
        values = facets.get("authFullNamePersonIDIDHal_fs") or []
        out: dict[str, AuthorCandidate] = {}
        for value, count in zip(values[::2], values[1::2], strict=False):
            full, _, rest = str(value).partition("_FacetSep_")
            person, _, idhal = rest.partition("_FacetSep_")
            has_person = person.isdigit() and person != "0"  # 0: not identified
            if name_similarity(name, full) < 0.75 or not (idhal or has_person):
                continue
            ext = f"idhal:{idhal}" if idhal else f"person:{person}"
            if ext in out:
                out[ext].works_count = (out[ext].works_count or 0) + count
                continue
            out[ext] = AuthorCandidate(
                source=self.name,
                external_id=ext,
                display_name=full + ("" if idhal else f" (HAL person {person}, no idHAL)"),
                url=self.profile_url(ext),
                works_count=count,
                extra={"idhal": idhal or None, "person": person, "via": "documents"},
            )
        return list(out.values())

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        q = _query(external_id)
        docs: list[dict[str, Any]] = []
        start = 0
        while start < CAP:
            data = await get_json(
                f"{API}/search/",
                params={
                    "q": q,
                    "fl": FL,
                    "rows": PAGE,
                    "start": start,
                    "wt": "json",
                    "sort": "producedDate_tdate desc",
                },
            )
            resp = data.get("response", {})
            page = resp.get("docs", [])
            docs.extend(page)
            start += len(page)
            if not page or start >= resp.get("numFound", 0):
                break
        return FetchResult(publications=[meta_from_doc(d, owner_names) for d in docs])


async def search_documents(words: list[str], rows: int = 10) -> list[dict[str, Any]]:
    """HAL documents whose title has the most of ``words`` (best first): their id, title,
    year, authors and URL."""
    words = [w for w in words if re.fullmatch(r"\w+", w) and w.lower() not in ("and", "or", "not")]
    if not words:
        return []
    data = await get_json(
        f"{API}/search/",
        params={
            "q": "title_t:(" + " OR ".join(words) + ")",
            "fl": "halId_s,title_s,producedDateY_i,authFullName_s,uri_s",
            "rows": rows,
            "wt": "json",
        },
    )
    return [
        {
            "hal": d["halId_s"],
            "title": first(d.get("title_s")),
            "year": to_year(d.get("producedDateY_i")),
            "authors": d.get("authFullName_s") or [],
            "url": d.get("uri_s") or f"https://hal.science/{d['halId_s']}",
        }
        for d in data.get("response", {}).get("docs", [])
        if d.get("halId_s")
    ]
