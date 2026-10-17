"""A person's career read by the LLM in their documents and profiles (see career.py): lines
of a CV suggested, to be checked before being added.

The documents are long (an application: tens of pages) and a local LLM's context short, so
only their passages likely about the career are sent: a page headed as a CV (“Curriculum
vitae”, “Parcours”…) whole, else the lines with a year and a word of a career (a position, a
diploma…), with the lines around them; in pieces of a few thousand characters, each read
apart (a structured output: the lines of the CV, each with the words it comes from). A line
whose words are not in the text is marked unchecked; those already in the CV are left
out."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from . import career, documents, llm
from .db.models import Period, Person, SourceLink
from .db.session import session_scope
from .source_settings import active_links

BUDGET = 16000  # (characters sent, at most, over all the pieces)
PIECE = 6000  # (characters of a piece)
AROUND = 2  # (lines kept around a line about the career)

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_CAREER = re.compile(
    r"\b(professor|professeure?|lecturer|ma[iî]tre de conf[ée]rences?|mcf|charg[ée]e? de "
    r"recherche|directeur|directrice|research(er)?|chercheu(r|se)|scientist|engineer|"
    r"ing[ée]nieure?|post-?doc\w*|ph\.?d|doctora[lt]\w*|th[èe]se|thesis|hdr|habilitation|"
    r"ater|fellow\w*|assistant|associate|master|dipl[ôo]m\w*|degree|lauréat|recruited|"
    r"recrut[ée]e?|since|depuis|position|poste)\b",
    re.IGNORECASE,
)
_CV_HEADING = re.compile(
    r"^\s*(\d+[.)]?\s*)?(curriculum vit(ae|æ)|cv\b|parcours|career|carri[èe]re|"
    r"positions?\b|employment|education|formation|dipl[ôo]mes|situation professionnelle)",
    re.IGNORECASE,
)

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


@dataclass
class Passage:
    source: str  # “Application, p. 3”
    text: str


def _fold(text: str) -> str:
    """(compared: lower case, no accents, single spaces)"""
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _pages(lines: list[dict]) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for line in lines:
        if t := (line.get("t") or "").strip():
            pages.setdefault(int(line.get("p") or 1), []).append(t)
    return pages


def passages(name: str, lines: list[dict]) -> list[Passage]:
    """A document's passages likely about the career (see the module), in order."""
    out = []
    for page, texts in sorted(_pages(lines).items()):
        where = f"{name}, p. {page}"
        if any(_CV_HEADING.match(t) for t in texts[:8]):
            out.append(Passage(where, "\n".join(texts)))
            continue
        keep = set()
        for i, t in enumerate(texts):
            if _YEAR.search(t) and _CAREER.search(t):
                keep.update(range(max(0, i - AROUND), min(len(texts), i + AROUND + 1)))
        if keep:
            out.append(Passage(where, "\n".join(texts[i] for i in sorted(keep))))
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


def sources(person_id: int, doc_ids: list[int] | None = None) -> tuple[str, list[Passage]]:
    """The person's name and the passages to read: their profiles' affiliations, their
    notes, then those of their documents (``doc_ids``; none: all of them) read already."""
    with session_scope() as s:
        person = s.get(Person, person_id)
        name, notes = person.name, person.notes
        known = [person.affiliation] if person.affiliation else []
        for link in s.scalars(
            select(SourceLink).where(SourceLink.person_id == person_id, active_links())
        ):
            if aff := (link.evidence or {}).get("affiliation"):
                known.append(f"{aff} ({link.source})")
    found = [Passage("profiles", "Affiliations: " + "; ".join(known))] if known else []
    if notes:
        found.append(Passage("notes", notes))
    for d in person_documents(person_id):
        if d.read and (doc_ids is None or d.id in doc_ids):
            found += passages(d.name, documents.lines_of(d.id) or [])
    return name, found


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
        out[-1].append(Passage(p.source, text))
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
        out.append(Suggestion(entry, quote, source, where is not None))
    return out


def requests(
    person_id: int, doc_ids: list[int] | None = None
) -> list[tuple[list[Passage], list[dict[str, str]]]]:
    """What the LLM is sent (see extract): each piece of the passages, and its messages (the
    instructions, then the passages)."""
    name, found = sources(person_id, doc_ids)
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
    progress: Callable[[int, int], None] | None = None,
) -> list[Suggestion]:
    """The lines of a CV the LLM reads in the person's profiles and documents (``doc_ids``;
    none: all those read), without those already in it (nor twice). Raises if no LLM.
    ``out``: where they are added, piece after piece (those found so far, if stopped);
    ``progress``: called with the pieces read and their number, before each one and at the
    end."""
    have = career.entries(person_id)
    out = [] if out is None else out
    split = requests(person_id, doc_ids)
    for i, (piece, chat) in enumerate(split):
        if progress:
            progress(i, len(split))
        answer = await llm.chat_json(chat, SCHEMA, folder_id)
        for sug in _suggestions(answer, piece):
            if not any(_same(sug.entry, e) for e in [*have, *(x.entry for x in out)]):
                out.append(sug)
        out.sort(key=lambda x: career._sort_key(x.entry), reverse=True)
    if progress:
        progress(len(split), len(split))
    return out
