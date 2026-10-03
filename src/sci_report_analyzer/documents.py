"""Documents of a person within a period / folder (e.g. their application, a CV): PDFs stored
next to the papers' ones, read and annotated in the browser like them, and the person's
papers they mention found and linked.

A paper is found by its title (the words in order, a few mistakes allowed in a long one), or
by its DOI / HAL id, in the document's lines of text (extracted by the viewer).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .db.models import Period, PeriodDocument, utcnow
from .db.session import session_scope
from .i18n import _
from .pdfs import PdfError, _slug, is_pdf, pdf_dir
from .ranking.normalize import normalize

SUBDIR = "documents"
_MIN_TOKENS = 3  # shorter titles are too common to be looked for


@dataclass
class DocView:
    id: int
    period_id: int
    name: str
    added_at: Any
    edited: bool
    note: str | None


def add(period_id: int, name: str, data: bytes) -> int:
    """Store ``data`` as a new document of a period; returns its id."""
    if not is_pdf(data):
        raise PdfError(_("not a PDF file"))
    name = Path(name or "document").stem.strip() or "document"
    with session_scope() as s:
        period = s.get(Period, period_id)
        if period is None:
            raise PdfError(_("no such period"))
        doc = PeriodDocument(period_id=period_id, name=name, path="")
        s.add(doc)
        s.flush()
        doc.path = f"{SUBDIR}/{period.person_id}/{doc.id}-{_slug(name)}.pdf"
        _write(pdf_dir() / doc.path, data)
        return doc.id


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)  # (never a half-written file)


def save(doc_id: int, data: bytes) -> None:
    """Replace the file of a document by its annotated version (from the viewer)."""
    if not is_pdf(data):
        raise PdfError(_("not a PDF file"))
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        if doc is None:
            raise PdfError(_("no such document"))
        _write(pdf_dir() / doc.path, data)
        doc.edited_at = utcnow()


def file_of(doc_id: int) -> Path | None:
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        path = pdf_dir() / doc.path if doc else None
    return path if path and path.is_file() else None


@dataclass
class DocInfo:
    id: int
    name: str
    person_id: int
    person: str
    period_id: int
    period: str
    note: str | None
    has_lines: bool


def info(doc_id: int) -> DocInfo | None:
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        if doc is None:
            return None
        p = doc.period
        return DocInfo(
            doc.id,
            doc.name,
            p.person_id,
            p.person.name,
            p.id,
            p.name,
            doc.note,
            doc.lines is not None,
        )


def of_person(person_id: int) -> dict[int, list[DocView]]:
    """The person's documents, by period id."""
    with session_scope() as s:
        rows = s.scalars(
            select(PeriodDocument)
            .join(Period)
            .where(Period.person_id == person_id)
            .order_by(PeriodDocument.id)
        )
        out: dict[int, list[DocView]] = {}
        for d in rows:
            out.setdefault(d.period_id, []).append(
                DocView(d.id, d.period_id, d.name, d.added_at, d.edited_at is not None, d.note)
            )
        return out


def rename(doc_id: int, name: str) -> None:
    with session_scope() as s:
        if (doc := s.get(PeriodDocument, doc_id)) and name.strip():
            doc.name = name.strip()


def set_note(doc_id: int, text: str) -> None:
    with session_scope() as s:
        if doc := s.get(PeriodDocument, doc_id):
            doc.note = text or None


def remove(doc_id: int) -> None:
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        if doc is None:
            return
        (pdf_dir() / doc.path).unlink(missing_ok=True)
        s.delete(doc)


def set_lines(doc_id: int, lines: list[dict[str, Any]]) -> None:
    """Keep the document's lines of text (as extracted by the viewer)."""
    clean = []
    for ln in lines:
        try:
            page, text = int(ln["p"]), str(ln["t"])
            rect = [float(v) for v in ln["r"]][:4]
        except (KeyError, TypeError, ValueError):
            continue
        runs = []
        for run in ln.get("c") or []:
            try:
                runs.append([int(run[0]), float(run[1]), float(run[2])])
            except (IndexError, TypeError, ValueError):
                continue
        if text.strip() and len(rect) == 4:
            clean.append({"p": page, "t": text, "r": rect, **({"c": runs} if runs else {})})
    with session_scope() as s:
        if doc := s.get(PeriodDocument, doc_id):
            doc.lines = clean


