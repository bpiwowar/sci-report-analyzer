"""Reports on a person within a period / folder: Markdown citing the person's papers with
Pandoc's syntax, substituted when copied.

- ``[@key]`` (or ``[@a; @b]``, ``[see @a, p. 3]``): the paper's number (``**#6**``,
  see ``number_format``);
- ``@key``, or ``[@key]{.full}``: its number, title, venue, year and category;
- ``[@key]{.notes}``: that, then its notes; ``[@key]{.tags}``: with its tags (#tags), and
  ``[@key]{.notes .tags}`` both;
- ``[@key]{.short-venue (.year)}``: a template, its fields (``.number``, ``.index``: the
  bare number, ``.title``, ``.venue``, ``.short-venue``: the acronym, else the venue,
  ``.year``, ``.tags``, ``.notes``) replaced and the rest kept: ``EMNLP (2026)``,
  ``{#.index}``: ``#2``, ``{**#.index** (.short-venue .year): .notes}``.

The report discusses the papers with some tags (or, without, those of the period's years):
each should be cited at least once.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from lark import Lark, Transformer
from lark.exceptions import LarkError
from sqlalchemy import select

from .db.models import (
    AppSetting,
    PeriodNote,
    PeriodTag,
    Publication,
    PublicationFlag,
    PublicationTag,
    Report,
)
from .db.session import session_scope
from .i18n import _
from .pubview import PubStat, hashtag, tagged

NUMBER_FORMAT = "**#{n}**"

_KEY = r"\w+(?:[:.#$%&+?<>~/-]\w+)*"
# A bracketed citation, then its classes (Pandoc's bracketed span): [see @a, p. 3; @b]{.notes}
_BRACKET = re.compile(
    r"\[(?P<body>[^\[\]]*?(?<![\w@])-?@" + _KEY + r"[^\[\]]*)\](?:\{(?P<attrs>[^{}\n]*)\})?"
)
_ITEM = re.compile(r"(?P<pre>.*?)(?<![\w@])-?@(?P<key>" + _KEY + r")(?P<post>.*)", re.S)
_INTEXT = re.compile(r"(?<![\w@\[])@(?P<key>" + _KEY + r")")
# Code (fenced blocks, spans) is left as is.
_CODE = re.compile(r"(```.*?(?:```|$)|`[^`\n]*`)", re.S)
_LIST_ITEM = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?")

_STOP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "from", "towards"}


# ---- Keys and numbers -----------------------------------------------------------------------


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _base_key(s: PubStat) -> str:
    """BibTeX-like: the first author's last name, the year, the title's first word."""
    names = (s.authors[0] if s.authors else "").replace(",", " ").split()
    author = _ascii(names[-1]) if names else ""
    words = [w for w in (_ascii(w) for w in (s.title or "").split()) if w and w not in _STOP]
    return f"{author or 'anon'}{s.year or 'nd'}{words[0] if words else ''}"


def citation_keys(stats: list[PubStat]) -> dict[int, str]:
    """A key per paper (id -> key), e.g. ``doe2021neural``; on a clash, the later paper (by
    id) gets a letter: ``doe2021neurala``. (Keys stay as papers are added.)"""
    out: dict[int, str] = {}
    used: set[str] = set()
    for s in sorted(stats, key=lambda s: s.id):
        base = key = _base_key(s)
        i = 0
        while key in used:
            i += 1
            key = base + (chr(96 + i) if i <= 26 else str(i))
        used.add(key)
        out[s.id] = key
    return out


@dataclass
class Paper:
    stat: PubStat
    key: str
    number: int
    in_report: bool = True  # (else cited, but without the report's tags)
    off_period: bool = False  # (with the tags, but its year outside the period's)


def in_years(s: PubStat, years: tuple[int | None, int | None]) -> bool:
    start, end = years
    return (start is None or (s.year or 0) >= start) and (end is None or (s.year or 9999) <= end)


def report_papers(
    stats: list[PubStat],
    keys: dict[int, str],
    tag_ids: list[int],
    period_id: int | None,
    years: tuple[int | None, int | None] = (None, None),
    primary: str | None = None,
) -> list[Paper]:
    """The papers to discuss, numbered: those with one of the tags (else those of the
    period's ``years``, not hidden, listed by the ``primary`` source if any), those of other
    years ``off_period``. Numbers: those of a
    tag put from a list (the first such tag), else by year (latest first), then title."""
    if tag_ids:
        rows = tagged(stats, tag_ids, period_id)
    else:
        rows = [
            s
            for s in stats
            if not s.hidden and in_years(s, years) and (primary is None or primary in s.sources)
        ]
    rows.sort(key=lambda r: (-(r.year or 0), (r.title or "").lower()))
    listed = next(
        (t for t in tag_ids if any(r.number_of(t, period_id) is not None for r in rows)), None
    )
    numbers: dict[int, int] = {}
    if listed is not None:
        for r in rows:
            if (n := r.number_of(listed, period_id)) is not None:
                numbers[r.id] = n
        rows.sort(key=lambda r: (r.id not in numbers, numbers.get(r.id, 0)))
    nxt = max(numbers.values(), default=0) + 1
    out = []
    for r in rows:
        if r.id not in numbers:
            numbers[r.id], nxt = nxt, nxt + 1
        out.append(Paper(r, keys[r.id], numbers[r.id], off_period=not in_years(r, years)))
    return out


# ---- Substitution ---------------------------------------------------------------------------


@dataclass
class Context:
    """What citations are resolved against."""

    papers: list[Paper]  # the report's
    stats: list[PubStat]  # every paper of the person
    keys: dict[int, str]
    tags: dict[int, str]  # tag names (id -> name)
    hide_tags: set[int] = field(default_factory=set)  # (the report's: not repeated)
    period_id: int | None = None
    number_format: str = NUMBER_FORMAT

    def __post_init__(self) -> None:
        self.by_key = {p.key: p for p in self.papers}
        self._stat_by_key = {self.keys[s.id]: s for s in self.stats if s.id in self.keys}
        self._next = max((p.number for p in self.papers), default=0) + 1

    def paper(self, key: str) -> Paper | None:
        """The paper of a key; one outside the report gets the next number."""
        if (p := self.by_key.get(key)) is None and (s := self._stat_by_key.get(key)):
            p = self.by_key[key] = Paper(s, key, self._next, in_report=False)
            self._next += 1
        return p

    def number(self, p: Paper) -> str:
        return (self.number_format or NUMBER_FORMAT).replace("{n}", str(p.number))

    def hashtags(self, p: Paper) -> str:
        """Its tags (but the report's)."""
        names = sorted(
            (
                self.tags[t]
                for t in p.stat.tags_in(self.period_id)
                if t in self.tags and t not in self.hide_tags
            ),
            key=str.lower,
        )
        return " ".join(hashtag(n) for n in names)

    def notes(self, p: Paper, indent: int = 0) -> str:
        """Its notes (and those within the period), as paragraphs: the lines but the first
        indented, to stay within the list item the citation is in."""
        s = p.stat
        texts = [s.note] if s.note else []
        if self.period_id and (pn := s.period_notes.get(self.period_id)):
            texts.append(pn)
        pad = " " * indent
        lines = "\n\n".join(t.strip() for t in texts).splitlines()
        return "\n".join(lines[:1] + [pad + ln if ln.strip() else "" for ln in lines[1:]])

    def fields(self, p: Paper, attrs: Attrs, indent: int = 0) -> str:
        """A template's fields replaced (``{.short-venue (.year)}`` → ``EMNLP (2026)``)."""
        s = p.stat
        return attrs.fill(
            {
                "number": self.number(p),
                "index": str(p.number),
                "title": s.title or "",
                "venue": s.venue or "",
                "short-venue": s.venue_short or s.venue or "",
                "year": str(s.year or ""),
                "tags": self.hashtags(p),
                "notes": self.notes(p, indent),
            }
        )

    def entry(self, p: Paper, *, notes: bool = False, tags: bool = False, indent: int = 0) -> str:
        s = p.stat
        about = [
            f"**{s.title or _('(untitled)')}**",
            f"*{s.venue}*" if s.venue else None,
            str(s.year) if s.year else None,
            s.category.label,
            (self.hashtags(p) or None) if tags else None,
        ]
        text = f"{self.number(p)} " + " · ".join(a for a in about if a)
        if notes and (n := self.notes(p, indent)):
            text += "\n\n" + " " * indent + n
        return text


@dataclass
class Rendered:
    text: str
    cited: Counter = field(default_factory=Counter)  # key -> times
    unknown: list[str] = field(default_factory=list)


# ---- Attributes: classes ({.notes .tags}) or a template ({.short-venue (.year)}) ---------

FIELDS = {"number", "index", "title", "venue", "short-venue", "year"}

_ATTRS = Lark(
    r"""
    start: item*
    ?item: CLASS -> cls | "(" item* ")" -> group | TEXT -> text
    CLASS: /\.[A-Za-z][\w-]*/
    TEXT: /([^.()]|\.(?![A-Za-z]))+/
    """,
    parser="lalr",
)


class _Attrs(Transformer):
    """The parse tree → ("cls", name) | ("text", text) | ("group", items)."""

    def start(self, items):
        return items

    def cls(self, items):
        return ("cls", str(items[0])[1:])

    def text(self, items):
        return ("text", str(items[0]))

    def group(self, items):
        return ("group", items)


@dataclass
class Attrs:
    items: list[tuple]

    @property
    def classes(self) -> set[str]:
        def walk(items):
            for kind, v in items:
                if kind == "cls":
                    yield v
                elif kind == "group":
                    yield from walk(v)

        return set(walk(self.items))

    @property
    def template(self) -> bool:
        return bool(self.classes & FIELDS)

    def fill(self, values: dict[str, str]) -> str:
        """The fields replaced, the other classes dropped, and a group left without any
        value (``(.year)`` without a year) too, as the separator before a last field
        without a value (``: .notes`` without notes)."""

        def walk(items) -> tuple[str, bool]:  # (text, some field had a value)
            out, filled = [], False
            for kind, v in items:
                if kind == "text":
                    out.append(re.sub(r"\s+", " ", v))
                elif kind == "cls":
                    out.append(values.get(v, ""))
                    filled |= bool(values.get(v))
                else:
                    inner, ok = walk(v)
                    if ok:
                        out.append(f"({inner})")
                    filled |= ok
            return "".join(out), filled

        text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", walk(self.items)[0]).strip()
        last = next((x for x in reversed(self.items) if x[0] != "text" or x[1].strip()), None)
        if last and last[0] == "cls" and not values.get(last[1]):
            text = text.rstrip(" :;,·–-")
        return text


@lru_cache(maxsize=256)
def parse_attrs(attrs: str) -> Attrs | None:
    """The attributes of a citation (``None``: not well formed, e.g. a ``(`` not closed)."""
    try:
        return Attrs(_Attrs().transform(_ATTRS.parse(attrs)))
    except LarkError:
        return None


def render(text: str, ctx: Context) -> Rendered:
    """The report with its citations substituted (unknown keys left as they are)."""
    out = Rendered("")
    unknown: dict[str, None] = {}

    def resolve(key: str) -> Paper | None:
        p = ctx.paper(key)
        if p is None:
            unknown.setdefault(key)
        else:
            out.cited[key] += 1
        return p

    def indent_at(src: str, pos: int) -> int:
        line = src[src.rfind("\n", 0, pos) + 1 : pos]
        return len(_LIST_ITEM.match(line).group(0)) if line.strip() else len(line)

    def bracket(src: str, m: re.Match) -> str:
        attrs = parse_attrs(m.group("attrs") or "")
        if attrs is None:
            return m.group(0)
        cls = attrs.classes
        full = bool(cls & {"full", "notes", "tags"})
        parts = []
        for item in m.group("body").split(";"):
            im = _ITEM.fullmatch(item)
            if im is None:
                parts.append(item.strip())
                continue
            p = resolve(im.group("key"))
            if p is None:
                return m.group(0)
            cited = (
                ctx.fields(p, attrs, indent_at(src, m.start()))
                if attrs.template
                else ctx.entry(
                    p, notes="notes" in cls, tags="tags" in cls, indent=indent_at(src, m.start())
                )
                if full
                else ctx.number(p)
            )
            parts.append(f"{im.group('pre')}{cited}{im.group('post')}".strip())
        return ", ".join(x for x in parts if x)

    def intext(m: re.Match) -> str:
        p = resolve(m.group("key"))
        return ctx.entry(p) if p else m.group(0)

    def prose(src: str) -> str:
        pieces, last = [], 0
        for m in _BRACKET.finditer(src):
            pieces.append(_INTEXT.sub(intext, src[last : m.start()]))
            pieces.append(bracket(src, m))
            last = m.end()
        pieces.append(_INTEXT.sub(intext, src[last:]))
        return "".join(pieces)

    chunks = _CODE.split(text)
    out.text = "".join(c if i % 2 else prose(c) for i, c in enumerate(chunks))
    out.unknown = list(unknown)
    return out


def bibliography(ctx: Context, *, notes: bool = False, tags: bool = False) -> str:
    """The list of the papers (the report's, and the others cited), by number."""
    papers = sorted(ctx.by_key.values(), key=lambda p: p.number)
    return "\n".join("- " + ctx.entry(p, notes=notes, tags=tags, indent=2) for p in papers)


REFERENCE_FORMAT = "[{n}]"


def note_context(stats: list[PubStat], keys: dict[int, str]) -> Context:
    """For a note citing the person's papers (``[@key]``…): numbered as first cited."""
    return Context([], stats, keys, {}, number_format=REFERENCE_FORMAT)


def with_references(text: str, ctx: Context) -> str:
    """The text, its citations substituted, then the papers cited (by number)."""
    out = render(text, ctx).text.rstrip()
    if ctx.by_key:
        out += "\n\n## " + _("References") + "\n\n" + bibliography(ctx)
    return out + "\n"


def uncited(ctx: Context, cited: Counter) -> list[Paper]:
    return [p for p in ctx.papers if not cited.get(p.key)]


# ---- Storage --------------------------------------------------------------------------------


@dataclass
class Saved:
    text: str = ""
    tag_ids: list[int] = field(default_factory=list)
    number_format: str = NUMBER_FORMAT


def get(period_id: int) -> Saved:
    with session_scope() as s:
        r = s.get(Report, period_id)
        if r is None:
            return Saved()
        return Saved(r.text, list(r.tag_ids or []), r.number_format or NUMBER_FORMAT)


def save(period_id: int, **values) -> None:
    """Save some of the report's fields (text, tag_ids, number_format)."""
    with session_scope() as s:
        r = s.get(Report, period_id)
        if r is None:
            r = Report(period_id=period_id)
            s.add(r)
        for k, v in values.items():
            setattr(r, k, v)


def signature(person_id: int) -> str:
    """Changes when the person's papers are edited (titles, notes, tags, flags...): the
    report is then updated."""
    pubs = select(Publication.id).where(Publication.person_id == person_id)
    h = hashlib.sha1()
    with session_scope() as s:
        for q in (
            select(Publication.__table__)
            .where(Publication.person_id == person_id)
            .order_by(Publication.id),
            select(PublicationTag.__table__).where(PublicationTag.publication_id.in_(pubs)),
            select(PeriodTag.__table__).where(PeriodTag.publication_id.in_(pubs)),
            select(PeriodNote.__table__).where(PeriodNote.publication_id.in_(pubs)),
            select(PublicationFlag.__table__).where(PublicationFlag.publication_id.in_(pubs)),
        ):
            for row in sorted(map(repr, s.execute(q).all())):
                h.update(row.encode())
    return h.hexdigest()


# ---- Citation templates (Settings → Report templates) ---------------------------------------

TEMPLATES_KEY = "report_templates"  # an AppSetting


@dataclass
class Template:
    label: str
    attrs: str  # after [@key], e.g. "{.notes}" ("": the number)


@dataclass
class Templates:
    items: list[Template]
    default: str  # the attrs of the one a paper is cited with when inserted

    def cite(self, key: str, attrs: str | None = None) -> str:
        return f"[@{key}]{self.default if attrs is None else attrs}"


def default_templates() -> Templates:
    return Templates(
        [
            Template("Number, venue (year)", "{**#.index** (.short-venue .year)}"),
            Template("… and notes", "{**#.index** (.short-venue .year): .notes}"),
            Template("Number", ""),
            Template("Title, venue…", "{.full}"),
            Template("Title, venue… and notes", "{.notes}"),
            Template("… and tags", "{.tags}"),
            Template("… notes and tags", "{.notes .tags}"),
        ],
        "{**#.index** (.short-venue .year)}",
    )


def check_template(attrs: str) -> str | None:
    """Why a template cannot be used (else None)."""
    attrs = attrs.strip()
    if not attrs:
        return None
    if not (attrs.startswith("{") and attrs.endswith("}")) or parse_attrs(attrs[1:-1]) is None:
        return _("Expected {…} with balanced parentheses, e.g. {.short-venue (.year)}")
    return None


def load_templates() -> Templates:
    with session_scope() as s:
        row = s.get(AppSetting, TEMPLATES_KEY)
        saved = dict(row.value or {}) if row else {}
    if not saved.get("items"):
        return default_templates()
    items = [Template(t.get("label", ""), t.get("attrs", "")) for t in saved["items"]]
    default = saved.get("default", "")
    if all(t.attrs != default for t in items):
        default = items[0].attrs
    return Templates(items, default)


def save_templates(t: Templates) -> None:
    for x in t.items:
        if err := check_template(x.attrs):
            raise ValueError(f"{x.label or x.attrs}: {err}")
    if all(x.attrs != t.default for x in t.items):
        t.default = t.items[0].attrs if t.items else ""
    with session_scope() as s:
        s.merge(AppSetting(key=TEMPLATES_KEY, value=asdict(t)))
