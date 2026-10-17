"""A person's career read by the LLM in their documents and profiles (see career.py): lines
of a CV suggested, to be checked before being added.

The documents are long (an application: tens of pages) and a local LLM slow with a long
text, so only their pages likely about the career are sent (see career_words for the
words, editable by language): those headed as a CV (“Curriculum vitae”, “Parcours”…) first,
whole; then the others by the number of their lines with a year and a position or a
diploma (those lines sent, with the lines around them), within BUDGET characters; in pieces
of PIECE characters, each read apart (a structured output: the lines of the CV, each with
the words it comes from). A line whose words are not in the text is marked unchecked; those
already in the CV are left out."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from . import career, career_words, documents, llm
from .db.models import Period, Person, SourceLink
from .db.session import session_scope
from .source_settings import active_links

BUDGET = 6000  # (characters sent, at most, over all the pieces: one piece)
PIECE = 6000  # (characters of a piece)
AROUND = 2  # (lines kept around a line about the career)
HEADING_LENGTH = 60  # (a heading: a short line)
HEADING_LINES = 8  # (… or among the first lines of its page)

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["position", "diploma"]},
                    "title": {"type": "string"},
                    "institution": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "details": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["kind", "title", "quote"],
            },
        }
    },
    "required": ["entries"],
}

PROMPT = """You read excerpts of documents about a researcher, {name}, to write their CV: \
the positions they held and the diplomas they obtained.
- Only {name}'s own positions and diplomas (not those of students, colleagues, supervisors).
- Only what the text states: never guess a date or an institution (leave it empty).
- Dates as YYYY, YYYY-MM or YYYY-MM-DD. A position: start and end (empty end: held now). \
A diploma: end is when it was obtained; for a PhD, the title of the thesis in details.
- title: the position or diploma (e.g. "Associate professor", "PhD in computer science"), \
in the language of the text; institution: where (e.g. "Sorbonne University, ISIR").
- quote: the words of the text it comes from, copied exactly (a short passage).
Answer with an empty list if the excerpts tell nothing of the kind."""


@dataclass
class Suggestion:
    entry: career.Entry
    quote: str  # (the words it comes from)
    source: str  # (where: a document and its page, a profile)
    checked: bool  # (whether the quote is in the text sent)
    # Where: the page with the quote (else those of the piece it was read in).
    pages: list[Passage] = field(default_factory=list)


@dataclass
class Passage:
    source: str  # “Application, p. 3”
    text: str
    doc_id: int | None = None  # (where: a document's page; none: the profiles)
    page: int | None = None


_fold = career_words.fold


def _pages(lines: list[dict]) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for line in lines:
        if t := (line.get("t") or "").strip():
            pages.setdefault(int(line.get("p") or 1), []).append(t)
    return pages


@dataclass
class Candidate:
    """A page's passage about the career: its rank (a heading's first, then by score)."""

    passage: Passage
    heading: bool
    score: int  # (its lines with a year and a position or diploma)


def candidates(name: str, lines: list[dict], doc_id: int | None = None) -> list[Candidate]:
    """A document's pages likely about the career (see the module), in order."""
    heading, about = (
        career_words.pattern("headings"),
        [p for g in ("positions", "diplomas") if (p := career_words.pattern(g))],
    )
    out = []
    for page, texts in sorted(_pages(lines).items()):
        where = f"{name}, p. {page}"
        folded = [_fold(t) for t in texts]
        if heading and any(
            heading.match(t) and (i < HEADING_LINES or len(t) <= HEADING_LENGTH)
            for i, t in enumerate(folded)
        ):
            hits = sum(1 for t in folded if _YEAR.search(t))
            out.append(Candidate(Passage(where, "\n".join(texts), doc_id, page), True, hits))
            continue
        hits = [
            i for i, t in enumerate(folded) if _YEAR.search(t) and any(p.search(t) for p in about)
        ]
        keep = {j for i in hits for j in range(max(0, i - AROUND), min(len(texts), i + AROUND + 1))}
        if keep:
            text = "\n".join(texts[i] for i in sorted(keep))
            out.append(Candidate(Passage(where, text, doc_id, page), False, len(hits)))
    return out


def passages(name: str, lines: list[dict]) -> list[Passage]:
    """A document's passages about the career, the most likely first (see candidates)."""
    return [c.passage for c in ranked(candidates(name, lines))]


def ranked(found: list[Candidate]) -> list[Candidate]:
    """The pages headed as a CV first (in order), then the others by score."""
    return sorted(found, key=lambda c: (not c.heading, -c.score if not c.heading else 0))


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


