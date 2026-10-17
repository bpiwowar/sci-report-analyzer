"""A person's timeline within a folder: a vertical axis of years, the most recent at the top
(every year a tick, the gaps seen), with arrows from it to what is dated about them. On the
left, their career (see timeline.career_spans): its lines as bars along their years; on the
right, their excerpts (see categories.timeline), the theses they supervised and their
papers (small marks). Each kind shown or hidden; what is without a date listed apart."""

from __future__ import annotations

from collections.abc import Callable

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
# Most papers drawn as marks in a year (the others counted).
MAX_MARKS = 12

# The grid: the career's lanes (--lane wide), the axis (its line at the middle, the years
# on it), the items on the right. A connector: from the axis to its item, an arrow at the
# item; on the left, across the lanes nearer the axis (--k of them).
_CSS = """
.vr-tl { --tl-bg:#fff; --tl-axis:#8c959f; --tl-band:rgba(9,105,218,.07); --lane:11rem;
  --axis:4rem; --gap:1.25rem; display:grid; grid-auto-rows:minmax(1.75rem,auto);
  position:relative; min-width:min-content; }
body.body--dark .vr-tl { --tl-bg:var(--q-dark); --tl-band:rgba(88,166,255,.10); }
.vr-tl-band { grid-column:1/-1; border-radius:6px; background:var(--tl-band); }
.vr-tl-axis { position:relative; z-index:1; }
.vr-tl-axis::before { content:""; position:absolute; left:calc(50% - 1px); top:.4rem;
  bottom:0; width:2px; background:var(--tl-axis); }
.vr-tl-axis::after { content:""; position:absolute; left:calc(50% - 6px); top:0;
  border:6px solid transparent; border-bottom:9px solid var(--tl-axis); border-top:0; }
.vr-tl-year { z-index:3; justify-self:center; align-self:start; margin-top:.3rem;
  padding:0 .4rem; border-radius:9px; font-size:.7rem; line-height:1.1rem;
  color:var(--tl-axis); background:var(--tl-bg); }
.vr-tl-year.full { font-size:.85rem; font-weight:600; color:inherit;
  border:1.5px solid var(--q-primary); }
.vr-tl-items { padding:.15rem 0 .5rem var(--gap); min-width:0; z-index:2; }
.vr-tl-card { position:relative; border-left:3px solid var(--c); border-radius:4px;
  padding:.1rem .5rem; background:color-mix(in srgb, var(--c) 7%, var(--tl-bg)); }
.vr-tl-card::before { content:""; position:absolute; top:.75rem;
  left:calc(-1 * (var(--gap) + var(--axis) / 2) - 3px);
  width:calc(var(--gap) + var(--axis) / 2 - 4px); border-top:2px solid var(--c); }
.vr-tl-card::after { content:""; position:absolute; top:calc(.75rem - 4px); left:-10px;
  border:5px solid transparent; border-left:7px solid var(--c); border-right:0; }
.vr-tl-items > .vr-tl-card { max-width:48rem; }
.vr-tl-papers { --c:#8c959f; }
.vr-tl-papers::before { border-top-style:dashed; }
.vr-tl-mark { display:inline-block; width:6px; height:9px; margin-right:2px;
  border-radius:1px; background:var(--c); }
.vr-tl-span { position:relative; margin:.15rem 0; z-index:2; }
.vr-tl-span .vr-tl-bar { position:absolute; right:.35rem; top:.45rem; bottom:.2rem;
  width:5px; border-radius:3px; background:var(--c); }
.vr-tl-span.now .vr-tl-bar { top:0; background:linear-gradient(to top, var(--c) 75%,
  transparent); }
.vr-tl-span .vr-tl-link { position:absolute; top:.75rem; left:calc(100% - .35rem);
  width:calc(var(--k) * var(--lane) + var(--axis) / 2 + .35rem); z-index:-1;
  border-top:2px solid var(--c); }
.vr-tl-span .vr-tl-link::after { content:""; position:absolute; left:-2px; top:-6px;
  border:5px solid transparent; border-right:7px solid var(--c); border-left:0; }
.vr-tl-span .vr-tl-card { margin-right:1rem; border-left-width:0;
  border-right:3px solid var(--c); }
.vr-tl-span .vr-tl-card::before, .vr-tl-span .vr-tl-card::after { display:none; }
"""
ui.add_css(_CSS, shared=True)


