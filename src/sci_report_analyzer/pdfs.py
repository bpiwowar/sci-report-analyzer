"""PDFs of papers, stored in the data directory to read and annotate (with PDF.js, in the
browser): downloaded from the papers' open-access links, or uploaded by hand."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import config
from .db.models import PeriodDocument, Publication, PublicationPdf, SourcePub, utcnow
from .db.session import session_scope
from .files import atomic_write
from .sources import PRIORITY
from .sources.base import client, contact_email, normalize_doi
from .text import ascii_fold

logger = logging.getLogger(__name__)

ROOT: Path | None = None  # the PDF directory (tests: a temporary one), else in the data dir
MAX_SIZE = 100 * 1024 * 1024
CONCURRENCY = 4

VIEWER_VERSION = "6.3.289"
VIEWER_URL = (
    f"https://github.com/mozilla/pdf.js/releases/download/v{VIEWER_VERSION}/"
    f"pdfjs-{VIEWER_VERSION}-dist.zip"
)


class PdfError(Exception):
    """A PDF that could not be downloaded or stored (user-facing)."""


def pdf_dir() -> Path:
    return ROOT or config.DATA_DIR / "pdfs"


def viewer_dir() -> Path:
    """Where PDF.js (its generic viewer, with the annotation tools) is installed."""
    return config.DATA_DIR / "pdfjs" / VIEWER_VERSION


def has_viewer() -> bool:
    return (viewer_dir() / "web" / "viewer.html").is_file()


async def install_viewer() -> None:
    """Download PDF.js (once, about 6 MB) into the data directory."""
    if has_viewer():
        return
    try:
        res = await client().get(VIEWER_URL)
        res.raise_for_status()
    except httpx.HTTPError as e:
        raise PdfError(f"could not download the PDF viewer: {e}") from e
    dest = viewer_dir()
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        for name in z.namelist():
            if not name.endswith(".map"):  # (source maps: 2/3 of the size)
                z.extract(name, tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)


# ---- Stored PDFs ----------------------------------------------------------------------------


def slug(title: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_fold(title or "")).strip("-")[:60] or "paper"


def file_of(pub_id: int) -> Path | None:
    """The stored PDF of a paper, if any (and still on disk)."""
    with session_scope() as s:
        row = s.get(PublicationPdf, pub_id)
        path = pdf_dir() / row.path if row else None
    return path if path and path.is_file() else None


def stored(person_id: int) -> dict[int, bool]:
    """The papers of a person with a stored PDF: id -> edited (saved with annotations)."""
    with session_scope() as s:
        rows = s.execute(
            select(PublicationPdf.publication_id, PublicationPdf.edited_at)
            .join(Publication)
            .where(Publication.person_id == person_id)
        )
        return {pid: edited is not None for pid, edited in rows}


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF")


def save(pub_id: int, data: bytes, origin: str | None, *, edited: bool = False) -> Path:
    """Store ``data`` as the PDF of a paper (replacing the previous one)."""
    if not is_pdf(data):
        raise PdfError("not a PDF file")
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        if pub is None:
            raise PdfError("no such paper")
        row = s.get(PublicationPdf, pub_id)
        rel = row.path if row else f"{pub.person_id}/{pub_id}-{slug(pub.title)}.pdf"
        path = pdf_dir() / rel
        atomic_write(path, data)
        if row is None:
            s.add(PublicationPdf(publication_id=pub_id, path=rel, origin=origin))
        elif edited:
            row.edited_at = utcnow()
        else:  # a new download / upload replaces the annotated file
            row.origin, row.added_at, row.edited_at = origin, utcnow(), None
    return path


def remove(pub_id: int) -> None:
    with session_scope() as s:
        row = s.get(PublicationPdf, pub_id)
        if row is None:
            return
        (pdf_dir() / row.path).unlink(missing_ok=True)
        s.delete(row)
    from .documents import forget_place  # (documents imports this module)

    forget_place("pub", pub_id)


def move(session, source_id: int, target_id: int) -> None:
    """Give the PDF of ``source`` to ``target`` (joined papers), unless it has one."""
    row = session.get(PublicationPdf, source_id)
    if row is None:
        return
    if session.get(PublicationPdf, target_id) is None:
        session.add(
            PublicationPdf(
                publication_id=target_id,
                path=row.path,
                origin=row.origin,
                added_at=row.added_at,
                edited_at=row.edited_at,
                bookmarks=row.bookmarks,
            )
        )
    session.delete(row)  # (else: its file is left behind, for the clean-up)


@dataclass
class Usage:
    files: int = 0
    size: int = 0
    orphans: list[Path] = field(default_factory=list)  # files of no paper nor document
    lost: int = 0  # papers / documents whose file is gone


def usage() -> Usage:
    root = pdf_dir()
    with session_scope() as s:
        rows = set(s.scalars(select(PublicationPdf.path)))
        rows |= set(s.scalars(select(PeriodDocument.path)))
    u = Usage()
    on_disk = (
        {p.relative_to(root).as_posix(): p for p in root.rglob("*.pdf")} if root.is_dir() else {}
    )
    for rel, p in on_disk.items():
        if rel in rows:
            u.files += 1
            u.size += p.stat().st_size
        else:
            u.orphans.append(p)
    u.lost = sum(1 for rel in rows if rel not in on_disk)
    return u


def cleanup() -> tuple[int, int]:
    """Delete the files of papers (and documents) no longer in the database, and forget the
    PDFs whose file was deleted by hand. Returns (files deleted, PDFs forgotten)."""
    u = usage()
    for p in u.orphans:
        p.unlink(missing_ok=True)
    root = pdf_dir()
    forgotten = 0
    with session_scope() as s:
        for row in [*s.scalars(select(PublicationPdf)), *s.scalars(select(PeriodDocument))]:
            if not (root / row.path).is_file():
                s.delete(row)
                forgotten += 1
    if root.is_dir():
        for d in sorted(root.rglob("*"), reverse=True):  # emptied person directories
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    return len(u.orphans), forgotten


# ---- Downloads ------------------------------------------------------------------------------


def _url_order(m: SourcePub) -> tuple[int, int]:
    url = m.pdf_url or ""
    # Open archives first (their links are PDFs), then by source priority.
    archive = 0 if re.search(r"arxiv\.org|hal\.science|archives-ouvertes\.fr", url) else 1
    src = m.link.source
    return archive, PRIORITY.index(src) if src in PRIORITY else len(PRIORITY)


def links(pub_ids: Iterable[int]) -> dict[int, tuple[list[str], str | None]]:
    """For each paper: its open-access PDF links (from the sources), and its DOI."""
    with session_scope() as s:
        pubs = s.scalars(
            select(Publication)
            .where(Publication.id.in_(list(pub_ids)))
            .options(selectinload(Publication.members).selectinload(SourcePub.link))
        )
        out = {}
        for pub in pubs:
            members = sorted((m for m in pub.members if m.pdf_url), key=_url_order)
            urls = list(dict.fromkeys(m.pdf_url for m in members))
            out[pub.id] = (urls, normalize_doi(pub.doi_manual or pub.doi))
        return out


async def unpaywall(doi: str) -> list[str]:
    """Open-access PDF links of a DOI, from Unpaywall."""
    try:
        res = await client().get(
            f"https://api.unpaywall.org/v2/{doi}", params={"email": contact_email()}
        )
        if res.status_code != 200:
            return []
        data = res.json()
    except (httpx.HTTPError, ValueError):
        return []
    locations = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
    return list(dict.fromkeys(u for loc in locations if (u := loc.get("url_for_pdf"))))


async def fetch(url: str) -> bytes:
    """The PDF at ``url`` (not an HTML landing page)."""
    try:
        async with client().stream(
            "GET", url, headers={"Accept": "application/pdf,*/*;q=0.8"}
        ) as res:
            if res.status_code != 200:
                raise PdfError(f"HTTP {res.status_code}")
            data = bytearray()
            async for chunk in res.aiter_bytes():
                data += chunk
                if len(data) > MAX_SIZE:
                    raise PdfError("too large")
                if len(data) >= 1024 and not is_pdf(bytes(data)):
                    raise PdfError("not a PDF (a web page?)")
    except httpx.HTTPError as e:
        raise PdfError(f"network error: {e}") from e
    if not is_pdf(bytes(data)):
        raise PdfError("not a PDF (a web page?)")
    return bytes(data)


async def download(pub_id: int, urls: list[str] | None = None, doi: str | None = None) -> str:
    """Download the PDF of a paper from its open-access links (then Unpaywall's, with a
    DOI). Returns the link used; raises PdfError when none gives a PDF."""
    if urls is None:
        urls, doi = links([pub_id]).get(pub_id, ([], None))
    errors = []
    tried: set[str] = set()

    async def attempt(candidates: list[str]) -> str | None:
        for url in candidates:
            if url in tried:
                continue
            tried.add(url)
            try:
                data = await fetch(url)
            except PdfError as e:
                errors.append(f"{url}: {e}")
                continue
            save(pub_id, data, url)
            return url
        return None

    if url := await attempt(urls):
        return url
    if doi and (url := await attempt(await unpaywall(doi))):
        return url
    if not tried:
        raise PdfError("no open-access link")
    raise PdfError("; ".join(errors))


@dataclass
class Batch:
    done: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)  # paper id -> why
    skipped: int = 0  # already stored


async def download_many(
    pub_ids: Iterable[int], on_progress: Callable[[int, int], None] | None = None
) -> Batch:
    """Download the PDFs of the papers that have none yet (a few at a time)."""
    ids = list(dict.fromkeys(pub_ids))
    with session_scope() as s:
        have = set(
            s.scalars(
                select(PublicationPdf.publication_id).where(PublicationPdf.publication_id.in_(ids))
            )
        )
    batch = Batch(skipped=sum(1 for i in ids if i in have))
    todo = [i for i in ids if i not in have]
    found = links(todo)
    sem = asyncio.Semaphore(CONCURRENCY)
    count = 0

    async def one(pub_id: int) -> None:
        nonlocal count
        urls, doi = found.get(pub_id, ([], None))
        async with sem:
            try:
                await download(pub_id, urls, doi)
                batch.done.append(pub_id)
            except PdfError as e:
                batch.failed[pub_id] = str(e)
            except Exception as e:  # (one paper never stops the others)
                logger.exception("PDF of paper %s", pub_id)
                batch.failed[pub_id] = str(e)
        count += 1
        if on_progress:
            on_progress(count, len(todo))

    await asyncio.gather(*(one(i) for i in todo))
    return batch
