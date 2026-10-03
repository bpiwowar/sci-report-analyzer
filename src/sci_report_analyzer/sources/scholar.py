"""Google Scholar profiles.

Scholar has no API: public profile pages are fetched politely (paged, with delays) and
parsed. When Scholar blocks automated access,
a saved profile page (after "Show more") can be uploaded and parsed with ``parse_profile``.
"""

from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from ..authors import author_position
from .base import (
    AuthorCandidate,
    FetchedPub,
    FetchResult,
    SourceAdapter,
    SourceError,
    contact_email,
    to_year,
)

BASE = "https://scholar.google.com"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
PAGE = 100
MAX_PAGES = 20
_USER_RE = re.compile(r"[?&]user=([\w-]{12})")


def parse_profile(html: str, owner_names: list[str] | None = None) -> FetchResult:
    """Parse a Scholar profile page (live or saved)."""
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("#gsc_prf_in")
    owner = name_el.get_text(strip=True) if name_el else None
    names = [*(owner_names or []), *([owner] if owner else [])]
    pubs: list[FetchedPub] = []
    for row in soup.select("tr.gsc_a_tr"):
        link = row.select_one("a.gsc_a_at")
        if link is None:
            continue
        href = link.get("href") or link.get("data-href") or ""
        qs = parse_qs(urlparse(str(href)).query)
        key = (qs.get("citation_for_view") or [None])[0] or link.get_text(strip=True)
        grays = row.select(".gsc_a_t .gs_gray")
        authors_txt = grays[0].get_text(" ", strip=True) if grays else ""
        truncated = "…" in authors_txt or "..." in authors_txt
        authors = [] if truncated else [a.strip() for a in authors_txt.split(",") if a.strip()]
        venue = None
        if len(grays) > 1:
            for oph in grays[-1].select(".gs_oph"):
                oph.decompose()
            # Scholar truncates long venues with "…"; the matcher copes with the prefix.
            venue = re.sub(r"[\s\xa0]*…$", "", grays[-1].get_text(" ", strip=True)) or None
        year_el = row.select_one(".gsc_a_y")
        url = f"{BASE}{href}" if str(href).startswith("/") else str(href) or None
        pubs.append(
            FetchedPub(
                external_key=str(key),
                title=link.get_text(" ", strip=True),
                year=to_year(year_el.get_text(strip=True) if year_el else None),
                venue=venue,
                authors=authors,
                author_pos=author_position(authors, names) if authors else None,
                num_authors=len(authors) or None,
                url=url,
                archival=bool(venue and re.match(r"(?i)arxiv preprint|corr\b", venue)),
                raw={"authors_text": authors_txt},
            )
        )
    return FetchResult(publications=pubs, display_name=owner)


def profile_id_from_html(html: str) -> str | None:
    m = _USER_RE.search(html)
    return m.group(1) if m else None


class ScholarAdapter(SourceAdapter):
    name = "scholar"
    label = "Google Scholar"

    def profile_url(self, external_id: str) -> str:
        return f"{BASE}/citations?user={external_id}"

    def parse_url(self, url: str) -> str | None:
        url = url.strip()
        if m := _USER_RE.search(url):
            return m.group(1)
        return url if re.fullmatch(r"[\w-]{12}", url) else None

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict) -> str:
        contact_email()  # required for any request, even here
        res = await client.get(url, params=params)
        blocked = (
            res.status_code in (302, 403, 429)
            or "gs_captcha" in res.text
            or ("/sorry/" in str(res.url))
        )
        if blocked:
            raise SourceError(
                "Google Scholar blocked automated access — save the profile page (after "
                "clicking “Show more” until the end) and upload it instead."
            )
        res.raise_for_status()
        return res.text

    async def search(self, name: str, affiliation: str | None = None) -> list[AuthorCandidate]:
        async with httpx.AsyncClient(
            headers={"User-Agent": BROWSER_UA}, timeout=30, follow_redirects=False
        ) as client:
            try:
                html = await self._get(
                    client,
                    f"{BASE}/citations",
                    {"view_op": "search_authors", "mauthors": name, "hl": "en"},
                )
            except (SourceError, httpx.HTTPError):
                # Author search needs a signed-in browser nowadays: add the profile by URL.
                return []
        soup = BeautifulSoup(html, "lxml")
        out = []
        for card in soup.select(".gsc_1usr"):
            link = card.select_one(".gs_ai_name a")
            if not link:
                continue
            sid = self.parse_url(str(link.get("href")))
            if not sid:
                continue
            aff = card.select_one(".gs_ai_aff")
            out.append(
                AuthorCandidate(
                    source=self.name,
                    external_id=sid,
                    display_name=link.get_text(strip=True),
                    url=self.profile_url(sid),
                    affiliation=aff.get_text(strip=True) if aff else None,
                )
            )
        return out

    async def fetch(self, external_id: str, owner_names: list[str]) -> FetchResult:
        result = FetchResult()
        async with httpx.AsyncClient(
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "en"},
            timeout=30,
            follow_redirects=False,
        ) as client:
            for page in range(MAX_PAGES):
                html = await self._get(
                    client,
                    f"{BASE}/citations",
                    {"user": external_id, "hl": "en", "cstart": page * PAGE, "pagesize": PAGE},
                )
                part = parse_profile(html, owner_names)
                result.display_name = result.display_name or part.display_name
                result.publications.extend(part.publications)
                if len(part.publications) < PAGE:
                    break
                await asyncio.sleep(random.uniform(2.0, 5.0))
        if not result.publications and result.display_name is None:
            raise SourceError(f"Scholar profile {external_id} not found")
        return result
