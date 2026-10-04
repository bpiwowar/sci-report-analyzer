"""Categories of a folder (an ordered tree, e.g. a committee's grid: "Research", under it
"Projects"…): those of its settings (shared, see folders.settings_id), and the excerpts of
its people's PDFs filed in them: listed by category, as Markdown for a report."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from . import folders
from .db.models import Category, Excerpt, PeriodDocument, Publication
from .db.session import session_scope
from .i18n import _

# The colours given to new categories, in turn (each category's can be changed); the first
# one also tints the excerpts of a category without any.
PALETTE = ["#ffc107", "#4caf50", "#2196f3", "#e91e63", "#9c27b0", "#ff5722", "#009688", "#795548"]
DEFAULT_COLOUR = PALETTE[0]


def years_label(a: int | None, b: int | None) -> str:
    """Years: "2020–2025", "since 2020", "until 2025" (else "")."""
    if a and b:
        return f"{a}–{b}" if a != b else str(a)
    if a:
        return _("since {year}").format(year=a)
    return _("until {year}").format(year=b) if b else ""


# A year (1900–2099) not within a longer number, a decimal, a date, a DOI…
_YEAR = r"(?<![\w.,/:–—-])((?:19|20)\d\d)"
_SINGLE = re.compile(_YEAR + r"(?!\d|[.,/]\d)")
_RANGE = re.compile(
    _YEAR + r"(\s*[-–—/]\s*|\s+(?:to|until|till|à|au|jusqu'en|jusqu’en)\s+)"
    r"((?:19|20)\d\d|\d\d)(?!\d|[-/.,]\d)",
    re.IGNORECASE,
)
_PAGES = re.compile(r"\b(?:pp?|pages?)\.?\s*$", re.IGNORECASE)


def detect_years(text: str) -> tuple[int, int] | None:
    """The years a passage is about, as (start, end): its first range ("2026-2032",
    "2026–2032", "2026 - 2032", "2026/2027", "2026 to 2032", "de 2026 à 2032"; short:
    "2026-32" is 2026–2032, "1998-03" 1998–2003), else the span of its years ("2026": 2026–2026;
    "in 2019, then 2023": 2019–2023), else None. Years are 1900–2099 (a range's end not
    before its start, a short end only right after a dash or slash); not part of a longer
    number, a decimal, a date ("2026-05-12": 2026 alone) or a DOI, nor page numbers
    ("pp. 2026-2032")."""

    def pages(m: re.Match) -> bool:
        return bool(_PAGES.search(text[: m.start()]))

    for m in _RANGE.finditer(text):
        a, sep, b = int(m[1]), m[2], int(m[3])
        if len(m[3]) == 2:
            if sep not in ("-", "–", "—", "/"):
                continue  # (a short end: "2026-32" only)
            b = a // 100 * 100 + int(m[3])
            if b < a:
                b += 100
        if a <= b < 2100 and not pages(m):
            return a, b
    found = {int(m[1]) for m in _SINGLE.finditer(text) if not pages(m)}
    return (min(found), max(found)) if found else None


# Years ("2026", "2026-32", "2026 to 2032"…: see detect_years) leading a passage (with a
# colon) or ending it (after a colon, a dash, or in brackets).
_EXPR = (
    r"(?:19|20)\d\d(?:(?:\s*[-–—/]\s*|\s+(?:to|until|till|à|au|jusqu'en|jusqu’en)\s+)"
    r"(?:(?:19|20)\d\d|\d\d))?"
)
_LEADING = re.compile(rf"^\s*(?:(?:from|de|du)\s+)?({_EXPR})\s*:\s*", re.IGNORECASE)
_TRAILING = re.compile(
    rf"(?:(?<=[^\d\s])\s*(?::|—|\s[–-])\s*|\s*\()({_EXPR})\)?\s*(\.?)\s*$", re.IGNORECASE
)


def split_years(text: str) -> tuple[tuple[int, int] | None, str]:
    """The years of a passage (see detect_years) and its text without them when they lead
    it, with a colon ("2026-32: Led a project" → "Led a project"), or end it after a colon,
    a dash or in brackets ("Led a project: 2026-32." → "Led a project."; the years leading
    or ending it are its years then); years elsewhere stay in the text."""
    for rx in (_LEADING, _TRAILING):
        if (m := rx.search(text)) and (years := detect_years(m[1])):
            rest = (text[: m.start()] + text[m.end() :]).strip()
            if rx is _TRAILING and m[2] and rest and rest[-1] not in ".!?…":
                rest += "."
            if rest:
                return years, rest
    return detect_years(text), text


@dataclass
class Node:
    id: int
    name: str
    parent_id: int | None
    depth: int
    path: str  # "Research › Projects"
    start_year: int | None
    end_year: int | None
    children: list[Node] = field(default_factory=list)
    influence: bool = False  # (the folder's "rayonnement": see set_influence)
    colour: str = DEFAULT_COLOUR  # (that of its excerpts: their tint on the PDFs)

    @property
    def years(self) -> str:
        """Its years ("2020–2025", "since 2020", "until 2025"), if any."""
        return years_label(self.start_year, self.end_year)


def tree(folder_id: int) -> list[Node]:
    """The folder's categories, in order (depth first: a category, then its subcategories)."""
    with session_scope() as s:
        return _tree(s, folders.settings_id(s, folder_id))


