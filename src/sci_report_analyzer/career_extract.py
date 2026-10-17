"""A person's career read by the LLM in their documents and profiles (see career.py): lines of
a CV suggested, to be checked before being added.

The documents are long (an application: tens of pages) and a local LLM slow with a long
text, so only their pages likely about the career are sent (see career_words for the words,
editable by language), chosen by the user (see pages): those headed as a CV (“Curriculum
vitae”, “Parcours”… a short line among their first ones), whole; the others by the number of
their lines with a year and a position or a diploma (those lines sent, with the lines around
them); ticked first, the most likely, within BUDGET characters. They are read in parts (see
batches): the pages following each other in a document together, up to PIECE characters;
each part read apart (a structured output, as the data model: a position, or a diploma and
its thesis; each with the words it comes from). The instructions (see prompt) can be
changed, and saved. A line whose words are not in the text is marked unchecked; those
already in the CV are left out."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from . import career, career_words, documents, llm
from .db.app_settings import delete_setting, get_setting, set_setting
from .db.models import Period, Person, SourceLink
from .db.session import session_scope
from .source_settings import active_links

BUDGET = 6000  # (characters of the pages ticked first)
PIECE = 6000  # (characters of a part, at most)
AROUND = 2  # (lines kept around a line about the career)
HEADING_LENGTH = 60  # (a heading: a short line…)
HEADING_LINES = 8  # (… among the first lines of its page)
_PROMPT_SETTING = "career_prompt"

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

_DATE = {"type": "string", "description": "YYYY, YYYY-MM or YYYY-MM-DD; empty if not stated"}
_QUOTE = {"type": "string", "description": "the words of the text it comes from, copied"}
# The answer: lines as the data model (career.Entry), a position or a diploma.
SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["position"]},
                            "title": {"type": "string"},
                            "institution": {"type": "string"},
                            "start": _DATE,
                            "end": _DATE,
                            "details": {"type": "string"},
                            "quote": _QUOTE,
                        },
                        "required": ["kind", "title", "institution", "start", "end", "quote"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["diploma"]},
                            "title": {"type": "string"},
                            "institution": {"type": "string"},
                            "started": _DATE,
                            "obtained": _DATE,
                            "thesis": {"type": "string"},
                            "quote": _QUOTE,
                        },
                        "required": ["kind", "title", "institution", "obtained", "quote"],
                    },
                ]
            },
        }
    },
    "required": ["entries"],
}

PROMPT = """You read excerpts of documents about a researcher, {name}, to write their CV: \
the positions they held and the diplomas they obtained.
- Only {name}'s own positions and diplomas (not those of students, colleagues, supervisors).
- Only what the text states: never guess a date or an institution (leave it empty).
- Dates as YYYY, YYYY-MM or YYYY-MM-DD.
- A position (kind "position"): title (e.g. "Associate professor"), institution (e.g. \
"Sorbonne University, ISIR"), start and end (empty end: held now), details (team, subject).
- A diploma (kind "diploma"): title (e.g. "PhD in computer science", "HDR"), institution, \
obtained (when), started (a PhD's start, if stated), thesis (its title, for a PhD or an HDR).
- Titles in the language of the text.
- quote: the words of the text it comes from, copied exactly (a short passage).
Answer with an empty list if the excerpts tell nothing of the kind."""


def prompt() -> str:
    """The instructions sent (those saved, else PROMPT); ``{name}``: the person's name."""
    try:
        return get_setting(_PROMPT_SETTING) or PROMPT
    except Exception:  # (no database: e.g. in unit tests)
        return PROMPT


def save_prompt(text: str) -> None:
    """(blank or the default one: following the default)"""
    if not text.strip() or text.strip() == PROMPT.strip():
        delete_setting(_PROMPT_SETTING)
    else:
        set_setting(_PROMPT_SETTING, text)


@dataclass
class Passage:
    source: str  # “Application, p. 3”
    text: str
    doc_id: int | None = None  # (where: a document's page; none: the profiles)
    page: int | None = None


@dataclass
class Suggestion:
    entry: career.Entry
    quote: str  # (the words it comes from)
    source: str  # (where: a document and its page, a profile)
    checked: bool  # (whether the quote is in the text sent)
    # Where: the page with the quote (else those of the part it was read in).
    pages: list[Passage] = field(default_factory=list)


_fold = career_words.fold


def _pages(lines: list[dict]) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for line in lines:
        if t := (line.get("t") or "").strip():
            pages.setdefault(int(line.get("p") or 1), []).append(t)
    return pages


@dataclass
class Page:
    """A page of a document, and what of it would be sent: the whole page, or (found by its
    lines about a career) those lines and the lines around them."""

    doc_id: int | None
    name: str  # (its document's)
    page: int
    text: str
    reason: str = ""  # heading (headed as a CV), lines (lines about a career), or none
    score: int = 0  # (its lines with a year (and, not a heading, a position or diploma))

    @property
    def key(self) -> tuple[int | None, int]:
        return self.doc_id, self.page

    @property
    def source(self) -> str:
        return f"{self.name}, p. {self.page}"

    def passage(self) -> Passage:
        return Passage(self.source, self.text, self.doc_id, self.page)


def document_pages(name: str, lines: list[dict], doc_id: int | None = None) -> list[Page]:
    """A document's pages, in order, those likely about the career with their reason (see
    the module)."""
    heading, about = (
        career_words.pattern("headings"),
        [p for g in ("positions", "diplomas") if (p := career_words.pattern(g))],
    )
    out = []
    for page, texts in sorted(_pages(lines).items()):
        folded = [career_words.normalize(t) for t in texts]
        whole = "\n".join(texts)
        if heading and any(
            heading.match(t) and len(t) <= HEADING_LENGTH for t in folded[:HEADING_LINES]
        ):
            hits = sum(1 for t in folded if _YEAR.search(t))
            out.append(Page(doc_id, name, page, whole, "heading", hits))
            continue
        hits = [
            i for i, t in enumerate(folded) if _YEAR.search(t) and any(p.search(t) for p in about)
        ]
        keep = {j for i in hits for j in range(max(0, i - AROUND), min(len(texts), i + AROUND + 1))}
        if keep:
            text = "\n".join(texts[i] for i in sorted(keep))
            out.append(Page(doc_id, name, page, text, "lines", len(hits)))
        else:
            out.append(Page(doc_id, name, page, whole))
    return out


def ranked(found: list[Page]) -> list[Page]:
    """The pages likely about the career: those headed as a CV first (in order), then the
    others by score."""
    return sorted(
        (p for p in found if p.reason),
        key=lambda p: (p.reason != "heading", -p.score if p.reason != "heading" else 0),
    )


def passages(name: str, lines: list[dict]) -> list[Passage]:
    """A document's passages about the career, the most likely first."""
    return [p.passage() for p in ranked(document_pages(name, lines))]


def ticked(found: list[Page]) -> set[tuple[int | None, int]]:
    """The pages ticked first: the most likely about the career, within BUDGET characters
    (at least one)."""
    out, size = set(), 0
    for p in ranked(found):
        if out and size + len(p.text) > BUDGET:
            continue
        out.add(p.key)
        size += len(p.text)
    return out


@dataclass
class Document:
    id: int
    name: str
    period: str  # (the period it is in: its folder's)
    read: bool  # (its text extracted: opened once in the viewer)


def person_documents(person_id: int) -> list[Document]:
    """The person's documents, those not read yet (never opened in the viewer) marked."""
    with session_scope() as s:
        periods = {
            p.id: p.name for p in s.scalars(select(Period).where(Period.person_id == person_id))
        }
    return [
        Document(d.id, d.name, periods.get(d.period_id, ""), documents.lines_of(d.id) is not None)
        for docs in documents.of_person(person_id).values()
        for d in docs
    ]


def pages(person_id: int, doc_ids: list[int] | None = None) -> list[Page]:
    """The pages of the person's documents (``doc_ids``; none: all of them) read already,
    in order."""
    return [
        p
        for d in person_documents(person_id)
        if d.read and (doc_ids is None or d.id in doc_ids)
        for p in document_pages(d.name, documents.lines_of(d.id) or [], d.id)
    ]


def affiliations(person_id: int) -> list[str]:
    """The affiliations of the person's validated profiles, each once (compared without case
    nor accents) with its sources: “Quokka University (from hal, openalex)”."""
    found: dict[str, tuple[str, list[str]]] = {}
    with session_scope() as s:
        for link in s.scalars(
            select(SourceLink)
            .where(SourceLink.person_id == person_id, active_links())
            .order_by(SourceLink.id)
        ):
            if aff := " ".join(str((link.evidence or {}).get("affiliation") or "").split()):
                _first, where = found.setdefault(_fold(aff), (aff, []))
                if link.source not in where:
                    where.append(link.source)
    return [f"{aff} (from {', '.join(sorted(where))})" for aff, where in found.values()]


def _split(p: Passage) -> list[Passage]:
    """A passage in pieces of PIECE characters at most (cut between lines)."""
    if len(p.text) <= PIECE:
        return [p]
    out, lines, size = [], [], 0
    for line in p.text.splitlines():
        for i in range(0, max(1, len(line)), PIECE):  # (a line itself too long: cut)
            chunk = line[i : i + PIECE]
            if lines and size + len(chunk) + 1 > PIECE:
                out.append(lines)
                lines, size = [], 0
            lines.append(chunk)
            size += len(chunk) + 1
    out.append(lines)
    return [Passage(p.source, "\n".join(x), p.doc_id, p.page) for x in out]


def batches(found: list[Passage]) -> list[list[Passage]]:
    """The passages in parts read apart: the pages following each other in a document
    together, up to PIECE characters (the others apart)."""
    out: list[list[Passage]] = []
    size, prev = 0, None
    for p in (x for found_ in found for x in _split(found_)):
        follows = (
            prev is not None
            and p.doc_id is not None
            and p.doc_id == prev.doc_id
            and p.page is not None
            and prev.page is not None
            and p.page - prev.page in (0, 1)
        )
        if not out or not follows or size + len(p.text) > PIECE:
            out.append([])
            size = 0
        out[-1].append(p)
        size += len(p.text)
        prev = p
    return out


def _date(value: str | None) -> str | None:
    """A date as expected (else its year; none: none found)."""
    value = (value or "").strip()
    if career.valid_date(value):
        return value or None
    m = _YEAR.search(value)
    return m.group(0) if m else None


def _same(a: career.Entry, b: career.Entry) -> bool:
    """Whether two lines of a CV are the same: their kind, title (or institution) and years."""
    if a.kind != b.kind:
        return False
    years = (a.start or "")[:4], (a.end or "")[:4]
    if years != ((b.start or "")[:4], (b.end or "")[:4]):
        return False
    return _fold(a.title) == _fold(b.title) or bool(
        a.institution and b.institution and _fold(a.institution) == _fold(b.institution)
    )


def _text(x: dict, key: str) -> str | None:
    return " ".join(str(x.get(key) or "").split()) or None


def entry_of(x: dict) -> career.Entry | None:
    """A line of the CV from one of the answer (see SCHEMA; none: not one)."""
    title = _text(x, "title")
    if not title or x.get("kind") not in career.KINDS:
        return None
    if x["kind"] == "diploma":
        return career.Entry(
            "diploma",
            title,
            _date(x.get("started")),
            _date(x.get("obtained")),
            _text(x, "institution"),
            _text(x, "thesis"),
            elapsed=True,
        )
    return career.Entry(
        "position",
        title,
        _date(x.get("start")),
        _date(x.get("end")),
        _text(x, "institution"),
        _text(x, "details"),
    )


def _suggestions(answer: dict, part: list[Passage]) -> list[Suggestion]:
    out = []
    for x in answer.get("entries") or []:
        if not (entry := entry_of(x)):
            continue
        quote = " ".join((x.get("quote") or "").split())
        where = next((p for p in part if quote and _fold(quote) in _fold(p.text)), None)
        source = where.source if where else ", ".join(dict.fromkeys(p.source for p in part))
        out.append(Suggestion(entry, quote, source, where is not None, [where] if where else part))
    return out


def quoted(text: str, quote: str) -> tuple[int, int] | None:
    """Where the quote is in a text (its words, whatever the case and spaces; none: not)."""
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE)
    return m.span() if m else None


