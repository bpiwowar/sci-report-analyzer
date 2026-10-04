"""Citing a person's papers in Markdown (the notes of a folder, of a document…) with Pandoc's
syntax, substituted when shown or copied.

- ``[@key]`` (or ``[@a; @b]``, ``[@a, @b]``, ``[see @a, p. 3]``): the paper's number
  (``**#6**``, see ``number_format``; a listed paper's, ``listed_format``); several keys
  with a template (below) each cited with it, also without brackets: ``@a, @b{.notes}``;
- ``@key``, or ``[@key]{.full}``: its number, title, venue, year and category;
- ``[@key]{.notes}``: that, then its notes; ``[@key]{.tags}``: with its tags (#tags), and
  ``[@key]{.notes .tags}`` both;
- ``[@key]{.short-venue (.year)}``: a template, its fields (``.number``, ``.index``: the
  bare number, ``.title``, ``.venue``, ``.short-venue``: the acronym, else the venue,
  ``.year``, ``.tags``, ``.notes``) replaced and the rest kept: ``EMNLP (2026)``,
  ``{#.index}``: ``#2``, ``{**#.index** (.short-venue .year): .notes}``;
- ``[@key]{.starred}``: a named template (Settings → Citation templates, or the folder's),
  also usable within another one (``{.starred: .notes}``);
- ``[]{.publications}``, ``[]{.excerpts}`` (alone on their line): a block (see
  ``Context.blocks``), e.g. the summary of the period's publications, its excerpts by
  category (their headings below that of the block).

Within a folder, the papers with its numbered tag (see ``Numbering``) are numbered as listed
(else by year), and the other papers cited apart, each from 1, as first cited: the former
are the papers to discuss, each to be cited at least once (without a numbered tag, those of
the period's years, every paper numbered as first cited).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from lark import Lark, Transformer
from lark.exceptions import LarkError

from .db.models import AppSetting, Folder, FolderSettings, Period
from .db.session import session_scope
from .i18n import _
from .pubview import PubStat, hashtag, saved_summary, tagged

NUMBER_FORMAT = "**#{index}**"
REFERENCE_FORMAT = "[{index}]"  # (in the notes: by default)

_KEY = r"\w+(?:[:.#$%&+?<>~/-]\w+)*"
# A bracketed citation, then its classes (Pandoc's bracketed span): [see @a, p. 3; @b]{.notes}
_BRACKET = re.compile(
    r"\[(?P<body>[^\[\]]*?(?<![\w@])-?@" + _KEY + r"[^\[\]]*)\](?:\{(?P<attrs>[^{}\n]*)\})?"
)
# Without brackets, keys followed by their classes (``@a, @b{.notes}``): as [@a, @b]{.notes}
_BARE = re.compile(
    r"(?<![\w@\[])(?P<body>@" + _KEY + r"(?:[ \t]*,[ \t]*@" + _KEY + r")*)"
    r"\{(?P<attrs>[^{}\n]*)\}"
)
_CITATION = re.compile(f"{_BRACKET.pattern}|{_BARE.pattern.replace('?P<', '?P<bare_')}")
_ITEM = re.compile(r"(?P<pre>.*?)(?<![\w@])-?@(?P<key>" + _KEY + r")(?P<post>.*)", re.S)
# The items of a citation: split at ";", and at "," before a key ([@a, @b], not [@a, p. 3]).
_SEPARATOR = re.compile(r";|,(?=\s*-?@" + _KEY + r")")
_INTEXT = re.compile(r"(?<![\w@\[])@(?P<key>" + _KEY + r")")
# Code (fenced blocks, spans) is left as is.
_CODE = re.compile(r"(```.*?(?:```|$)|`[^`\n]*`)", re.S)
_LIST_ITEM = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?")
# A block, alone on its line: []{.publications}
_BLOCK = re.compile(r"^[ \t]*\[\]\{\.(?P<name>[A-Za-z][\w-]*)\}[ \t]*$", re.M)
_HEADING = re.compile(r"^(#{1,6})[ \t]", re.M)

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
    in_report: bool = True  # (else cited, but not among the papers to discuss)
    off_period: bool = False  # (with the tags, but its year outside the period's)


def in_years(s: PubStat, years: tuple[int | None, int | None]) -> bool:
    start, end = years
    return (start is None or (s.year or 0) >= start) and (end is None or (s.year or 9999) <= end)


def papers_to_discuss(
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


class TemplateCycle(ValueError):
    """A template using itself (through others): ``path``, the names from the first one."""

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        super().__init__(_("Template cycle: {path}").format(path=" → ".join(f".{n}" for n in path)))


@dataclass
class Context:
    """What citations are resolved against."""

    papers: list[Paper]  # the numbered ones (e.g. a folder's papers to discuss)
    stats: list[PubStat]  # every paper of the person
    keys: dict[int, str]
    tags: dict[int, str]  # tag names (id -> name)
    hide_tags: set[int] = field(default_factory=set)  # (the numbered tag: not repeated)
    period_id: int | None = None
    number_format: str = NUMBER_FORMAT
    # That of the numbered papers, the others numbered apart (none: after them, as number_format).
    listed_format: str | None = None
    templates: dict[str, str] = field(default_factory=dict)  # named ones: name -> {attrs}
    # The papers each to be cited (by default, the numbered ones; see citation_status).
    discuss: list[Paper] | None = None
    # The blocks ([]{.name} on its line): name -> its Markdown, given the level of its
    # headings (that below the heading the block is under).
    blocks: dict[str, Callable[[int], str]] = field(default_factory=dict)
    numbered_tag: int | None = None  # (the tag whose numbers the papers have, if any)

    def __post_init__(self) -> None:
        self.by_key = {p.key: p for p in self.papers}
        self._stat_by_key = {self.keys[s.id]: s for s in self.stats if s.id in self.keys}
        apart = self.listed_format is not None
        self._next = 1 if apart else max((p.number for p in self.papers), default=0) + 1
        if self.discuss is None:
            self.discuss = list(self.papers)

    def paper(self, key: str) -> Paper | None:
        """The paper of a key; one outside the numbered ones gets the next number."""
        if (p := self.by_key.get(key)) is None and (s := self._stat_by_key.get(key)):
            p = self.by_key[key] = Paper(s, key, self._next, in_report=False)
            self._next += 1
        return p

    def number(self, p: Paper) -> str:
        """Its number, formatted (``{index}``, or ``{n}``: the number): as a listed paper
        (one of the numbered ones), or as the others."""
        listed = p.in_report and self.listed_format
        fmt = (self.listed_format if listed else self.number_format) or NUMBER_FORMAT
        return fmt.replace("{index}", str(p.number)).replace("{n}", str(p.number))

    def hashtags(self, p: Paper) -> str:
        """Its tags (but the hidden ones)."""
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

    def is_template(self, attrs: Attrs) -> bool:
        """Fields (or named templates) to fill, rather than classes (``{.notes}``…)."""
        return bool(attrs.classes & (FIELDS | set(self.templates)))

    def cite(self, p: Paper, attrs: Attrs, indent: int = 0, using: tuple[str, ...] = ()) -> str:
        """The paper cited with ``attrs`` (``using``: the named templates being expanded)."""
        cls = attrs.classes
        if self.is_template(attrs):
            return self.fields(p, attrs, indent, using)
        if cls & {"full", "notes", "tags"}:
            return self.entry(p, notes="notes" in cls, tags="tags" in cls, indent=indent)
        return self.number(p)

    def fields(self, p: Paper, attrs: Attrs, indent: int = 0, using: tuple[str, ...] = ()) -> str:
        """A template's fields replaced (``{.short-venue (.year)}`` → ``EMNLP (2026)``), and
        its named templates expanded (a cycle: ``TemplateCycle``)."""
        s = p.stat
        values = {
            "number": self.number(p),
            "index": str(p.number),
            "title": s.title or "",
            "venue": s.venue or "",
            "short-venue": s.venue_short or s.venue or "",
            "year": str(s.year or ""),
            "tags": self.hashtags(p),
            "notes": self.notes(p, indent),
        }
        for name in sorted((attrs.classes & set(self.templates)) - set(values)):
            if name in using:
                raise TemplateCycle((*using[using.index(name) :], name))
            sub = parse_attrs(_inner(self.templates[name]))
            values[name] = "" if sub is None else self.cite(p, sub, indent, (*using, name))
        return attrs.fill(values)

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
    errors: list[str] = field(default_factory=list)  # (e.g. a template cycle)


# ---- Attributes: classes ({.notes .tags}) or a template ({.short-venue (.year)}) ---------

FIELDS = {"number", "index", "title", "venue", "short-venue", "year"}
# (not a template's name: they have their meaning)
RESERVED = FIELDS | {"full", "notes", "tags"}

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


def _inner(attrs: str) -> str:
    """A template without its braces (``{.year}`` → ``.year``)."""
    attrs = attrs.strip()
    return attrs[1:-1] if attrs.startswith("{") and attrs.endswith("}") else attrs


def render(text: str, ctx: Context) -> Rendered:
    """The text with its citations substituted (unknown keys left as they are; a citation
    with a template cycle too, followed by the error)."""
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
        bare = m.group("body") is None  # (@a, @b{.notes})
        body, attrs = m.group("bare_body", "bare_attrs") if bare else m.group("body", "attrs")
        attrs = parse_attrs(attrs or "")
        if attrs is None:
            return _INTEXT.sub(intext, m.group(0)) if bare else m.group(0)
        parts = []
        for item in _SEPARATOR.split(body):
            im = _ITEM.fullmatch(item)
            if im is None:
                parts.append(item.strip())
                continue
            p = resolve(im.group("key"))
            if p is None:
                return m.group(0)
            try:
                cited = ctx.cite(p, attrs, indent_at(src, m.start()))
            except TemplateCycle as e:
                out.errors.append(str(e))
                return f"{m.group(0)} (⚠ {e})"
            parts.append(f"{im.group('pre')}{cited}{im.group('post')}".strip())
        return ", ".join(x for x in parts if x)

    def intext(m: re.Match) -> str:
        p = resolve(m.group("key"))
        return ctx.entry(p) if p else m.group(0)

    def prose(src: str) -> str:
        pieces, last = [], 0
        for m in _CITATION.finditer(src):
            pieces.append(_INTEXT.sub(intext, src[last : m.start()]))
            pieces.append(bracket(src, m))
            last = m.end()
        pieces.append(_INTEXT.sub(intext, src[last:]))
        return "".join(pieces)

    # The blocks: their Markdown (not cited from) put back in place of a mark once cited.
    made: list[str] = []

    def blocks(src: str, before: str) -> str:
        def block(m: re.Match) -> str:
            make = ctx.blocks.get(m.group("name"))
            if make is None:
                return m.group(0)
            head = _HEADING.findall(before + src[: m.start()])
            made.append(make(min(len(head[-1]) + 1, 6) if head else 2).rstrip("\n"))
            return f"\0{len(made) - 1}\0"

        return _BLOCK.sub(block, src) if ctx.blocks else src

    chunks = _CODE.split(text)
    pieces = [c if i % 2 else prose(blocks(c, "".join(chunks[:i]))) for i, c in enumerate(chunks)]
    out.text = re.sub(r"\0(\d+)\0", lambda m: made[int(m.group(1))], "".join(pieces))
    out.unknown = list(unknown)
    out.errors = list(dict.fromkeys(out.errors))
    return out


def bibliography(ctx: Context, *, notes: bool = False, tags: bool = False) -> str:
    """The list of the papers (the numbered ones, then the others cited), by number."""
    papers = sorted(ctx.by_key.values(), key=lambda p: (not p.in_report, p.number))
    return "\n".join("- " + ctx.entry(p, notes=notes, tags=tags, indent=2) for p in papers)


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


# ---- A folder's citations: its numbering, and the status of its papers ---------------------


@dataclass
class Numbering:
    """How a folder's notes number papers: those with ``tag_id`` (within the person's
    period), as listed (else by year), and the others apart (from 1) as first cited;
    ``format``: how a number is written (``{index}``: the number), ``listed_format``: that
    of the papers with the tag. No tag: every paper as first cited."""

    tag_id: int | None = None
    format: str = REFERENCE_FORMAT
    listed_format: str = NUMBER_FORMAT


def _citations(folder_id: int | None) -> dict:
    """How a folder's notes cite papers: its settings' (see folders.settings_id)."""
    from . import folders

    if not folder_id:
        return {}
    with session_scope() as s:
        if s.get(Folder, folder_id) is None:
            return {}
        return dict(s.get(FolderSettings, folders.settings_id(s, folder_id)).citations or {})


def _save_citations(folder_id: int, **values) -> None:
    from . import folders

    with session_scope() as s:
        if s.get(Folder, folder_id) is not None:
            c = s.get(FolderSettings, folders.settings_id(s, folder_id))
            c.citations = {**(c.citations or {}), **values}


def numbering(folder_id: int | None) -> Numbering:
    c = _citations(folder_id)
    return Numbering(
        c.get("tag_id"),
        c.get("format") or REFERENCE_FORMAT,
        c.get("listed_format") or NUMBER_FORMAT,
    )


def save_numbering(folder_id: int, n: Numbering) -> None:
    _save_citations(
        folder_id,
        tag_id=n.tag_id,
        format=n.format.strip() or REFERENCE_FORMAT,
        listed_format=n.listed_format.strip() or NUMBER_FORMAT,
    )


def skeleton(folder_id: int | None) -> str:
    """The starting text of a person's notes in the folder (while empty), e.g. its headings
    and blocks."""
    return _citations(folder_id).get("skeleton") or ""


def save_skeleton(folder_id: int, text: str) -> None:
    _save_citations(folder_id, skeleton=text.strip() and text.rstrip() + "\n")


def folder_context(stats: list[PubStat], period_id: int | None) -> Context:
    """For the notes of the folder of a period (the person's in it): its numbering and its
    templates; the papers to discuss, those with its numbered tag (else those of the
    period's years)."""
    from . import annotations, source_settings

    keys = citation_keys(stats)
    with session_scope() as s:
        period = s.get(Period, period_id) if period_id else None
        found = (
            (period.person_id, period.folder_id, (period.start_year, period.end_year))
            if period is not None
            else None
        )
    if found is None:
        return note_context(stats, keys)
    person_id, folder_id, years = found

    def publications(_level: int) -> str:
        return "\n".join(saved_summary([s for s in stats if not s.hidden and in_years(s, years)]))

    def excerpts(level: int) -> str:
        from . import categories

        return categories.markdown(folder_id, period_id, level=level)

    n = numbering(folder_id)
    names = {t.id: t.name for t in annotations.all_tags()}
    tag = n.tag_id if n.tag_id in names else None
    primary = source_settings.primary_for(person_id, period_id)
    papers = papers_to_discuss(stats, keys, [tag], period_id, years) if tag else []
    discuss = papers if tag else papers_to_discuss(stats, keys, [], period_id, years, primary)
    return Context(
        papers,
        stats,
        keys,
        names,
        hide_tags={tag} if tag else set(),
        period_id=period_id,
        number_format=n.format,
        listed_format=n.listed_format if tag else None,
        templates=load_templates(folder_id).names,
        discuss=discuss,
        blocks={"publications": publications, "excerpts": excerpts},
        numbered_tag=tag,
    )


@dataclass
class Status:
    """The papers to discuss, cited or not."""

    discuss: list[Paper]  # (of the period's years)
    missing: list[Paper]  # of those, not cited
    off: list[Paper]  # cited, to discuss but not of the period's years
    outside: list[Paper]  # cited, not to discuss

    @property
    def colour(self) -> str:
        if self.missing:
            return "negative"
        if self.off:
            return "warning"
        return "positive" if self.discuss else "grey"


def citation_status(ctx: Context, cited: Counter) -> Status:
    """Which papers to discuss the text cites (``cited``: as rendered)."""
    keys = {p.key for p in ctx.discuss}
    inside = [p for p in ctx.discuss if not p.off_period]
    return Status(
        inside,
        [p for p in inside if not cited.get(p.key)],
        [p for p in ctx.discuss if p.off_period and cited.get(p.key)],
        [p for k in cited if k not in keys and (p := ctx.by_key.get(k)) is not None],
    )


# ---- Citation templates (Settings → Citation templates; a folder's own) --------------------

TEMPLATES_KEY = "report_templates"  # an AppSetting
_NAME = re.compile(r"[A-Za-z][\w-]*")


@dataclass
class Template:
    label: str
    attrs: str  # after [@key], e.g. "{.notes}" ("": the number)
    name: str = ""  # (if any: usable as .name, e.g. [@key]{.name}, and within others)


@dataclass
class Templates:
    items: list[Template]
    default: str  # the attrs of the one a paper is cited with when inserted

    def cite(self, key: str, attrs: str | None = None) -> str:
        return f"[@{key}]{self.default if attrs is None else attrs}"

    @property
    def names(self) -> dict[str, str]:
        """The named ones: name -> attrs."""
        return {t.name: t.attrs for t in self.items if t.name}


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


def check_name(name: str) -> str | None:
    """Why a template's name cannot be used (else None; "": no name)."""
    name = name.strip().removeprefix(".")
    if not name:
        return None
    if not _NAME.fullmatch(name):
        return _("A letter, then letters, digits, - or _ (e.g. starred)")
    if name in RESERVED:
        return _("Already a field or a class: .{name}").format(name=name)
    return None


def template_cycle(templates: dict[str, str]) -> tuple[str, ...] | None:
    """A cycle among named templates (name -> attrs), if any: its names, from the first."""

    def uses(name: str) -> set[str]:
        a = parse_attrs(_inner(templates[name]))
        return (a.classes & set(templates)) if a else set()

    def walk(name: str, path: tuple[str, ...]) -> tuple[str, ...] | None:
        if name in path:
            return (*path[path.index(name) :], name)
        for other in sorted(uses(name)):
            if found := walk(other, (*path, name)):
                return found
        return None

    return next((c for n in sorted(templates) if (c := walk(n, ()))), None)


def check_templates(items: list[Template], over: dict[str, str] | None = None) -> str | None:
    """Why templates cannot be saved (else None): a bad one, a name twice, a cycle (with
    ``over``: the named ones they are used with, e.g. the general ones for a folder's)."""
    seen: set[str] = set()
    for t in items:
        if err := check_template(t.attrs) or check_name(t.name):
            return f"{t.label or t.name or t.attrs}: {err}"
        if t.name and t.name in seen:
            return _("Two templates named .{name}").format(name=t.name)
        seen.add(t.name)
    named = {**(over or {}), **{t.name: t.attrs for t in items if t.name}}
    if cycle := template_cycle(named):
        return str(TemplateCycle(cycle))
    return None


def _general() -> Templates:
    with session_scope() as s:
        row = s.get(AppSetting, TEMPLATES_KEY)
        saved = dict(row.value or {}) if row else {}
    if not saved.get("items"):
        return default_templates()
    items = [
        Template(t.get("label", ""), t.get("attrs", ""), t.get("name") or "")
        for t in saved["items"]
    ]
    default = saved.get("default", "")
    if all(t.attrs != default for t in items):
        default = items[0].attrs
    return Templates(items, default)


def folder_templates(folder_id: int | None) -> list[Template]:
    """A folder's own templates (named: each overrides the general one of its name)."""
    return [
        Template(t.get("label") or "", t.get("attrs", ""), t.get("name") or "")
        for t in _citations(folder_id).get("templates") or []
    ]


def load_templates(folder_id: int | None = None) -> Templates:
    """The general templates; with a folder, its own over them (by name)."""
    t = _general()
    for own in folder_templates(folder_id):
        same = next((x for x in t.items if x.name and x.name == own.name), None)
        if same is not None:
            if t.default == same.attrs:
                t.default = own.attrs
            same.attrs = own.attrs
        else:
            t.items.append(Template(own.label or f".{own.name}", own.attrs, own.name))
    return t


def save_templates(t: Templates) -> None:
    for x in t.items:
        x.name = x.name.strip().removeprefix(".")
    if err := check_templates(t.items):
        raise ValueError(err)
    if all(x.attrs != t.default for x in t.items):
        t.default = t.items[0].attrs if t.items else ""
    with session_scope() as s:
        s.merge(AppSetting(key=TEMPLATES_KEY, value=asdict(t)))


def save_folder_templates(folder_id: int, items: list[Template]) -> None:
    """A folder's own templates (each named); refused (``ValueError``) if one is wrong, or
    with a cycle among them and the general ones."""
    for x in items:
        x.name = x.name.strip().removeprefix(".")
        if not x.name:
            raise ValueError(_("A folder's template needs a name"))
    if err := check_templates(items, _general().names):
        raise ValueError(err)
    _save_citations(
        folder_id, templates=[{"name": x.name, "attrs": x.attrs, "label": x.label} for x in items]
    )