def _tree(s, settings_id: int) -> list[Node]:
    """The categories of some settings, in order (see tree)."""
    rows = list(
        s.scalars(
            select(Category)
            .where(Category.settings_id == settings_id)
            .order_by(Category.position, Category.id)
        )
    )
    kids: dict[int | None, list[Category]] = {}
    for c in rows:
        kids.setdefault(c.parent_id, []).append(c)
    out: list[Node] = []

    def walk(parent: int | None, depth: int, prefix: str) -> list[Node]:
        nodes = []
        for c in kids.get(parent, []):
            path = f"{prefix} › {c.name}" if prefix else c.name
            n = Node(c.id, c.name, c.parent_id, depth, path, c.start_year, c.end_year)
            n.influence = bool(c.influence)
            n.colour = c.colour or DEFAULT_COLOUR
            out.append(n)
            n.children = walk(c.id, depth + 1, path)
            nodes.append(n)
        return nodes

    walk(None, 0, "")
    return out


def next_colour(s, settings_id: int) -> str:
    """The colour of a new category of some settings: the first of the palette none has yet
    (all taken: in turn)."""
    used = list(s.scalars(select(Category.colour).where(Category.settings_id == settings_id)))
    free = [c for c in PALETTE if c not in used]
    return free[0] if free else PALETTE[len(used) % len(PALETTE)]


def add(folder_id: int, name: str, parent_id: int | None = None) -> int:
    """A new category (the last of its siblings, in the next colour); returns its id."""
    with session_scope() as s:
        return _add(s, folders.settings_id(s, folder_id), name.strip() or "Category", parent_id)


def _add(s, settings_id: int, name: str, parent_id: int | None, **values) -> int:
    last = s.scalar(
        select(func.max(Category.position)).where(
            Category.settings_id == settings_id,
            Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
        )
    )
    c = Category(
        settings_id=settings_id,
        parent_id=parent_id,
        name=name,
        position=(last or 0) + 1,
        colour=values.pop("colour", None) or next_colour(s, settings_id),
        **values,
    )
    s.add(c)
    s.flush()
    return c.id


def update(
    cat_id: int, name: str | None = None, start: int | None = None, end: int | None = None
) -> None:
    with session_scope() as s:
        if c := s.get(Category, cat_id):
            if name is not None and name.strip():
                c.name = name.strip()
            c.start_year, c.end_year = start, end


def set_colour(cat_id: int, colour: str | None) -> None:
    """The colour of a category's excerpts (their tint on the PDFs; None: the default)."""
    with session_scope() as s:
        if c := s.get(Category, cat_id):
            c.colour = colour or None


def set_influence(folder_id: int, cat_id: int | None) -> None:
    """The category gathering the excerpts flagged "influence" of the others (none:
    ``None``); at most one per folder."""
    with session_scope() as s:
        sid = folders.settings_id(s, folder_id)
        for c in s.scalars(select(Category).where(Category.settings_id == sid)):
            c.influence = c.id == cat_id


def move(cat_id: int, delta: int) -> None:
    """Move a category up (-1) or down (+1) among its siblings."""
    with session_scope() as s:
        c = s.get(Category, cat_id)
        if c is None:
            return
        siblings = list(
            s.scalars(
                select(Category)
                .where(
                    Category.settings_id == c.settings_id,
                    Category.parent_id.is_(None)
                    if c.parent_id is None
                    else Category.parent_id == c.parent_id,
                )
                .order_by(Category.position, Category.id)
            )
        )
        i = siblings.index(c)
        j = i + delta
        if 0 <= j < len(siblings):
            siblings[i], siblings[j] = siblings[j], siblings[i]
        for k, x in enumerate(siblings, 1):
            x.position = k


