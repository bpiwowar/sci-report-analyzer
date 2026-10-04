"""A pasted list of references (e.g. a report's numbered publication list, copied from a PDF):
its items, and the person's papers they match.

An item is matched by its HAL id or DOI when it has one, else by its words: the share of a
paper's title words found in the item (the authors and the venue around it do not matter),
the year as a tie-breaker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .manual import parse_reference
from .merge import title_tokens
from .pubview import PubStat
from .ranking.service import service
from .sources.base import normalize_doi
from .sources.hal import document_id

_MIN_TITLE_TOKENS = 2

# An item's number at the start of a line: "3-", "3.", "3)", "[3]", "(3)".
_NUMBER = re.compile(r"^\s*(?:\[(\d{1,4})\]|\((\d{1,4})\)|(\d{1,4})\s*[-.)–:])\s*")
_URL = re.compile(r"https?://\S+|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+", re.I)
_HAL = re.compile(r"\b[a-z][a-z0-9]*-\d{6,}(?:v\d+)?\b", re.I)
# A link with its label ("Lien : https://…", "DOI: 10.…").
_LINK = re.compile(r"(?:\b(?:lien|link|url|doi|hal)\s*:\s*)?(?:" + _URL.pattern + ")", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
# Junk copied from a PDF: a line of marks only (e.g. "🔗" link icons), or a page number.
_JUNK = re.compile(r"^\s*(?:\d{1,3}|[^\w\s]+(?:\s+[^\w\s]+)*)\s*$")


@dataclass
class Item:
    number: int | None
    text: str
    hal: str | None = None
    doi: str | None = None
    url: str | None = None
    years: set[int] = field(default_factory=set)

    @property
    def ref(self) -> str | None:
        """What to add the paper by (a HAL id or a DOI), if the item has one."""
        return self.hal or self.doi

    @property
    def words(self) -> str:
        """The item's text without its links."""
        return re.sub(r"\s+", " ", _LINK.sub(" ", self.text)).strip(" .,;")


@dataclass
class Candidate:
    pub_id: int
    title: str | None
    year: int | None
    score: float  # 1: by HAL id / DOI
    by_id: bool = False


@dataclass
class Match:
    item: Item
    candidates: list[Candidate]  # best first

    @property
    def best(self) -> Candidate | None:
        c = self.candidates[0] if self.candidates else None
        return c if c and c.score >= service.settings.reflist_match else None


def join_wrapped(lines: list[str]) -> str:
    """Lines as one text, the words broken at their end joined again."""
    text = ""
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        if text.endswith("-") and line[:1].islower() and not _URL.search(text.split()[-1]):
            text = text[:-1] + line  # a word broken at the end of the line
        else:
            text = f"{text} {line}" if text else line
    return text


def split_items(text: str) -> list[Item]:
    """The items of a pasted list: a numbered line starts one, the next lines continue it
    (an unnumbered list: one item per paragraph)."""
    lines = (text or "").replace("\r", "").split("\n")
    groups: list[tuple[int | None, list[str]]] = []
    numbered = any(_NUMBER.match(ln) for ln in lines)
    for line in lines:
        if _JUNK.match(line):
            continue
        if numbered and (m := _NUMBER.match(line)):
            n = next(int(g) for g in m.groups() if g)
            groups.append((n, [line[m.end() :]]))
        elif numbered:
            if groups:
                groups[-1][1].append(line)
        elif not line.strip():
            groups.append((None, []))
        else:
            if not groups:
                groups.append((None, []))
            groups[-1][1].append(line)
    items = []
    for n, group in groups:
        if t := join_wrapped(group):
            items.append(item_of(n, t))
    return items


def item_of(number: int | None, text: str) -> Item:
    """An item of a list (its HAL id, DOI, URL and years found in its text)."""
    item = Item(number, text, years={int(y) for y in _YEAR.findall(text)})
    for u in _URL.findall(text):
        u = u.rstrip(".,;)")
        ref = parse_reference(u)
        if ref and ref.kind == "hal" and not item.hal:
            item.hal = ref.id
        elif ref and ref.kind == "doi" and not item.doi:
            item.doi = ref.id
        if u.lower().startswith("http") and not item.url:
            item.url = u
    if not item.hal:
        for m in _HAL.finditer(text):
            if hal := document_id(m.group(0)):
                item.hal = hal
                break
    return item


def paper_ids(r: PubStat) -> tuple[set[str], set[str]]:
    """A paper's HAL ids and DOIs."""
    hal = {m.external_key.lower() for m in r.members if m.source == "hal" and m.external_key}
    hal |= {h for m in r.members if m.url and (h := document_id(m.url))}
    hal = {re.sub(r"v\d+$", "", h) for h in hal}
    dois = {d for d in [r.doi, *(m.doi for m in r.members)] if d}
    return hal, {normalize_doi(d) for d in dois}


def title_score(title: frozenset[str], words: frozenset[str]) -> float:
    """The share of a title's words (``title_tokens``) among an item's; 0 for a short one."""
    return len(title & words) / len(title) if len(title) >= _MIN_TITLE_TOKENS else 0.0


def match(items: list[Item], rows: list[PubStat]) -> list[Match]:
    """Each item's candidates among the papers ``rows``: the one with its HAL id / DOI,
    else those with most of their title words in the item."""
    ids = {r.id: paper_ids(r) for r in rows}
    titles = {r.id: title_tokens(r.title) for r in rows}
    out = []
    for item in items:
        cands: dict[int, Candidate] = {}
        for r in rows:
            hal, dois = ids[r.id]
            if (item.hal and item.hal in hal) or (item.doi and item.doi in dois):
                cands[r.id] = Candidate(r.id, r.title, r.year, 1.0, by_id=True)
        if not cands:
            words = title_tokens(item.words)
            for r in rows:
                if (score := title_score(titles[r.id], words)) >= service.settings.reflist_suggest:
                    cands[r.id] = Candidate(r.id, r.title, r.year, score)
        ranked = sorted(
            cands.values(),
            key=lambda c: (-round(c.score, 2), c.year not in item.years, -len(titles[c.pub_id])),
        )
        out.append(Match(item, ranked[:5]))
    return out


def label(item: Item, width: int = 140) -> str:
    text = item.words
    text = text if len(text) <= width else text[: width - 1] + "…"
    return f"{item.number}. {text}" if item.number is not None else text


@dataclass
class Found:
    """A document found elsewhere for an item (to add to the person's papers)."""

    ref: str  # HAL id
    title: str | None
    year: int | None
    authors: list[str]
    url: str
    score: float


async def search(item: Item) -> list[Found]:
    """HAL documents for an item that matches none of the person's papers, best first."""
    from .sources import hal

    words = title_tokens(item.words)
    docs = await hal.search_documents(sorted(words))
    out = []
    for d in docs:
        if (
            score := title_score(title_tokens(d["title"]), words)
        ) >= service.settings.reflist_suggest:
            out.append(Found(d["hal"], d["title"], d["year"], d["authors"], d["url"], score))
    return sorted(out, key=lambda f: (-round(f.score, 2), f.year not in item.years))[:3]