def lines_of(doc_id: int) -> list[dict[str, Any]] | None:
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        return doc.lines if doc else None


# ---- Papers mentioned in a document ---------------------------------------------------------


@dataclass
class Mention:
    """A paper found in a document: where (its page, and one rectangle per line, in PDF
    units), and how: its title, its DOI / HAL id, a citation of its reference ("[11]"), or
    a link made by hand (from a selection)."""

    pub_id: int
    page: int
    rects: list[list[float]] = field(default_factory=list)
    kind: str = "title"  # title | id | cite | manual
    line: int = 0  # (index of its first line: its order, and what a rejection names)
    label: str | None = None  # its reference's label ("11", "C3"), or the one cited
    link: int | None = None  # (manual: its index in the document's links)


def _tokens(lines: list[dict[str, Any]]) -> tuple[list[str], list[int]]:
    """The words of the lines (a word broken at a line's end joined back), with the line
    each starts on."""
    words: list[str] = []
    where: list[int] = []
    broken = False
    for i, ln in enumerate(lines):
        toks = normalize(ln["t"]).split()
        if broken and toks and words and ln["t"].lstrip()[:1].islower():
            words[-1] += toks.pop(0)
        words += toks
        where += [i] * len(toks)
        broken = ln["t"].rstrip().endswith("-") and not ln["t"].rstrip().endswith("--")
    return words, where


def _allowed(n: int) -> int:
    """Mistakes allowed when looking for a title of ``n`` words."""
    return 0 if n <= 4 else n // 8


def _distance(title: list[str], words: list[str], start: int, k: int) -> tuple[int, int]:
    """Edit distance between the title and the best text from ``start`` (the end is free):
    (distance, end index)."""
    window = words[start : start + len(title) + k]
    prev = list(range(len(window) + 1))
    for i, t in enumerate(title, 1):
        cur = [i] + [0] * len(window)
        for j, w in enumerate(window, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (t != w))
        prev = cur
    end = min(range(len(window) + 1), key=lambda j: (prev[j], -j))
    return prev[end], start + end


def _rects(lines: list[dict[str, Any]], first: int, last: int) -> list[list[float]]:
    return [lines[i]["r"] for i in range(first, last + 1) if lines[i]["p"] == lines[first]["p"]]


def _by_title(lines: list[dict[str, Any]], rows: list) -> list[Mention]:
    words, where = _tokens(lines)
    at: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        at.setdefault(w, []).append(i)
    found: list[tuple[int, int, int]] = []  # (start, end, pub id)
    for r in rows:
        title = normalize(r.title).split()
        if len(title) < _MIN_TOKENS:
            continue
        k = _allowed(len(title))
        starts = set(at.get(title[0], []))
        if k:
            starts |= {i - 1 for i in at.get(title[1], []) if i > 0}
        last_end = -1
        for s in sorted(starts):
            if s < last_end:
                continue
            dist, end = _distance(title, words, s, k)
            if dist <= k and end > s:
                found.append((s, end, r.id))
                last_end = end
    # A title within the mention of a longer one (e.g. "Deep ranking" in "Deep ranking for
    # search") is not a mention.
    kept: list[tuple[int, int, int]] = []
    for f in sorted(found, key=lambda f: (f[0], -(f[1] - f[0]))):
        if not any(
            g[0] <= f[0] and f[1] <= g[1] and g[2] != f[2] and g[1] - g[0] > f[1] - f[0]
            for g in kept
        ):
            kept.append(f)
    out = []
    for s, e, pid in kept:
        first, last = where[s], where[e - 1]
        out.append(Mention(pid, lines[first]["p"], _rects(lines, first, last), line=first))
    return out