def timeline_view(
    person_id: int,
    period: Period,
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
    show_tab: Callable[[str], None],
) -> None:
    """The timeline of a person, their excerpts those in the folder of ``period``: an
    excerpt clicked, selected in the Excerpts tab (``select``), its PDF opened beside the
    page (``open_pdf``); a line of their career, the Profile tab shown (``show_tab``), a
    thesis, the Theses tab."""
    dated, undated = categories.timeline(period.id)
    spans, no_date = tl.career_spans(person_id)
    directed = tl.supervised(person_id)
    papers = tl.papers_by_year(person_id)
    paths = {n.id: n.path for n in categories.tree(period.folder.id)}
    shown = {"career": True, "excerpts": True, "theses": True, "papers": True}
    counts = {
        "career": len(spans) + len(no_date),
        "excerpts": sum(len(items) for _y, items in dated) + len(undated),
        "theses": len(directed),
        "papers": sum(map(len, papers.values())),
    }

    @ui.refreshable
    def drawn() -> None:
        on = {k: v and counts[k] for k, v in shown.items()}
        _axis(
            spans if on["career"] else [],
            dated if on["excerpts"] else [],
            [t for t in directed if t.year] if on["theses"] else [],
            papers if on["papers"] else {},
            period,
            paths,
            select,
            open_pdf,
            show_tab,
        )
        if on["excerpts"] and undated:
            _undated_excerpts(undated, paths, select, open_pdf)
        if on["career"] and no_date:
            _undated_career(no_date, show_tab)

    def toggle(kind: str, value: bool) -> None:
        shown[kind] = value
        drawn.refresh()

    with ui.column().classes("w-full gap-2").mark("timeline"):
        ui.label(
            _(
                "What is dated about them, the most recent first: their career on the left "
                "(a line of their CV: a bar along its years), their excerpts, the theses they "
                "supervised and their papers on the right. An excerpt is at its first year "
                "(a paper's excerpt without years: at the paper's); clicked, it is shown in "
                "the Excerpts tab, where its years are edited (its pencil)."
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


def _axis(
    spans: list[tl.CareerSpan],
    dated: list[tuple[int, list[categories.Dated]]],
    directed: list[tl.Supervised],
    papers: dict[int, list[str]],
    period: Period,
    paths: dict[int, str],
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
    show_tab: Callable[[str], None],
) -> None:
    """The axis of years, from the latest to the earliest of the items; the career on its
    left, the rest on its right."""
    years = (
        [y for sp in spans for y in (sp.first, sp.last)]
        + [y for y, _items in dated]
        + [t.year for t in directed]
        + list(papers)
    )
    if not years:
        ui.label(_("Nothing dated to place on the timeline.")).classes("text-grey").mark(
            "timeline-nothing"
        )
        return
    top, bottom = max(years), min(years)
    lanes = max((sp.lane + 1 for sp in spans), default=0)
    excerpts = dict(dated)
    by_year: dict[int, list[tl.Supervised]] = {}
    for t in directed:
        by_year.setdefault(t.year, []).append(t)

    def row(year: int) -> int:
        return top - year + 1

    columns = f"repeat({lanes}, var(--lane)) var(--axis) minmax(14rem, 1fr)"
    with (
        ui.element("div").classes("w-full overflow-x-auto"),
        ui.element("div").classes("vr-tl").style(f"grid-template-columns:{columns}"),
    ):
        lo, hi = max(period.start_year or bottom, bottom), min(period.end_year or top, top)
        if (period.start_year or period.end_year) and lo <= hi:
            ui.element("div").classes("vr-tl-band").style(f"grid-row:{row(hi)}/{row(lo) + 1}").mark(
                "timeline-period"
            )
        ui.element("div").classes("vr-tl-axis").style(
            f"grid-column:{lanes + 1};grid-row:1/{row(bottom) + 1}"
        )
        for year in range(top, bottom - 1, -1):
            full = year in excerpts or year in by_year or year in papers
            full = full or any(sp.first <= year <= sp.last for sp in spans)
            ui.label(str(year)).classes("vr-tl-year" + (" full" if full else "")).style(
                f"grid-column:{lanes + 1};grid-row:{row(year)}"
            ).mark(f"timeline-tick-{year}")
            if year in excerpts or year in by_year or year in papers:
                with (
                    ui.column()
                    .classes("vr-tl-items gap-2")
                    .style(f"grid-column:{lanes + 2};grid-row:{row(year)}")
                    .mark(f"timeline-{year}")
                ):
                    for d in excerpts.get(year, []):
                        _item(d.excerpt, paths, select, open_pdf, d.paper_year)
                    for t in by_year.get(year, []):
                        _thesis(t, show_tab)
                    if titles := papers.get(year):
                        _papers(year, titles)
        for sp in spans:
            _span(sp, lanes, row, show_tab)


def _span(
    sp: tl.CareerSpan, lanes: int, row: Callable[[int], int], show_tab: Callable[[str], None]
) -> None:
    """A line of the career: its bar along its years, its card at their top, an arrow to it
    from the axis."""
    e = sp.entry
    colour = KIND_COLOUR["diploma" if e.kind == "diploma" else "position"]
    with (
        ui.element("div")
        .classes("vr-tl-span" + (" now" if e.current else ""))
        .style(
            f"--c:{colour};--k:{sp.lane};grid-column:{lanes - sp.lane};"
            f"grid-row:{row(sp.last)}/{row(sp.first) + 1}"
        )
        .mark(f"timeline-career-{e.id}")
    ):
        ui.element("div").classes("vr-tl-bar")
        ui.element("div").classes("vr-tl-link")
        with (
            ui.column()
            .classes("vr-tl-card gap-0 cursor-pointer")
            .on("click", lambda: show_tab("profile"))
            .tooltip(_("Edited in the Profile tab (Career)"))
        ):
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("school" if e.kind == "diploma" else "work", size="xs").style(
                    f"color:{colour}"
                )
                ui.label(e.when()).classes("text-xs text-grey-8")
                if e.current:
                    ui.badge(_("current"), color="primary").props("outline")
            ui.label(e.line()).classes("text-sm font-medium line-clamp-3")
            if e.details:
                ui.label(e.details).classes("text-xs text-grey line-clamp-2")


def _thesis(t: tl.Supervised, show_tab: Callable[[str], None]) -> None:
    """A thesis they supervised, at its defence (in progress: at its start)."""
    with (
        ui.column()
        .classes("vr-tl-card gap-0 cursor-pointer")
        .style(f"--c:{KIND_COLOUR['thesis']}")
        .on("click", lambda: show_tab("theses"))
        .tooltip(_("See it in the Theses tab"))
        .mark(f"timeline-thesis-{t.id}")
    ):
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon("school", size="xs").style(f"color:{KIND_COLOUR['thesis']}")
            ui.label(
                _("PhD of {student}").format(student=t.student or "?")
                + (f", {t.years}" if t.years else "")
            ).classes("text-xs vr-student")
            if t.in_progress:
                ui.badge(_("in progress"), color="grey").props("outline")
        ui.label(t.title).classes("text-sm line-clamp-2")


def _papers(year: int, titles: list[str]) -> None:
    """Their papers of a year: a mark each (up to MAX_MARKS), their titles hovered."""
    marks = '<span class="vr-tl-mark"></span>' * min(len(titles), MAX_MARKS)
    more = f" +{len(titles) - MAX_MARKS}" if len(titles) > MAX_MARKS else ""
    with (
        ui.row()
        .classes("vr-tl-card vr-tl-papers items-center gap-2 no-wrap")
        .mark(f"timeline-papers-{year}")
    ):
        ui.html(marks + more, sanitize=False).classes("text-xs text-grey")
        ui.label(ngettext("{n} paper", "{n} papers", len(titles)).format(n=len(titles))).classes(
            "text-xs text-grey"
        )
        with ui.tooltip():
            for title in titles[:20]:
                ui.label(title).classes("text-xs")
            if len(titles) > 20:
                ui.label(_("and {n} more").format(n=len(titles) - 20)).classes("text-xs")


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
        with ui.column().classes("w-full gap-2 pl-3").style("border-left: 2px dashed grey"):
            for e in undated:
                _item(e, paths, select, open_pdf, arrow=False)


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
    arrow: bool = True,
) -> None:
    """An excerpt (or a group: its text) on the timeline, an arrow to it from the axis (but
    without years): its category, years, markers, its text and where it is from."""
    with (
        ui.column()
        .classes(
            "w-full gap-0 cursor-pointer"
            + (" vr-tl-card" if arrow else "")
            + (" opacity-50" if e.discarded else "")
        )
        .style(
            f"--c:{e.colour}" if arrow else f"border-left:3px solid {e.colour};padding-left:.5rem"
        )
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
            elif e.years and e.start_year != e.end_year:
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
