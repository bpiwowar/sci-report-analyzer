"""A person's timeline within a folder: a horizontal axis of years, the oldest on the left
(every year a column, the gaps seen; this year marked, their period in the folder shaded),
a row for each kind of what is dated about them. Their career (see timeline.career_spans)
and the PhDs they supervised: bars along their years, one above the other when they
overlap; their excerpts (see categories.timeline): the beginning of their text above a
thin bar along their years (one above the other too); their papers: a small bar a year, as
high as their number. An excerpt, or a year's, clicked: shown in the details below (there,
its PDF opened beside the page at it, clicked; edited, its pencil). Each kind shown or
hidden; what is without a date listed apart."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from nicegui import ui

from .. import career, categories, persons, reports
from .. import timeline as tl
from ..db.models import Period
from ..i18n import _, ngettext
from ..sources.thesesfr import ROLE_LABELS
from .viewer_side import MARKER_ICONS

# The colours of the kinds of items (an excerpt: its category's).
KIND_COLOUR = {
    "position": "#1a7f37",
    "diploma": "#bc4c00",
    "thesis": "#6639ba",  # (as the PhD students' names: .vr-student)
    "jury": "#0969da",
    "papers": "#8c959f",
}
# The roles on a thesis's jury: their colour (a dot each, at the defence).
JURY_COLOUR = {"rapporteur": "#cf222e", "examiner": "#0969da", "president": "#9a6700"}
# Papers earlier than the rest by more years than this: left out of the axis (counted).
PAPERS_BEFORE = 3
# Up to that many years, each named on the axis whatever the width (beyond: every fifth one
# when too narrow for all, see _year_names_css).
NAMED_YEARS = 16
# The zoom (its slider): a year's width, times that of the years fitting the width (more:
# scrolled), from fitting it to four times as wide.
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 1.0, 4.0, 0.1
ZOOM_START = 2.0  # (twice: scrolled to the period in the folder, else to this year)
# The width of the column of the rows' names, of a year named.
LABEL_PX, NAMED_PX = 84, 34  # (a year at least: .95rem, see _CSS)

# The grid: the rows' names (sticky, on the left), then a column a year. Behind: the
# years' lines, the period's band, the rows' separators; on them, the items.
_CSS = """
.vr-tl-wrap { overflow-x:auto; container-type:inline-size; }
.vr-tl { --tl-bg:#fff; --tl-line:rgba(140,149,159,.25); --tl-band:rgba(0,150,136,.14);
  --tl-band-edge:rgba(0,150,136,.55);
  --tl-now:#d4a72c; --label:5.25rem; display:grid; position:relative; width:max-content;
  min-width:100%; grid-template-columns:var(--label) repeat(var(--n), max(.95rem,
  min(calc(6rem * var(--tl-zoom)), calc((100cqw - var(--label)) * var(--tl-zoom) / var(--n)))));
  font-size:.75rem; }
body.body--dark .vr-tl { --tl-bg:var(--q-dark); --tl-line:rgba(160,170,180,.2);
  --tl-band:rgba(77,208,192,.16); --tl-band-edge:rgba(77,208,192,.6); }
.vr-tl-col { grid-row:1/-1; border-left:1px solid var(--tl-line); }
.vr-tl-col.now { background:linear-gradient(to right, transparent calc(50% - 1px),
  var(--tl-now) calc(50% - 1px), var(--tl-now) calc(50% + 1px), transparent calc(50% + 1px)); }
.vr-tl-band { grid-row:1/-1; background:var(--tl-band);
  border-left:1.5px dashed var(--tl-band-edge); border-right:1.5px dashed var(--tl-band-edge); }
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
.vr-tl-item.dot .vr-tl-bar, .vr-tl-dot { height:9px !important; width:9px; border-radius:50%;
  background:var(--c); }
.vr-tl-item.approx .vr-tl-bar, .vr-tl-approx { height:5px; opacity:.6; border-radius:0;
  background:repeating-linear-gradient(to right, var(--c) 0 5px, transparent 5px 9px); }