def _by_id(lines: list[dict[str, Any]], rows: list, near: list[Mention]) -> list[Mention]:
    """By DOI / HAL id, unless the title was found just before (the same reference)."""
    from .reflist import _ids, _item

    ids = {r.id: _ids(r) for r in rows}
    seen: dict[int, list[int]] = {}
    for m in near:
        seen.setdefault(m.pub_id, []).append(m.line)
    out = []
    for i, ln in enumerate(lines):
        item = _item(None, ln["t"])
        if not (item.hal or item.doi):
            continue
        for r in rows:
            hal, dois = ids[r.id]
            if (item.hal and item.hal in hal) or (item.doi and item.doi in dois):
                before = sorted(seen.get(r.id, []))
                j = bisect.bisect_right(before, i)
                if j and i - before[j - 1] <= 6:
                    continue
                out.append(Mention(r.id, ln["p"], [ln["r"]], kind="id", line=i))
    return out


def _line_at(lines: list[dict[str, Any]], page: int, rect: list[float]) -> int | None:
    """The line under a rectangle (the first of a selection)."""
    best, score = None, 0.0
    for i, ln in enumerate(lines):
        if ln["p"] != page:
            continue
        r = ln["r"]
        dy = min(r[3], rect[3]) - max(r[1], rect[1])
        dx = min(r[2], rect[2]) - max(r[0], rect[0])
        if dy > 0 and dx > 0 and dy * dx > score:
            best, score = i, dy * dx
    return best


# A reference's label at the start of its line: "[11]", "[C3]", "11." or "11)".
_LABEL = re.compile(r"^\s*(?:\[([A-Za-z]{0,3}\d{1,4})\]|(\d{1,3})[.)]\s)")
# A citation: "[11]", "[3, 11]", "[3-5]", "[C3; J1]".
_CITE = re.compile(r"\[([A-Za-z]{0,3}\d{1,4}(?:\s*[,;–-]\s*[A-Za-z]{0,3}\d{1,4})*)\]")
_PART = re.compile(r"([A-Za-z]{0,3})(\d{1,4})(?:\s*[–-]\s*\1(\d{1,4}))?")
_LOOK_BACK = 4  # lines between a reference's label and its title
# The heading of a list of references ("References", "7 Bibliography").
_REF_HEADING = re.compile(
    r"^\s*(?:[A-Z]?\d*\.?\s+)?(?:references|bibliography|références|bibliographie"
    r"|works cited|literature cited)\s*:?\s*$",
    re.I,
)


def _headings(lines: list[dict[str, Any]]) -> list[int]:
    """The lines heading a list of references (a paper and its appendix may have one each)."""
    return [i for i, ln in enumerate(lines) if _REF_HEADING.match(ln["t"])]


def _section(heads: list[int], line: int) -> int:
    """The list of references a line is in (0: before the first heading)."""
    return bisect.bisect_right(heads, line)


def _cited(heads: list[int], line: int) -> int:
    """The list of references cited from a line: the closest one after it (the last one
    after them all)."""
    return min(_section(heads, line) + 1, len(heads))


def _closest(found: set[tuple[int, int]], section: int) -> set[int]:
    """The papers of (paper, list) found, those of the list cited if any is."""
    there = {p for p, s in found if s == section}
    return there or {p for p, _x in found}


def _labels(
    lines: list[dict[str, Any]], mentions: list[Mention], heads: list[int]
) -> dict[str, set[tuple[int, int]]]:
    """Label -> (paper, list of references), for the numbered references whose paper was
    found."""
    starts = sorted(m.line for m in mentions)
    out: dict[str, set[tuple[int, int]]] = {}
    for m in mentions:
        j = bisect.bisect_left(starts, m.line)
        previous = starts[j - 1] if j else -1
        for i in range(m.line, max(m.line - _LOOK_BACK, previous, -1) - 1, -1):
            if lines[i]["p"] != lines[m.line]["p"]:
                break
            if found := _LABEL.match(lines[i]["t"]):
                m.label = found.group(1) or found.group(2)
                out.setdefault(m.label, set()).add((m.pub_id, _section(heads, m.line)))
                break
    return out