def place(cat_id: int, target_id: int, where: str) -> bool:
    """Move a category (with its subcategories) "before" / "after" another one, or "inside"
    it (its last subcategory); never within itself. Returns whether it moved."""
    with session_scope() as s:
        c, t = s.get(Category, cat_id), s.get(Category, target_id)
        if c is None or t is None or c.id == t.id or c.settings_id != t.settings_id:
            return False
        p: Category | None = t
        while p is not None:  # (the target must not be below the category)
            if p.id == c.id:
                return False
            p = s.get(Category, p.parent_id) if p.parent_id else None
        parent = t.id if where == "inside" else t.parent_id
        siblings = [
            x
            for x in s.scalars(
                select(Category)
                .where(
                    Category.settings_id == c.settings_id,
                    Category.parent_id.is_(None)
                    if parent is None
                    else Category.parent_id == parent,
                )
                .order_by(Category.position, Category.id)
            )
            if x.id != c.id
        ]
        if where == "inside":
            siblings.append(c)
        else:
            i = siblings.index(t) + (where == "after")
            siblings.insert(i, c)
        c.parent_id = parent
        for k, x in enumerate(siblings, 1):
            x.position = k
        return True


def subtree(cat_id: int) -> list[int]:
    """A category and those below it."""
    with session_scope() as s:
        c = s.get(Category, cat_id)
        if c is None:
            return []
        node = next(n for n in _tree(s, c.settings_id) if n.id == cat_id)
    out: list[int] = []

    def walk(n: Node) -> None:
        out.append(n.id)
        for c in n.children:
            walk(c)

    walk(node)
    return out


def excerpt_count(cat_id: int) -> int:
    """The excerpts filed in a category or below it (of all the people of the folders using
    it)."""
    with session_scope() as s:
        return s.scalar(
            select(func.count(Excerpt.id)).where(Excerpt.category_id.in_(subtree(cat_id)))
        )


def move_excerpts(cat_id: int, target_id: int) -> int:
    """Move the excerpts of a category and of those below it (with their groups) to another
    one, after its own; returns how many (none: the target is within it)."""
    ids = subtree(cat_id)
    if target_id in ids:
        return 0
    with session_scope() as s:
        moved = list(
            s.scalars(
                select(Excerpt)
                .where(Excerpt.category_id.in_(ids))
                .order_by(Excerpt.position, Excerpt.id)
            )
        )
        last: dict[int, int] = {}
        for e in moved:
            if e.period_id not in last:
                last[e.period_id] = _last(s, target_id, e.period_id)
            last[e.period_id] += 1
            e.category_id, e.position = target_id, last[e.period_id]
        return len(moved)


def delete(cat_id: int) -> bool:
    """Delete a category, with its subcategories, if no excerpt is filed there (else
    False: to move them first)."""
    if excerpt_count(cat_id):
        return False
    with session_scope() as s:
        if c := s.get(Category, cat_id):
            s.delete(c)
    return True


def copy_tree(source_folder: int, folder_id: int) -> int:
    """Add the categories of another folder (without their excerpts); returns how many."""
    with session_scope() as s:
        return copy_settings(
            s, folders.settings_id(s, source_folder), folders.settings_id(s, folder_id)
        )


def copy_settings(s, source_id: int, settings_id: int) -> int:
    """Add the categories of some settings to others (without their excerpts); returns how
    many."""
    nodes = _tree(s, source_id)
    ids: dict[int, int] = {}
    for n in nodes:
        c = Category(
            settings_id=settings_id,
            parent_id=ids.get(n.parent_id) if n.parent_id else None,
            name=n.name,
            position=len(ids) + 1,
            start_year=n.start_year,
            end_year=n.end_year,
            influence=n.influence,
            colour=n.colour,
        )
        s.add(c)
        s.flush()
        ids[n.id] = c.id
    return len(nodes)


