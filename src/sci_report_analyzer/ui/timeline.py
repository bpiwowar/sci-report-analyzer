"""A person's timeline within a folder: a horizontal axis of years, the oldest on the left
(every year a column, the gaps seen; this year marked, their period in the folder shaded),
a row for each kind of what is dated about them. Their career (see timeline.career_spans)
and the PhDs they supervised: bars along their years, one above the other when they
overlap; their excerpts (see categories.timeline): the beginning of their text above a
thin bar along their years (one above the other too); their papers: a small bar a year, as
high as their number. An excerpt, or a year's, clicked: shown in the details below. Each
kind shown or hidden; what is without a date listed apart."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from nicegui import ui

from .. import career, categories
from .. import timeline as tl
from ..db.models import Period
from ..i18n import _, ngettext
from .viewer_side import MARKER_ICONS

# The colours of the kinds of items (an excerpt: its category's).
KIND_COLOUR = {
    "position": "#1a7f37",
    "diploma": "#bc4c00",
    "thesis": "#6639ba",  # (as the PhD students' names: .vr-student)
    "papers": "#8c959f",
}
# Papers earlier than the rest by more years than this: left out of the axis (counted).
PAPERS_BEFORE = 3
# Up to that many years, each named on the axis whatever the width (beyond: every fifth one
# when too narrow for all, see _year_names).
NAMED_YEARS = 16
# The width of the column of the rows' names, of a year named.
LABEL_PX, NAMED_PX = 84, 34  # (a year at least: .95rem, see _CSS)

# The grid: the rows' names (sticky, on the left), then a column a year. Behind: the
# years' lines, the period's band, the rows' separators; on them, the items.
_CSS = """
.vr-tl-wrap { overflow-x:auto; container-type:inline-size; }
.vr-tl { --tl-bg:#fff; --tl-line:rgba(140,149,159,.25); --tl-band:rgba(9,105,218,.08);
  --tl-now:#d4a72c; --label:5.25rem; display:grid; position:relative; width:100%;
  grid-template-columns:var(--label) repeat(var(--n), minmax(0, 6rem));
  min-width:calc(var(--label) + var(--n) * .95rem); font-size:.75rem; }
body.body--dark .vr-tl { --tl-bg:var(--q-dark); --tl-line:rgba(160,170,180,.2);
  --tl-band:rgba(88,166,255,.13); }
.vr-tl-col { grid-row:1/-1; border-left:1px solid var(--tl-line); }
.vr-tl-col.now { background:linear-gradient(to right, transparent calc(50% - 1px),
  var(--tl-now) calc(50% - 1px), var(--tl-now) calc(50% + 1px), transparent calc(50% + 1px)); }
.vr-tl-band { grid-row:1/-1; background:var(--tl-band); }
.vr-tl-sep { grid-column:1/-1; border-top:1px solid var(--tl-line); }
.vr-tl-head { grid-column:1; position:sticky; left:0; z-index:4; display:flex; gap:.25rem;
  align-items:center; padding:.35rem .4rem .35rem .1rem; background:var(--tl-bg);
  font-weight:500; line-height:1.05rem; border-right:1px solid var(--tl-line); }