def _citations(
    lines: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]], heads: list[int]
) -> list[Mention]:
    """The numbered citations; a label given to different papers (e.g. by two lists) is
    the one of the closest list after, or left out."""
    out = []
    for i, ln in enumerate(lines):
        text = ln["t"]
        section = _cited(heads, i)
        for c in _CITE.finditer(text):
            if not text[: c.start()].strip() and _LABEL.match(text):
                continue  # a reference's own label
            for part in _PART.finditer(c.group(1)):
                prefix, lo, hi = part.group(1), int(part.group(2)), part.group(3)
                numbers = range(lo, int(hi) + 1) if hi and int(hi) - lo < 30 else [lo]
                start = c.start(1) + part.start()
                end = c.start(1) + part.end()
                rect = _part_rect(ln, start, end)
                step = (rect[2] - rect[0]) / len(numbers)  # (a range: its share each)
                for k, n in enumerate(numbers):
                    pids = _closest(labels.get(f"{prefix}{n}", set()), section)
                    if len(pids) == 1:
                        [pid] = pids
                        x = rect[0] + k * step
                        out.append(
                            Mention(
                                pid,
                                ln["p"],
                                [[x, rect[1], x + step, rect[3]]],
                                kind="cite",
                                line=i,
                                label=f"{prefix}{n}",
                            )
                        )
    return out


# Approximate glyph widths (per mille of the font size, as Helvetica's): to place a part of
# a run of text.
_WIDTHS = {
    **dict.fromkeys(" ijl.,;:'|!()[]/fIt", 278),
    **dict.fromkeys("r-", 333),
    **dict.fromkeys("mM", 833),
    **dict.fromkeys("wW", 778),
}


def _width(ch: str) -> int:
    if ch in _WIDTHS:
        return _WIDTHS[ch]
    if ch.isupper():
        return 667
    return 556  # (digits, most lower-case letters)


def _part_rect(line: dict[str, Any], start: int, end: int) -> list[float]:
    """The rectangle of characters ``start:end`` of a line: interpolated within its runs of
    text (as the viewer gave them; else within the line), by approximate glyph widths."""
    x0, y0, x1, y1 = line["r"]
    text = line["t"]
    runs = line.get("c") or [[0, x0, x1]]

    def x(i: int) -> float:
        k = max(j for j, r in enumerate(runs) if r[0] <= i or j == 0)
        first, a, b = runs[k]
        last = runs[k + 1][0] if k + 1 < len(runs) else len(text)
        total = sum(_width(c) for c in text[first:last]) or 1
        return a + (b - a) * sum(_width(c) for c in text[first:i]) / total

    return [x(start), y0, x(end), y1]


# An author-year citation: "Lyu et al., 2023b", "Lyu et al. (2023b)", "Gari Soler and
# Apidianaki, 2021, 2020", "van der Berg & Doe 2023" (within parentheses or not); the
# last word of each author is what is matched.
_NAME = r"[A-Z][\w’'-]+"
_PARTICLE = r"(?:(?:van|von|de|der|den|da|di|du|le|la|del|dos)\s+)"
_AUTHOR = rf"(?:{_PARTICLE}*{_NAME})(?:\s+{_PARTICLE}*{_NAME}){{0,2}}"
# Words starting a sentence before a citation ("In Lyu et al. (2023)"): not its authors.
_LEAD = re.compile(r"(?:(?:In|As|See|By|From|Following|Like|Unlike|While|And|But|Then)\s+)+")
_YEAR = r"(?:19|20)\d\d[a-z]?"
_AUTHOR_YEAR = re.compile(
    rf"\b({_AUTHOR})(?:\s+(et\s+al\.?)|\s+(?:and|&)\s+({_AUTHOR}))?,?\s*\(?"
    rf"({_YEAR}(?:\s*,\s*{_YEAR})*)\b\)?"
)
_ONE_YEAR = re.compile(r"((?:19|20)\d\d)([a-z])?")
# Before a citation, its first author when it starts with a later one ("Marie and
# Apidianaki, 2015", cut after "Marie").
_FIRST = re.compile(rf"({_AUTHOR})\s+(?:and|&)\s*$")


def _surname(name: str) -> str:
    """The surname of an author ("Jane Doe", "Doe, Jane"), normalized."""
    name = name.split(",")[0] if "," in name else name
    words = normalize(name).split()
    return words[-1] if words else ""


