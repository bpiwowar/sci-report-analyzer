"""A pasted list of references read by the local LLM (see reflist; optional: Tag from a list's
“Read with the LLM”): a list copied from a PDF is often messy (lines broken, words cut, items
run together, page numbers and marks mixed in), split into wrong items; the LLM reads it into
references (their number, title, authors, venue, year, DOI and link: a structured output),
matched by their title (and DOI, HAL id) instead.

A long list is read in parts (see parts: up to PIECE characters, cut before an item), one
after the other, in its order. A part without a valid answer is split as before (see
reflist.split_items), as are those not read when stopped. A DOI or link not in the text sent
is left out (never matched by an id the LLM made up)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import llm, reflist
from .manual import parse_reference
from .reflist import Item
from .sources.base import normalize_doi

logger = logging.getLogger(__name__)

PIECE = 3000  # (characters of a part, at most, unless a single item is longer)
TIMEOUT = 300.0  # seconds (a part)


class Reference(BaseModel):
    """A reference of the list, as the LLM reads it."""

    number: int | None = Field(description="Its number in the list, if numbered")
    title: str = Field(description="Its title, whole")
    authors: list[str] = Field(description="Its authors, as written")
    venue: str = Field(description="Where it was published; empty if not stated")
    year: int | None = Field(description="Its year of publication, if stated")
    doi: str = Field(description="Its DOI, if written; else empty")
    url: str = Field(description="Its link or identifier (e.g. a HAL id), if written")


class Answer(BaseModel):
    """The LLM's answer (its schema: SCHEMA)."""

    references: list[Reference]


SCHEMA = Answer.model_json_schema()

PROMPT = """You read a list of references (publications) copied from a document, e.g. a \
PDF. It may be messy: lines broken, words cut by a hyphen at the end of a line, several \
references on one line, page numbers, headings or link marks mixed in.

The list:
<list>{text}</list>

Answer in JSON, "references": each reference of the list, once, in the list's order:
- "number": its number in the list (3 for "3-", "3.", "[3]", "(3)"), null if not numbered.
- "title": its title, whole, as written (the words cut at the end of a line joined again), \
without its authors, venue or year.
- "authors": its authors, as written.
- "venue": where it was published (journal, conference, workshop, book, publisher, \
university for a thesis…), with its pages or volume if stated; empty if not stated.
- "year": its year of publication, null if not stated.
- "doi": its DOI (e.g. 10.1234/abcd), only if written in the list; else empty.
- "url": its link (e.g. https://hal.science/hal-01234567) or its HAL id, only if written in \
the list; else empty.
Copy, never guess: nothing that is not in the list. Leave out what is not a reference (a \
heading, a page number, a sentence about the list)."""


def prompt() -> str:
    """The instructions sent (those saved, else PROMPT); ``{text}``: a part of the list."""
    return llm.prompt("reflist", PROMPT)


def save_prompt(text: str) -> None:
    """(blank or the default one: following the default)"""
    llm.save_prompt("reflist", PROMPT, text)


def model() -> str:
    """The model reading the lists (blank: none, the LLM being off or no model chosen)."""
    return llm.model(task="reflist")


def messages(text: str) -> list[dict[str, str]]:
    try:
        content = prompt().format(text=text.strip())
    except (KeyError, IndexError, ValueError):  # (instructions edited: braces of their own)
        logger.warning("The instructions reading a list are not a valid template: the default")
        content = PROMPT.format(text=text.strip())
    return [{"role": "user", "content": content}]


def parts(text: str, size: int | None = None) -> list[str]:
    """The list in parts read apart, in its order: up to ``size`` characters each (none:
    PIECE), cut before an item (a numbered line, or one after a blank line) when there is
    one, else between two lines."""
    size = size or PIECE
    out: list[list[str]] = []
    lines: list[str] = []
    length = 0
    for line in (text or "").replace("\r", "").split("\n"):
        if lines and length + len(line) > size:
            starts = [
                i
                for i in range(1, len(lines))
                if reflist.numbered(lines[i]) or (lines[i].strip() and not lines[i - 1].strip())
            ]
            cut = starts[-1] if starts else len(lines)
            out.append(lines[:cut])
            lines = lines[cut:]
            length = sum(len(x) + 1 for x in lines)
        lines.append(line)
        length += len(line) + 1
    out.append(lines)
    return [t for x in out if (t := "\n".join(x).strip())]


