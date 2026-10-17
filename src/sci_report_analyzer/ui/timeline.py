"""A person's timeline within a folder: their excerpts placed by their years (see
categories.timeline), those without one listed apart."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import categories
from ..i18n import _, ngettext
from .viewer_side import MARKER_ICONS


def timeline_view(
    period_id: int,
    folder_id: int,
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
) -> None:
    """The timeline of a person's excerpts in a folder: each clicked, selected in the
    Excerpts tab (``select``); its PDF opened beside the page (``open_pdf``)."""
    dated, undated = categories.timeline(period_id)
    paths = {n.id: n.path for n in categories.tree(folder_id)}
    with ui.column().classes("w-full gap-2").mark("timeline"):
        if not dated and not undated:
            ui.label(
                _(
                    "No excerpt yet: file passages of their documents in the Excerpts tab (E "
                    "on a selection of a PDF beside the page), with their years, to see them "
                    "here."
                )
            ).classes("text-grey").mark("timeline-empty")
            return
        ui.label(
            _(
                "Their excerpts by year, the most recent first: each at its first year (a "
                "paper's excerpt without years: at the paper's). Click one to see it in the "
                "Excerpts tab, where its years are edited (its pencil)."
            )
        ).classes("text-sm text-grey")
        for year, items in dated:
            with ui.row().classes("w-full items-start gap-3 no-wrap").mark(f"timeline-{year}"):
                ui.label(str(year)).classes("w-12 shrink-0 text-right font-medium text-lg")
                with (
                    ui.column()
                    .classes("grow min-w-0 gap-2 pl-3")
                    .style("border-left: 2px solid var(--q-primary)")
                ):
                    for d in items:
                        _item(d.excerpt, paths, select, open_pdf, d.paper_year)
        if undated:
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
                        "Not on the timeline: set their years in the Excerpts tab (an "
                        "excerpt's pencil) to place them."
                    )
                ).classes("text-sm text-grey")
                with ui.column().classes("w-full gap-2 pl-3").style("border-left: 2px dashed grey"):
                    for e in undated:
                        _item(e, paths, select, open_pdf)


def _item(
    e: categories.ExcerptView,
    paths: dict[int, str],
    select: Callable[[int], None],
    open_pdf: Callable[[categories.ExcerptView], None],
    paper_year: bool = False,
) -> None:
    """An excerpt (or a group: its text) on the timeline: its category, years, markers, its
    text and where it is from."""
    with (
        ui.column()
        .classes("w-full gap-0 cursor-pointer" + (" opacity-50" if e.discarded else ""))
        .style(f"border-left: 3px solid {e.colour}; padding-left: 0.5rem")
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