def affiliations(person_id: int) -> list[str]:
    """The person's affiliation, and those of their validated profiles (with their source)."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        known = [person.affiliation] if person.affiliation else []
        for link in s.scalars(
            select(SourceLink).where(SourceLink.person_id == person_id, active_links())
        ):
            if aff := (link.evidence or {}).get("affiliation"):
                known.append(f"{aff} ({link.source})")
    return known


def sources(
    person_id: int, doc_ids: list[int] | None = None, profiles: bool = True
) -> tuple[str, list[Passage]]:
    """The person's name and the passages to read: their profiles' affiliations (if
    ``profiles``), then those of their documents (``doc_ids``; none: all of them) read
    already. (Not the notes: one's own, not facts.)"""
    with session_scope() as s:
        name = s.get(Person, person_id).name
    known = affiliations(person_id) if profiles else []
    found = [Passage("profiles", "Affiliations: " + "; ".join(known))] if known else []
    pages = [
        c
        for d in person_documents(person_id)
        if d.read and (doc_ids is None or d.id in doc_ids)
        for c in candidates(d.name, documents.lines_of(d.id) or [], d.id)
    ]
    return name, found + [c.passage for c in ranked(pages)]


def pieces(found: list[Passage]) -> list[list[Passage]]:
    """The passages in pieces of about PIECE characters, BUDGET in all (cut beyond)."""
    out: list[list[Passage]] = []
    size = total = 0
    for p in found:
        text = p.text[: max(0, BUDGET - total)]
        if not text:
            break
        if not out or size + len(text) > PIECE:
            out.append([])
            size = 0
        out[-1].append(Passage(p.source, text, p.doc_id, p.page))
        size += len(text)
        total += len(text)
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


def _suggestions(answer: dict, piece: list[Passage]) -> list[Suggestion]:
    out = []
    for x in answer.get("entries") or []:
        title = (x.get("title") or "").strip()
        if not title or x.get("kind") not in career.KINDS:
            continue
        entry = career.Entry(
            x["kind"],
            title,
            _date(x.get("start")),
            _date(x.get("end")),
            (x.get("institution") or "").strip() or None,
            (x.get("details") or "").strip() or None,
            elapsed=x["kind"] == "diploma",
        )
        quote = " ".join((x.get("quote") or "").split())
        where = next((p for p in piece if quote and _fold(quote) in _fold(p.text)), None)
        source = where.source if where else ", ".join(dict.fromkeys(p.source for p in piece))
        pages = [where] if where else piece
        out.append(Suggestion(entry, quote, source, where is not None, pages))
    return out


def left_out(person_id: int, doc_ids: list[int] | None = None) -> list[Passage]:
    """The pages about the career not sent (beyond BUDGET), the most likely first."""
    _name, found = sources(person_id, doc_ids, profiles=False)
    sent = {(p.doc_id, p.page) for piece in pieces(found) for p in piece}
    return [p for p in found if (p.doc_id, p.page) not in sent]


def requests(
    person_id: int, doc_ids: list[int] | None = None, profiles: bool = True
) -> list[tuple[list[Passage], list[dict[str, str]]]]:
    """What the LLM is sent (see extract): each piece of the passages, and its messages (the
    instructions, then the passages)."""
    name, found = sources(person_id, doc_ids, profiles)
    return [
        (
            piece,
            [
                {"role": "system", "content": PROMPT.format(name=name)},
                {"role": "user", "content": "\n\n".join(f"[{p.source}]\n{p.text}" for p in piece)},
            ],
        )
        for piece in pieces(found)
    ]


async def extract(
    person_id: int,
    doc_ids: list[int] | None = None,
    folder_id: int | None = None,
    out: list[Suggestion] | None = None,
    profiles: bool = True,
    progress: Callable[[int, int], None] | None = None,
    phase: Callable[[str, int], None] | None = None,
) -> list[Suggestion]:
    """The lines of a CV the LLM reads in the person's profiles and documents (``doc_ids``;
    none: all those read), without those already in it (nor twice). Raises if no LLM.
    ``out``: where they are added, piece after piece (those found so far, if stopped);
    ``progress``: called with the pieces read and their number, before each one and at the
    end; ``phase``: with the model's phase in a piece (see llm.chat_json)."""
    have = career.entries(person_id)
    out = [] if out is None else out
    split = requests(person_id, doc_ids, profiles)
    for i, (piece, chat) in enumerate(split):
        if progress:
            progress(i, len(split))
        answer = await llm.chat_json(chat, SCHEMA, folder_id, task="career", progress=phase)
        for sug in _suggestions(answer, piece):
            if not any(_same(sug.entry, e) for e in [*have, *(x.entry for x in out)]):
                out.append(sug)
        out.sort(key=lambda x: career._sort_key(x.entry), reverse=True)
    if progress:
        progress(len(split), len(split))
    return out
