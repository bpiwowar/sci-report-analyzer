"""Publications panel: statistics, with periods, tags and notes."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
from collections.abc import Callable
from html import escape
from itertools import groupby
from urllib.parse import urlencode

from nicegui import background_tasks, ui

from .. import annotations, contribution, manual, source_settings
from ..i18n import N_, _, ngettext
from ..pubview import (
    HIST_CAP,
    SUMMARY_DETAILS,
    SUMMARY_LANGUAGES,
    PubStat,
    Sel,
    category_list,
    load_stats,
    match_sels,
    pick_sel,
    save_summary_settings,
    summary_lines,
    summary_settings,
    year_bin_defs,
)
from ..ranking import tracks
from ..ranking.badge import KIND_ORDER, PREDATORY_COLOUR
from ..ranking.kinds import KIND_SHORT
from ..sources import ADAPTERS
from .dialogs import actions, ok_handler, transient_dialog
from .pdf_viewer import download_dialog, pdf_button, watch
from .pub_details import open_details, source_badge
from .reflist import tag_from_list
from .tags import tag_chip, tags_dialog
from .theme import (
    DIM_OPACITY,
    NOTE_EXTRAS,
    author_html,
    int_or_none,
    rank_chip,
    span,
    stripes,
    track_chip,
)

logger = logging.getLogger(__name__)

DISCLAIMER = N_(
    "These rankings rate the venue (journal / conference), not the quality or impact "
    "of any individual paper."
)


def encode_sels(sels: list[Sel]) -> str:
    return json.dumps([dataclasses.asdict(s) for s in sels], separators=(",", ":"))


def decode_sels(text: str | None) -> list[Sel]:
    try:
        value = json.loads(text) if text else []
        # (earlier links: a single selection)
        return [Sel(**s) for s in (value if isinstance(value, list) else [value])]
    except (ValueError, TypeError):
        return []


def click_mode(e) -> dict[str, bool]:
    """The modifier keys of a click (shift: add or remove, alt: only this one)."""
    args = getattr(e, "args", None)
    if isinstance(args, list):
        args = args[0] if args else None
    args = args if isinstance(args, dict) else {}
    return {"add": bool(args.get("shiftKey")), "only": bool(args.get("altKey"))}


# A click on a bar, with its modifier keys (the chart's own event has no serialisable keys).
CHART_CLICK_JS = (
    "(e) => e.componentType === 'series' && emit({seriesName: e.seriesName, "
    "dataIndex: e.dataIndex, shiftKey: !!e.event?.event?.shiftKey, "
    "altKey: !!e.event?.event?.altKey})"
)


def on_chart_click(chart: ui.echart, handler: Callable[[str, int, dict[str, bool]], None]) -> None:
    """Call ``handler(series name, data index, modifiers)`` on a click on a bar."""
    chart.on(
        "componentClick",
        lambda e: handler(e.args["seriesName"], e.args["dataIndex"], click_mode(e)),
        js_handler=CHART_CLICK_JS,
    )


MULTI_TIP = N_("Shift-click: add to the selection (or remove) · Alt-click: only this one")

# The summary's table of detail levels: hovering a cell highlights its column and row headers.
_HL = "{background:var(--q-primary);color:white !important;border-radius:4px}"
ui.add_css(
    "".join(
        f'.vr-sum:has(.vr-sum-cell[data-col="{level}"]:hover) .vr-sum-col[data-col="{level}"]{_HL}'
        for level in SUMMARY_DETAILS
    )
    + f".vr-sum-line:has(.vr-sum-cell:hover) .vr-sum-row{_HL}",
    shared=True,
)


class PublicationsPanel:
    def __init__(
        self,
        person_id: int,
        period_id: int | None = None,
        query: dict[str, str] | None = None,
    ) -> None:
        self.person_id = person_id
        # Where the name variants to review are shown (the Sources tab), if anywhere.
        self.names_box: ui.element | None = None
        self.stats: list[PubStat] = []
        self.loaded = False
        self.lo: int | None = None
        self.hi: int | None = None
        # Cross-filter selections: papers matching one of each facet's (e.g. Q1 or CORE A*).
        self.sels: list[Sel] = []
        self.period_id: int | None = None
        # Tags to show (any of them): global ones, and ones within the period.
        self.tag_filter: list[int] = []
        # Tracks to show (any of them; tracks.MAIN: the main track).
        self.track_filter: list[str] = []
        self.text = ""
        self.hide_preprints = False
        self.show_hidden = False
        # The source a paper must be in to count (None: any), and whether to show only the
        # papers it doesn't list (to fix them there).
        self.primary: str | None = None
        self.show_outside = False
        self.problems_only = False
        self.manual_only = False  # papers whose venue, rank or kind was decided by hand
        self.venue_filter: int | None = None  # only the papers of a venue
        self.open_pub: int | None = None  # a publication to open once loaded
        # Called on each render (e.g. the Theses tab, which follows the period).
        self.on_render: list[Callable[[], None]] = []
        self.tags = annotations.all_tags()
        self.starred_id = annotations.starred_tag_id()
        self.categories = annotations.author_categories()
        self.cat_colours = {f"cat:{c.id}": c.colour for c in self.categories}
        # The chosen period (and its tag filter) stick across visits; a period given in the
        # URL (e.g. opened from a folder) wins.
        state = annotations.panel_state(person_id)
        self._load_periods(period_id or state.get("period_id"))
        if period_id and any(p.id == period_id for p in self.periods):
            self.period_id = period_id
            if period_id == state.get("period_id"):
                self.tag_filter = self._saved_tags(state)
            self._apply_period()
            self._save_state()
        elif any(p.id == state.get("period_id") for p in self.periods):
            self.period_id = state["period_id"]
            self.tag_filter = self._saved_tags(state)
            self._apply_period()
        # Filters from the URL (so that a reload gives back the same view).
        self.base_url = f"/person/{person_id}"
        self.url_extra: dict[str, str] = {}
        self._from_query(query or {})

    def _saved_tags(self, state: dict) -> list[int]:
        return self._known_tags(state.get("tag_filter", []))

    def _known_tags(self, ids) -> list[int]:
        """The tags that can filter: global ones, and per-period ones with a period."""
        usable = {t.id for t in self.tags if self.period_id or not t.per_period}
        return [i for i in ids if i in usable]

    # ---- URL state ----------------------------------------------------------------------

    def _from_query(self, q: dict[str, str]) -> None:
        if "tags" in q:
            self.tag_filter = self._known_tags(int_or_none(x) for x in q["tags"].split(","))
        elif "starred" in q and self.period_id:  # earlier links
            self.tag_filter = [self.starred_id] if q["starred"] == "1" else []
        self.track_filter = [t for t in q.get("tracks", "").split(",") if t]
        if "from" in q:
            self.lo = int_or_none(q["from"])
        if "to" in q:
            self.hi = int_or_none(q["to"])
        self.text = q.get("q", "")
        self.hide_preprints = q.get("nopre") == "1"
        self.show_hidden = q.get("hidden") == "1"
        self.problems_only = q.get("problems") == "1"
        self.manual_only = q.get("manual") == "1"
        self.venue_filter = int_or_none(q.get("venue", "")) or None
        self.open_pub = int_or_none(q.get("pub", "")) or None
        self.sels = decode_sels(q.get("sel"))

    def url_params(self) -> dict[str, str]:
        out = dict(self.url_extra)
        p = next((p for p in self.periods if p.id == self.period_id), None)
        if self.tag_filter:
            out["tags"] = ",".join(map(str, self.tag_filter))
        if self.track_filter:
            out["tracks"] = ",".join(self.track_filter)
        if self.lo is not None and (not p or self.lo != p.start_year):
            out["from"] = str(self.lo)
        if self.hi is not None and (not p or self.hi != p.end_year):
            out["to"] = str(self.hi)
        if p and self.lo is None and p.start_year is not None:
            out["from"] = ""
        if p and self.hi is None and p.end_year is not None:
            out["to"] = ""
        if self.text:
            out["q"] = self.text
        for key, on in (
            ("nopre", self.hide_preprints),
            ("hidden", self.show_hidden),
            ("problems", self.problems_only),
            ("manual", self.manual_only),
        ):
            if on:
                out[key] = "1"
        if self.sels:
            out["sel"] = encode_sels(self.sels)
        if self.venue_filter:
            out["venue"] = str(self.venue_filter)
        return out

    def push_url(self) -> None:
        """Show the current filters in the address bar (without reloading)."""
        query = urlencode(self.url_params())
        path = self.base_url + (f"/{self.period_id}" if self.period_id else "")
        url = path + (f"?{query}" if query else "")
        with contextlib.suppress(Exception):  # e.g. the page was left meanwhile
            ui.run_javascript(f"history.replaceState(history.state, '', {json.dumps(url)})")

    # ---- data ---------------------------------------------------------------------------

    def build(self) -> None:
        # Dialogs (publication details) live outside the re-rendered container.
        self.dialogs = ui.element("div")
        self.container = ui.column().classes("w-full gap-3")
        watch(self)  # (changes made from PDF windows)
        with self.container:
            ui.spinner(size="lg")
        ui.timer(0.05, self.reload, once=True)

    async def reload(self) -> None:
        try:
            self.stats = await load_stats(self.person_id)
            self.primary = source_settings.primary_for(self.person_id, self.period_id)
            self.categories = annotations.author_categories()
            self.cat_colours = {f"cat:{c.id}": c.colour for c in self.categories}
            if self.container.is_deleted:
                return  # the page was left while loading
            self.loaded = True
            self.render()
            wanted, self.open_pub = self.open_pub, None
            if pub := next((x for x in self.stats if x.id == wanted), None):
                open_details(self, pub)
        except Exception as e:
            if self.container.is_deleted:
                return
            logger.exception("Could not render the publications panel")
            self.container.clear()
            with self.container:
                ui.label(_("Could not display publications: {error}").format(error=e)).classes(
                    "text-negative"
                )
                ui.button(_("Retry"), on_click=self.reload)

    def _load_periods(self, keep: int | None = None) -> None:
        """Periods to choose from (those of hidden folders only when selected)."""
        self.periods = [
            p
            for p in annotations.periods(self.person_id, include_hidden_folders=True)
            if not (p.folder and p.folder.hidden) or p.id == keep
        ]

    def reload_periods(self) -> None:
        self._load_periods(self.period_id)
        if self.period_id and not any(p.id == self.period_id for p in self.periods):
            self.period_id = None
        self.tag_filter = self._known_tags(self.tag_filter)
        self._apply_period()
        self.render()

    def _save_state(self) -> None:
        annotations.save_panel_state(
            self.person_id, {"period_id": self.period_id, "tag_filter": self.tag_filter}
        )

    def _apply_period(self) -> None:
        p = next((p for p in self.periods if p.id == self.period_id), None)
        if p:
            self.lo, self.hi = p.start_year, p.end_year

    # ---- filtering ----------------------------------------------------------------------

    def base_rows(self, *, problems_filter: bool = True) -> list[PubStat]:
        """Rows after the non-crossfilter filters (year range, period, tags, text)."""
        rows = []
        text = self.text.lower().strip()
        for s in self.stats:
            if not self.in_years(s):
                continue
            if self.tag_filter and not s.tags_in(self.period_id) & set(self.tag_filter):
                continue
            if self.track_filter and (s.track or tracks.MAIN) not in self.track_filter:
                continue
            if self.hide_preprints and s.archival_only:
                continue
            if s.hidden and not self.show_hidden:
                continue
            if self.primary and self.outside(s) != self.show_outside:
                continue
            if problems_filter and self.problems_only and not s.problems:
                continue
            if self.manual_only and not s.decided_by_hand:
                continue
            if self.venue_filter and s.venue_id != self.venue_filter:
                continue
            if (
                text
                and text not in (s.title or "").lower()
                and text not in (s.venue or "").lower()
                and text not in ((s.badge and s.badge.name) or "").lower()
                and not any(text in a.lower() for a in s.authors)
            ):
                continue
            rows.append(s)
        return rows

    def outside(self, s: PubStat) -> bool:
        """Whether the primary source doesn't list the paper (it doesn't count)."""
        return bool(self.primary) and self.primary not in s.sources

    def in_years(self, s: PubStat) -> bool:
        return s.year is None or (
            (self.lo is None or s.year >= self.lo) and (self.hi is None or s.year <= self.hi)
        )

    def not_shown(self, pubs: list[PubStat]) -> str:
        """Why papers are not shown: outside the year range, or hidden by the other filters."""
        out = sum(not self.in_years(s) for s in pubs)
        parts = (
            [
                _("{n} outside the years {start}–{end}").format(
                    n=out, start=self.lo or "…", end=self.hi or "…"
                )
            ]
            if out
            else []
        )
        if len(pubs) > out:
            parts.append(_("{n} hidden by the filters").format(n=len(pubs) - out))
        return ", ".join(parts)

    def pick(self, sel: Sel, e=None) -> None:
        """A click on a bar, legend entry or chip (shift: add or remove, alt: only this one)."""
        self.sels = pick_sel(self.sels, sel, **click_mode(e))
        self.render()

    def unpick(self, sel: Sel) -> None:
        self.sels = [x for x in self.sels if x != sel]
        self.render()

    def filtering(self) -> bool:
        """Whether some filter is on (the period and its years aside)."""
        return bool(
            self.sels
            or self.tag_filter
            or self.track_filter
            or self.text
            or self.hide_preprints
            or self.problems_only
            or self.manual_only
            or self.venue_filter
            or self.show_outside
        )

    def reset_filters(self) -> None:
        """Clear the filters (but the period and its years)."""
        self.sels = []
        self.tag_filter = []
        self.track_filter = []
        self.text = ""
        self.hide_preprints = self.problems_only = self.manual_only = self.show_outside = False
        self.venue_filter = None
        self._save_state()
        self.render()

    # ---- rendering ----------------------------------------------------------------------

    def render(self) -> None:
        for f in list(self.on_render):
            f()
        self.container.clear()
        with self.container:
            if not self.loaded:
                ui.spinner(size="lg")
                return
            self.render_names()
            if not self.stats:
                ui.label(
                    _("No publication yet: validate and sync sources in the Sources tab.")
                ).classes("text-grey")
                return
            filtered = self.base_rows()
            sels = self.sels

            def matching(*skip: str) -> list[PubStat]:
                """The papers of a chart: within the selections of the other charts."""
                return [s for s in filtered if match_sels(s, sels, skip)]

            conditioned = matching()
            self._toolbar(filtered, conditioned)
            cats = category_list(filtered)
            self._distribution(matching("category"), cats, sels)
            self._years(matching("year"), cats, sels)
            with ui.row().classes("w-full gap-4 no-wrap"):
                self._coauthors(matching("coauthors"), sels)
                self._contributions(matching("contribution", "phd", "authorcat"), sels)
            self._list(conditioned)
            ui.label(_(DISCLAIMER)).classes("text-xs text-grey")
        self.push_url()

    def _toolbar(self, filtered: list[PubStat], conditioned: list[PubStat]) -> None:
        years = [s.year for s in self.stats if s.year is not None]
        with ui.row().classes("w-full items-center gap-3"):
            n = (
                ngettext(
                    "{n} of {total} publication", "{n} of {total} publications", len(filtered)
                ).format(n=len(conditioned), total=len(filtered))
                if self.sels
                else ngettext("{n} publication", "{n} publications", len(filtered)).format(
                    n=len(filtered)
                )
            )
            ui.label(n).classes("text-lg font-medium")
            if len(filtered) != len(self.stats):
                ui.label(_("({n} in total)").format(n=len(self.stats))).classes("text-grey")
            ui.button(
                icon="summarize", on_click=lambda rows=conditioned: self._summary(rows)
            ).props("flat round dense").tooltip(
                _("Summary of the publications shown, by category (to copy)")
            ).mark("summary")
            ui.button(icon="playlist_add_check", on_click=lambda: tag_from_list(self)).props(
                "flat round dense"
            ).tooltip(_("Tag from a list: find the papers of a pasted list and tag them")).mark(
                "tag-from-list"
            )
            ui.button(
                icon="download_for_offline",
                on_click=lambda rows=conditioned: download_dialog(self, rows),
            ).props("flat round dense").tooltip(
                _("Download the open-access PDFs of the papers shown (to view and annotate them)")
            ).mark("download-pdfs")
            ui.button(icon="add", on_click=self._add_publication).props("flat round dense").tooltip(
                _("Add a publication by hand (DOI or HAL id)")
            ).mark("add-publication")

            options = {0: _("All years"), **{p.id: period_label(p) for p in self.periods}}

            def set_period(e) -> None:
                self.period_id = e.value or None
                if self.period_id:
                    self._apply_period()
                else:
                    self.lo = self.hi = None
                self.tag_filter = self._known_tags(self.tag_filter)
                self.primary = source_settings.primary_for(self.person_id, self.period_id)
                self.sels = []
                self._save_state()
                self.render()

            ui.select(options, value=self.period_id or 0, on_change=set_period).props(
                "dense outlined"
            ).classes("w-48").tooltip(_("Period of interest"))

            def set_year(which: str, v) -> None:
                setattr(self, which, int_or_none(v))
                self.sels = []
                self.render()

            if years:
                ui.number(
                    _("from"),
                    value=self.lo,
                    min=min(years),
                    max=max(years),
                    format="%d",
                    placeholder=str(min(years)),
                    on_change=lambda e: set_year("lo", e.value),
                ).props("dense outlined debounce=600").classes("w-24")
                ui.number(
                    _("to"),
                    value=self.hi,
                    min=min(years),
                    max=max(years),
                    format="%d",
                    placeholder=str(max(years)),
                    on_change=lambda e: set_year("hi", e.value),
                ).props("dense outlined debounce=600").classes("w-24")
            tag_options = {
                t.id: ("⏱ " if t.per_period else "") + t.name
                for t in self.tags
                if self.period_id or not t.per_period
            }
            if tag_options:

                def set_tags(e) -> None:
                    self.tag_filter = list(e.value or [])
                    self.sels = []
                    self._save_state()
                    self.render()

                ui.select(
                    tag_options,
                    value=self.tag_filter,
                    multiple=True,
                    label=_("tags"),
                    on_change=set_tags,
                ).props("dense outlined use-chips clearable").classes("min-w-32").tooltip(
                    _("Papers with one of these tags (⏱: within the period)")
                ).mark("tag-filter")
            ui.button(icon="sell", on_click=self.manage_tags).props("flat round dense").tooltip(
                _("Manage tags (names, colours)")
            ).mark("manage-tags-panel")
            # The tracks of the papers (in their order), when some are of one.
            present = {s.track for s in self.stats if s.track} | {
                t for t in self.track_filter if t != tracks.MAIN
            }
            if present:

                def set_tracks(e) -> None:
                    self.track_filter = list(e.value or [])
                    self.sels = []
                    self.render()

                order = {t: i for i, t in enumerate(tracks.track_ids())}
                ordered = sorted(present, key=lambda t: (order.get(t, len(order)), t))
                ui.select(
                    {tracks.MAIN: _("main track"), **{t: tracks.name(t) for t in ordered}},
                    value=self.track_filter,
                    multiple=True,
                    label=_("tracks"),
                    on_change=set_tracks,
                ).props("dense outlined use-chips clearable").classes("min-w-32").tooltip(
                    _("Papers of one of these tracks")
                ).mark("track-filter")

            def set_text(e) -> None:
                self.text = e.value or ""
                self.render()

            ui.input(
                placeholder=_("search title / venue / author"), value=self.text, on_change=set_text
            ).props("dense outlined clearable debounce=400").classes("w-64")

            def set_hide(e) -> None:
                self.hide_preprints = e.value
                self.render()

            ui.switch(_("hide preprints"), value=self.hide_preprints, on_change=set_hide)
            # Within the current filters (year range, period, tags, search…).
            n_problems = sum(bool(s.problems) for s in self.base_rows(problems_filter=False))
            if n_problems or self.problems_only:

                def set_problems(e) -> None:
                    self.problems_only = e.value
                    self.sels = []
                    self.render()

                ui.switch(
                    _("⚠ with problems ({n})").format(n=n_problems),
                    value=self.problems_only,
                    on_change=set_problems,
                ).mark("problems-only")
            n_manual = sum(s.decided_by_hand for s in self.base_rows())
            if n_manual or self.manual_only:

                def set_manual(e) -> None:
                    self.manual_only = e.value
                    self.sels = []
                    self.render()

                ui.switch(
                    _("✎ decided by hand ({n})").format(n=n_manual),
                    value=self.manual_only,
                    on_change=set_manual,
                ).mark("manual-only").tooltip(
                    _(
                        "Papers whose venue was picked or validated, or whose rank or kind was set "
                        "by hand"
                    )
                )
            if self.venue_filter:
                name = next(
                    (
                        m.venue_name
                        for x in self.stats
                        for m in x.members
                        if m.venue_id == self.venue_filter and m.venue_name
                    ),
                    None,
                ) or next((x.venue for x in self.stats if x.venue_id == self.venue_filter), "?")

                def clear_venue() -> None:
                    self.venue_filter = None
                    self.sels = []
                    self.render()

                ui.chip(
                    _("venue: {name}").format(name=name),
                    removable=True,
                    on_value_change=lambda e: clear_venue(),
                ).props("dense color=primary text-color=white").mark("venue-filter")
            n_hidden = sum(s.hidden for s in self.stats)
            if n_hidden:

                def set_show_hidden(e) -> None:
                    self.show_hidden = e.value
                    self.sels = []
                    self.render()

                ui.switch(
                    _("show hidden ({n})").format(n=n_hidden),
                    value=self.show_hidden,
                    on_change=set_show_hidden,
                )
            n_outside = sum(
                self.outside(s) for s in self.stats if not s.hidden and self.in_years(s)
            )
            if self.primary and (n_outside or self.show_outside):

                def set_show_outside(e) -> None:
                    self.show_outside = e.value
                    self.sels = []
                    self.render()

                ui.switch(
                    _("only those not in {source} ({n})").format(
                        source=ADAPTERS[self.primary].label, n=n_outside
                    ),
                    value=self.show_outside,
                    on_change=set_show_outside,
                ).tooltip(
                    _("Papers the primary source doesn't list: not counted (add them there)")
                ).mark("show-outside")
            for sel in self.sels:
                ui.chip(sel.label, removable=True, color="primary", text_color="white").on(
                    "remove", lambda s=sel: self.unpick(s)
                ).mark("selection")
            if self.filtering():
                ui.button(_("Reset"), icon="filter_alt_off", on_click=self.reset_filters).props(
                    "flat dense no-caps"
                ).tooltip(_("Clear the filters (the period and its years are kept)")).mark(
                    "reset-filters"
                )

    def render_names(self) -> None:
        """Refresh the name variants (shown outside the panel, in the Sources tab)."""
        if self.names_box is None or self.names_box.is_deleted:
            return
        self.names_box.clear()
        with self.names_box:
            self._name_variants()

    def _name_variants(self) -> None:
        """Spellings of the person's name seen in the sources (to confirm as aliases)."""
        from collections import Counter

        pending: Counter[str] = Counter()
        confirmed: Counter[str] = Counter()
        for st in self.stats:
            for i, m in enumerate(st.author_marks):
                if m == "owner?":
                    pending[st.authors[i]] += 1
                elif m == "owner":
                    confirmed[st.authors[i]] += 1
        if not pending and len(confirmed) <= 1:
            return
        title = (
            _("Name variants · {n} to review").format(n=len(pending))
            if pending
            else _("Name variants")
        )
        with ui.expansion(title, icon="badge", value=bool(pending)).classes("w-full"):
            if confirmed:
                ui.label(
                    _("Confirmed: {names}").format(
                        names=", ".join(f"{n} ({c})" for n, c in confirmed.most_common())
                    )
                ).classes("text-sm")
            for name, count in pending.most_common():
                with ui.row().classes("items-center gap-2"):
                    span(author_html(name, "owner?", None))
                    ui.label(ngettext("{n} paper", "{n} papers", count).format(n=count)).classes(
                        "text-xs text-grey"
                    )

                    def accept(n=name) -> None:
                        annotations.add_alias(self.person_id, n)
                        background_tasks.create(self.reload())

                    def reject(n=name) -> None:
                        annotations.reject_alias(self.person_id, n)
                        background_tasks.create(self.reload())

                    ui.button(_("it's them"), icon="check", on_click=accept).props("dense flat")
                    ui.button(_("not them"), icon="close", on_click=reject).props(
                        "dense flat color=negative"
                    )

    def _add_publication(self) -> None:
        """A paper the sources miss, by its DOI or its HAL id (or their URLs)."""
        with (
            self.dialogs,
            transient_dialog(_("Add a publication"), width="w-full max-w-xl") as (dlg, _card),
        ):
            ui.label(
                _(
                    "Its record is fetched and merged with the other sources' (listed in the "
                    "Sources tab, where it can be removed)."
                )
            ).classes("text-sm text-grey")
            ref = (
                ui.input(
                    _("DOI or HAL id, or a URL"),
                    placeholder=_("10.1145/…, hal-01234567, or a doi.org, publisher or HAL URL"),
                )
                .classes("w-full")
                .props("autofocus clearable")
                .mark("add-publication-ref")
            )
            busy = ui.spinner(size="sm")
            busy.visible = False

            async def add() -> bool:
                if busy.visible:
                    return False
                busy.visible = True
                try:
                    title = await manual.add_publication(self.person_id, ref.value or "")
                except ValueError as e:
                    ui.notify(str(e), type="warning")
                    return False
                finally:
                    busy.visible = False
                ui.notify(_("Added: {title}").format(title=title))
                await self.reload()
                return True

            ref.on("keydown.enter", ok_handler(dlg, add))
            actions(dlg, _("Add"), add, icon="add", mark="add-publication-ok")

    def _summary(self, rows: list[PubStat]) -> None:
        """The publications shown, by kind of venue and category with their venues and years.
        Kinds can be left out, and each category says more or less (a table of detail
        levels); the settings are remembered."""
        kinds = [k for k in (*KIND_ORDER, None) if any(_kind(r) == k for r in rows)]
        cats = category_list(rows)
        saved = summary_settings()
        off_kinds = set(saved.get("off_kinds", []))
        # (earlier settings: a switch for the years)
        default = "years" if saved.get("years", True) else "list"
        levels = dict(saved.get("details") or {})
        for c in cats:
            levels.setdefault(c.key, default)
        other = _("Other")
        with (
            self.dialogs,
            transient_dialog(_("Summary"), width="w-full max-w-3xl") as (dlg, _card),
        ):
            with ui.row().classes("w-full items-center gap-1"):
                ui.label(_("Kinds:")).classes("text-sm text-grey w-24")
                kind_boxes = {
                    k: ui.checkbox(
                        f"{(KIND_SHORT.get(k) if k else None) or other} "
                        f"({sum(_kind(r) == k for r in rows)})",
                        value=(k or "none") not in off_kinds,
                        on_change=lambda: fill(),
                    )
                    .props("dense")
                    .mark(f"summary-kind-{k or 'none'}")
                    for k in kinds
                }
            ui.label(_("Details")).classes("text-sm text-grey").tooltip(
                _(
                    "Off: only counted in its kind · Count: the number of papers · List: their "
                    "venues · List + years: with the years"
                )
            )

            @ui.refreshable
            def detail_table() -> None:
                with ui.grid(columns=f"auto repeat({len(SUMMARY_DETAILS)}, 6rem)").classes(
                    "vr-sum gap-x-2 gap-y-0 items-center text-sm"
                ):
                    ui.label("")
                    for level, label in SUMMARY_DETAILS.items():
                        ui.label(label).classes("vr-sum-col text-center text-grey").props(
                            f"data-col={level}"
                        )
                    for c in cats:
                        n = sum(r.category.key == c.key for r in rows)
                        # (a row: its cells in the grid, the hovered one found by :has())
                        with ui.element("div").classes("vr-sum-line").style("display:contents"):
                            span(
                                f'<i style="display:inline-block;width:10px;height:10px;'
                                f'background:{c.colour};margin-right:4px"></i>'
                                f"{escape(c.label)} ({n})"
                            ).classes("vr-sum-row px-1")
                            for level in SUMMARY_DETAILS:
                                chosen = levels[c.key] == level
                                ui.label("✅" if chosen else "·").classes(
                                    "vr-sum-cell text-center cursor-pointer rounded hover:bg-grey-3"
                                    + ("" if chosen else " text-grey-5")
                                ).props(f"data-col={level}").on(
                                    "click", lambda c=c, level=level: pick(c.key, level)
                                ).mark(f"summary-cat-{c.key}-{level}")

            def pick(key: str, level: str) -> None:
                levels[key] = level
                detail_table.refresh()
                fill()

            detail_table()
            text = (
                ui.textarea()
                .props("outlined readonly autogrow")
                .classes("w-full font-mono text-sm")
                .mark("summary-text")
            )

            def fill() -> None:
                kept = [r for r in rows if kind_boxes[_kind(r)].value]
                text.value = "\n".join(
                    summary_lines(
                        kept,
                        short=short.value,
                        by_kind=by_kind.value,
                        details=levels,
                        lang=lang.value,
                        markdown=markdown.value,
                    )
                )
                # Remembered (the kinds and categories not shown here keep their setting).
                shown_kinds = {k or "none" for k in kinds}
                save_summary_settings(
                    {
                        "by_kind": by_kind.value,
                        "short": short.value,
                        "markdown": markdown.value,
                        "lang": lang.value,
                        "off_kinds": sorted(
                            (off_kinds - shown_kinds)
                            | {k or "none" for k, box in kind_boxes.items() if not box.value}
                        ),
                        "details": levels,
                    }
                )

            with ui.row().classes("w-full items-center"):
                by_kind = ui.switch(
                    _("By kind of venue"), value=saved.get("by_kind", True), on_change=fill
                ).mark("summary-by-kind")
                short = ui.switch(
                    _("Short names"), value=saved.get("short", False), on_change=fill
                ).mark("summary-short")
                markdown = ui.switch(
                    _("Markdown list"), value=saved.get("markdown", False), on_change=fill
                ).mark("summary-markdown")
                short.tooltip(_("Use the venues' short names (acronyms) when they have one"))
                lang = (
                    ui.select(SUMMARY_LANGUAGES, value=saved.get("lang", "en"), on_change=fill)
                    .props("dense outlined")
                    .classes("w-32")
                    .mark("summary-lang")
                )
                ui.space()
                ui.button(
                    _("Copy"),
                    icon="content_copy",
                    on_click=lambda: (
                        ui.clipboard.write(text.value),
                        ui.notify(_("Copied")),
                    ),
                ).props("flat dense")
                ui.button(_("Close"), on_click=dlg.close).props("flat dense")
            fill()

    def _distribution(self, rows: list[PubStat], cats, sels: list[Sel]) -> None:
        total = len(rows)
        counts: dict[str, int] = {}
        predatory = 0
        for s in rows:
            counts[s.category.key] = counts.get(s.category.key, 0) + 1
            predatory += bool(s.badge and s.badge.predatory)
        chosen = {x.key for x in sels if x.facet == "category"}
        present = [c for c in cats if counts.get(c.key)]

        def pct(n: int) -> str:
            return f"{round(100 * n / total) if total else 0}%"

        with ui.row().classes("w-full h-6 no-wrap rounded overflow-hidden select-none"):
            for c in present:
                n = counts[c.key]
                dim = chosen and c.key not in chosen
                seg = (
                    span(
                        f'<span class="{"vr-track" if c.striped else ""}'
                        f'{" vr-dim" if dim else ""}" '
                        f'style="display:block;height:24px;width:100%;background:{c.colour}'
                        f'{stripes(c)}"></span>'
                    )
                    .classes("cursor-pointer")
                    .style(f"width:{100 * n / max(total, 1)}%")
                )
                seg.tooltip(f"{c.label}: {pct(n)} ({n}) · {_(MULTI_TIP)}")
                seg.on(
                    "click",
                    lambda e, c=c: self.pick(Sel("category", c.label, key=c.key), e),
                    ["shiftKey", "altKey"],
                )
        with ui.row().classes("w-full gap-3 select-none"):
            items = [(c.key, c.label, c.colour, stripes(c), counts[c.key]) for c in present]
            if predatory:
                items.append(("predatory", _("⚠ predatory"), PREDATORY_COLOUR, "", predatory))
            for key, label, colour, striped, n in items:
                dim = chosen and key not in chosen
                dim_cls = "vr-dim" if dim else "underline" if key in chosen else ""
                el = span(
                    f'<span class="{dim_cls}"><i'
                    f' style="display:inline-block;width:10px;height:10px;background:{colour};'
                    f'margin-right:4px{striped}"></i>{escape(label)} {pct(n)} <b>({n})</b></span>'
                ).classes("cursor-pointer text-sm")
                el.on(
                    "click",
                    lambda e, k=key, lab=label: self.pick(Sel("category", lab, key=k), e),
                    ["shiftKey", "altKey"],
                ).tooltip(_(MULTI_TIP)).mark(f"category-{key}")

    def _years(self, rows: list[PubStat], cats, sels: list[Sel]) -> None:
        years = [s.year for s in rows if s.year is not None]
        if len(set(years)) < 2:
            return
        bins = year_bin_defs(years)
        chosen = {x.label for x in sels if x.facet == "year"}
        series = []
        for c in cats:
            data = []
            for label, lo, hi in bins:
                n = sum(
                    1
                    for s in rows
                    if s.category.key == c.key and s.year is not None and lo <= s.year <= hi
                )
                dim = chosen and label not in chosen
                data.append({"value": n, "itemStyle": {"opacity": DIM_OPACITY if dim else 1}})
            if any(d["value"] for d in data):
                item = {"color": c.colour}
                if c.striped:
                    item["decal"] = {
                        "symbol": "rect",
                        "dashArrayX": [1, 0],
                        "dashArrayY": [2, 4],
                        "rotation": 0.8,
                        "color": c.stripe if c.track else "rgba(255,255,255,0.45)",
                    }
                series.append(
                    {
                        "name": c.label,
                        "type": "bar",
                        "stack": "y",
                        "data": data,
                        "itemStyle": item,
                        "emphasis": {"focus": "none"},
                    }
                )
        chart = (
            ui.echart(
                {
                    "title": {
                        "text": _("Publications by year · {start}–{end} ({n})").format(
                            start=min(years), end=max(years), n=len(years)
                        ),
                        "textStyle": {"fontSize": 13},
                    },
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 30, "right": 10, "top": 35, "bottom": 25},
                    "xAxis": {"type": "category", "data": [b[0] for b in bins]},
                    "yAxis": {"type": "value", "minInterval": 1},
                    "aria": {"enabled": True, "decal": {"show": False}},
                    "series": series,
                }
            )
            .classes("w-full h-56")
            .mark("years-chart")
        )

        def click(_series: str, index: int, mode: dict[str, bool]) -> None:
            label, lo, hi = bins[index]
            self.sels = pick_sel(self.sels, Sel("year", label, lo=lo, hi=hi), **mode)
            self.render()

        on_chart_click(chart, click)

    def _hist(
        self, title: str, counts: dict[int, int], colour: str, facet: str, sels: list[Sel]
    ) -> ui.echart:
        keys = list(range(1, HIST_CAP + 1)) if counts else []
        top = max((k for k in counts), default=0)
        keys = [k for k in keys if k <= max(top, 1)]
        labels = [f"{k}+" if k == HIST_CAP else str(k) for k in keys]
        chosen = {x.value for x in sels if x.facet == facet}
        data = [
            {
                "value": counts.get(k, 0),
                "itemStyle": {"opacity": DIM_OPACITY if chosen and k not in chosen else 1},
            }
            for k in keys
        ]
        chart = ui.echart(
            {
                "title": {"text": title, "textStyle": {"fontSize": 13}},
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 30, "right": 10, "top": 35, "bottom": 25},
                "xAxis": {"type": "category", "data": labels},
                "yAxis": {"type": "value", "minInterval": 1},
                "series": [{"type": "bar", "data": data, "itemStyle": {"color": colour}}],
            }
        ).classes("w-full h-48")

        def click(_series: str, index: int, mode: dict[str, bool]) -> None:
            label = (_("co-authors {n}") if facet == "coauthors" else _("position {n}")).format(
                n=labels[index]
            )
            self.sels = pick_sel(self.sels, Sel(facet, label, value=keys[index]), **mode)
            self.render()

        on_chart_click(chart, click)
        return chart

    def _coauthors(self, rows: list[PubStat], sels: list[Sel]) -> None:
        with_authors = [s.num_authors for s in rows if s.num_authors is not None]
        with ui.column().classes("w-1/2"):
            if not with_authors:
                return
            counts: dict[int, int] = {}
            for n in with_authors:
                counts[min(n, HIST_CAP)] = counts.get(min(n, HIST_CAP), 0) + 1
            avg = sum(with_authors) / len(with_authors)
            self._hist(
                _("Co-authors per paper · avg {avg} ({n})").format(
                    avg=f"{avg:.1f}", n=len(with_authors)
                ),
                counts,
                "#6639ba",
                "coauthors",
                sels,
            )

    def _contributions(self, rows: list[PubStat], sels: list[Sel]) -> None:
        """The person's role in the papers (first author, contributor… last author), overall
        and by years: shares of the papers of each column."""
        known = [s for s in rows if s.contribution]
        with ui.column().classes("w-1/2 gap-0").mark("contributions"):
            if not known:
                return
            chosen = [x for x in sels if x.facet == "contribution"]
            years = [s.year for s in known if s.year is not None]
            bins = year_bin_defs(years) if len(set(years)) > 1 else []
            columns = [(_("All"), None, None), *bins]
            totals = [
                sum(lo is None or (s.year is not None and lo <= s.year <= hi) for s in known)
                for _label, lo, hi in columns
            ]
            cfg = contribution.load_config()
            series = []
            for key, label, colour in ((r.key, r.label, r.colour) for r in cfg.roles):
                counts = [
                    sum(
                        s.contribution == key
                        and (lo is None or (s.year is not None and lo <= s.year <= hi))
                        for s in known
                    )
                    for _label, lo, hi in columns
                ]
                if not counts[0]:
                    continue
                data = []
                for (_label, lo, _hi), n, total in zip(columns, counts, totals, strict=True):
                    picked = any(x.key == key and x.lo == (lo or 0) for x in chosen)
                    data.append(
                        {
                            "value": round(100 * n / total, 1) if total else 0,
                            "count": n,
                            "itemStyle": {"opacity": DIM_OPACITY if chosen and not picked else 1},
                        }
                    )
                series.append(
                    {
                        "name": label,
                        "type": "bar",
                        "stack": "role",
                        "itemStyle": {"color": colour},
                        "emphasis": {"focus": "none"},
                        "data": data,
                    }
                )
            share = {
                k: round(100 * sum(s.contribution in keys for s in known) / len(known))
                for k, keys in (("first", ("sole", "first")), ("last", ("last",)))
            }
            chart = ui.echart(
                {
                    "title": {
                        "text": _("Contribution role ({n}) · first {first}% · last {last}%").format(
                            n=len(known), first=share["first"], last=share["last"]
                        ),
                        "textStyle": {"fontSize": 13},
                    },
                    "tooltip": {
                        "trigger": "axis",
                        "axisPointer": {"type": "shadow"},
                        ":formatter": "(ps) => ps[0].axisValue + ps.filter(p => p.data.count)"
                        ".map(p => `<br>${p.marker}${p.seriesName}: ${p.data.count}"
                        " (${p.value}%)`).join('')",
                    },
                    "legend": {"bottom": 0, "itemWidth": 12, "itemHeight": 10},
                    "grid": {"left": 40, "right": 10, "top": 35, "bottom": 50},
                    "xAxis": {"type": "category", "data": [c[0] for c in columns]},
                    "yAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
                    "series": series,
                }
            ).classes("w-full h-64")

            def click(series: str, index: int, mode: dict[str, bool]) -> None:
                key = next(r.key for r in cfg.roles if r.label == series)
                col, lo, hi = columns[index]
                label = _("{role} author").format(role=series.lower()) + (f" ({col})" if lo else "")
                sel = Sel("contribution", label, lo=lo or 0, hi=hi or 0, key=key)
                self.sels = pick_sel(self.sels, sel, **mode)
                self.render()

            on_chart_click(chart, click)
            with ui.row().classes("items-center gap-1 -mt-1"):
                with ui.icon("help_outline", size="xs").classes("text-grey"), ui.tooltip():
                    ui.label(_("The first rule that matches:"))
                    for r in cfg.rules:
                        ui.label(f"{cfg.label(r.role)}: {r.condition}")
                    ui.label(_("{role}: otherwise").format(role=cfg.label(cfg.fallback)))
                ui.link(_("rules"), "/settings?tab=contribution").classes("text-xs text-grey")
            with_phd = sum("student" in s.author_marks for s in rows)
            with ui.row().classes("gap-2 -mt-2"):
                if with_phd or any(s.author_notes for s in rows):
                    label = _("with PhD student {pct}%").format(
                        pct=round(100 * with_phd / max(len(rows), 1))
                    )
                    ui.chip(
                        f"{label} ({with_phd})",
                        selectable=True,
                        selected=any(x.facet == "phd" for x in sels),
                    ).props("dense clickable").on(
                        "click",
                        lambda e: self.pick(Sel("phd", _("with a PhD student")), e),
                        ["shiftKey", "altKey"],
                    ).tooltip(
                        _("papers co-authored with a (confirmed) PhD student from theses.fr")
                        + " · "
                        + _(MULTI_TIP)
                    ).mark("phd-chip")
                for c in self.categories:
                    n = sum(f"cat:{c.id}" in s.author_marks for s in rows)
                    if not n:
                        continue
                    active = any(x.facet == "authorcat" and x.key == str(c.id) for x in sels)
                    ui.chip(
                        _("with {category} {pct}% ({n})").format(
                            category=c.name, pct=round(100 * n / max(len(rows), 1)), n=n
                        ),
                        selectable=True,
                        selected=active,
                    ).props("dense clickable").on(
                        "click",
                        lambda e, c=c: self.pick(
                            Sel(
                                "authorcat",
                                _("with {category}").format(category=c.name),
                                key=str(c.id),
                            ),
                            e,
                        ),
                        ["shiftKey", "altKey"],
                    ).tooltip(_(MULTI_TIP)).style(f"--q-primary:{c.colour}").mark(
                        f"authorcat-{c.id}"
                    )

    def list_tag(self) -> int | None:
        """The tag filtered on, when it is the only one and was put from a list (the papers
        are then in the list's order)."""
        if len(self.tag_filter) != 1:
            return None
        tid = self.tag_filter[0]
        return tid if any(s.number_of(tid, self.period_id) for s in self.stats) else None

    def _list(self, rows: list[PubStat]) -> None:
        rows = sorted(rows, key=lambda s: (-(s.year or 0), (s.title or "").lower()))
        period = next((p for p in self.periods if p.id == self.period_id), None)
        if (tid := self.list_tag()) is not None:

            def order(s: PubStat) -> tuple[bool, int]:
                n = s.number_of(tid, self.period_id)
                return n is None, n or 0

            shown = {s.id for s in rows}
            rest = [s for s in self.stats if s.id not in shown and tid in s.tags_in(self.period_id)]
            with ui.column().classes("w-full gap-0"):
                ui.label(_("In the order of the list")).classes("text-sm text-grey mt-4")
                for s in sorted(rows, key=order):
                    self._row(s, period)
                if rest:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("visibility_off", color="orange-8")
                        ui.label(_("Not shown: {why}").format(why=self.not_shown(rest))).classes(
                            "text-sm text-orange-9"
                        ).mark("list-not-shown")
                        if any(not self.in_years(s) for s in rest):
                            ui.button(_("Show all years"), on_click=self._all_years).props(
                                "flat dense"
                            ).mark("list-all-years")
            return
        with ui.column().classes("w-full gap-0"):
            for year, group in groupby(rows, key=lambda s: s.year):
                group = list(group)
                with ui.row().classes(
                    "w-full items-center bg-grey-2 dark:bg-grey-9 px-3 py-2 mt-6 mb-2 rounded"
                ):
                    ui.label(str(year or _("Unknown year"))).classes("font-bold")
                    ui.label(f"{len(group)}").classes("text-grey text-sm")
                for s in group:
                    self._row(s, period)

    def _all_years(self) -> None:
        self.lo = self.hi = None
        self.sels = []
        self.render()

    def _row(self, s: PubStat, period) -> None:
        row = ui.row().classes(
            "vr-row w-full items-start no-wrap gap-2 py-1 border-b cursor-pointer"
        )
        # Clicking the row opens the details, except on links / buttons inside it.
        row.on(
            "click",
            lambda pub=s: open_details(self, pub),
            js_handler=(
                "(e) => { if (!e.target.closest("
                "'a, button, .q-btn, .vr-src, .vr-venue-link, .vr-problems'))"
                " emit(); }"
            ),
        ).mark(f"pub-{s.id}")
        with row:
            with ui.element("div").classes("w-28 shrink-0"):
                rank_chip(s.badge, s.track, s.kind)
                if s.overridden:
                    ui.icon("push_pin", size="xs", color="primary").tooltip(
                        _("venue or rank set by hand")
                    )
                if problems := s.problems:
                    # Clicking it opens the details on the tab where they are settled.
                    with (
                        ui.icon("warning", size="xs", color="orange-8")
                        .classes("vr-problems cursor-pointer")
                        .on("click", lambda pub=s: open_details(self, pub, pub.problem_tab))
                        .mark(f"problems-{s.id}")
                    ):
                        ui.tooltip("\n".join(problems)).style("white-space:pre-line")
            if period:
                starred = self.starred_id in s.period_tags.get(period.id, set())

                def star(pub=s, p=period) -> None:
                    on = annotations.toggle_tag(pub.id, self.starred_id, p.id)
                    tags = pub.period_tags.setdefault(p.id, set())
                    (tags.add if on else tags.discard)(self.starred_id)
                    self.render()

                ui.button(icon="star" if starred else "star_border", on_click=star).props(
                    f"flat round dense size=sm color={'amber-8' if starred else 'grey'}"
                ).mark(f"star-{s.id}")
            with ui.column().classes("gap-0 grow min-w-0"):
                # 1. title (the title opens the details: row click)
                with ui.row().classes("items-center gap-1 no-wrap"):
                    title = escape(s.title or _("(untitled)"))
                    year = f' <span class="text-grey">({s.year})</span>' if s.year else ""
                    span(f'<span class="font-medium">{title}</span>{year}')
                    pdf_button(self, s)
                    if s.missing:
                        ui.badge(_("missing from sources"), color="grey")
                    if s.archival_only:
                        ui.badge(_("preprint"), color="grey-6")
                    if s.hidden:
                        ui.badge(_("hidden"), color="grey-8")
                    if s.has_notes(period.id if period else None):
                        notes = [s.note] if s.note else []
                        if period and (pn := s.period_notes.get(period.id)):
                            notes.append(f"**{period.name}:** {pn}")
                        with (
                            ui.icon("sticky_note_2", size="xs", color="amber-9")
                            .classes("vr-src cursor-pointer")
                            .on("click", lambda pub=s: open_details(self, pub, "notes"))
                            .mark(f"has-note-{s.id}"),
                            ui.tooltip().props("max-width=480px"),
                        ):
                            ui.markdown("\n\n".join(notes), extras=NOTE_EXTRAS)
                # 2. venue: the source's text → the venue it is matched to
                orig, mapped = self._venue_html(s)
                if orig or mapped:
                    with ui.row().classes("items-center gap-1 text-sm").mark(f"pub-venue-{s.id}"):
                        if orig:
                            span(orig)
                        if mapped and s.venue_id:
                            span(mapped).classes("vr-venue-link cursor-pointer").on(
                                "click", lambda vid=s.venue_id: self.open_venue(vid)
                            ).mark(f"pub-venue-link-{s.id}")
                        elif mapped:
                            span(mapped)
                # 3. authors
                if s.authors:
                    names = []
                    for i, a in enumerate(s.authors):
                        mark = s.author_marks[i] if i < len(s.author_marks) else None
                        note = s.author_notes.get(i, (None, None))[0]
                        names.append(author_html(a, mark, note, self.cat_colours.get(mark)))
                    span(f'<span class="text-sm">{", ".join(names)}</span>')
                # 4. quick links and badges
                with ui.row().classes("items-center gap-1"):
                    if s.publisher_url:
                        with ui.link(target=s.publisher_url, new_tab=True).mark(
                            f"publisher-{s.id}"
                        ):
                            ui.icon("launch", size="xs", color="primary").tooltip(
                                _("Publisher's page: {url}").format(url=s.publisher_url)
                            )
                    shown: set[str] = set()
                    for m in s.members:
                        source_badge(m)
                        # Its PDF right after the source's badge (each PDF once).
                        if m.pdf_url and m.pdf_url not in shown:
                            shown.add(m.pdf_url)
                            with ui.link(target=m.pdf_url, new_tab=True).mark(f"pdf-{m.id}"):
                                ui.icon("picture_as_pdf", size="xs", color="red-7").tooltip(
                                    _("PDF from {source}: {url}").format(
                                        source=m.source, url=m.pdf_url
                                    )
                                )
                    if s.track:
                        track_chip(s.track, f"pub-track-{s.id}")
                    pid = period.id if period else None
                    mine = s.tags_in(pid)
                    for tag in self.tags:
                        number = s.number_of(tag.id, pid)
                        if tag.id in mine and (tag.id != self.starred_id or number is not None):
                            tag_chip(tag, number)

    @property
    def period(self):
        return next((p for p in self.periods if p.id == self.period_id), None)

    def manage_tags(self) -> None:
        def changed() -> None:
            self.tags = annotations.all_tags()
            self.tag_filter = self._known_tags(self.tag_filter)
            self.render()

        with self.dialogs:
            tags_dialog(changed)

    async def open_venue(self, venue_id: int) -> None:
        """The venue's dialog, over the publication list (reloaded after a change)."""
        from .venues_page import open_venue

        with self.dialogs:
            await open_venue(venue_id, lambda: background_tasks.create(self.reload()))

    @staticmethod
    def _venue_html(s: PubStat) -> tuple[str, str]:
        """(``original text →``, matched venue): the ranking record's name on hover."""
        orig, venue = s.venue_raw, s.venue
        short = f'<b class="vr-short">{escape(s.venue_short)}</b> ' if s.venue_short else ""
        b = s.badge
        record = (
            b.name
            if b is not None and b.name and b.source in ("core", "scimago", "jcr", "openalex")
            else None
        )
        tip = (
            _("open the venue — ranked as: {record}").format(record=record)
            if record
            else _("open the venue")
        )
        mapped = (
            f'<span title="{escape(tip, quote=True)}">{short}'
            f'<span class="vr-matched">{escape(venue)}</span></span>'
            if venue
            else ""
        )
        if orig and venue and orig != venue:
            return f'<i class="text-grey">{escape(orig)}</i> →', mapped
        return ("" if venue else (f"<i>{escape(orig)}</i>" if orig else "")), mapped


def _kind(s: PubStat) -> str | None:
    return s.kind if s.kind in KIND_ORDER else None


def period_label(p) -> str:
    rng = f"{p.start_year or '…'}–{p.end_year or '…'}"
    return f"{'📁 ' if p.folder_id else ''}{p.name} ({rng})"