def _places(nodes: list[Node]) -> dict[int, tuple]:
    """Each category's place by its names: its path, each name numbered among its siblings
    of the same name (so that the copies of categories are found back)."""
    out: dict[int, tuple] = {}
    seen: dict[tuple, int] = {}
    for n in nodes:  # (parents first)
        up = out.get(n.parent_id, ()) if n.parent_id else ()
        k = seen[(up, n.name)] = seen.get((up, n.name), -1) + 1
        out[n.id] = (*up, (n.name, k))
    return out


def remap(s, period_ids: list[int], source_id: int, settings_id: int) -> int:
    """File the excerpts of some people (their periods) filed in categories of some settings
    in the categories of others, at the same place (same names; see _places); those missing
    are added (with their colour and years). Returns how many were added."""
    if not period_ids or source_id == settings_id:
        return 0
    old, new = _tree(s, source_id), _tree(s, settings_id)
    used = set(
        s.scalars(
            select(Excerpt.category_id).where(
                Excerpt.period_id.in_(period_ids),
                Excerpt.category_id.in_([n.id for n in old]),
            )
        )
    )
    if not used:
        return 0
    by_id = {n.id: n for n in old}
    needed: set[int] = set()
    for i in used:  # (and the categories above them)
        while i is not None and i not in needed:
            needed.add(i)
            i = by_id[i].parent_id
    places, there = _places(old), {p: i for i, p in _places(new).items()}
    target: dict[int, int] = {}
    added = 0
    for n in old:  # (parents first)
        if n.id not in needed:
            continue
        if (found := there.get(places[n.id])) is None:
            parent = target.get(n.parent_id) if n.parent_id else None
            found = _add(
                s,
                settings_id,
                n.name,
                parent,
                colour=n.colour,
                start_year=n.start_year,
                end_year=n.end_year,
            )
            there[places[n.id]] = found
            added += 1
        target[n.id] = found
    for e in s.scalars(
        select(Excerpt).where(Excerpt.period_id.in_(period_ids), Excerpt.category_id.in_(used))
    ):
        e.category_id = target[e.category_id]
    s.flush()
    return added


# ---- Excerpts -------------------------------------------------------------------------------


@dataclass
class ExcerptView:
    id: int
    category_id: int
    text: str
    page: int | None
    rects: list
    document_id: int | None
    publication_id: int | None
    source: str  # the document's name, or the paper's title
    created_at: datetime
    start_year: int | None = None
    end_year: int | None = None
    influence: bool = False
    colour: str = DEFAULT_COLOUR  # (its category's)
    group_id: int | None = None  # (merged: the excerpt leading its group)
    members: list[ExcerptView] = field(default_factory=list)  # (leading a group: the others)
    ref_only: bool = False  # (merged as a reference only: its place cited, not its text)
    group_text: str | None = None  # (leading a group: its text, as edited)
    original: str | None = None  # (its text as selected)

    @property
    def years(self) -> str:
        return years_label(self.start_year, self.end_year)

    @property
    def place(self) -> tuple[str, int, float]:
        """Where it starts (to order a group): its PDF, page, then from the top."""
        tops = [r[3] for r in self.rects if len(r) < 5 or r[4] == self.page]
        return (self.source, self.page or 0, -max(tops, default=0))

    @property
    def parts(self) -> list[ExcerptView]:
        """It and the excerpts merged with it, in the order of their PDFs."""
        return sorted([self, *self.members], key=lambda x: x.place)

    @property
    def quoted(self) -> str:
        """The text of the item (leading a group: the group's): as edited, else its quotes
        (but those merged as references only), joined by " […] "."""
        if self.group_text:
            return self.group_text
        return " […] ".join(x.text for x in self.parts if not x.ref_only) or self.text

    @property
    def words(self) -> set[str]:
        """The words of the item (and of the group it leads), to find similar ones."""
        return _words(" ".join([self.group_text or "", *(x.text for x in self.parts)]))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def similar_excerpts(
    period_id: int, text: str, query: str = "", *, threshold: float = 0.5
) -> list[ExcerptView]:
    """The excerpts (leading a group, or alone) that ``text`` might be already: sharing at
    least ``threshold`` of the words of the shorter one (or containing it), most similar
    first; with a ``query``: those having all its words, the most similar first."""
    mine, q = _words(text), query.lower().split()
    flat = " ".join(text.lower().split())
    scored = []
    for e in excerpts(period_id, grouped=True):
        words = e.words
        score = len(mine & words) / max(1, min(len(mine), len(words)))
        if any(flat and flat in " ".join(x.text.lower().split()) for x in e.parts):
            score = max(score, 1.0)
        if q:
            hay = " ".join([e.group_text or "", *(f"{x.text} {x.source}" for x in e.parts)])
            if all(w in hay.lower() for w in q):
                scored.append((score, e))
        elif score >= threshold and mine:
            scored.append((score, e))
    scored.sort(key=lambda p: -p[0])
    return [e for _s, e in scored]