def _text(value: str | None) -> str:
    return " ".join((value or "").split()).strip(" .,;")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def item_of(ref: Reference, sent: str) -> Item | None:
    """An item of the list from a reference read (none: no title); its DOI and link kept
    only if in the text ``sent``. Its text: its parts in a line (shown, and edited)."""
    title = _text(ref.title)
    if not title:
        return None
    authors = [a for a in map(_text, ref.authors) if a]
    venue = _text(ref.venue) or None
    year = ref.year if ref.year is not None and 1900 <= ref.year <= 2099 else None
    doi, url = normalize_doi(_text(ref.doi)), _text(ref.url)
    doi = doi if doi and _compact(doi) in _compact(sent) else None
    url = url if url and _compact(url) in _compact(sent) else ""
    where = ", ".join(str(x) for x in (venue, year) if x)
    text = ". ".join(x for x in (title, ", ".join(authors), where) if x) + "."
    links = " ".join(x for x in (f"doi: {doi}" if doi else "", url) if x)
    item = reflist.item_of(ref.number, f"{text} {links}".strip())
    item.title, item.authors, item.venue = title, authors, venue
    if year:
        item.years = {year}
    return item


def items_of(answer: dict, sent: str) -> list[Item] | None:
    """The items in the LLM's answer for a part ``sent`` (none if not valid)."""
    try:
        a = Answer.model_validate(answer)
    except ValidationError as e:
        logger.info("Invalid list read: %s", e)
        return None
    return [i for r in a.references if (i := item_of(r, sent))]


def structured(
    title: str | None,
    authors: str | None = "",
    venue: str | None = "",
    year: int | None = None,
    ref: str | None = "",
    number: int | None = None,
) -> Item | None:
    """An item from its parts edited by hand (``authors``: separated by commas; ``ref``: its
    DOI, HAL id or link, as typed; none: no title)."""
    ref = (ref or "").strip()
    parsed = parse_reference(ref) if ref else None
    doi = parsed.id if parsed and parsed.kind == "doi" else ""
    r = Reference(
        number=number,
        title=title or "",
        authors=[a for a in (authors or "").split(",") if a.strip()],
        venue=venue or "",
        year=year,
        doi=doi,
        url="" if doi else ref,
    )
    return item_of(r, ref)


@dataclass
class Reading:
    """A list read (see read), part after part: the items of the parts read, in order; the
    parts without a valid answer (split as before) and their error."""

    parts: list[str]
    items: list[Item] = field(default_factory=list)
    done: int = 0  # (parts read)
    failed: list[int] = field(default_factory=list)
    error: str = ""

    def rest(self) -> list[Item]:
        """The items of the parts not read (e.g. stopped), split as before."""
        return [i for p in self.parts[self.done :] for i in reflist.split_items(p)]


async def read(
    text: str,
    out: Reading | None = None,
    progress: Callable[[int, int], None] | None = None,
    phase: Callable[[str, int], None] | None = None,
    timeout: float = TIMEOUT,
) -> Reading:
    """The list ``text`` read by the LLM, part after part (``out``: its parts, see parts,
    filled as they are read: those read so far if stopped); ``progress``: called with the
    parts read and their number, ``phase`` with the phase in a part (see llm.chat_json). A
    part without a valid answer: split as before; the server unreachable (or no model):
    raised."""
    reading = out if out is not None else Reading(parts(text))
    total = len(reading.parts)
    for i, part in enumerate(reading.parts):
        if progress:
            progress(i, total)
        try:
            answer = await llm.chat_json(
                messages(part), SCHEMA, task="reflist", progress=phase, timeout=timeout
            )
            found = items_of(answer, part) if isinstance(answer, dict) else None
            if found is None:
                raise ValueError("not a list of references")
        except (httpx.ConnectError, llm.NotLocal):
            raise
        except RuntimeError as e:
            if str(e).startswith("No LLM"):
                raise
            found = _failed(reading, i, part, e)
        except Exception as e:  # (no valid answer: the part split as before)
            found = _failed(reading, i, part, e)
        reading.items.extend(found)
        reading.done = i + 1
    if progress:
        progress(total, total)
    return reading


def _failed(reading: Reading, i: int, part: str, e: Exception) -> list[Item]:
    logger.info("Part %d of the list not read: %s", i + 1, e)
    reading.failed.append(i)
    reading.error = str(e) or type(e).__name__
    return reflist.split_items(part)