.vr-tl-item.approx.now .vr-tl-bar { mask-image:linear-gradient(to right, #000 65%, transparent); }
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
# A timeline wider than the page: scrolled (once laid out) to the period in the folder, whole
# if it fits (else its start), or else to this year; drawn again (``keep``): where it was (its
# middle at the same place along it, vrTlAt).
ui.add_head_html(
    """<script>
window.vrTimelineScroll = (id, keep) => {
  const go = () => {
    const w = getHtmlElement(id);
    if (!w) return;
    w.onscroll = () => { window.vrTlAt = (w.scrollLeft + w.clientWidth / 2) / w.scrollWidth; };
    if (keep && window.vrTlAt != null) {
      w.scrollLeft = window.vrTlAt * w.scrollWidth - w.clientWidth / 2;
      return;
    }
    const b = w.querySelector('.vr-tl-band');
    const label = w.querySelector('.vr-tl-head')?.offsetWidth || 0;
    if (!b) { w.scrollLeft = w.scrollWidth; return; }
    const room = w.clientWidth - label - 32;
    w.scrollLeft = b.offsetWidth <= room
      ? b.offsetLeft + b.offsetWidth - w.clientWidth + 32
      : b.offsetLeft - label - 16;
  };
  for (const t of keep ? [0, 100, 300] : [300, 900]) setTimeout(go, t);
};
// The zoom's slider moved: the years wider or narrower at once (the middle kept in view).
window.vrTimelineZoom = (z) => {
  const w = document.querySelector('.vr-tl-wrap'), g = w && w.querySelector('.vr-tl');
  if (!g) return;
  const at = (w.scrollLeft + w.clientWidth / 2) / w.scrollWidth;
  g.style.setProperty('--tl-zoom', z);
  w.scrollLeft = at * w.scrollWidth - w.clientWidth / 2;
  window.vrTlAt = at;
};
</script>""",
    shared=True,
)


@dataclass
class _Span:
    """An excerpt (``d``) on the timeline, along its years (one: that year); ``lane``: its row
    (see timeline.lanes); ``now``: still going on (to this year, fading); ``dot``: a moment
    (a one-off event), in the middle of its year; ``approx``: sometime within them (within
    the person's period: dotted, lighter)."""

    d: categories.Dated
    first: int
    last: int
    lane: int = 0
    now: bool = False
    dot: bool = False
    approx: bool = False


@dataclass
class _Acts:
    """What an excerpt in the details does: ``show``, its PDF beside the page at it (clicked);
    ``edit``, its dialog (as in the Excerpts tab; then the timeline drawn again); ``select``,
    it in the Excerpts tab; ``on_timeline``, its switch."""

    show: Callable[[categories.ExcerptView], None]
    edit: Callable[[categories.ExcerptView], Awaitable[None] | None]
    select: Callable[[int], None]
    on_timeline: Callable[[categories.ExcerptView, bool], None]
    within: tuple[int | None, int | None] = (None, None)  # (the years of the person's period)


def _spanned(d: categories.Dated, this_year: int) -> _Span:
    """An excerpt along its years: since one (not a one-off event), up to this year; a
    one-off event, a dot at its year (its first one); without years of its own, along the
    person's period (within it), else at its paper's year."""
    e = d.excerpt
    if d.period:
        first = d.period[0] or d.period[1] or this_year
        return _Span(
            d, first, d.period[1] or max(first, this_year), now=not d.period[1], approx=True
        )
    a, b = e.start_year, e.end_year
    if d.paper_year or not (a or b):
        return _Span(d, d.year, d.year)
    if e.punctual:
        return _Span(d, d.year, d.year, dot=True)
    if a and b:
        return _Span(d, min(a, b), max(a, b), approx=e.in_period)
    if a and a < this_year:  # (still going on)
        return _Span(d, a, this_year, now=True, approx=e.in_period)
    return _Span(d, d.year, d.year, approx=e.in_period)


def _when(e: categories.ExcerptView, within: tuple[int | None, int | None]) -> str:
    """Its years, as said: since one (still going on), a one-off event's year; within the
    person's period (``within``: its years), its own ones, else the period's (said if none)."""
    if e.in_period:
        years = e.years or categories.years_label(*within)
        if not years:
            return _("within the period (its years not set)")
        return _("within the period ({years})").format(years=years)
    if e.punctual:
        return str(e.start_year or e.end_year or "")
    return e.years


def timeline_view(
    person_id: int,
    period: Period,
    select: Callable[[int], None],
    show: Callable[[categories.ExcerptView], None],
    edit: Callable[[categories.ExcerptView, Callable[[], None]], Awaitable[None] | None],
    show_tab: Callable[[str], None],
) -> None:
    """The timeline of a person, their excerpts those in the folder of ``period``: an
    excerpt (or a year's) clicked, shown in the details below, where clicked its PDF is
    opened beside the page at it (``show``), edited with its pencil (``edit``: its dialog,
    given what draws the timeline again once saved), seen in the Excerpts tab (``select``);
    a line of their career or a PhD student clicked, in the details too, edited there (or
    seen in its tab: ``show_tab``). Drawn again once one is edited (what is shown, the zoom
    kept)."""
    state: dict = {
        "shown": {
            "career": True,
            "excerpts": True,
            "theses": True,
            "juries": True,
            "papers": True,
        },
        "picked": {},  # ("year", "excerpt", "career" or "thesis": shown in the details)
        "zoom": ZOOM_START,  # (see ZOOM_MIN)
        "drawn": False,  # (the chart drawn already: drawn again, scrolled where it was)
        # (the excerpts shown: those with one of these markers, and of one of these levels;
        # none: whatever)
        "filters": {"markers": set(), "levels": set()},
    }

    @ui.refreshable
    def view() -> None:
        _view(person_id, period, select, show, edit, show_tab, state, view.refresh)

    view()


def _view(
    person_id: int,
    period: Period,
    select: Callable[[int], None],
    show: Callable[[categories.ExcerptView], None],
    edit: Callable[[categories.ExcerptView, Callable[[], None]], Awaitable[None] | None],
    show_tab: Callable[[str], None],
    state: dict,
    rebuild: Callable[[], None],
) -> None:
    left_out: list[categories.ExcerptView] = []
    dated, undated = categories.timeline(period.id, left_out)
    spans, no_date = tl.career_spans(person_id)
    directed = tl.supervised(person_id)
    juries = tl.juries(person_id)
    papers = tl.papers_by_year(person_id)
    nodes = categories.tree(period.folder.id)
    paths = {n.id: n.path for n in nodes}
    excerpts = [_spanned(d, date.today().year) for _y, items in dated for d in items]
    shown, picked, filters = state["shown"], state["picked"], state["filters"]
    levels, default = reports.level_tags(period.folder.id)
    counts = {
        "career": len(spans) + len(no_date),
        "excerpts": len(excerpts) + len(undated) + len(left_out),
        "theses": len(directed),
        "juries": len(juries),
        "papers": sum(map(len, papers.values())),
    }

    # (the items of the chart, by kind and id: marked once picked, the chart kept as it is)
    items: dict[tuple[str, int], ui.element] = {}
    layout: dict = {}  # (of the chart drawn: see _chart)

    def pick(kind: str | None = None, value: int = 0) -> None:
        picked.clear()
        if kind:
            picked[kind] = value
        for key, item in items.items():
            if not item.is_deleted:
                item.classes(**{"add" if picked.get(key[0]) == key[1] else "remove": "picked"})
        below.refresh()

    def on_timeline(e: categories.ExcerptView, value: bool) -> None:
        categories.set_in_timeline(e.id, value)
        if not value and picked.get("excerpt") == e.id:  # (no longer there to show)
            picked.clear()
        rebuild()

    acts = _Acts(
        show,
        lambda e: edit(e, rebuild),
        select,
        on_timeline,
        (period.start_year, period.end_year),
    )

    def on() -> dict[str, bool]:
        return {k: bool(v and counts[k]) for k, v in shown.items()}

    def kept() -> tuple[list[_Span], list[categories.ExcerptView], list[categories.ExcerptView]]:
        """The excerpts (on the timeline, without years, left out) the filters let through."""
        ok = lambda e: _passes(e, filters, default)  # noqa: E731
        return (
            [x for x in excerpts if ok(x.d.excerpt)],
            [e for e in undated if ok(e)],
            [e for e in left_out if ok(e)],
        )

    def filtered() -> None:
        """The filters changed: what they hide no longer picked, all drawn again."""
        if (i := picked.get("excerpt")) and not any(x.d.excerpt.id == i for x in kept()[0]):
            picked.clear()
        chart.refresh()
        below.refresh()
        bar.refresh()

    @ui.refreshable
    def bar() -> None:
        if counts["excerpts"]:
            _filters_bar(filters, levels, counts["excerpts"] - sum(map(len, kept())), filtered)

    @ui.refreshable
    def chart() -> None:
        on_ = on()
        items.clear()
        _chart(
            spans if on_["career"] else [],
            [t for t in directed if t.year] if on_["theses"] else [],
            [t for t in juries if t.year] if on_["juries"] else [],
            kept()[0] if on_["excerpts"] else [],
            papers if on_["papers"] else {},
            nodes,
            period,
            picked,
            pick,
            state["zoom"],
            items,
            layout,
            state["drawn"],
        )
        state["drawn"] = True

    @ui.refreshable
    def below() -> None:
        """The details of what is picked, and what is listed apart."""
        on_ = on()
        _line_details(person_id, spans, directed, juries, picked, pick, show_tab, rebuild)
        if on_["excerpts"]:
            spans_, undated_, left_out_ = kept()
            _details(spans_, picked, paths, pick, acts)
            if undated_:
                _undated_excerpts(undated_, paths, acts)
            if left_out_:
                _left_out(left_out_, paths, acts)
        if on_["career"] and no_date:
            _undated_career(no_date, show_tab)

    def toggle(kind: str, value: bool) -> None:
        shown[kind] = value
        chart.refresh()
        below.refresh()

    def zoom(e) -> None:
        """The slider released (its years shown already, see vrTimelineZoom): the chart
        drawn again only if its lanes change (the room for the labels), else as it is."""
        try:
            z = max(ZOOM_MIN, min(ZOOM_MAX, float(e.args)))
        except (TypeError, ValueError):
            return
        state["zoom"] = z
        if layout.get("room") != _room(layout.get("n", 1), z):
            chart.refresh()
            return
        if (grid := layout.get("grid")) is not None and not grid.is_deleted:
            grid.style(f"--tl-zoom:{z}")
        if (names := layout.get("names")) is not None and not names.is_deleted:
            names.set_content(_year_names_css(layout["n"], z))

    with ui.column().classes("w-full gap-2").mark("timeline"):
        ui.label(
            _(
                "What is dated about them, by year (the oldest on the left): their career and "
                "the PhDs they supervised (a bar along their years), their excerpts (the "
                "beginning of their text above a thin bar along their years) and their papers "
                "(their number a year). Clicked, what it is shown below: a line of their career "
                "or a PhD student, edited there; an excerpt, or a year's: clicked there, its PDF "
                "beside the page, at it; its pencil edits it (its years…). An excerpt without "
                "years of its own is at its paper's year. The slider widens the years; the chips "
                "filter the excerpts (by marker, by level)."
            )
        ).classes("text-sm text-grey")
        with ui.row().classes("w-full items-start no-wrap gap-2"):
            with ui.column().classes("grow gap-1"):
                _legend(shown, counts, period, toggle)
                bar()
            # (wider years, the timeline then scrolled; or back to fitting the width: at once
            # while moved, kept once released)
            with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                ui.icon("zoom_out", color="grey", size="xs")
                ui.slider(min=ZOOM_MIN, max=ZOOM_MAX, step=ZOOM_STEP, value=state["zoom"]).props(
                    "dense"
                ).classes("w-28").on(
                    "update:model-value", js_handler="(v) => vrTimelineZoom(v)"
                ).on("change", zoom).tooltip(
                    _("The width of the years (left: the whole timeline fitting the width)")
                ).mark("timeline-zoom")
                ui.icon("zoom_in", color="grey", size="xs")
        chart()
        below()


def _passes(e: categories.ExcerptView, filters: dict, default: str | None) -> bool:
    """Whether an excerpt has one of the markers of ``filters`` and one of its levels (that
    of an excerpt without one: the folder's ``default``); none of either: whatever."""
    if (markers := filters["markers"]) and not any(getattr(e, k) for k in markers):
        return False
    return not (levels := filters["levels"]) or (e.level or default) in levels


def _filters_bar(
    filters: dict, levels: dict[str, str], hidden: int, changed: Callable[[], None]
) -> None:
    """The excerpts filtered by their markers and their levels (several of each: one of
    them; both: both), each a chip to tick; how many they hide, to clear them."""

    def chip(kind: str, key: str, label: str, icon: str | None, colour: str) -> None:
        def toggle(e) -> None:
            (filters[kind].add if e.value else filters[kind].discard)(key)
            changed()

        on = key in filters[kind]
        ui.chip(
            label,
            icon=icon,
            color=colour,
            text_color="white" if on else None,
            selectable=True,
            selected=on,
            on_selection_change=toggle,
        ).props("dense" if on else "dense outline").mark(f"timeline-filter-{kind[:-1]}-{key}")

    with ui.row().classes("items-center gap-x-1 gap-y-0").mark("timeline-filters"):
        ui.icon("filter_alt", color="grey", size="xs").tooltip(
            _(
                "Only the excerpts with one of the markers ticked, and of one of the levels "
                "ticked (none ticked: all)"
            )
        )
        ui.label(_("Excerpts:")).classes("text-sm text-grey")
        for key, (label, _tip, icon, colour) in MARKER_ICONS.items():
            chip("markers", key, _(label), icon, colour)
        ui.label("·").classes("mx-1 text-grey")
        if levels:
            for key, tag in levels.items():
                chip("levels", key, tag, None, "primary")
        else:
            ui.label(
                _("No levels in the folder's settings (Settings → Folder settings → Excerpts)")
            ).classes("text-xs text-grey").mark("timeline-filter-no-levels")
        if hidden:
            ui.label(
                ngettext(
                    "{n} excerpt hidden by the filters",
                    "{n} excerpts hidden by the filters",
                    hidden,
                ).format(n=hidden)
            ).classes("text-xs text-orange-9 ml-2").mark("timeline-filter-hidden")
        if filters["markers"] or filters["levels"]:
            ui.button(
                _("Clear"),
                icon="filter_alt_off",
                on_click=lambda: (filters["markers"].clear(), filters["levels"].clear(), changed()),
            ).props("flat dense no-caps size=sm").mark("timeline-filter-clear")


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
        "juries": (_("Thesis juries"), "gavel", KIND_COLOUR["jury"]),
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
            if kind == "juries" and counts[kind]:  # (a dot each, coloured by its role)
                with ui.row().classes("items-center gap-1 no-wrap").mark("timeline-legend-jury"):
                    for role, colour in JURY_COLOUR.items():
                        ui.element("span").classes("vr-tl-dot inline-block").style(f"--c:{colour}")
                        ui.label(ROLE_LABELS[role]).classes("text-xs text-grey mr-1")
        if period.start_year or period.end_year:
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.element("span").classes("inline-block w-4 h-4 rounded").style(
                    "background:rgba(0,150,136,.25)"
                )
                ui.label(
                    _("their period in the folder ({years})").format(
                        years=categories.years_label(period.start_year, period.end_year)
                    )
                ).classes("text-sm text-grey")
        if counts["excerpts"]:  # (the looks of the excerpts' dates)
            with ui.row().classes("items-center gap-1 no-wrap").mark("timeline-legend-dot"):
                ui.element("span").classes("vr-tl-dot inline-block").style("--c:#8c959f")
                ui.label(_("one-off event")).classes("text-sm text-grey")
            with ui.row().classes("items-center gap-1 no-wrap").mark("timeline-legend-approx"):
                ui.element("span").classes("vr-tl-approx inline-block w-6").style("--c:#57606a")
                ui.label(_("within the period (no precise date)")).classes("text-sm text-grey")
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
        "juries": _("No thesis jury they were on: they come from their theses.fr profile."),
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
    juries: list[tl.Jury],
    excerpts: list[_Span],
    papers: dict[int, list[str]],
    nodes: list[categories.Node],
    period: Period,
    picked: dict[str, int],
    pick: Callable[..., None],
    zoom: float,
    items: dict[tuple[str, int], ui.element],
    layout: dict,
    keep: bool,
) -> None:
    """The years, from the earliest of the items (papers not much earlier than the rest) to
    this year at least; a row for each kind (the excerpts: for each main category); a year
    ``width`` rem wide (none: the years fitting the width); ``items``: given its items (by kind
    and id); ``layout``: given its number of years ("n"), the room for a label ("room"), its
    grid and the style naming its years ("grid", "names"); ``keep``: drawn again, scrolled
    where it was."""
    this_year = date.today().year
    core = [y for sp in [*spans, *directed, *juries, *excerpts] for y in (sp.first, sp.last)]
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
    room = _room(n, zoom)
    layout.clear()
    layout.update(n=n, room=room)

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
    if juries:
        lanes = tl.lanes(juries, room=room)
        rows.append(("juries", _("Juries"), "gavel", KIND_COLOUR["jury"], lanes, "juries", []))
    for main, xs in _by_main(excerpts, nodes):
        lanes = tl.lanes(xs, room=room)
        name, colour = (main.name, main.colour) if main else (_("Excerpts"), "var(--q-primary)")
        mark = f"excerpts timeline-row-main-{main.id if main else 0}"
        rows.append(("excerpts", name, "format_quote", colour, lanes, mark, xs))
    if papers:
        rows.append(("papers", _("Papers"), "article", KIND_COLOUR["papers"], 1, "papers", []))
    total = 1 + sum(row[4] for row in rows)
    cover = {y for x in excerpts for y in range(x.first, x.last + 1)}

    with ui.element("div").classes("vr-tl-wrap w-full").mark("timeline-chart") as wrap:
        if n > NAMED_YEARS:
            layout["names"] = ui.html(_year_names_css(n, zoom), sanitize=False).classes("hidden")
        # (wider than the page: scrolled to the period, else to this year)
        ui.run_javascript(f"vrTimelineScroll({wrap.id}, {str(keep).lower()})")
        with (
            ui.element("div")
            .classes(f"vr-tl vr-tl-n{n}")
            .style(f"--n:{n};--tl-zoom:{zoom};grid-template-rows:repeat({total}, auto)")
        ) as grid:
            layout["grid"] = grid
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
                full = full or any(
                    sp.first <= year <= sp.last for sp in [*spans, *directed, *juries]
                )
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
                        items["career", sp.entry.id] = _career(sp, row, col, end, picked, pick)
                elif kind == "theses":
                    for t, end in _cells(directed, hi):
                        items["thesis", t.id] = _thesis(t, row, col, end, picked, pick)
                elif kind == "juries":
                    for t, end in _cells(juries, hi):
                        items["jury", t.id] = _jury(t, row, col, end, picked, pick)
                elif kind == "excerpts":
                    for x, end in _cells(xs, hi):
                        items["excerpt", x.d.excerpt.id] = _excerpt(x, row, col, end, picked, pick)
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


def _room(n: int, zoom: float) -> int:
    """The years' room for a label, about (``n`` years, ``zoom`` times wider than fitting the
    width): the narrower they are, the more."""
    return max(1, min(6, math.ceil(n / (6 * zoom))))


def _year_names_css(n: int, zoom: float) -> str:
    """Beyond NAMED_YEARS years, those not a fifth one unnamed when too narrow for all (the
    years ``zoom`` times wider than fitting the width)."""
    width = LABEL_PX + n * NAMED_PX / zoom
    return (
        f"<style>@container (max-width:{width:.0f}px)"
        f"{{.vr-tl-n{n} .vr-tl-year.minor span{{visibility:hidden}}}}</style>"
    )


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
    dot: float | None = None,
) -> ui.element:
    """A span (``sp``: its ``first``, ``last`` years, its ``lane``; see timeline.bounds) in
    its row's lane, over the years it is in: its ``text`` (as far as ``end``, where the next
    one in its lane begins) above its bar along its time (``now``: still going on); ``dot``:
    a moment instead (where, in years: e.g. the middle of one), a dot there."""
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
        if dot is not None:
            ui.element("div").classes("vr-tl-bar").style(
                f"margin-left:calc({pct(dot - sp.first)} - 4.5px)"
            )
            item.classes(add="dot")
        else:
            ui.element("div").classes("vr-tl-bar").style(
                f"margin-left:{pct(begin - sp.first)};width:{pct(stop - begin)}"
            )
    return item