def add_excerpt(
    category_id: int,
    period_id: int,
    text: str,
    page: int | None,
    rects: list,
    *,
    document_id: int | None = None,
    publication_id: int | None = None,
    start: int | None = None,
    end: int | None = None,
    influence: bool = False,
) -> int:
    """File a passage (its years and influence flag: see update_excerpt); one filed there
    already is kept."""
    text = " ".join(text.split())
    with session_scope() as s:
        same = s.scalar(  # (filed there already)
            select(Excerpt.id).where(
                Excerpt.category_id == category_id,
                Excerpt.period_id == period_id,
                Excerpt.document_id.is_(None)
                if document_id is None
                else Excerpt.document_id == document_id,
                Excerpt.publication_id.is_(None)
                if publication_id is None
                else Excerpt.publication_id == publication_id,
                Excerpt.page.is_(None) if page is None else Excerpt.page == page,
                Excerpt.text == text,
            )
        )
        if same is not None:
            return same
        e = Excerpt(
            position=_last(s, category_id, period_id) + 1,
            category_id=category_id,
            period_id=period_id,
            text=text,
            original=text,
            page=page,
            rects=rects or [],
            document_id=document_id,
            publication_id=publication_id,
            start_year=start,
            end_year=end,
            influence=influence,
        )
        s.add(e)
        s.flush()
        return e.id


def excerpts(period_id: int, *, grouped: bool = False) -> list[ExcerptView]:
    """The excerpts of a person in a folder (their period), in their order (as placed, else
    as filed);
    ``grouped``: those leading a group (or alone), the others of the group as ``members``."""
    with session_scope() as s:
        rows = s.execute(
            select(Excerpt, PeriodDocument.name, Publication.title, Category.colour)
            .join(Category, Excerpt.category_id == Category.id)
            .outerjoin(PeriodDocument, Excerpt.document_id == PeriodDocument.id)
            .outerjoin(Publication, Excerpt.publication_id == Publication.id)
            .where(Excerpt.period_id == period_id)
            .order_by(Excerpt.position, Excerpt.id)
        ).all()
        views = [
            ExcerptView(
                e.id,
                e.category_id,
                e.text,
                e.page,
                list(e.rects or []),
                e.document_id,
                e.publication_id,
                doc or title or "(untitled)",
                e.created_at,
                e.start_year,
                e.end_year,
                e.influence,
                colour or DEFAULT_COLOUR,
                e.group_id,
                ref_only=e.ref_only,
                group_text=e.group_text,
                original=e.original or e.text,
            )
            for e, doc, title, colour in rows
        ]
    if not grouped:
        return views
    by_id = {v.id: v for v in views}
    for v in views:
        if v.group_id in by_id:
            by_id[v.group_id].members.append(v)
    return [v for v in views if v.group_id not in by_id]


def tints(period_id: int, document_id: int, names: dict[int, str]) -> list[dict]:
    """The excerpts of a document, to tint on its pages: each part of a group (even one
    cited as a reference only, or under the group's text), in its category's colour (its
    id: to select it, clicked); ``names``: the paths of the categories."""
    return [
        {
            "id": x.id,
            "page": x.page,
            "rects": x.rects,
            "category": names.get(e.category_id, ""),
            "colour": e.colour,
        }
        for e in excerpts(period_id, grouped=True)
        for x in e.parts
        if x.document_id == document_id and x.page
    ]


def _last(s, category_id: int, period_id: int) -> int:
    """The position of the last excerpt of a category (of a person)."""
    return (
        s.scalar(
            select(func.max(Excerpt.position)).where(
                Excerpt.category_id == category_id, Excerpt.period_id == period_id
            )
        )
        or 0
    )


