"""Categories of a folder (an ordered tree, e.g. a committee's grid: "Research", under it
"Projects"…), and the excerpts of its people's PDFs filed in them: listed by category, as
Markdown for a report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from .db.models import Category, Excerpt, PeriodDocument, Publication
from .db.session import session_scope
from .i18n import _


def years_label(a: int | None, b: int | None) -> str:
    """Years: "2020–2025", "since 2020", "until 2025" (else "")."""
    if a and b:
        return f"{a}–{b}" if a != b else str(a)
    if a:
        return _("since {year}").format(year=a)
    return _("until {year}").format(year=b) if b else ""


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

    @property
    def years(self) -> str:
        """Its years ("2020–2025", "since 2020", "until 2025"), if any."""
        return years_label(self.start_year, self.end_year)


def tree(folder_id: int) -> list[Node]:
    """The folder's categories, in order (depth first: a category, then its subcategories)."""
    with session_scope() as s:
        rows = list(
            s.scalars(
                select(Category)
                .where(Category.folder_id == folder_id)
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
            out.append(n)
            n.children = walk(c.id, depth + 1, path)
            nodes.append(n)
        return nodes

    walk(None, 0, "")
    return out


def add(folder_id: int, name: str, parent_id: int | None = None) -> int:
    """A new category (the last of its siblings); returns its id."""
    with session_scope() as s:
        last = s.scalar(
            select(func.max(Category.position)).where(
                Category.folder_id == folder_id,
                Category.parent_id.is_(None)
                if parent_id is None
                else Category.parent_id == parent_id,
            )
        )
        c = Category(
            folder_id=folder_id,
            parent_id=parent_id,
            name=name.strip() or "Category",
            position=(last or 0) + 1,
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


def set_influence(folder_id: int, cat_id: int | None) -> None:
    """The category gathering the excerpts flagged "influence" of the others (none:
    ``None``); at most one per folder."""
    with session_scope() as s:
        for c in s.scalars(select(Category).where(Category.folder_id == folder_id)):
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
                    Category.folder_id == c.folder_id,
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
        if c is None or t is None or c.id == t.id or c.folder_id != t.folder_id:
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
                    Category.folder_id == c.folder_id,
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
        folder_id = c.folder_id if c else None
    if folder_id is None:
        return []
    node = next(n for n in tree(folder_id) if n.id == cat_id)
    out: list[int] = []

    def walk(n: Node) -> None:
        out.append(n.id)
        for c in n.children:
            walk(c)

    walk(node)
    return out


def excerpt_count(cat_id: int) -> int:
    """The excerpts filed in a category or below it (of all the people of its folder)."""
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
    nodes = tree(source_folder)
    ids: dict[int, int] = {}
    with session_scope() as s:
        for n in nodes:
            c = Category(
                folder_id=folder_id,
                parent_id=ids.get(n.parent_id) if n.parent_id else None,
                name=n.name,
                position=len(ids) + 1,
                start_year=n.start_year,
                end_year=n.end_year,
                influence=n.influence,
            )
            s.add(c)
            s.flush()
            ids[n.id] = c.id
    return len(nodes)


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
    colour: str | None = None
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
    colour: str | None = None,
) -> int:
    """File a passage (its years, influence flag and colour: see update_excerpt); one filed
    there already is kept."""
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
            colour=colour or None,
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
            select(Excerpt, PeriodDocument.name, Publication.title)
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
                e.colour,
                e.group_id,
                ref_only=e.ref_only,
                group_text=e.group_text,
                original=e.original or e.text,
            )
            for e, doc, title in rows
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
    cited as a reference only, or under the group's text), in the group's colour;
    ``names``: the paths of the categories."""
    return [
        {
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
                lead.influence, lead.colour = e.influence, e.colour
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
    colour: str | None = None,
) -> None:
    """Its text (unless None or blank), years, "influence" flag and colour (None: the
    default tint)."""
    with session_scope() as s:
        if e := s.get(Excerpt, excerpt_id):
            if text is not None and text.strip():
                e.text = " ".join(text.split())
            e.start_year, e.end_year, e.influence = start, end, influence
            e.colour = colour or None


def merge_excerpts(source_id: int, target_id: int, *, ref_only: bool = False) -> str | None:
    """Group an excerpt (with its group, if any) with another one: each keeps its text and
    place (``ref_only``: only its place is cited); the group takes the target group's
    category, years, influence, colour and text. Returns why it cannot be done, if so."""
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


def markdown(folder_id: int, period_id: int, *, level: int = 2) -> str:
    """The excerpts by category (headings; a category's years in its heading), as Markdown
    (unquoted, their places between Obsidian comments: ``%% Application, p. 4; p. 12 %%``),
    with their years. The excerpts with the "influence" flag (left in their categories too)
    are also listed, one item per category (its path in bold, them nested below), in the
    folder's "rayonnement" category (after its own excerpts; see set_influence), else in a
    "Rayonnement" section at the end. The categories with no excerpt (nor below) are left
    out."""
    nodes = tree(folder_id)
    by_cat: dict[int, list[ExcerptView]] = {}
    for e in excerpts(period_id, grouped=True):
        by_cat.setdefault(e.category_id, []).append(e)
    gather = next((n for n in nodes if n.influence), None)
    influence: list[str] = []  # (by category, in tree order)
    for n in nodes:
        if n is not gather and (
            flagged := [_markdown_item(e) for e in by_cat.get(n.id, []) if e.influence]
        ):
            influence += [f"- **{n.path}**", *(f"  - {x}" for x in flagged)]

    def count(n: Node) -> int:
        own = len(by_cat.get(n.id, [])) + (len(influence) if n is gather else 0)
        return own + sum(count(c) for c in n.children)

    out: list[str] = []
    for n in nodes:
        if not count(n):
            continue
        heading = "#" * min(level + n.depth, 6)
        years = f" ({n.years})" if n.years else ""
        out.append(f"{heading} {n.name}{years}\n")
        for e in by_cat.get(n.id, []):  # (a group: its quotes, on one item)
            out.append("- " + _markdown_item(e))
        if n is gather:
            out += influence
        if by_cat.get(n.id) or (n is gather and influence):
            out.append("")
    if influence and gather is None:
        out += [f"{'#' * min(level, 6)} Rayonnement\n", *influence]
    return "\n".join(out).strip() + "\n" if out else ""