def _career(
    sp: tl.CareerSpan,
    row: int,
    col: Callable[[int], int],
    end: int,
    picked: dict[str, int],
    pick: Callable[..., None],
) -> ui.element:
    """A line of the career, along its years (clicked: in the details)."""
    e = sp.entry
    colour = KIND_COLOUR["diploma" if e.kind == "diploma" else "position"]
    item = _bar(
        colour,
        sp,
        row,
        col,
        end,
        e.current,
        e.line(),
        f"timeline-career-{e.id}",
        " picked" if picked.get("career") == e.id else "",
    )
    item.on("click", lambda: pick("career", e.id))
    with item, ui.tooltip().classes("max-w-sm"):
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon("school" if e.kind == "diploma" else "work", size="xs")
            ui.label(e.when() + (f" ({_('current')})" if e.current else ""))
        ui.label(e.line()).classes("text-sm font-medium")
        if e.details:
            ui.label(e.details)
        ui.label(_("Click: its details, to edit it")).classes("italic")
    return item


def _thesis(
    t: tl.Supervised,
    row: int,
    col: Callable[[int], int],
    end: int,
    picked: dict[str, int],
    pick: Callable[..., None],
) -> ui.element:
    """A thesis they supervised, from its start to its defence (in progress: to now;
    clicked: in the details)."""
    text = t.student or t.title
    item = _bar(
        KIND_COLOUR["thesis"],
        t,
        row,
        col,
        end,
        t.in_progress,
        text,
        f"timeline-thesis-{t.id}",
        " picked" if picked.get("thesis") == t.id else "",
    )
    item.on("click", lambda: pick("thesis", t.id))
    with item, ui.tooltip().classes("max-w-sm"):
        ui.label(
            _("PhD of {student}").format(student=t.student or "?")
            + (f", {t.years}" if t.years else "")
            + (f" ({_('in progress')})" if t.in_progress else "")
        )
        ui.label(t.title).classes("text-sm font-medium")
        ui.label(_("Click: its details, to edit it")).classes("italic")
    return item