def place_excerpt(excerpt_id: int, target_id: int, where: str) -> None:
    """Put an excerpt (with its group) "before" or "after" another one (in its category)."""
    with session_scope() as s:
        e, t = s.get(Excerpt, excerpt_id), s.get(Excerpt, target_id)
        if e is None or t is None:
            return
        e = s.get(Excerpt, e.group_id) if e.group_id else e
        t = s.get(Excerpt, t.group_id) if t.group_id else t
        if e.id == t.id:
            return
        group = [e, *_members(s, e.id)]
        for x in group:
            x.category_id = t.category_id
        siblings = [
            x
            for x in s.scalars(
                select(Excerpt)
                .where(
                    Excerpt.category_id == t.category_id,
                    Excerpt.period_id == t.period_id,
                    Excerpt.group_id.is_(None),
                )
                .order_by(Excerpt.position, Excerpt.id)
            )
            if x.id != e.id
        ]
        siblings.insert(siblings.index(t) + (where == "after"), e)
        for k, x in enumerate(siblings, 1):
            x.position = k


def _members(s, lead_id: int) -> list[Excerpt]:
    return list(s.scalars(select(Excerpt).where(Excerpt.group_id == lead_id)))


def remove_excerpt(excerpt_id: int) -> None:
    """Remove an excerpt; one leading a group passes the group (and its category, years…)
    to the next one."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            if others := sorted(_members(s, e.id), key=lambda x: x.id):
                lead = others[0]
                lead.group_id = None
                lead.start_year, lead.end_year = e.start_year, e.end_year
                lead.influence = e.influence
                lead.group_text = e.group_text
                for x in others[1:]:
                    x.group_id = lead.id
            s.delete(e)


def update_excerpt(
    excerpt_id: int,
    *,
    text: str | None = None,
    start: int | None = None,
    end: int | None = None,
    influence: bool = False,
) -> None:
    """Its text (unless None or blank), years and "influence" flag."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            if text is not None and text.strip():
                e.text = " ".join(text.split())
            e.start_year, e.end_year, e.influence = start, end, influence


def merge_excerpts(source_id: int, target_id: int, *, ref_only: bool = False) -> str | None:
    """Group an excerpt (with its group, if any) with another one: each keeps its text and
    place (``ref_only``: only its place is cited); the group takes the target group's
    category, years, influence and text. Returns why it cannot be done, if so."""
    with session_scope() as s:
        a, b = s.get(Excerpt, source_id), s.get(Excerpt, target_id)
        if a is None or b is None:
            return _("Nothing to merge")
        lead = s.get(Excerpt, b.group_id) if b.group_id else b
        source = s.get(Excerpt, a.group_id) if a.group_id else a
        if source.id == lead.id:
            return _("Already together")
        source.group_text = None
        for x in [source, *_members(s, source.id)]:
            x.group_id, x.category_id = lead.id, lead.category_id
            x.ref_only = x.ref_only or ref_only
    return None


def set_ref_only(excerpt_id: int, ref_only: bool) -> None:
    """Cite only the place of an excerpt of a group (or its text, too)."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            e.ref_only = ref_only


def set_group_text(excerpt_id: int, text: str | None) -> None:
    """The text of the group an excerpt leads (blank: its quotes)."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            e.group_text = " ".join(text.split()) if text and text.strip() else None


def split_excerpt(excerpt_id: int) -> None:
    """Take an excerpt out of its group (alone, in the same category)."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            e.group_id, e.ref_only = None, False


def move_excerpt(excerpt_id: int, category_id: int) -> None:
    """Move an excerpt (with the others of its group)."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            last = _last(s, category_id, e.period_id)
            for x in [e, *_members(s, e.id)]:
                x.category_id, x.position = category_id, last + 1


def move_paper(session, source_id: int, target_id: int) -> None:
    """The excerpts of a paper's PDF go with it (joined papers)."""
    for e in session.scalars(select(Excerpt).where(Excerpt.publication_id == source_id)):
        e.publication_id = target_id


def cited_documents(period_id: int) -> list[tuple[int, str]]:
    """The documents (id, name) that excerpts of a person in a folder come from, by name."""
    with session_scope() as s:
        rows = s.execute(
            select(PeriodDocument.id, PeriodDocument.name)
            .join(Excerpt, Excerpt.document_id == PeriodDocument.id)
            .where(Excerpt.period_id == period_id)
            .distinct()
            .order_by(PeriodDocument.name)
        ).all()
    return [(i, n) for i, n in rows]


