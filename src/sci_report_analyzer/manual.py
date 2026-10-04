"""Publications added by hand to a person: by DOI, or by HAL document.

A DOI is kept on the person's DOI source (its record is fetched like the others' DOIs); a
HAL document gets its own HAL link (``doc:<id>``), synced with the person's other sources.
Both then merge with the records of the other sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from .db.models import Person, SourceLink
from .db.session import session_scope
from .i18n import _
from .source_settings import enabled
from .sources import doi as doi_source
from .sources.base import normalize_doi
from .sources.hal import document_id, is_document
from .sync import add_link, added_dois, doi_link, merge, sync_dois, sync_link

_DOI = re.compile(r"10\.\d{4,9}/\S+")


@dataclass
class Reference:
    kind: str  # doi | hal
    id: str


@dataclass
class AddedItem:
    kind: str  # doi | hal
    id: str
    title: str | None
    url: str
    link_id: int | None = None  # the HAL document's link
    state: str | None = None  # its update status


def parse_reference(text: str) -> Reference | None:
    """A DOI (bare, ``doi:``, a doi.org URL or a publisher's URL with the DOI in its path:
    dl.acm.org/doi/10.1145/…, link.springer.com/chapter/10.1007/…) or a HAL document (id or
    URL on any HAL portal)."""
    text = (text or "").strip()
    if hal := document_id(text):
        return Reference("hal", hal)
    doi = normalize_doi(unquote(text))
    if doi and _DOI.fullmatch(doi):
        return Reference("doi", doi)
    if re.match(r"https?://", text, re.I):
        path = urlsplit(unquote(text)).path
        if m := _DOI.search(path):
            return Reference("doi", normalize_doi(m.group(0).rstrip("/")))
    return None


async def add_publication(person_id: int, text: str) -> str:
    """Add a publication by DOI or HAL id; returns its title. ValueError when it cannot be."""
    ref = parse_reference(text)
    if ref is None:
        raise ValueError(_("Not a DOI nor a HAL document (or their URL)"))
    if not enabled(ref.kind):
        raise ValueError(
            _("The {source} source is disabled (Settings → Sources)").format(
                source=ref.kind.upper()
            )
        )
    if ref.kind == "doi":
        return await _add_doi(person_id, ref.id)
    return await _add_hal(person_id, ref.id)


async def _add_doi(person_id: int, doi: str) -> str:
    await doi_source.ensure([doi])
    record = doi_source.cached([doi]).get(doi)
    if record is None:
        raise ValueError(_("DOI {doi} could not be fetched (try again later)").format(doi=doi))
    if record.status != "ok":
        raise ValueError(_("DOI {doi} is not registered").format(doi=doi))
    with session_scope() as s:
        link = doi_link(s, s.get(Person, person_id))
        if doi not in (added := added_dois(link)):
            link.evidence = {**(link.evidence or {}), "added": [*added, doi]}
    await sync_dois(person_id)
    return (record.data or {}).get("title") or doi


async def _add_hal(person_id: int, hal_id: str) -> str:
    link_id = add_link(person_id, "hal", f"doc:{hal_id}")
    await sync_link(link_id)
    with session_scope() as s:
        link = s.get(SourceLink, link_id)
        error = link.last_error if link.sync_state == "error" else None
        found = bool(link.source_pubs)
        title = link.source_pubs[0].title if found else None
        if not found:
            s.delete(link)  # (a failed sync can be retried by adding it again)
    if not found:
        raise ValueError(
            f"HAL: {error}" if error else _("HAL document {id} not found").format(id=hal_id)
        )
    await sync_dois(person_id)  # its DOI's record
    return title or hal_id


def added(person_id: int) -> list[AddedItem]:
    """The publications added by hand, HAL documents first."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        items = [
            AddedItem(
                "hal",
                ln.external_id.removeprefix("doc:"),
                next((sp.title for sp in ln.source_pubs if sp.title), None),
                ln.url or f"https://hal.science/{ln.external_id.removeprefix('doc:')}",
                link_id=ln.id,
                state=ln.status_label,
            )
            for ln in person.links
            if is_hal_document(ln)
        ]
        dois = [d for ln in person.links if ln.source == "doi" for d in added_dois(ln)]
    records = doi_source.cached(dois)
    for d in dois:
        r = records.get(d)
        title = (r.data or {}).get("title") if r and r.status == "ok" else None
        items.append(AddedItem("doi", d, title, f"https://doi.org/{d}"))
    return items


def is_hal_document(link: SourceLink) -> bool:
    return link.source == "hal" and is_document(link.external_id)


async def remove(person_id: int, item: AddedItem) -> None:
    """Remove a publication added by hand (it stays if another source has it)."""
    if item.kind == "hal":
        with session_scope() as s:
            link = s.scalar(
                select(SourceLink).where(
                    SourceLink.id == item.link_id, SourceLink.person_id == person_id
                )
            )
            if link is not None:
                s.delete(link)
        merge(person_id)
        return
    with session_scope() as s:
        link = next(ln for ln in s.get(Person, person_id).links if ln.source == "doi")
        link.evidence = {
            **(link.evidence or {}),
            "added": [d for d in added_dois(link) if d != item.id],
        }
    await sync_dois(person_id)