def requests(
    person_id: int,
    chosen: list[Page] | None = None,
    profiles: bool = True,
    instructions: str | None = None,
) -> list[tuple[list[Passage], list[dict[str, str]]]]:
    """What the LLM is sent (see extract): each part, and its messages (the instructions,
    ``{name}`` the person's; then the passages). ``chosen``: the pages (none: those ticked
    first, see ticked); ``profiles``: their profiles' affiliations too (in the first part).
    (Not the notes: one's own, not facts.)"""
    with session_scope() as s:
        name = s.get(Person, person_id).name
    if chosen is None:
        found = pages(person_id)
        keys = ticked(found)
        chosen = [p for p in found if p.key in keys]
    parts = batches([p.passage() for p in chosen])
    known = affiliations(person_id) if profiles else []
    if known:
        first = Passage("profiles", "Affiliations from their profiles: " + "; ".join(known))
        parts = [[first, *parts[0]], *parts[1:]] if parts else [[first]]
    system = (instructions or prompt()).replace("{name}", name)
    return [
        (
            part,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n\n".join(f"[{p.source}]\n{p.text}" for p in part)},
            ],
        )
        for part in parts
    ]


async def extract(
    person_id: int,
    chosen: list[Page] | None = None,
    folder_id: int | None = None,
    out: list[Suggestion] | None = None,
    profiles: bool = True,
    instructions: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    phase: Callable[[str, int], None] | None = None,
) -> list[Suggestion]:
    """The lines of a CV the LLM reads in the person's profiles and pages (see requests),
    without those already in it (nor twice). Raises if no LLM. ``out``: where they are
    added, part after part (those found so far, if stopped); ``progress``: called with the
    parts read and their number, before each one and at the end; ``phase``: with the model's
    phase in a part (see llm.chat_json)."""
    have = career.entries(person_id)
    out = [] if out is None else out
    split = requests(person_id, chosen, profiles, instructions)
    for i, (part, chat) in enumerate(split):
        if progress:
            progress(i, len(split))
        answer = await llm.chat_json(chat, SCHEMA, folder_id, task="career", progress=phase)
        for sug in _suggestions(answer, part):
            if not any(_same(sug.entry, e) for e in [*have, *(x.entry for x in out)]):
                out.append(sug)
        out.sort(key=lambda x: career._sort_key(x.entry), reverse=True)
    if progress:
        progress(len(split), len(split))
    return out