.vr-tl-year { z-index:2; padding:.25rem 0; display:flex; justify-content:center; white-space:nowrap;
  font-size:.68rem; color:#8c959f; }
.vr-tl-year.full { color:inherit; font-weight:600; }
.vr-tl-year.now span { padding:0 .2rem; border-radius:6px; color:#fff;
  background:var(--tl-now); }
.vr-tl-year.pick { cursor:pointer; }
.vr-tl-year.pick:hover span { text-decoration:underline; }
.vr-tl-item { z-index:2; min-width:0; align-self:center; padding:.2rem 0; cursor:pointer; }
.vr-tl-text { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1rem;
  font-size:.72rem; padding:0 .15rem 1px 1px; }
.vr-tl-bar { height:7px; border-radius:4px; background:var(--c); }
.vr-tl-item.now .vr-tl-bar { border-radius:4px 0 0 4px;
  background:linear-gradient(to right, var(--c) 65%, transparent); }
.vr-tl-item.thin { padding:3px 0; }
.vr-tl-item.thin .vr-tl-bar { height:5px; }
.vr-tl-item.off { opacity:.45; }
.vr-tl-item:hover .vr-tl-bar, .vr-tl-item.picked .vr-tl-bar {
  box-shadow:0 0 0 2px color-mix(in srgb, var(--c) 40%, transparent); }
.vr-tl-item.paper .vr-tl-bar { background:transparent; border:1.5px solid var(--c); }
.vr-tl-papers { z-index:2; height:2.3rem; display:flex; flex-direction:column;
  align-items:center; justify-content:flex-end; padding-top:.2rem; }
.vr-tl-papers .n { font-size:.6rem; line-height:.75rem; color:#8c959f; }
.vr-tl-hist { width:min(65%, 14px); border-radius:2px 2px 0 0; background:#8c959f; }
.vr-tl-details { display:grid; gap:.5rem; grid-template-columns:repeat(auto-fill,
  minmax(17rem, 1fr)); }
@container (max-width: 640px) {
  .vr-tl { --label:4.25rem; }
  .vr-tl-head .q-icon { display:none; }
}
"""
ui.add_css(_CSS, shared=True)


@dataclass
class _Span:
    """An excerpt (``d``) on the timeline, along its years (one: that year); ``lane``: its row
    (see timeline.lanes)."""

    d: categories.Dated
    first: int
    last: int
    lane: int = 0


def _spanned(d: categories.Dated) -> _Span:
    """An excerpt along its years (without both: at its year)."""
    e = d.excerpt
    if d.paper_year or not (e.start_year and e.end_year):
        return _Span(d, d.year, d.year)
    return _Span(d, min(e.start_year, e.end_year), max(e.start_year, e.end_year))


def timeline_view(
    person_id: int,
    period: Period,
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
    show_tab: Callable[[str], None],
) -> None:
    """The timeline of a person, their excerpts those in the folder of ``period``: an
    excerpt (or a year's) clicked, shown in the details below, where clicked it is selected
    in the Excerpts tab (``select``), its PDF opened beside the page (``open_pdf``); a line
    of their career, the Profile tab shown (``show_tab``), a thesis, the Theses tab."""
    dated, undated = categories.timeline(period.id)
    spans, no_date = tl.career_spans(person_id)
    directed = tl.supervised(person_id)
    papers = tl.papers_by_year(person_id)
    nodes = categories.tree(period.folder.id)
    paths = {n.id: n.path for n in nodes}
    excerpts = [_spanned(d) for _y, items in dated for d in items]
    shown = {"career": True, "excerpts": True, "theses": True, "papers": True}
    picked: dict[str, int] = {}  # ("year" or "excerpt": shown in the details)
    counts = {
        "career": len(spans) + len(no_date),
        "excerpts": len(excerpts) + len(undated),
        "theses": len(directed),
        "papers": sum(map(len, papers.values())),
    }

    def pick(kind: str | None = None, value: int = 0) -> None:
        picked.clear()
        if kind:
            picked[kind] = value
        drawn.refresh()

    @ui.refreshable
    def drawn() -> None:
        on = {k: v and counts[k] for k, v in shown.items()}
        _chart(
            spans if on["career"] else [],
            [t for t in directed if t.year] if on["theses"] else [],
            excerpts if on["excerpts"] else [],
            papers if on["papers"] else {},
            nodes,
            period,
            picked,
            pick,
            show_tab,
        )
        if on["excerpts"]:
            _details(excerpts, picked, paths, pick, select, open_pdf)
            if undated:
                _undated_excerpts(undated, paths, select, open_pdf)
        if on["career"] and no_date:
            _undated_career(no_date, show_tab)

    def toggle(kind: str, value: bool) -> None:
        shown[kind] = value
        drawn.refresh()

    with ui.column().classes("w-full gap-2").mark("timeline"):
        ui.label(
            _(
                "What is dated about them, by year (the oldest on the left): their career and "
                "the PhDs they supervised (a bar along their years), their excerpts (the "
                "beginning of their text above a thin bar along their years) and their papers "
                "(their number a year). An excerpt or a year clicked: its excerpts shown below, "
                "where clicked one is shown in the Excerpts tab (its years edited there: its "
                "pencil). An excerpt without years of its own is at its paper's year."
            )
        ).classes("text-sm text-grey")
        _legend(shown, counts, period, toggle)
        drawn()


def _legend(
    shown: dict[str, bool],
    counts: dict[str, int],
    period: Period,
    toggle: Callable[[str, bool], None],
) -> None:
    """The kinds of items, each shown or hidden; those there are none of, said so."""
    kinds = {
        "career": (_("Career"), "work", KIND_COLOUR["position"]),
        "excerpts": (_("Excerpts"), "format_quote", "primary"),
        "theses": (_("Supervised theses"), "school", KIND_COLOUR["thesis"]),
        "papers": (_("Papers"), "article", KIND_COLOUR["papers"]),
    }
    with ui.row().classes("items-center gap-x-4 gap-y-0"):
        for kind, (label, icon, colour) in kinds.items():
            with ui.row().classes("items-center gap-0 no-wrap"):
                ui.icon(icon, color=colour, size="sm")
                ui.switch(
                    f"{label} ({counts[kind]})",
                    value=shown[kind],
                    on_change=lambda e, kind=kind: toggle(kind, e.value),
                ).props("dense").classes("ml-1").mark(f"timeline-show-{kind}").set_enabled(
                    bool(counts[kind])
                )
        if period.start_year or period.end_year:
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.element("span").classes("inline-block w-4 h-4 rounded").style(
                    "background:rgba(9,105,218,.15)"
                )
                ui.label(
                    _("their period in the folder ({years})").format(
                        years=categories.years_label(period.start_year, period.end_year)
                    )
                ).classes("text-sm text-grey")
    missing = {
        "career": _(
            "No line in their career yet: add their positions and diplomas in the Profile "
            "tab (Career), or have them read in their documents there."
        ),
        "excerpts": _(
            "No excerpt yet: file passages of their documents in the Excerpts tab (E on a "
            "selection of a PDF beside the page), with their years, to see them here."
        ),
        "theses": _("No thesis they supervised: they come from their theses.fr profile."),
        "papers": _("No paper with a year: they come from their sources."),
    }
    for kind, text in missing.items():
        if not counts[kind]:
            mark = "timeline-empty" if kind == "excerpts" else f"timeline-none-{kind}"
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("info_outline", color="grey", size="xs")
                ui.label(text).classes("text-sm text-grey").mark(mark)


def _chart(
    spans: list[tl.CareerSpan],
    directed: list[tl.Supervised],
    excerpts: list[_Span],
    papers: dict[int, list[str]],
    nodes: list[categories.Node],
    period: Period,
    picked: dict[str, int],
    pick: Callable[..., None],
    show_tab: Callable[[str], None],
) -> None:
    """The years, from the earliest of the items (papers not much earlier than the rest) to
    this year at least; a row for each kind (the excerpts: for each main category)."""
    this_year = date.today().year
    core = [y for sp in [*spans, *directed, *excerpts] for y in (sp.first, sp.last)]
    if not core and not papers:
        ui.label(_("Nothing dated to place on the timeline.")).classes("text-grey").mark(
            "timeline-nothing"
        )
        return
    lo = min(core) - PAPERS_BEFORE if core else min(papers)
    lo = min([*core, *(y for y in papers if y >= lo)])
    earlier = sum(len(titles) for y, titles in papers.items() if y < lo)
    papers = {y: titles for y, titles in papers.items() if y >= lo}
    hi = max([*core, *papers, this_year])
    n = hi - lo + 1
    # (the years' room for a label, about: the narrower they are, the more)
    room = max(1, min(6, math.ceil(n / 6)))

    def col(year: int) -> int:
        return year - lo + 2

    # (kind, name, icon, colour, its rows, its marker, its excerpts)
    rows: list[tuple[str, str, str, str, int, str, list[_Span]]] = []
    if spans:
        lanes = tl.lanes(spans, room=room)
        rows.append(("career", _("Career"), "work", KIND_COLOUR["position"], lanes, "career", []))
    if directed:
        lanes = tl.lanes(directed, room=room)
        rows.append(
            ("theses", _("PhD students"), "school", KIND_COLOUR["thesis"], lanes, "theses", [])
        )
    for main, xs in _by_main(excerpts, nodes):
        lanes = tl.lanes(xs, room=room)
        name, colour = (main.name, main.colour) if main else (_("Excerpts"), "var(--q-primary)")
        mark = f"excerpts timeline-row-main-{main.id if main else 0}"
        rows.append(("excerpts", name, "format_quote", colour, lanes, mark, xs))
    if papers:
        rows.append(("papers", _("Papers"), "article", KIND_COLOUR["papers"], 1, "papers", []))
    total = 1 + sum(row[4] for row in rows)
    cover = {y for x in excerpts for y in range(x.first, x.last + 1)}

    with ui.element("div").classes("vr-tl-wrap w-full").mark("timeline-chart"):
        _year_names(n)
        with (
            ui.element("div")
            .classes(f"vr-tl vr-tl-n{n}")
            .style(f"--n:{n};grid-template-rows:repeat({total}, auto)")
        ):
            first, last = max(period.start_year or lo, lo), min(period.end_year or hi, hi)
            if (period.start_year or period.end_year) and first <= last:
                ui.element("div").classes("vr-tl-band").style(
                    f"grid-column:{col(first)}/{col(last) + 1}"
                ).mark("timeline-period")
            for year in range(lo, hi + 1):
                ui.element("div").classes(
                    "vr-tl-col" + (" now" if year == this_year else "")
                ).style(f"grid-column:{col(year)}")
            ui.element("div").classes("vr-tl-head").style("grid-row:1")
            for year in range(lo, hi + 1):
                full = year in cover or year in papers
                full = full or any(sp.first <= year <= sp.last for sp in [*spans, *directed])
                label = (
                    ui.label()
                    .classes(
                        "vr-tl-year"
                        + (" full" if full else "")
                        + (" now" if year == this_year else "")
                        + ("" if year % 5 == 0 else " minor")
                        + (" pick" if year in cover else "")
                    )
                    .style(f"grid-column:{col(year)};grid-row:1")
                    .mark(f"timeline-tick-{year}")
                )
                with label:
                    ui.html(f"<span>{year}</span>", sanitize=False)
                if year in cover:
                    label.on("click", lambda year=year: pick("year", year)).tooltip(
                        _("Its excerpts, below")
                    )
            row = 2
            for kind, name, icon, colour, k, mark, xs in rows:
                ui.element("div").classes("vr-tl-sep").style(f"grid-row:{row}")
                with (
                    ui.element("div")
                    .classes("vr-tl-head")
                    .style(f"grid-row:{row}/span {k}")
                    .mark(f"timeline-row-{mark}")
                ):
                    ui.icon(icon, size="xs").style(f"color:{colour}")
                    ui.label(name)
                if kind == "career":
                    for sp, end in _cells(spans, hi):
                        _career(sp, row, col, end, show_tab)
                elif kind == "theses":
                    for t, end in _cells(directed, hi):
                        _thesis(t, row, col, end, show_tab)
                elif kind == "excerpts":
                    for x, end in _cells(xs, hi):
                        _excerpt(x, row, col, end, picked, pick)
                else:
                    top = max(map(len, papers.values()))
                    for year, titles in papers.items():
                        _papers(year, titles, top, row, col)
                row += k
    if earlier:
        ui.label(
            ngettext(
                "{n} earlier paper not shown (before {year})",
                "{n} earlier papers not shown (before {year})",
                earlier,
            ).format(n=earlier, year=lo)
        ).classes("text-xs text-grey").mark("timeline-papers-earlier")


def _by_main(
    excerpts: list[_Span], nodes: list[categories.Node]
) -> list[tuple[categories.Node | None, list[_Span]]]:
    """The excerpts by main category (that at the top of theirs), in the categories' order
    (those of none known: last, together)."""
    by_id = {n.id: n for n in nodes}

    def main(cat_id: int) -> categories.Node | None:
        n = by_id.get(cat_id)
        while n is not None and n.parent_id is not None and n.parent_id in by_id:
            n = by_id[n.parent_id]
        return n

    groups: dict[int, list[_Span]] = {}
    for x in excerpts:
        m = main(x.d.excerpt.category_id)
        groups.setdefault(m.id if m else 0, []).append(x)
    order = [n for n in nodes if n.id in groups] + ([None] if 0 in groups else [])
    return [(n, groups[n.id if n else 0]) for n in order]


def _year_names(n: int) -> None:
    """Beyond NAMED_YEARS years, those not a fifth one unnamed when too narrow for all."""
    if n > NAMED_YEARS:
        width = LABEL_PX + n * NAMED_PX
        ui.html(
            f"<style>@container (max-width:{width}px)"
            f"{{.vr-tl-n{n} .vr-tl-year.minor span{{visibility:hidden}}}}</style>",
            sanitize=False,
        ).classes("hidden")


def _cells(spans: list, hi: int):
    """Each span (in its lane) with where its text may go up to (in years): where the next
    one in its lane begins, else the end of the axis."""
    by_lane: dict[int, list] = {}
    for sp in spans:
        by_lane.setdefault(sp.lane, []).append(sp)
    for lane in by_lane.values():
        lane.sort(key=lambda x: tl.bounds(x)[0])
        for sp, after in pairwise([*lane, None]):
            yield sp, tl.bounds(after)[0] if after else hi + 1


def _bar(
    colour: str,
    sp,
    row: int,
    col: Callable[[int], int],
    end: int,
    now: bool,
    text: str,
    mark: str,
    classes: str = "",
) -> ui.element:
    """A span (``sp``: its ``first``, ``last`` years, its ``lane``; see timeline.bounds) in
    its row's lane, over the years it is in: its ``text`` (as far as ``end``, where the next
    one in its lane begins) above its bar along its time (``now``: still going on)."""
    begin, stop = tl.bounds(sp)
    cell = max(sp.last + 1, math.ceil(end - 1e-9))  # (the year its cell ends at, excluded)
    span = cell - sp.first

    def pct(years: float) -> str:
        return f"{years / span * 100:.3f}%"

    with (
        ui.element("div")
        .classes("vr-tl-item" + (" now" if now else "") + classes)
        .style(f"--c:{colour};grid-column:{col(sp.first)}/{col(cell)};grid-row:{row + sp.lane}")
        .mark(mark) as item
    ):
        ui.label(text).classes("vr-tl-text").style(
            f"margin-left:{pct(begin - sp.first)};max-width:{pct(end - begin)}"
        )
        ui.element("div").classes("vr-tl-bar").style(
            f"margin-left:{pct(begin - sp.first)};width:{pct(stop - begin)}"
        )
    return item


def _career(
    sp: tl.CareerSpan,
    row: int,
    col: Callable[[int], int],
    end: int,
    show_tab: Callable[[str], None],
) -> None:
    """A line of the career, along its years."""
    e = sp.entry
    colour = KIND_COLOUR["diploma" if e.kind == "diploma" else "position"]
    item = _bar(colour, sp, row, col, end, e.current, e.line(), f"timeline-career-{e.id}")
    item.on("click", lambda: show_tab("profile"))
    with item, ui.tooltip().classes("max-w-sm"):
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon("school" if e.kind == "diploma" else "work", size="xs")
            ui.label(e.when() + (f" ({_('current')})" if e.current else ""))
        ui.label(e.line()).classes("text-sm font-medium")
        if e.details:
            ui.label(e.details)
        ui.label(_("Edited in the Profile tab (Career)")).classes("italic")


def _thesis(
    t: tl.Supervised,
    row: int,
    col: Callable[[int], int],
    end: int,
    show_tab: Callable[[str], None],
) -> None:
    """A thesis they supervised, from its start to its defence (in progress: to now)."""
    text = t.student or t.title
    item = _bar(
        KIND_COLOUR["thesis"], t, row, col, end, t.in_progress, text, f"timeline-thesis-{t.id}"
    )
    item.on("click", lambda: show_tab("theses"))
    with item, ui.tooltip().classes("max-w-sm"):
        ui.label(
            _("PhD of {student}").format(student=t.student or "?")
            + (f", {t.years}" if t.years else "")
            + (f" ({_('in progress')})" if t.in_progress else "")
        )
        ui.label(t.title).classes("text-sm font-medium")
        ui.label(_("See it in the Theses tab")).classes("italic")


def _excerpt(
    x: _Span,
    row: int,
    col: Callable[[int], int],
    end: int,
    picked: dict[str, int],
    pick: Callable[..., None],
) -> None:
    """An excerpt: the beginning of its text above a thin bar along its years (a single
    one: that year; at its paper's: hollow), clicked shown below."""
    e = x.d.excerpt
    classes = (
        " thin"
        + (" paper" if x.d.paper_year else "")
        + (" off" if e.discarded else "")
        + (" picked" if picked.get("excerpt") == e.id else "")
    )
    item = _bar(e.colour, x, row, col, end, False, e.quoted, f"timeline-span-{e.id}", classes)
    item.on("click", lambda: pick("excerpt", e.id))
    with item, ui.tooltip().classes("max-w-sm"):
        ui.label(
            _("the paper's year ({year})").format(year=x.first) if x.d.paper_year else e.years
        ).classes("font-medium")
        ui.label(e.quoted).classes("line-clamp-4")


def _papers(year: int, titles: list[str], top: int, row: int, col: Callable[[int], int]) -> None:
    """Their papers of a year: a bar as high as their number (``top``: the most of a
    year's), their titles hovered."""
    with (
        ui.element("div")
        .classes("vr-tl-papers")
        .style(f"grid-column:{col(year)};grid-row:{row}")
        .mark(f"timeline-papers-{year}")
    ):
        ui.label(str(len(titles))).classes("n")
        ui.element("div").classes("vr-tl-hist").style(
            f"height:{max(3, round(len(titles) / top * 100)) * 0.014:.3f}rem"
        )
        with ui.tooltip().classes("max-w-sm"):
            ui.label(
                ngettext("{n} paper in {year}", "{n} papers in {year}", len(titles)).format(
                    n=len(titles), year=year
                )
            ).classes("font-medium")
            for title in titles[:20]:
                ui.label(title).classes("text-xs")
            if len(titles) > 20:
                ui.label(_("and {n} more").format(n=len(titles) - 20)).classes("text-xs")


def _details(
    excerpts: list[_Span],
    picked: dict[str, int],
    paths: dict[int, str],
    pick: Callable[..., None],
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
) -> None:
    """The excerpts picked on the timeline: those of a year (along it, or in it), or one."""
    if not excerpts:
        return
    with ui.column().classes("w-full gap-2 mt-2").mark("timeline-details"):
        if not picked:
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("touch_app", color="grey", size="xs")
                ui.label(_("Click an excerpt or a year to see its excerpts here.")).classes(
                    "text-sm text-grey"
                ).mark("timeline-details-hint")
            return
        if year := picked.get("year"):
            found = [x for x in excerpts if x.first <= year <= x.last]
            title = ngettext("{n} excerpt in {year}", "{n} excerpts in {year}", len(found)).format(
                n=len(found), year=year
            )
        else:
            found = [x for x in excerpts if x.d.excerpt.id == picked.get("excerpt")]
            title = _("An excerpt, {years}").format(years=found[0].d.excerpt.years if found else "")
        with ui.row().classes("items-center gap-2 w-full"):
            ui.icon("format_quote", color="primary")
            ui.label(title).classes("font-medium")
            ui.space()
            ui.button(icon="close", on_click=lambda: pick()).props(
                "flat dense round size=sm"
            ).tooltip(_("Close")).mark("timeline-details-close")
        with ui.element("div").classes("vr-tl-details w-full"):
            for x in found:
                _item(x.d.excerpt, paths, select, open_pdf, x.d.paper_year)


def _undated_excerpts(
    undated: list[categories.ExcerptView],
    paths: dict[int, str],
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
) -> None:
    with ui.column().classes("w-full gap-2 mt-4").mark("timeline-undated"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("event_busy", color="grey")
            ui.label(
                ngettext(
                    "{n} excerpt without years", "{n} excerpts without years", len(undated)
                ).format(n=len(undated))
            ).classes("font-medium")
        ui.label(
            _(
                "Not on the timeline: set their years in the Excerpts tab (an excerpt's "
                "pencil) to place them."
            )
        ).classes("text-sm text-grey")
        with ui.element("div").classes("vr-tl-details w-full"):
            for e in undated:
                _item(e, paths, select, open_pdf)


def _undated_career(entries: list[career.Entry], show_tab: Callable[[str], None]) -> None:
    with ui.column().classes("w-full gap-1 mt-4").mark("timeline-career-undated"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("event_busy", color="grey")
            ui.label(
                ngettext(
                    "{n} line of their career without dates",
                    "{n} lines of their career without dates",
                    len(entries),
                ).format(n=len(entries))
            ).classes("font-medium")
        ui.label(_("Not on the timeline: set their dates in the Profile tab (Career).")).classes(
            "text-sm text-grey"
        )
        for e in entries:
            with (
                ui.row()
                .classes("items-center gap-2 pl-3 cursor-pointer")
                .on("click", lambda: show_tab("profile"))
            ):
                ui.icon("school" if e.kind == "diploma" else "work", size="xs", color="grey")
                ui.label(e.line()).classes("text-sm")


def _item(
    e: categories.ExcerptView,
    paths: dict[int, str],
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
    paper_year: bool = False,
) -> None:
    """An excerpt (or a group: its text): its category, years, markers, its text and where
    it is from."""
    with (
        ui.column()
        .classes("w-full gap-0 cursor-pointer rounded" + (" opacity-50" if e.discarded else ""))
        .style(f"border-left:3px solid {e.colour};padding:.15rem .5rem")
        .on("click", lambda: select(e.id))
        .tooltip(_("See it in the Excerpts tab"))
        .mark(f"timeline-excerpt-{e.id}")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.label(paths.get(e.category_id, "")).classes("text-xs text-grey-8")
            if paper_year:
                ui.badge(_("the paper's year"), color="grey").props("outline").tooltip(
                    _("No years of its own: placed at the year of its paper")
                ).mark(f"timeline-paper-year-{e.id}")
            elif e.years:
                ui.badge(e.years, color="blue-grey").props("outline").mark(f"timeline-years-{e.id}")
            for key, (label, _tip, icon, colour) in MARKER_ICONS.items():
                if getattr(e, key):
                    ui.icon(icon, size="xs", color=colour).tooltip(_(label))
            if e.discarded:
                ui.badge(_("discarded"), color="grey").props("outline")
        ui.label(e.quoted).classes(
            "text-sm line-clamp-3" + (" line-through" if e.discarded else "")
        )
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label(e.source + (f", p. {e.page}" if e.page else "")).classes(
                "text-xs text-grey ellipsis min-w-0"
            )
            ui.button(icon="picture_as_pdf").props("flat dense round size=xs color=red-8").on(
                "click.stop", lambda: open_pdf(e)
            ).tooltip(_("Open its PDF beside the page")).mark(f"timeline-pdf-{e.id}")