def _markdown_item(e: ExcerptView) -> str:
    """An excerpt as Markdown: its quotes (a group: on one item, as one text if edited; those
    merged as references only: their places), each with its places between Obsidian comments
    (``%% Application, p. 4; p. 12 %%``), then its years."""
    chunks: list[tuple[str | None, list[str]]] = []  # (a quote, its places)
    pending: list[str] = []  # (places cited only, before any quote)
    last = None
    for x in e.parts:
        where = f"p. {x.page}" if x.source == last else x.source
        if x.source != last and x.page:
            where += f", p. {x.page}"
        last = x.source
        if e.group_text or x.ref_only:
            (chunks[-1][1] if chunks else pending).append(where)
        else:
            chunks.append((x.text, [*pending, where]))
            pending = []
    if e.group_text:
        chunks = [(e.group_text, pending)]
    elif pending:
        chunks = [(None, pending)]
    quotes = [(f"{t} " if t else "") + f"%% {'; '.join(places)} %%" for t, places in chunks]
    return " […] ".join(quotes) + (f" — {e.years}" if e.years else "")


def markdown(folder_id: int, period_id: int, *, level: int = 2, nested: bool | None = None) -> str:
    """The excerpts by category (headings; a category's years in its heading), as Markdown
    (unquoted, their places between Obsidian comments: ``%% Application, p. 4; p. 12 %%``),
    with their years. The excerpts with the "influence" flag (left in their categories too)
    are also listed, one item per category (its path in bold, them nested below), in the
    folder's "rayonnement" category (after its own excerpts; see set_influence), else in a
    "Rayonnement" section at the end. ``nested`` (None: the folder's choice, see
    reports.nested_influence): them only there instead, under subsections named after their
    categories (as nested as these, one level below), taken out of their categories. The
    categories with no excerpt (nor below) are left out."""
    if nested is None:
        from . import reports

        nested = reports.nested_influence(folder_id)
    nodes = tree(folder_id)
    by_cat: dict[int, list[ExcerptView]] = {}
    for e in excerpts(period_id, grouped=True):
        by_cat.setdefault(e.category_id, []).append(e)
    gather = next((n for n in nodes if n.influence), None)
    # Those whose flagged excerpts are listed apart (nested: not those below the gathering one).
    apart = [n for n in nodes if n is not gather]
    if nested and gather is not None:
        below: set[int] = set()

        def mark(n: Node) -> None:
            below.add(n.id)
            for c in n.children:
                mark(c)

        mark(gather)
        apart = [n for n in nodes if n.id not in below]
    flagged = {n.id: [e for e in by_cat.get(n.id, []) if e.influence] for n in apart}
    shown = {
        k: [e for e in v if not (nested and e.influence and k in flagged)]
        for k, v in by_cat.items()
    }

    def heading(n: Node, depth: int) -> str:
        years = f" ({n.years})" if n.years else ""
        return f"{'#' * min(depth, 6)} {n.name}{years}\n"

    def counted(n: Node, own: Callable[[Node], int]) -> int:
        return own(n) + sum(counted(c, own) for c in n.children)

    def influence(base: int) -> list[str]:
        """The flagged excerpts: items (by path), or subsections below the ``base`` level."""
        out: list[str] = []
        for n in apart:
            items = [_markdown_item(e) for e in flagged.get(n.id, [])]
            if not nested:
                if items:
                    out += [f"- **{n.path}**", *(f"  - {x}" for x in items)]
            elif counted(n, lambda m: len(flagged.get(m.id, []))):
                out.append(heading(n, base + 1 + n.depth))
                out += [*(f"- {x}" for x in items), *([""] if items else [])]
        return out

    gathered = influence(level + gather.depth) if gather is not None else influence(level)

    def own(n: Node) -> int:
        return len(shown.get(n.id, [])) + (len(gathered) if n is gather else 0)

    out: list[str] = []
    for n in nodes:
        if not counted(n, own):
            continue
        out.append(heading(n, level + n.depth))
        for e in shown.get(n.id, []):  # (a group: its quotes, on one item)
            out.append("- " + _markdown_item(e))
        if shown.get(n.id) and (n is not gather or nested or not gathered):
            out.append("")
        if n is gather:
            out += gathered + ([""] if gathered and not nested else [])
    if gathered and gather is None:
        out += [f"{'#' * min(level, 6)} Rayonnement\n", *gathered]
    return "\n".join(out).strip() + "\n" if out else ""