def _author_years(
    lines: list[dict[str, Any]], refs: list[Mention], rows: list, heads: list[int]
) -> dict[tuple[str, int, str], set[tuple[int, int]]]:
    """(first author's surname, year, suffix) -> (paper, list of references), for the
    references found (their suffix, e.g. the "b" of "2023b", as in the reference: the
    closest to its title, before it then after, not one of the reference before)."""
    by_id = {r.id: r for r in rows}
    keys: dict[tuple[str, int, str], set[tuple[int, int]]] = {}
    for m in refs:
        r = by_id.get(m.pub_id)
        if r is None or not r.year or not r.authors:
            continue
        name = _surname(r.authors[0])
        suffix = ""
        near = [*range(m.line, max(m.line - _LOOK_BACK, 0) - 1, -1), m.line + 1, m.line + 2]
        for i in near:
            if i >= len(lines):
                continue
            # (on the title's line, the last one: the one before it, "(2023b). Faithful…")
            if years := re.findall(rf"\b{r.year}([a-z])\b", lines[i]["t"]):
                suffix = years[-1] if i == m.line else years[0]
                break
        keys.setdefault((name, r.year, suffix), set()).add((r.id, _section(heads, m.line)))
    return keys


def _cites(authors: list[str], second: str | None, etal: bool) -> bool:
    """Whether a paper's authors (surnames, the first one matched) are those cited: one
    alone, two (the second one named), or more ("et al.")."""
    if second is not None:
        return len(authors) == 2 and authors[1] == second
    return len(authors) >= 2 if etal else len(authors) == 1


def _author_year_citations(
    lines: list[dict[str, Any]],
    keys: dict[tuple[str, int, str], set[tuple[int, int]]],
    authors: dict[int, list[str]],
    skip: set[int],
    heads: list[int],
) -> list[Mention]:
    loose: dict[tuple[str, int], set[tuple[int, int]]] = {}  # (a citation without the suffix)
    for (name, year, _x), pids in keys.items():
        loose.setdefault((name, year), set()).update(pids)
    out = []
    for i, ln in enumerate(lines):
        if i in skip:  # (a reference's own lines)
            continue
        # With the end of the line before (a citation across lines; a broken word joined).
        prev = lines[i - 1] if i and i - 1 not in skip and lines[i - 1]["p"] == ln["p"] else None
        head = prev["t"].rstrip() if prev else ""
        broken = head.endswith("-") and not head.endswith("--") and ln["t"][:1].islower()
        head = head[:-1] if broken else head
        off = len(head) + (0 if broken or not head else 1)
        text = (head + ("" if broken else " ") if head else "") + ln["t"]
        for c in _AUTHOR_YEAR.finditer(text):
            name = normalize(c.group(1)).split()[-1]
            second = normalize(c.group(3)).split()[-1] if c.group(3) else None
            begin = c.start()
            if re.search(r"(?:\band|&)\s*$", text[: c.start()]):  # (a later author)
                first = _FIRST.search(text[: c.start()])
                if first is None or c.group(2) or second:
                    continue
                name, second, begin = normalize(first.group(1)).split()[-1], name, first.start()
            for k, y in enumerate(_ONE_YEAR.finditer(c.group(4))):
                if c.start(4) + y.start() < off:  # (found with the line before)
                    continue
                year, suffix = int(y.group(1)), y.group(2) or ""
                etal = bool(c.group(2))
                fits = {
                    (p, s)
                    for p, s in keys.get((name, year, suffix), ())
                    if _cites(authors[p], second, etal)
                }
                if not fits and not suffix:
                    fits = {
                        (p, s)
                        for p, s in loose.get((name, year), ())
                        if _cites(authors[p], second, etal)
                    }
                fits = _closest(fits, _cited(heads, i))
                if len(fits) != 1:
                    continue
                [pid] = fits
                # The first year: with its authors; the next ones: on their own.
                lead = _LEAD.match(text, begin)
                first = lead.end() if lead else begin
                start = first if k == 0 else c.start(4) + y.start()
                end = c.start(4) + y.end()
                label = " ".join(text[first : c.start(4)].split()).rstrip(",( ")
                label = f"{label}, {y.group(0)}"
                rects = [_part_rect(ln, max(start - off, 0), end - off)]
                if start < off and prev:
                    rects.insert(0, _part_rect(prev, start, len(head)))
                line = i - 1 if start < off else i
                out.append(Mention(pid, ln["p"], rects, "cite", line, label))
    return out