def _jury_line(t: tl.Jury) -> str:
    """Its role, the student, the date of the defence."""
    return " · ".join(
        filter(
            None,
            [ROLE_LABELS[t.role], _("PhD of {student}").format(student=t.student or "?"), t.date],
        )
    )


def _jury(
    t: tl.Jury,
    row: int,
    col: Callable[[int], int],
    end: int,
    picked: dict[str, int],
    pick: Callable[..., None],
) -> ui.element:
    """A thesis they were on the jury of: a dot at its defence (to the month, if known), in
    its role's colour, the student's name above it (clicked: in the details)."""
    item = _bar(
        JURY_COLOUR.get(t.role, KIND_COLOUR["jury"]),
        t,
        row,
        col,
        end,
        False,
        t.student or t.title,
        f"timeline-jury-{t.id}",
        " picked" if picked.get("jury") == t.id else "",
        dot=t.at,
    )
    item.on("click", lambda: pick("jury", t.id))
    with item, ui.tooltip().classes("max-w-sm"):
        ui.label(_jury_line(t))
        ui.label(t.title).classes("text-sm font-medium")
        ui.label(_("Click: its details")).classes("italic")
    return item


def _excerpt(
    x: _Span,
    row: int,
    col: Callable[[int], int],
    end: int,
    picked: dict[str, int],
    pick: Callable[..., None],
) -> ui.element:
    """An excerpt: the beginning of its text above a thin bar along its years (a single
    one: that year; at its paper's: hollow; within the period: dotted; since a year: up to
    now, fading), or a dot (a one-off event); clicked shown below."""
    e = x.d.excerpt
    classes = (
        " thin"
        + (" paper" if x.d.paper_year else "")
        + (" approx" if x.approx else "")
        + (" off" if e.discarded else "")
        + (" picked" if picked.get("excerpt") == e.id else "")
    )
    item = _bar(
        e.colour,
        x,
        row,
        col,
        end,
        x.now,
        e.quoted,
        f"timeline-span-{e.id}",
        classes,
        dot=x.first + 0.5 if x.dot else None,
    )
    item.on("click", lambda: pick("excerpt", e.id))
    with item, ui.tooltip().classes("max-w-sm"):
        if x.d.paper_year:
            when = _("the paper's year ({year})").format(year=x.first)
        else:
            when = _when(e, x.d.period or (None, None))
        if e.punctual:
            when += f" · {_('one-off event')}"
        ui.label(when).classes("font-medium")
        ui.label(e.quoted).classes("line-clamp-4")
    return item


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