def find_papers(
    lines: list[dict[str, Any]], rows: list, links: list[dict[str, Any]] | None = None
) -> list[Mention]:
    """Where the papers ``rows`` (PubStat) are mentioned in a document's lines (and its
    links made by hand), in order."""
    links = links or []
    known = {r.id for r in rows}
    rejected = {(x["pub"], x.get("line")) for x in links if x.get("reject")}
    found = [m for m in _by_title(lines, rows) if (m.pub_id, m.line) not in rejected]
    found += [m for m in _by_id(lines, rows, found) if (m.pub_id, m.line) not in rejected]
    for i, x in enumerate(links):
        if x.get("reject") or x["pub"] not in known or not x.get("r"):
            continue
        line = _line_at(lines, x["p"], x["r"][0])
        found.append(Mention(x["pub"], x["p"], x["r"], kind="manual", line=line or 0, link=i))
    refs = [m for m in found if m.line is not None]
    heads = _headings(lines)
    found += _citations(lines, _labels(lines, refs, heads), heads)
    own = {i for m in refs for i in range(max(m.line - 1, 0), m.line + len(m.rects))}
    authors = {r.id: [_surname(a) for a in r.authors or []] for r in rows}
    keys = _author_years(lines, refs, rows, heads)
    found += _author_year_citations(lines, keys, authors, own, heads)
    return sorted(found, key=lambda m: (m.line, m.kind == "cite"))


def links_of(doc_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        return list(doc.links or []) if doc else []


def add_link(doc_id: int, pub_id: int, page: int, rects: list[list[float]], text: str) -> None:
    """Link a selection of the document to a paper."""
    with session_scope() as s:
        if doc := s.get(PeriodDocument, doc_id):
            doc.links = [*(doc.links or []), {"pub": pub_id, "p": page, "r": rects, "text": text}]


def reject(doc_id: int, m: Mention) -> None:
    """A paper wrongly found: forget it (a link made by hand), or never find it there again."""
    with session_scope() as s:
        doc = s.get(PeriodDocument, doc_id)
        if doc is None:
            return
        links = list(doc.links or [])
        if m.kind == "manual" and m.link is not None and m.link < len(links):
            links.pop(m.link)
        else:
            links.append({"pub": m.pub_id, "line": m.line, "reject": True})
        doc.links = links


def selection_item(text: str):
    """A selection of the document as a reference (reflist.Item) to look for."""
    from .reflist import _item, _join

    joined = _join(text.splitlines())
    return _item(None, _LABEL.sub("", joined, count=1) or joined)


# ---- Bookmarks (of a document, or of a paper's stored PDF) ----------------------------------


def _bookmarked(s, kind: str, key: int):
    from .db.models import PublicationPdf

    return s.get(PeriodDocument if kind == "doc" else PublicationPdf, key)


def bookmarks(kind: str, key: int) -> list[dict[str, Any]]:
    """The bookmarks of a document (``kind`` "doc") or of a paper's PDF ("pub"), by page."""
    with session_scope() as s:
        row = _bookmarked(s, kind, key)
        return list(row.bookmarks or []) if row else []


def add_bookmark(kind: str, key: int, name: str, page: int, y: float | None) -> None:
    with session_scope() as s:
        if row := _bookmarked(s, kind, key):
            marks = [
                *(row.bookmarks or []),
                {"name": name.strip() or f"Page {page}", "p": page, "y": y},
            ]
            row.bookmarks = sorted(marks, key=lambda b: (b["p"], -(b["y"] or 0)))


def set_bookmarks(kind: str, key: int, marks: list[dict[str, Any]]) -> None:
    with session_scope() as s:
        if row := _bookmarked(s, kind, key):
            row.bookmarks = list(marks)