def _line_details(
    person_id: int,
    spans: list[tl.CareerSpan],
    directed: list[tl.Supervised],
    juries: list[tl.Jury],
    picked: dict[str, int],
    pick: Callable[..., None],
    show_tab: Callable[[str], None],
    rebuild: Callable[[], None],
) -> None:
    """The line of their career, or the PhD student, picked: all about it, to edit (the
    timeline drawn again then), or to see in its tab; a thesis they were on the jury of,
    to see in its tab."""
    from .person import _career_dialog, _student_dialog  # (it uses this module)

    if (cid := picked.get("career")) and (
        sp := next((x for x in spans if x.entry.id == cid), None)
    ):
        e = sp.entry
        icon, colour = ("school", "diploma") if e.kind == "diploma" else ("work", "position")
        lines = [e.when() + (f" ({_('current')})" if e.current else ""), e.details or ""]
        title, tab, tab_name = e.line(), "profile", _("Profile tab")
        which = f"career-{e.id}"
        edit = lambda: _career_dialog(person_id, e, rebuild)  # noqa: E731
    elif (tid := picked.get("thesis")) and (t := next((x for x in directed if x.id == tid), None)):
        icon, colour = "school", "thesis"
        lines = [
            _("PhD of {student}").format(student=t.student or "?")
            + (f", {t.years}" if t.years else "")
            + (f" ({_('in progress')})" if t.in_progress else "")
        ]
        title, tab, tab_name = t.title, "theses", _("Theses tab")
        which = f"thesis-{t.id}"

        def edit() -> None:
            found = next((x for x in persons.theses(person_id, "director") if x.id == tid), None)
            if found is not None:
                _student_dialog(person_id, found, rebuild)

    elif (jid := picked.get("jury")) and (j := next((x for x in juries if x.id == jid), None)):
        icon, colour = "gavel", "jury"
        lines = [_jury_line(j)]
        title, tab, tab_name = j.title, "theses", _("Theses tab")
        which = f"jury-{j.id}"
        edit = None
    else:
        return
    with (
        ui.column()
        .classes("w-full gap-1 mt-2 vr-tl-card")
        .style(f"--c:{KIND_COLOUR[colour]};border-left:3px solid var(--c);padding-left:.5rem")
        .mark("timeline-line-details", f"timeline-line-{which}")
    ):
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.icon(icon, size="sm").style(f"color:{KIND_COLOUR[colour]}")
            ui.label(title).classes("font-medium")
            ui.space()
            if edit is not None:
                ui.button(_("Edit"), icon="edit", on_click=edit).props("flat dense no-caps").mark(
                    "timeline-line-edit"
                )
            ui.button(tab_name, icon="open_in_new", on_click=lambda: show_tab(tab)).props(
                "flat dense no-caps"
            ).mark("timeline-line-tab")
            ui.button(icon="close", on_click=lambda: pick()).props(
                "flat dense round size=sm"
            ).tooltip(_("Close")).mark("timeline-line-close")
        for line in filter(None, lines):
            ui.label(line).classes("text-sm text-grey-8")


def _details(
    excerpts: list[_Span],
    picked: dict[str, int],
    paths: dict[int, str],
    pick: Callable[..., None],
    acts: _Acts,
) -> None:
    """The excerpts picked on the timeline: those of a year (along it, or in it), or one."""
    if not excerpts or picked.keys() & {"career", "thesis", "jury"}:
        return
    with ui.column().classes("w-full gap-2 mt-2").mark("timeline-details"):
        if not picked:
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("touch_app", color="grey", size="xs")
                ui.label(_("Click an item or a year to see it here.")).classes(
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
            title = _("An excerpt, {years}").format(
                years=_when(found[0].d.excerpt, acts.within) if found else ""
            )
        with ui.row().classes("items-center gap-2 w-full"):
            ui.icon("format_quote", color="primary")
            ui.label(title).classes("font-medium")
            ui.space()
            ui.button(icon="close", on_click=lambda: pick()).props(
                "flat dense round size=sm"
            ).tooltip(_("Close")).mark("timeline-details-close")
        with ui.element("div").classes("vr-tl-details w-full"):
            for x in found:
                _item(x.d.excerpt, paths, acts, x.d.paper_year)


def _undated_excerpts(
    undated: list[categories.ExcerptView],
    paths: dict[int, str],
    acts: _Acts,
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
                _item(e, paths, acts)


def _left_out(
    left_out: list[categories.ExcerptView],
    paths: dict[int, str],
    acts: _Acts,
) -> None:
    """The excerpts left out of the timeline (their switch off): listed, folded."""
    with (
        ui.expansion(
            ngettext(
                "{n} excerpt left out of the timeline",
                "{n} excerpts left out of the timeline",
                len(left_out),
            ).format(n=len(left_out)),
            icon="visibility_off",
        )
        .props("dense")
        .classes("w-full mt-4")
        .mark("timeline-left-out")
    ):
        ui.label(
            _("Still kept, and in the Excerpts tab: their switch puts them back on the timeline.")
        ).classes("text-sm text-grey")
        with ui.element("div").classes("vr-tl-details w-full"):
            for e in left_out:
                _item(e, paths, acts)


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
    acts: _Acts,
    paper_year: bool = False,
) -> None:
    """An excerpt (or a group: its text): its category, years, markers, its text and where
    it is from; clicked, its PDF beside the page at it; its pencil, its dialog."""
    with (
        ui.column()
        .classes("w-full gap-0 cursor-pointer rounded" + (" opacity-50" if e.discarded else ""))
        .style(f"border-left:3px solid {e.colour};padding:.15rem .5rem")
        .on("click", lambda: acts.show(e))
        .tooltip(_("Click: its PDF beside the page, at it"))
        .mark(f"timeline-excerpt-{e.id}")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.label(paths.get(e.category_id, "")).classes("text-xs text-grey-8")
            if paper_year:
                ui.badge(_("the paper's year"), color="grey").props("outline").tooltip(
                    _("No years of its own: placed at the year of its paper")
                ).mark(f"timeline-paper-year-{e.id}")
            elif when := _when(e, acts.within):
                ui.badge(when, color="blue-grey").props("outline").mark(f"timeline-years-{e.id}")
            if e.punctual:
                ui.badge(_("one-off event"), color="blue-grey").props("outline").mark(
                    f"timeline-punctual-{e.id}"
                )
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
            # (its buttons and switch: clicked, the PDF not opened)
            with (
                ui.row()
                .classes("items-center gap-0 no-wrap shrink-0")
                .on("click", js_handler="(e) => e.stopPropagation()")
            ):
                ui.button(icon="edit", on_click=lambda: acts.edit(e)).props(
                    "flat dense round size=xs color=grey-8"
                ).tooltip(_("Edit it (its years, markers, text…)")).mark(f"timeline-edit-{e.id}")
                ui.button(icon="format_list_bulleted", on_click=lambda: acts.select(e.id)).props(
                    "flat dense round size=xs color=grey-8"
                ).tooltip(_("See it in the Excerpts tab")).mark(f"timeline-select-{e.id}")
            with (
                ui.row()
                .classes("items-center gap-0 no-wrap shrink-0")
                .on("click", js_handler="(e) => e.stopPropagation()")
                .tooltip(_("On the timeline (off: left out of it, still kept)"))
            ):
                ui.icon("timeline", size="xs", color="grey")
                ui.switch(
                    value=e.in_timeline, on_change=lambda v: acts.on_timeline(e, v.value)
                ).props("dense size=xs").mark(f"timeline-switch-{e.id}")
