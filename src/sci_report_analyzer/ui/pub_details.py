"""Publication details dialog and lazily-filled provenance tooltips."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from nicegui import background_tasks, ui
from sqlalchemy import select

from .. import annotations, merge, pdfs, sync, venues
from ..db.models import Person, Publication, SourceLink, SourcePub, Thesis
from ..db.session import session_scope
from ..i18n import N_, Labels, _, ngettext
from ..pubview import (
    MemberView,
    PubStat,
    doi_member,
    pick_reason,
    pick_venue_member,
    same_venue,
    track_of,
    venue_members,
)
from ..ranking.badge import TRACK_LABEL, Badge
from ..ranking.kinds import (
    CONFERENCE_LIKE,
    KINDS,
    NO_VENUE_KINDS,
    PUBLICATION_ONLY_KINDS,
    VENUE_KINDS,
    WORKSHOP_KINDS,
)
from ..ranking.service import VenuePattern, paren_acronym, service
from ..source_settings import active_links
from ..sources import ADAPTERS
from ..sources.base import normalize_doi
from . import scimago_years
from .theme import (
    author_html,
    badge_details,
    chip_style,
    level_hint,
    level_legend,
    level_options,
    merge_direction,
    rank_chip,
    source_tag,
    span,
)

if TYPE_CHECKING:
    from .panel import PublicationsPanel

CORE_RANKS = ["A*", "A", "B", "C"]
QUARTILES = ["Q1", "Q2", "Q3", "Q4"]


async def _record_details(m: MemberView, box: ui.element, *, links: bool) -> None:
    """Fill ``box`` with what a source says about the record (and links to check it)."""
    with session_scope() as s:
        sp = s.get(SourcePub, m.id)
        if sp is None:
            return
        data = {
            k: getattr(sp, k)
            for k in (
                "title",
                "venue",
                "year",
                "authors",
                "doc_type",
                "doi",
                "issn",
                "venue_type",
                "external_key",
                "pdf_url",
                "archival",
                "url",
                "raw",
            )
        }
        adapter = ADAPTERS[m.source]
        profile = sp.link.url or adapter.profile_url(sp.link.external_id)
    badge = m.badge
    if badge is None and data["venue"]:
        badge = await service.resolve(
            data["venue"], data["issn"], data["venue_type"], source=m.source
        )
    raw = data["raw"] or {}
    box.clear()
    with box:
        ui.label(
            _("{source} (preprint)").format(source=adapter.label)
            if data["archival"]
            else adapter.label
        ).classes("font-bold")
        ui.label(data["title"] or "")
        lines = [
            (_("Venue"), data["venue"]),
            (_("Cleaned as"), service.clean(data["venue"], m.source) if data["venue"] else None),
            (_("Venue"), m.venue_name if m.venue_name != data["venue"] else None),
            (_("Year"), data["year"]),
            (_("Type"), data["doc_type"]),
            ("DOI", data["doi"]),
            ("ISSN", data["issn"]),
            (_("Id"), data["external_key"]),
            (_("Declared by"), raw.get("source") if m.source == "orcid" else None),
            (
                _("Authors"),
                ", ".join(data["authors"][:12]) + (" …" if len(data["authors"]) > 12 else "")
                if data["authors"]
                else None,
            ),
        ]
        if m.source == "doi" and data["doi"]:
            doi_details(data["doi"])
        else:
            for k, v in lines:
                if v:
                    ui.label(_("{label}: {value}").format(label=k, value=v))
        with ui.row().classes("items-center gap-2"):
            ui.label(_("Rank:")).classes("italic")
            if links:
                rank_chip(badge)
            ui.label(
                badge_details(badge).replace("\n", " · ") if badge else _("not ranked")
            ).classes("italic")
        if badge is None or badge.source == "scimago" or badge.type == "journal":
            scimago_years.missing_hint(data["year"])
        if not links:
            ui.label(_("click: open the record · right-click: more links")).classes(
                "text-xs text-grey"
            )
            return
        with ui.column().classes("gap-0 mt-1"):
            url = data["url"]
            if url and m.source == "orcid":
                # ORCID has no page per work: its link is the one declared (often HAL).
                ui.link(
                    _("Link declared in ORCID ({host})").format(host=_host(url)), url, new_tab=True
                )
            elif url:
                ui.link(
                    _("Open the record on {source}").format(source=adapter.label), url, new_tab=True
                ).mark(f"record-link-{m.id}")
            if profile:
                ui.link(
                    _("Profile on {source}").format(source=adapter.label), profile, new_tab=True
                ).mark(f"profile-link-{m.id}")
            if m.publisher_url:
                ui.link(_("Publisher's page"), m.publisher_url, new_tab=True)
            if data["doi"]:
                doi_link(data["doi"])
            if data["pdf_url"]:
                ui.link("PDF", data["pdf_url"], new_tab=True)
            if badge:
                _check_link(badge)


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]


def source_tooltip(m: MemberView) -> None:
    """A tooltip on a provenance badge; its content is loaded when first shown."""
    tip = ui.tooltip().classes("text-sm").style("max-width:420px;white-space:normal")
    with tip:
        ui.label(_("{source} — loading…").format(source=ADAPTERS[m.source].label))
    state = {"loaded": False}

    async def fill() -> None:
        if not state["loaded"]:
            state["loaded"] = True
            await _record_details(m, tip, links=False)

    tip.on("before-show", fill)


def record_url(m: MemberView) -> str | None:
    """Where a source badge links: the record on the source (ORCID: the profile, as ORCID
    has no page per work and its declared link is often HAL's)."""
    if m.source == "orcid":
        return m.profile_url or m.url
    return m.url or m.profile_url


def source_badge(m: MemberView) -> None:
    """A source badge linking to the record; details on hover, more links on right-click."""
    tag = source_tag(m.source, m.archival, record_url(m)).mark(f"source-{m.id}")
    with tag:
        source_tooltip(m)
        with ui.menu().props("context-menu") as menu:
            box = ui.column().classes("p-3 gap-0 text-sm").style("max-width:480px")
            with box:
                ui.spinner()
    state = {"loaded": False}

    async def fill() -> None:
        if not state["loaded"]:
            state["loaded"] = True
            await _record_details(m, box, links=True)

    menu.on("before-show", fill)


def venue_rule_dialog(
    done,
    *,
    sample: tuple[str, str] | None = None,
    venue_id: int | None = None,
    index: int | None = None,
) -> None:
    """Add or edit a venue's regex rule: the source venue texts it matches belong to the venue.

    ``sample`` is a (source, raw venue text) the rule is made for (from a publication);
    ``venue_id`` / ``index`` pick the venue and, when editing, its rule.
    """
    source, raw = sample or (None, "")
    options = venues.venue_options()
    rules = venues.venue_patterns(venue_id) if venue_id is not None else []
    old = rules[index] if index is not None and index < len(rules) else None
    label = ADAPTERS[source].label if source else None

    with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl vr-details"):
        ui.label(_("Venue rule")).classes("text-lg font-medium")
        ui.label(
            _(
                "Source venue texts matching the regex belong to the venue, and are ranked as it "
                "(its level, ranking record or name). Check below the other texts it captures."
            )
        ).classes("text-xs text-grey")
        if sample:
            with ui.row().classes("items-center gap-2"):
                source_tag(source)
                ui.label(f"“{raw}”").classes("font-mono text-sm")
        target = (
            ui.select(options, value=venue_id, with_input=True, label=_("Venue"))
            .props("dense outlined")
            .classes("w-full")
            .mark("rule-venue")
        )
        if old is None:
            target.props("clearable")
        else:
            target.disable()  # an existing rule stays on its venue
        pattern = (
            ui.input(
                _("regex (Python)"),
                value=old.pattern if old else (f"^{re.escape(raw)}$" if raw else ""),
            )
            .props("dense outlined debounce=400")
            .classes("w-full font-mono")
            .mark("rule-pattern")
        )
        with ui.row().classes("items-center gap-4"):
            only = (
                ui.select(
                    {k: a.label for k, a in ADAPTERS.items()},
                    multiple=True,
                    label=_("only for sources"),
                    value=list(old.sources) if old else [],
                )
                .props("dense outlined use-chips")
                .classes("min-w-48")
                .tooltip(_("Empty: texts of every source"))
                .mark("rule-sources")
            )
            icase = ui.checkbox(_("ignore case"), value=old.ignore_case if old else True)
            track = (
                ui.select(
                    {"": _("no track"), **TRACK_LABEL},
                    value=(old.track or "") if old else "",
                    label=_("Mark papers as"),
                )
                .props("dense outlined")
                .classes("w-44")
                .tooltip(
                    _("Track of the publications whose venue text matches (demo, findings...)")
                )
                .mark("rule-track")
            )
        note = (
            ui.input(_("note (optional)"), value=(old.note or "") if old else "")
            .props("dense outlined")
            .classes("w-full")
        )
        if label and old is None:
            ui.button(
                _("Only {source} texts").format(source=label),
                on_click=lambda: only.set_value([source]),
            ).props("flat dense size=sm")
        out = ui.column().classes("w-full gap-0")

        def candidate() -> VenuePattern | None:
            try:
                re.compile(pattern.value or "")
            except re.error as e:
                out.clear()
                with out:
                    ui.label(_("Invalid regex: {error}").format(error=e)).classes(
                        "text-negative text-sm"
                    )
                return None
            if not pattern.value:
                return None
            return VenuePattern(
                pattern=pattern.value,
                ignore_case=icase.value,
                sources=list(only.value or []),
                track=track.value or None,
                note=note.value or None,
            )

        def preview(_e=None) -> None:
            rule = candidate()
            if rule is None:
                return
            out.clear()
            with out:
                if target.value is None:
                    ui.label(_("Choose the venue these texts belong to.")).classes(
                        "text-sm text-negative"
                    )
                    return
                if sample and not rule.applies(source, raw):
                    why = (
                        _("it is restricted to {sources}").format(
                            sources=", ".join(ADAPTERS[x].label for x in rule.sources)
                        )
                        if rule.sources and source not in rule.sources
                        else _("the regex does not match it")
                    )
                    ui.label(
                        _("⚠ This rule does not capture this {source} text: {why}.").format(
                            source=label, why=why
                        )
                    ).classes("text-sm text-negative").mark("rule-no-match")
                if rule.track:
                    ui.label(
                        _("Their publications are marked {track}.").format(
                            track=TRACK_LABEL.get(rule.track, rule.track)
                        )
                    ).classes("text-sm")
                effects = venues.pattern_effects(target.value, rule, index)
                if not effects:
                    ui.label(_("No venue text changes venue.")).classes("text-xs text-grey")
                    return
                ui.label(
                    _("{n} venue text(s) change venue ({records} record(s)):").format(
                        n=len(effects), records=sum(e.count for e in effects)
                    )
                ).classes("text-sm text-orange-9").mark("rule-effects")
                with ui.column().classes("gap-0").style("max-height:240px;overflow-y:auto"):
                    for e in effects[:100]:
                        with ui.row().classes("items-center gap-2 no-wrap text-xs"):
                            source_tag(e.source)
                            ui.label(f"{e.raw} ({e.count})")
                            ui.label(
                                f"{e.before or _('(no venue)')} → {e.after or _('(automatic)')}"
                            ).classes("text-grey")
                            if e.conflict:
                                ui.label(_("conflict: another venue's rule matches too")).classes(
                                    "text-orange-9"
                                )

        for el in (pattern, only, icase, track, target):
            el.on_value_change(preview)

        def store(new_rules: list[VenuePattern], message: str) -> None:
            venues.save_patterns(target.value, new_rules)
            # Refresh the caller first: a closed dialog is deleted, with its client context.
            done(message)
            dlg.close()

        def save() -> None:
            rule = candidate()
            if rule is None or target.value is None:
                preview()
                return
            new_rules = venues.venue_patterns(target.value)
            if old is not None:
                new_rules[index] = rule
            else:
                new_rules.append(rule)
            store(new_rules, _("Venue rule saved"))

        def delete_rule() -> None:
            new_rules = venues.venue_patterns(target.value)
            new_rules.pop(index)
            store(new_rules, _("Venue rule deleted"))

        with ui.row().classes("w-full justify-end gap-2"):
            if old is not None:
                ui.button(_("Delete rule"), on_click=delete_rule).props("flat color=negative").mark(
                    "rule-delete"
                )
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Save rule"), on_click=save).mark("rule-save")
    preview()
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def merge_venues_dialog(venue_ids: list[int], done, *, text: str | None = None) -> None:
    """Merge venues that match the same text into one (the one kept is chosen)."""
    names = venues.venue_names()
    ids = [i for i in dict.fromkeys(venue_ids) if i in names]
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl"):
        ui.label(_("Merge into one venue")).classes("text-lg font-medium")
        if text:
            ui.label(_("These venues all match “{text}”.").format(text=text)).classes("text-sm")
        ui.label(
            _(
                "The venue kept gets the others' variants (with their tracks), rules, ISSNs and "
                "papers; its own decisions win, the others' fill what it lacks."
            )
        ).classes("text-xs text-grey")
        ui.label(_("Venue kept:")).classes("text-sm font-medium")
        keep = ui.radio({i: names[i] for i in ids}, value=ids[0]).mark("merge-keep")
        direction = ui.column().classes("w-full border rounded p-2")

        def show_direction() -> None:
            direction.clear()
            with direction:
                merge_direction(names[keep.value], [names[i] for i in ids if i != keep.value])

        keep.on_value_change(show_direction)
        show_direction()

        def ok() -> None:
            venues.merge_venues(keep.value, [i for i in ids if i != keep.value])
            done(_("Merged into “{venue}”").format(venue=names[keep.value]))
            dlg.close()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Merge"), icon="merge", on_click=ok).mark("merge-confirm")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def search_venue_dialog(pub_id: int, paper_venues: list[int], done) -> None:
    """Search a venue (a known one or a ranking record) for a paper, and optionally merge the
    venues given by its sources into it."""
    names = venues.venue_names()
    others = [v for v in dict.fromkeys(paper_venues) if v in names]
    chosen: dict = {}
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-2xl"):
        ui.label(_("Search for a venue")).classes("text-lg font-medium")
        query = (
            ui.input(_("Venue name, acronym or ranking record"), on_change=lambda: search())
            .props("dense outlined debounce=300 autofocus clearable")
            .classes("w-full")
            .mark("venue-query")
        )
        results = ui.column().classes("w-full gap-1")
        picked = ui.column().classes("w-full gap-1")
        merge_boxes: dict[int, ui.checkbox] = {}

        def search() -> None:
            results.clear()
            text = (query.value or "").strip()
            with results:
                if len(text) < 2:
                    ui.label(_("Type at least 2 characters.")).classes("text-xs text-grey")
                    return
                hits = venues.find_venues(text)
                if not hits:
                    ui.label(_("No venue or ranking record found.")).classes("text-xs text-grey")
                for i, h in enumerate(hits):
                    with ui.row().classes("items-center gap-2 no-wrap w-full"):
                        if h.badge is not None:
                            rank_chip(h.badge)
                        else:
                            ui.icon("place", size="xs", color="grey-7")
                        with ui.row().classes("grow min-w-0 items-baseline gap-2 no-wrap"):
                            if h.short:
                                ui.label(h.short).classes("font-bold text-sm shrink-0").mark(
                                    f"venue-hit-short-{i}"
                                )
                            if h.kind:
                                ui.label(h.kind).classes("text-xs text-primary shrink-0")
                            ui.label(h.label).classes("text-sm min-w-0")
                        ui.label(h.detail).classes("text-xs text-grey shrink-0")
                        ui.button(_("Choose"), on_click=lambda h=h: choose(h)).props(
                            "dense flat color=primary"
                        ).mark(f"venue-hit-{i}")
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    ui.icon("add_location_alt", size="xs", color="primary")
                    ui.label(_("A new venue: “{text}”").format(text=text)).classes(
                        "text-sm grow min-w-0"
                    )
                    ui.button(
                        _("Create"),
                        on_click=lambda: choose(venues.VenueHit(text, detail="new"), new=True),
                    ).props("dense flat color=primary").mark("venue-new")

        def choose(h, new: bool = False) -> None:
            chosen.update(hit=h, new=new)
            picked.clear()
            merge_boxes.clear()
            with picked:
                ui.separator()
                ui.label(_("Use “{venue}” for this paper").format(venue=h.label)).classes(
                    "font-medium"
                ).mark("venue-chosen")
                if new:
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        chosen["short"] = (
                            ui.input(_("Short name (acronym)"), value=paren_acronym(h.label) or "")
                            .props("dense outlined")
                            .classes("w-48")
                            .mark("venue-new-short")
                        )
                        chosen["kind"] = (
                            ui.select(VENUE_KINDS, value="intl_conference", label=_("Kind"))
                            .props("dense outlined")
                            .classes("grow")
                            .mark("venue-new-kind")
                        )
                    ui.label(_("A venue is created with this name (unranked).")).classes(
                        "text-xs text-grey"
                    )
                elif h.venue_id is None:
                    ui.label(_("A venue is created for this ranking record.")).classes(
                        "text-xs text-grey"
                    )
                merge = [v for v in others if v != h.venue_id]
                if merge:
                    ui.label(
                        _(
                            "Also merge the sources' venues into it (their variants and papers "
                            "follow, for every person):"
                        )
                    ).classes("text-sm")
                    for v in merge:
                        merge_boxes[v] = (
                            ui.checkbox(names[v]).props("dense").mark(f"merge-into-{v}")
                        )
                    ui.label(
                        _("Otherwise only this paper is linked to it, by hand (kept on re-sync).")
                    ).classes("text-xs text-grey")
            use.set_enabled(True)

        def ok() -> None:
            h = chosen["hit"]
            if chosen.get("new"):
                target, created = venues.add_venue(
                    h.label, chosen["kind"].value, (chosen["short"].value or "").strip()
                )
                if not created:
                    ui.notify(_("A venue already has this name: it is used"))
            else:
                target = h.venue_id or venues.venue_for_record(h.record_key)
            merge = [v for v, box in merge_boxes.items() if box.value]
            venues.use_venue(pub_id, target, merge, others)
            done(_("Merged and linked") if merge else _("Linked to the venue (by hand)"))
            dlg.close()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            use = ui.button(_("Use this venue"), icon="check", on_click=ok).mark("venue-use")
            use.set_enabled(False)
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()
    search()


def open_details(panel: PublicationsPanel, s: PubStat, tab: str = "publication") -> None:
    with (
        panel.dialogs,
        ui.dialog() as dlg,
        ui.card()
        .classes("w-full max-w-4xl vr-details")
        .style("max-height: 90vh; overflow-y: auto") as card,
    ):
        pass
    show_details(panel, s, card, dlg.close, tab)
    # Dialogs are built per click: drop them once closed.
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def show_details(
    panel: PublicationsPanel,
    s: PubStat,
    box: ui.element,
    close: Callable[[], None] | None,
    tab: str = "publication",
    *,
    notes: bool = True,
) -> None:
    """The details of a publication in ``box`` (a dialog's card, or a side panel), shown
    again after each change. Without ``close``: always there (a tab), with no close button;
    without ``notes``: no tags & notes tab (shown elsewhere)."""
    state = {"tab": tab, "notes": notes}

    async def refresh() -> None:
        """Reload the panel, then show the updated publication (the box stays open)."""
        await panel.reload()
        if box.is_deleted:
            return
        new = next((o for o in panel.stats if o.id == s.id), None)
        if new is None:
            if close:
                close()
            else:
                box.clear()
                with box:
                    ui.label(_("This paper is no longer in the database")).classes("text-grey")
            return
        box.clear()
        with box:
            _details(panel, new, close, state, refresh)

    box.clear()
    with box:
        _details(panel, s, close, state, refresh)


def _details(panel: PublicationsPanel, s: PubStat, close, state: dict, refresh) -> None:
    def done(msg: str | None = None, *, close_it: bool = False) -> None:
        if msg:
            ui.notify(msg)
        if close_it and close:
            close()
            background_tasks.create(panel.reload())
        else:
            background_tasks.create(refresh())

    # -- header: title, result, tabs -------------------------------------------------------
    with ui.row().classes("w-full items-start justify-between no-wrap"):
        with ui.column().classes("gap-1 min-w-0"):
            ui.label(s.title or _("(untitled)")).classes("text-lg font-medium")
            with ui.row().classes("items-center gap-2"):
                rank_chip(s.badge, s.track, s.kind)
                ui.label(f"{s.year or '—'} · {s.venue or _('no venue')}").classes("text-grey")
        if close:
            ui.button(icon="close", on_click=close).props("flat round").mark("details-close")
    if problems := s.problems:
        # A possible match of the person (or of a PhD student): confirmed or discarded here.
        possible = {}  # (its question, among the problems)
        for i, m in enumerate(s.author_marks):
            note, st = s.author_notes.get(i, ("", None))
            if m == "owner?":
                possible[note] = (i, s.authors[i], ())
            elif m == "student?" and st:
                possible[note] = (i, s.authors[i], (st,))

        def decide(fn, a: str, extra: tuple, msg: str) -> None:
            fn(panel.person_id, a, *extra)
            done(msg)

        with ui.column().classes("w-full gap-0 bg-orange-1 rounded p-2"):
            for p in problems:
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.icon("warning", size="xs", color="orange-8")
                    ui.label(p).classes("text-sm")
                    hit = possible.get(p)
                    if hit is None:
                        continue
                    i, a, extra = hit
                    ui.button(
                        _("Yes"),
                        icon="check",
                        on_click=lambda a=a, x=extra: decide(
                            annotations.add_alias, a, x, _("Alias added")
                        ),
                    ).props("flat dense no-caps size=sm color=positive").mark(f"possible-yes-{i}")
                    ui.button(
                        _("No"),
                        icon="close",
                        on_click=lambda a=a, x=extra: decide(
                            annotations.reject_alias, a, x, _("Won't be suggested again")
                        ),
                    ).props("flat dense no-caps size=sm color=negative").tooltip(
                        _("Discard: not this person (won't be suggested again)")
                    ).mark(f"possible-no-{i}")
    tab = state["tab"]
    with (
        ui.tabs(value=tab, on_change=lambda e: state.update(tab=e.value))
        .props("dense align=left inline-label")
        .classes("w-full") as tabs
    ):
        ui.tab("publication", _("Publication"), icon="article")
        if state["notes"]:
            ui.tab("notes", _("Tags & notes"), icon="sell").mark("tab-notes")
        ui.tab("matching", _("Venue matching"), icon="rule").mark("tab-matching")
    with ui.tab_panels(tabs, value=tab).classes("w-full"):
        with ui.tab_panel("publication").classes("p-0 gap-2"):
            _publication_tab(panel, s, done)
        if state["notes"]:
            with ui.tab_panel("notes").classes("p-0 gap-2"):
                _notes_tab(panel, s, done)
        with ui.tab_panel("matching").classes("p-0 gap-2"):

            async def show_venue(vid: int) -> None:
                from .venues_page import open_venue

                # Hosted outside the details, which are rebuilt after a change.
                with panel.dialogs:
                    await open_venue(vid, lambda: done(_("Venue saved")))

            _matching_tab(s, done, show_venue)


def _notes_tab(panel: PublicationsPanel, s: PubStat, done) -> None:
    from .tags import paper_tags_and_notes, tags_dialog

    def changed_tags() -> None:
        panel.tags = annotations.all_tags()
        done()

    def manage_tags() -> None:
        with panel.dialogs:  # outside the details, rebuilt after a change
            tags_dialog(changed_tags)

    paper_tags_and_notes(
        s,
        panel.period,
        panel.tags,
        lambda: background_tasks.create(panel.reload()),
        manage_tags,
    )


def doi_link(doi: str) -> None:
    """A link to the DOI; hovering it shows its record (as the registry gave it)."""
    link = ui.link(f"doi:{doi}", f"https://doi.org/{doi}", new_tab=True).mark(f"doi-link-{doi}")
    with link, ui.tooltip().classes("text-sm").props("max-width=480px"):
        doi_details(doi)


def doi_details(doi: str) -> None:
    """The DOI's cached record, as the registry gave it."""
    from ..sources import doi as doi_source

    record = doi_source.cached([doi]).get(doi)
    with ui.column().classes("gap-0").mark(f"doi-details-{doi}"):
        if record is None:
            ui.label(_("Not fetched yet (on the next sync)"))
            return
        if record.status != "ok":
            ui.label(
                _("Unknown to the registries (asked {date})").format(
                    date=f"{record.fetched_at:%Y-%m-%d}"
                )
            )
            return
        d = (
            doi_source.parse(record.raw, record.origin or "crossref")
            if record.raw
            else record.data or {}
        )
        event = d.get("event") or {}
        authors = d.get("authors") or []
        lines = [
            (_("From"), "Crossref" if record.origin == "crossref" else "doi.org (DataCite…)"),
            (_("Type"), d.get("doc_type")),
            (_("Title"), d.get("title")),
            (_("Venue"), d.get("venue")),
            (_("In"), d.get("container")),
            (_("Series"), d.get("series")),
            (_("Event"), " ".join(filter(None, [event.get("name"), event.get("acronym")]))),
            (_("Year"), d.get("year")),
            (
                _("Authors"),
                f"{len(authors)}: " + ", ".join(authors[:6]) + (" …" if len(authors) > 6 else "")
                if authors
                else None,
            ),
            (_("Publisher"), d.get("publisher")),
            ("ISSN", d.get("issn")),
            (_("Fetched"), f"{record.fetched_at:%Y-%m-%d}"),
        ]
        for k, v in lines:
            if v:
                with ui.row().classes("gap-1 no-wrap items-baseline"):
                    ui.label(k).classes("font-bold shrink-0")
                    ui.label(str(v))


def _stored_pdf(s: PubStat, done) -> None:
    """The PDF stored for the paper (to annotate): view or remove it, else store one."""
    if not s.pdf:
        with (
            ui.link(target=f"/pdf/{s.id}", new_tab=True)
            .classes("flex items-center gap-1")
            .mark("store-pdf")
        ):
            ui.icon("upload_file", size="xs")
            ui.label(_("Store a PDF"))
        return
    with (
        ui.link(target=f"/pdf/{s.id}", new_tab=True)
        .classes("flex items-center gap-1")
        .mark("stored-pdf")
    ):
        ui.icon("edit_document", size="xs", color="red-8")
        ui.label(_("Stored PDF (annotated)") if s.pdf == "edited" else _("Stored PDF"))

    def remove() -> None:
        def ok() -> None:
            pdfs.remove(s.id)
            confirm.close()
            done(_("PDF removed"))

        with ui.dialog() as confirm, ui.card():
            ui.label(
                _("Remove the stored PDF, with its annotations?")
                if s.pdf == "edited"
                else _("Remove the stored PDF?")
            )
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=confirm.close).props("flat")
                ui.button(_("Remove"), color="negative", on_click=ok).mark("remove-pdf-ok")
        confirm.open()

    ui.button(icon="delete_outline", on_click=remove).props("flat dense round size=sm").tooltip(
        _("Remove the stored PDF")
    ).mark("remove-pdf")


def _publication_tab(panel: PublicationsPanel, s: PubStat, done) -> None:
    # -- links and actions -----------------------------------------------------------------
    with ui.row().classes("w-full items-center gap-3"):
        if s.publisher_url and s.publisher_url != f"https://doi.org/{s.doi}":
            with ui.link(target=s.publisher_url, new_tab=True).classes("flex items-center gap-1"):
                ui.icon("launch", size="xs")
                ui.label(_("Publisher's page"))
        if s.doi:
            doi_link(s.doi)
        for pdf in s.pdf_urls:
            with ui.link(target=pdf, new_tab=True).classes("flex items-center gap-1"):
                ui.icon("picture_as_pdf", size="xs", color="red-7")
                ui.label("PDF")
        _stored_pdf(s, done)
        ui.space()
        ui.button(
            _("Unhide") if s.hidden else _("Hide"),
            icon="visibility" if s.hidden else "visibility_off",
            on_click=lambda: (
                annotations.set_hidden(s.id, not s.hidden),
                done(
                    _("Shown again") if s.hidden else _("Hidden from lists and stats"),
                    close_it=not s.hidden,
                ),
            ),
        ).props("flat dense").tooltip(_("Hide a misattributed / irrelevant record")).mark(
            "hide-publication"
        )

    # -- flags -----------------------------------------------------------------------------
    current = {f[0] for f in s.flags}
    with ui.row().classes("items-center gap-1"):
        ui.label(_("Flags")).classes("font-medium mr-2")
        for f in panel.flags:

            def toggle(fid=f.id) -> None:
                on = annotations.toggle_flag(s.id, fid)
                (current.add if on else current.discard)(fid)
                background_tasks.create(panel.reload())

            ui.chip(f.name, selectable=True, selected=f.id in current, on_click=toggle).style(
                chip_style(f.colour, f.id in current)
            ).props("dense " + ("color=primary" if f.id in current else "")).mark(
                f"flag-{f.name}"
            ).tooltip(_("track: {track}").format(track=f.track) if f.track else "")

    _authors_section(panel, s, done)

    # -- sources ---------------------------------------------------------------------------
    ui.label(_("Sources")).classes("font-medium mt-2")
    with ui.column().classes("w-full gap-1"):
        for m in s.members:
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                source_badge(m)
                with ui.column().classes("gap-0 grow min-w-0"):
                    ui.label(m.title or m.external_key)
                    ui.label(
                        f"{m.year or '—'} · {m.venue or _('no venue')}"
                        + (" · " + _("preprint") if m.archival else "")
                    ).classes("text-xs text-grey")
                if m.pdf_url:
                    with ui.link(target=m.pdf_url, new_tab=True):
                        ui.icon("picture_as_pdf", size="xs", color="red-7")
                if len(s.members) > 1:

                    def split(mid=m.id) -> None:
                        with session_scope() as ss:
                            merge.split_member(ss, ss.get(SourcePub, mid))
                        done(_("Split into a separate publication"))

                    ui.button(icon="call_split", on_click=split).props("flat round dense").tooltip(
                        _("Not the same paper: split this record out")
                    )

    others = {o.id: f"{o.year or '—'} · {o.title}" for o in panel.stats if o.id != s.id}
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        join = (
            ui.select(others, with_input=True, label=_("Same paper as… (merge into this one)"))
            .props("dense outlined clearable")
            .classes("grow")
        )

        def do_join() -> None:
            if not join.value:
                return
            with session_scope() as ss:
                target = ss.get(Publication, s.id)
                merge.join_publications(ss, target, [ss.get(Publication, join.value)])
            done(_("Merged"))

        ui.button(icon="merge", on_click=do_join).props("flat round dense").tooltip(_("Merge"))

    _corrections_section(s, done)


def _corrections_section(s: PubStat, done) -> None:
    """Type, year, author position and DOI set by hand (the sources are sometimes wrong)."""
    ui.label(_("Corrections for this paper")).classes("font-medium mt-2")
    with ui.row().classes("w-full items-center gap-2"):
        auto = s.kind if s.kind_source != "forced" else None
        kind = (
            ui.select(
                {
                    "": _("Automatic ({kind})").format(kind=KINDS[auto])
                    if auto
                    else _("Automatic"),
                    **KINDS,
                },
                value=s.kind if s.kind_source == "forced" else "",
                label=_("Type"),
            )
            .props("dense outlined")
            .classes("w-72")
            .tooltip(
                _(
                    "What this publication is (e.g. edited proceedings: not ranked as a paper of "
                    "the venue); automatic: from the venue and the sources' document types"
                )
            )
            .mark("paper-type")
        )

        def save_kind(e) -> None:
            annotations.set_kind_override(s.id, e.value or None)
            done(
                _("Type: {kind}").format(kind=KINDS[e.value])
                if e.value
                else _("Type back to automatic")
            )

        kind.on_value_change(save_kind)
    with ui.row().classes("w-full items-center gap-2"):
        year = (
            ui.number(_("Year"), value=s.year if s.year_manual else None, format="%d")
            .props(f"dense outlined clearable placeholder='{s.year or ''}'")
            .classes("w-28")
            .tooltip(_("Empty: the year from the sources"))
            .mark("override-year")
        )
        pos = (
            ui.number(
                _("Author position"),
                value=s.author_pos if s.author_pos_manual else None,
                format="%d",
                min=0,
            )
            .props(f"dense outlined clearable placeholder='{s.author_pos or ''}'")
            .classes("w-36")
            .tooltip(
                _(
                    "Empty: from the sources / the person's names; 0: the order does not matter "
                    "(e.g. edited proceedings)"
                )
            )
            .mark("override-position")
        )

        def save() -> None:
            annotations.set_overrides(
                s.id,
                year_override=int(year.value) if year.value else None,
                author_pos_override=int(pos.value) if pos.value not in (None, "") else None,
            )
            done(_("Corrections saved (kept on re-sync)"))

        ui.button(_("Save"), on_click=save).props("dense flat").mark("override-save")
    with ui.row().classes("w-full items-center gap-2"):
        doi = (
            ui.input(_("DOI (by hand)"), value=s.doi_manual or "")
            .props(f"dense outlined clearable placeholder='{s.doi or '10.xxxx/…'}'")
            .classes("grow")
            .tooltip(
                _(
                    "The DOI of this paper, if the sources miss it or give a wrong one: its "
                    "record (from the publisher) becomes the paper's main source"
                )
            )
            .mark("override-doi")
        )

        async def save_doi() -> None:
            value = (doi.value or "").strip() or None
            if value and not normalize_doi(value).startswith("10."):
                ui.notify(_("A DOI starts with 10. (e.g. 10.1145/3404835.3462812)"), type="warning")
                return
            person_id = annotations.set_doi(s.id, value)
            ui.notify(_("Fetching the DOI record…") if value else _("DOI removed"))
            await sync.sync_dois(person_id)
            done(_("DOI saved") if value else _("Back to the sources' DOI"))

        ui.button(_("Use this DOI"), on_click=save_doi).props("dense flat").mark(
            "override-doi-save"
        )


def _authors_section(panel: PublicationsPanel, s: PubStat, done) -> None:
    """Author list: click a name to say who it is (the person, a PhD student, a category)."""
    if not s.authors:
        return
    with session_scope() as ss:
        person = ss.get(Person, panel.person_id)
        person_name = person.name
        own_aliases = set(person.aliases or [])
        student_aliases = {k: set(v) for k, v in (person.student_aliases or {}).items()}
        person_cats = {int(k): set(v) for k, v in (person.author_categories or {}).items()}
        students = sorted(
            {
                n
                for t in ss.scalars(
                    select(Thesis)
                    .join(SourceLink)
                    .where(
                        SourceLink.person_id == panel.person_id,
                        active_links(),
                        Thesis.role == "director",
                    )
                )
                for n in (t.student or "").split(", ")
                if n
            }
        )
    categories = annotations.author_categories()
    ui.label(_("Authors")).classes("font-medium mt-2")
    ui.label(
        _(
            "Click a name to say who it is: the person, one of their PhD students, or a co-author "
            "category (e.g. international collaborators). Bold: the person · purple: PhD "
            "students · pale italic purple: former PhD students (paper more than two years "
            "after the defence) · dashed orange: possible match to confirm."
        )
    ).classes("text-xs text-grey")

    def act(fn, *args, msg: str) -> None:
        fn(panel.person_id, *args)
        done(msg)

    with ui.row().classes("gap-0 items-center"):
        for i, a in enumerate(s.authors):
            mark = s.author_marks[i] if i < len(s.author_marks) else None
            note, student = s.author_notes.get(i, (None, None))
            colour = panel.cat_colours.get(mark or "")
            with (
                ui.button()
                .props("flat dense no-caps padding='0 4px'")
                .classes("text-body2")
                .mark(f"author-{i}")
            ):
                span(author_html(a, mark, note, colour) + ("," if i < len(s.authors) - 1 else ""))
                with ui.menu():
                    _author_menu(
                        a,
                        mark,
                        student,
                        person_name=person_name,
                        is_alias=a in own_aliases,
                        student_aliases=student_aliases,
                        students=students,
                        categories=categories,
                        person_cats=person_cats,
                        act=act,
                        done=done,
                        title=s.title,
                    )


def _author_menu(
    a: str,
    mark: str | None,
    student: str | None,
    *,
    person_name: str,
    is_alias: bool,
    student_aliases: dict[str, set[str]],
    students: list[str],
    categories: list,
    person_cats: dict[int, set[str]],
    act,
    done,
    title: str | None = None,
) -> None:
    ui.label(a).classes("px-4 pt-2 text-weight-bold")
    # -- the person -----------------------------------------------------------------------
    if mark == "owner":
        ui.menu_item(f"✓ {person_name}").props("disable")
        if is_alias:
            ui.menu_item(
                _("✗ Not {name}").format(name=person_name),
                on_click=lambda: act(annotations.remove_alias, a, msg=_("Alias removed")),
            )
    elif mark == "owner?":
        ui.menu_item(
            _("✓ This is {name}").format(name=person_name),
            on_click=lambda: act(annotations.add_alias, a, msg=_("Alias added")),
        ).mark("confirm-owner")
        ui.menu_item(
            _("✗ Not {name}").format(name=person_name),
            on_click=lambda: act(annotations.reject_alias, a, msg=_("Won't be suggested again")),
        )
    else:
        ui.menu_item(
            _("This is {name}").format(name=person_name),
            on_click=lambda: act(annotations.add_alias, a, msg=_("Alias added")),
        ).mark("this-is-owner")
    # -- PhD students ---------------------------------------------------------------------
    if mark in ("student", "former") and student:
        ui.menu_item(_("✓ PhD student {name}").format(name=student)).props("disable")
        if a in student_aliases.get(student, set()):
            ui.menu_item(
                _("✗ Not {name}").format(name=student),
                on_click=lambda: act(annotations.remove_alias, a, student, msg=_("Alias removed")),
            )
    elif mark == "student?" and student:
        ui.menu_item(
            _("✓ This is {name}").format(name=student),
            on_click=lambda: act(annotations.add_alias, a, student, msg=_("Student alias added")),
        )
        ui.menu_item(
            _("✗ Not {name}").format(name=student),
            on_click=lambda: act(
                annotations.reject_alias, a, student, msg=_("Won't be suggested again")
            ),
        )
    if mark not in ("owner", "student", "former") and students:
        ui.separator()
        ui.label(_("PhD student…")).classes("px-4 text-caption text-grey")
        for st in students:
            if st == student and mark == "student?":
                continue
            ui.menu_item(
                st,
                on_click=lambda st=st: act(
                    annotations.add_alias, a, st, msg=_("Student alias added")
                ),
            )
    # -- author categories ----------------------------------------------------------------
    if mark != "owner":
        ui.separator()
        ui.label(_("Category…")).classes("px-4 text-caption text-grey")
        for c in categories:
            member = a in person_cats.get(c.id, set())
            ui.menu_item(
                f"{'✓ ' if member else ''}{c.name}",
                on_click=lambda c=c, member=member: act(
                    annotations.set_author_category,
                    a,
                    c.id,
                    not member,
                    msg=_("Removed from {category}").format(category=c.name)
                    if member
                    else _("Added to {category}").format(category=c.name),
                ),
            ).style(f"color:{c.colour}").mark(f"cat-{c.name}")
        ui.menu_item(_("New category…"), on_click=lambda: _new_category(a, act)).mark(
            "new-category"
        )
        # -- who is it: look them up elsewhere (in a new window) ---------------------------
        ui.separator()
        ui.label(_("Look up on…")).classes("px-4 text-caption text-grey")
        with ui.row().classes("px-3 pb-2 gap-1"):
            for label, url in author_search_links(a, title):
                ui.link(_(label), url, new_tab=True).classes(
                    "text-sm px-1 rounded hover:bg-grey-3"
                ).mark(f"lookup-{label}")


def author_search_links(name: str, title: str | None = None) -> list[tuple[str, str]]:
    """Where to find out who a co-author is: author searches, and the web (with a paper's
    title, which tells namesakes apart)."""
    q = quote_plus(name)
    links = [
        (
            "Google Scholar",
            f"https://scholar.google.com/citations?view_op=search_authors&mauthors={q}",
        ),
        ("Semantic Scholar", f"https://www.semanticscholar.org/search?q={q}"),
        ("DBLP", f"https://dblp.org/search/author?q={q}"),
        ("ORCID", f"https://orcid.org/orcid-search/search?searchQuery={q}"),
        ("HAL", f"https://hal.science/search/index/?q={quote_plus(f'authFullName_s:"{name}"')}"),
    ]
    web = f'"{name}"' + (f' "{title}"' if title else "")
    links.append((N_("Web"), f"https://www.google.com/search?q={quote_plus(web)}"))
    return links


def _new_category(name: str, act) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-80"):
        ui.label(_("New co-author category")).classes("text-lg")
        cname = (
            ui.input(_("Name"), placeholder=_("e.g. Intl. collaborators"))
            .classes("w-full")
            .mark("category-name")
        )
        colour = ui.color_input(_("Colour"), value="#0969da", preview=True).classes("w-full")

        def save() -> None:
            if not cname.value.strip():
                return
            cid = annotations.save_author_category(cname.value.strip(), colour.value)
            dlg.close()
            act(
                annotations.set_author_category,
                name,
                cid,
                True,
                msg=_("Added {name} to {category}").format(name=name, category=cname.value.strip()),
            )

        with ui.row().classes("justify-end w-full"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Create"), on_click=save).mark("category-create")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


class _Step:
    """One step of the matching process: its result, and an editor revealed by "override"."""

    def __init__(
        self, n: int, title: str, *, manual: bool = False, reset=None, editable: bool = True
    ) -> None:
        self.box = ui.column().classes("w-full gap-1 border rounded p-2")
        with self.box:
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.label(str(n)).classes(
                    "rounded-full bg-primary text-white text-xs w-5 h-5 flex "
                    "items-center justify-center shrink-0"
                )
                ui.label(_(title)).classes("font-medium")
                ui.badge(
                    _("manual") if manual else _("automatic"), color="primary" if manual else "grey"
                ).props("outline" if not manual else "")
                ui.space()
                if reset is not None and manual:
                    ui.button(icon="undo", on_click=reset).props(
                        "flat round dense size=sm"
                    ).tooltip(_("Back to automatic"))
                self.override_btn = (
                    ui.button(icon="edit", on_click=self.toggle)
                    .props("flat round dense size=sm")
                    .tooltip(_("Override"))
                    .mark(f"override-{title.split()[0].lower()}")
                )
                self.override_btn.visible = editable
            self.result = ui.column().classes("w-full gap-0 pl-7")
            self.editor = ui.column().classes("w-full gap-1 pl-7 pt-1")
            self.editor.visible = False

    def cancel_button(self) -> ui.button:
        """Close the editor without saving."""
        return (
            ui.button(_("Cancel"), on_click=self.toggle).props("dense flat").mark("cancel-override")
        )

    def toggle(self) -> None:
        self.editor.visible = not self.editor.visible
        self.override_btn.props(f"icon={'expand_less' if self.editor.visible else 'edit'}")


VIA_LABEL = Labels(
    {
        "variant": N_("a variant of the venue (set by hand)"),
        "pattern": N_("captured by a venue rule"),
        "auto": N_("same cleaned text"),
        "identifier": N_("its ISSN identifies the venue"),
        "archival": N_("a preprint server"),
    }
)


def _matching_tab(s: PubStat, done, show_venue) -> None:
    """How the venue is matched, step by step; each step can be overridden."""
    options = venues.venue_options()
    names = venues.venue_names()
    venue_id = s.venue_id
    venue_name = options.get(venue_id or -1)

    def venue_scope() -> ui.checkbox | None:
        """Scope of a decision: the venue (default), or only this paper when ticked."""
        if venue_id is None:
            return None
        with ui.row().classes("items-center gap-2"):
            box = ui.checkbox(_("Only for this paper")).mark("only-this-paper")
            hint = ui.label().classes("text-xs text-grey")
        hint.bind_text_from(
            box,
            "value",
            lambda only: (
                ""
                if only
                else _(
                    "otherwise saved on the venue “{venue}”: applies to all its papers, "
                    "for every person, and is kept on re-sync"
                ).format(venue=venue_name)
            ),
        )
        return box

    def for_venue(box) -> bool:
        return box is not None and not box.value

    # 1. venue from the sources -----------------------------------------------------------
    step = _Step(
        1,
        N_("Venue from the sources"),
        manual=bool(s.venue_source or s.venue_manual),
        reset=lambda: (
            annotations.set_venue_source(s.id, None),
            venues.link_publication(s.id, None),
            done(_("Venue back to automatic")),
        ),
    )
    # Preprints (arXiv, HAL deposits...) don't rank the paper: only the published versions
    # are listed (unless there are only preprints).
    published = [m for m in s.members if not m.archival] or s.members
    preprints = len(s.members) - len(published)
    used = {m.id for m in venue_members(s.members)}
    # Sources grouped by venue and track (a different track is a disagreement too; a record
    # without one takes that of its venue's other records: a demo paper is a demo).
    Key = tuple[int | None, str | None]
    groups: dict[Key, list[MemberView]] = {}
    for m in published:
        groups.setdefault((m.venue_id, track_of(m, published)), []).append(m)
    first = [k for k in groups if k[0] == venue_id and k[1] == s.track] or [
        k for k in groups if k[0] == venue_id
    ]
    if first:  # the paper's venue first
        groups = {first[0]: groups.pop(first[0]), **groups}
    no_venue = s.kind in NO_VENUE_KINDS and venue_id is None
    if no_venue:
        groups = {}
    several = len([g for g in groups if g[0] is not None]) > 1
    # A workshop's main conference is no disagreement (the workshop is more precise).
    dm = doi_member(s.members)
    conflicting = len(
        {
            (same_venue(ms[0]), k[1])
            for k, ms in groups.items()
            if k[0] is not None and not all(m.minor for m in ms)
        }
    ) > 1 and (dm is None or dm.minor)
    picked: set[Key] = set()
    actions: dict[str, ui.button] = {}

    def toggle(key: Key, on: bool) -> None:
        (picked.add if on else picked.discard)(key)
        if actions:
            actions["validate"].set_enabled(len(picked) == 1)
            actions["merge"].set_enabled(len({k[0] for k in picked}) > 1)

    def validate() -> None:
        (key,) = picked
        vid, track = key
        members = [m for m in groups[key] if m.id in used] or groups[key]
        m = pick_venue_member(members) or members[0]
        label = ADAPTERS[m.source].label
        with ui.dialog() as confirm, ui.card():
            ui.label(_("Use “{venue}” for this paper?").format(venue=names.get(vid, "?"))).classes(
                "font-medium"
            )
            with ui.row().classes("items-center gap-2").mark("validate-what"):
                rank_chip(m.badge, track, s.kind)
                if track:
                    ui.badge(TRACK_LABEL.get(track, track), color="purple-7")
                what = (
                    _("the venue from {source}, {track} track").format(
                        source=label, track=TRACK_LABEL.get(track, track)
                    )
                    if track
                    else _("the venue from {source}, main track").format(source=label)
                )
                if not m.badge:
                    what = _("{what} (not ranked)").format(what=what)
                ui.label(what).classes("text-sm")
            ui.label(
                _(
                    "The other sources' venues are then ignored for this paper, also after a "
                    "re-sync."
                )
            ).classes("text-xs text-grey")

            def ok() -> None:
                annotations.set_venue_source(s.id, m.source)
                done(_("Validated: the venue from {source}").format(source=label))
                confirm.close()

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=confirm.close).props("flat")
                ui.button(_("Validate"), on_click=ok).mark("confirm-validate")
        confirm.on_value_change(lambda e: None if e.value else confirm.delete())
        confirm.open()

    def explain_box(m: MemberView) -> None:
        """The cleaned text; a click shows how it was cleaned and matched."""
        from ..venue_match import explain

        box = ui.column().classes("gap-0 pl-2 border-l-2 w-full")
        box.visible = False
        state = {"filled": False}

        def show() -> None:
            if not state["filled"]:
                state["filled"] = True
                e = explain(m.source, m.venue)
                with box:
                    ui.label(_("source text: “{text}”").format(text=m.venue)).classes(
                        "text-xs font-mono"
                    )
                    for name, text in e.steps:
                        ui.label(f"{name} → “{text}”").classes("text-xs font-mono text-grey")
                    ui.label(_("key: “{key}”").format(key=e.key)).classes(
                        "text-xs font-mono text-grey"
                    )
                    how = VIA_LABEL.get(e.via or "", e.via or _("no venue"))
                    if e.via == "pattern" and e.detail:
                        how += f": {e.detail}"
                    elif e.via in ("variant", "auto") and e.detail:
                        how = _("{how} (variant “{variant}”)").format(how=how, variant=e.detail)
                    ui.label(f"→ {e.venue_name or _('no venue')} — {how}").classes("text-xs")
            box.visible = not box.visible

        with ui.row().classes("items-center gap-2 no-wrap"):
            source_badge(m)
            ui.button(
                service.clean(m.venue, m.source) or m.venue or _("no venue"), on_click=show
            ).props("flat dense no-caps size=sm align=left").classes("text-body2 min-w-0").style(
                "white-space:normal;text-align:left"
            ).tooltip(_("Show how the text was cleaned and matched")).mark(f"member-text-{m.id}")
            if m.conflicts:
                others = ", ".join(names.get(c, "?") for c in m.conflicts)
                ui.button(
                    icon="warning",
                    on_click=lambda m=m: merge_venues_dialog(
                        [m.venue_id, *m.conflicts], done, text=m.venue
                    ),
                ).props("flat round dense size=sm color=orange-8").tooltip(
                    _(
                        "The rules of other venues match this text too: {venues}. "
                        "Click to merge them into one venue."
                    ).format(venues=others)
                ).mark(f"conflict-{m.id}")
            ui.button(
                icon="tune",
                on_click=lambda m=m: venue_rule_dialog(
                    done, sample=(m.source, m.venue), venue_id=m.venue_id
                ),
            ).props("flat round dense size=sm").tooltip(
                _("Add a venue rule: texts matching a regex belong to a venue")
            ).mark(f"mapping-{m.id}")
            if m.id not in used:
                ui.label(_("not used")).classes("text-xs text-grey").tooltip(
                    _("{source} venues are only used when no other source gives one").format(
                        source=ADAPTERS[m.source].label
                    )
                )
        box.move(target_index=-1)

    with step.result:
        if not s.members:
            ui.label(_("no source record")).classes("text-grey text-sm")
        if no_venue:
            ui.label(
                _(
                    "A {kind} has no venue (the sources give “{venue}”): change its kind if it "
                    "is a paper of a conference or journal."
                ).format(kind=KINDS[s.kind].lower(), venue=s.venue)
                if s.venue
                else _(
                    "A {kind} has no venue: change its kind if it is a paper of a conference or "
                    "journal."
                ).format(kind=KINDS[s.kind].lower())
            ).classes("text-sm text-grey").mark("no-venue-kind")
        if s.venue_manual:
            ui.label(_("Linked by hand to “{venue}”").format(venue=venue_name)).classes(
                "text-xs text-primary"
            )
        elif conflicting and not s.venue_source:
            ui.label(
                _(
                    "The sources give different venues or tracks: select one to validate it for "
                    "this paper, or several venues to merge them."
                )
            ).classes("text-xs text-orange-9")
        auto = pick_venue_member(s.members) if not (s.venue_source or s.venue_manual) else None
        if auto is not None and several:
            with ui.row().classes("items-start gap-1 no-wrap w-full").mark("auto-pick"):
                ui.icon("auto_awesome", size="xs", color="primary")
                ui.label(
                    _("Automatically using {source}: {reason}.").format(
                        source=ADAPTERS[auto.source].label, reason=pick_reason(auto, s.members)
                    )
                ).classes("text-xs text-primary min-w-0 grow")
        for key, members in groups.items():
            vid, track = key
            suffix = f"{vid}-{track}" if track else f"{vid}"
            with (
                ui.column().classes("w-full gap-0 border rounded p-1").mark(f"venue-group-{suffix}")
            ):
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    if several and vid is not None:
                        ui.checkbox(on_change=lambda e, k=key: toggle(k, e.value)).props(
                            "dense"
                        ).mark(f"pick-venue-{suffix}")
                    best = pick_venue_member(members) or members[0]
                    rank_chip(best.badge, track, s.kind)
                    ui.label(names.get(vid, _("no venue")) if vid else _("no venue")).classes(
                        "font-medium text-sm grow min-w-0"
                    ).mark(f"group-venue-{vid}")
                    if track:
                        why = (
                            _("set by the venue's variant / rule")
                            if any(m.track for m in members)
                            else _("found in the source's text")
                        )
                        if any(not m.eff_track for m in members):
                            why = _("{why}; it wins over the sources giving no track").format(
                                why=why
                            )
                        ui.badge(TRACK_LABEL.get(track, track), color="purple-7").tooltip(
                            _("Track: {why}").format(why=why)
                        ).mark(f"group-track-{suffix}")
                    if all(m.minor for m in members):
                        ui.label(_("main conference of the workshop")).classes(
                            "text-xs text-grey shrink-0"
                        ).tooltip(
                            _("A minor difference: the workshop's venue is more precise")
                        ).mark(f"group-minor-{suffix}")
                    if vid is not None:
                        ui.button(icon="visibility", on_click=lambda v=vid: show_venue(v)).props(
                            "flat round dense size=sm"
                        ).tooltip(_("Open the venue")).mark(f"open-venue-{vid}")
                    if key == next(iter(groups)) and vid == venue_id:
                        what = (
                            N_("linked by hand")
                            if s.venue_manual
                            else N_("validated")
                            if s.venue_source
                            else N_("used")
                        )
                        ui.label(_(what)).classes(
                            "text-xs shrink-0 "
                            + ("text-positive" if what != "used" else "text-primary")
                        )
                for m in members:
                    explain_box(m)
        if several:
            with ui.row().classes("items-center gap-2"):
                actions["validate"] = (
                    ui.button(_("Validate for this paper"), icon="task_alt", on_click=validate)
                    .props("dense")
                    .mark("validate-source")
                )
                actions["merge"] = (
                    ui.button(
                        _("Merge the selected venues"),
                        icon="merge",
                        on_click=lambda: merge_venues_dialog(
                            sorted({k[0] for k in picked if k[0]}), done
                        ),
                    )
                    .props("dense outline")
                    .mark("merge-selected-venues")
                )
                actions["validate"].set_enabled(False)
                actions["merge"].set_enabled(False)
        if preprints:
            ui.label(
                ngettext(
                    "{n} preprint record(s) ignored", "{n} preprint record(s) ignored", preprints
                ).format(n=preprints)
            ).classes("text-xs text-grey")
        ui.button(
            _("Search for another venue"),
            icon="search",
            on_click=lambda: search_venue_dialog(
                s.id, [k[0] for k in groups if k[0] is not None], done
            ),
        ).props("dense flat no-caps").mark("search-venue")
    with step.editor, ui.row().classes("items-center gap-2 w-full no-wrap"):
        pick_venue = (
            ui.select(options, value=venue_id, with_input=True, label=_("Link to another venue"))
            .props("dense outlined clearable")
            .classes("grow")
        )

        def link() -> None:
            if pick_venue.value:
                venues.link_publication(s.id, pick_venue.value)
                done(_("Linked to the venue (by hand: kept on re-sync)"))

        step.cancel_button()
        ui.button(_("Link"), icon="link", on_click=link).props("dense")

    # 2. kind ------------------------------------------------------------------------------
    source_label = {
        "forced": _("set for this paper"),
        "venue": _("set on the venue"),
        "detected": _("detected from the venue text, document type and ranking"),
    }[s.kind_source]
    step = _Step(
        2,
        N_("Kind of publication"),
        manual=s.kind_source != "detected",
        reset=(
            lambda: (annotations.set_kind_override(s.id, None), done(_("Kind back to automatic")))
        )
        if s.kind_source == "forced"
        else None,
    )
    with step.result:
        with ui.row().classes("items-center gap-2"):
            rank_chip(s.badge, s.track, s.kind)
            ui.label(KINDS[s.kind]).classes("text-sm")
            ui.label(source_label).classes("text-xs text-grey")
        if s.track:
            _track_line(s)
    with step.editor:
        kind = (
            ui.select(
                {"": _("Automatic ({kind})").format(kind=KINDS[s.kind]), **KINDS},
                value=s.kind if s.kind_source == "forced" else "",
            )
            .props("dense outlined")
            .classes("w-72")
        )
        kind_box = venue_scope()

        def save_kind() -> None:
            if for_venue(kind_box) and kind.value in PUBLICATION_ONLY_KINDS:
                ui.notify(
                    _("A venue cannot be “{kind}”: saved for this paper").format(
                        kind=KINDS[kind.value]
                    ),
                    type="info",
                )
            if for_venue(kind_box) and kind.value not in PUBLICATION_ONLY_KINDS:
                venues.update_venue(venue_id, kind=kind.value or None)
                annotations.set_kind_override(s.id, None)
                done(_("Kind saved on the venue"))
            else:
                annotations.set_kind_override(s.id, kind.value or None)
                done(_("Kind saved for this paper"))

        with ui.row().classes("gap-2"):
            step.cancel_button()
            ui.button(_("Save"), on_click=save_kind).props("dense")

    # 3. rank ------------------------------------------------------------------------------
    b = s.badge
    rank_manual = bool(s.rank_override) or bool(b and (b.manual or b.forced))
    step = _Step(
        3,
        N_("Rank"),
        manual=rank_manual,
        reset=(
            lambda: (annotations.set_rank_override(s.id, None), done(_("Back to the venue's rank")))
        )
        if s.rank_override
        else None,
    )
    with step.result:
        with ui.row().classes("items-center gap-2"):
            rank_chip(b, None, s.kind)
            if b and b.extra.get("kind_default"):
                ui.label(
                    _("default level of “{kind}” (Settings → Venue kinds)").format(
                        kind=KINDS[b.extra["kind_default"]]
                    )
                ).classes("text-sm")
            elif b:
                ui.label(b.name or "").classes("text-sm")
            elif s.kind in WORKSHOP_KINDS:
                ui.label(
                    _("a workshop without a main conference for this year: set it in its venue")
                ).classes("text-sm text-grey")
            else:
                ui.label(_("no ranking record matched")).classes("text-sm text-grey")
        if s.host_name and not s.rank_override:
            ui.label(
                _("Workshop: the rank of its main conference in {year}, “{host}”").format(
                    year=s.year or _("its year"), host=s.host_name
                )
            ).classes("text-sm").mark("workshop-host")
        if b and (parts := b.extra.get("joint")) and not s.rank_override:
            ui.label(
                _("Joint venue ({venues}): {how}").format(
                    venues=" + ".join(parts),
                    how=_("levels differ, the lowest is used until one is chosen in the venue")
                    if b.extra.get("joint_differ")
                    else _("the level of the conference chosen in the venue")
                    if b.manual
                    else _("the level its conferences share"),
                )
            ).classes("text-sm").mark("joint-venue")
        if b and not b.extra.get("kind_default"):
            with ui.row().classes("items-center gap-2"):
                ui.label(badge_details(b).split("\n", 1)[-1].replace("\n", " · ")).classes(
                    "text-xs text-grey"
                )
                _check_link(b)
        if s.rank_override:
            what = (
                _("record picked for this paper")
                if s.rank_override.get("record_key")
                else _("level set for this paper")
            )
            ui.label(
                _("{what}: {note}").format(what=what, note=s.rank_note) if s.rank_note else what
            ).classes("text-xs text-primary").mark("rank-note")
    with step.editor:
        _rank_editor(s, done, venue_scope, for_venue, venue_id, step)


def _track_line(s: PubStat) -> None:
    """Where the paper's track (demo, findings...) comes from."""
    name = TRACK_LABEL.get(s.track, s.track)
    flag = next((f for f in s.flags if f[1] == s.track), None)
    giving = [m for m in s.members if m.venue_id == s.venue_id and m.eff_track == s.track]
    silent = [
        m for m in s.members if m.venue_id == s.venue_id and not m.eff_track and not m.archival
    ]
    if flag is not None:
        why = _("set by the flag “{flag}”").format(flag=flag[1])
    elif giving:
        why = _("given by {sources}").format(
            sources=", ".join(dict.fromkeys(ADAPTERS[m.source].label for m in giving))
        )
        if silent:
            why = _("{why}; it wins over {sources} (no track: the main conference)").format(
                why=why,
                sources=", ".join(dict.fromkeys(ADAPTERS[m.source].label for m in silent)),
            )
    elif s.badge and s.badge.findings:
        why = _("the ranking record is the Findings")
    else:
        why = _("given by the sources")
    with ui.row().classes("items-center gap-2 no-wrap").mark("kind-track"):
        ui.badge(name, color="purple-7")
        ui.label(_("{track} track: {why}").format(track=name, why=why)).classes("text-xs")


SOURCE_SITES = Labels({"scimago": "Scimago", "core": N_("the CORE portal"), "jcr": "JCR"})


def _check_link(b: Badge) -> None:
    """Link to the ranking list's own page, to check a (possibly fuzzy) match."""
    if b.url and b.source in SOURCE_SITES:
        with ui.link(target=b.url, new_tab=True).classes("flex items-center gap-1 text-xs"):
            ui.icon("open_in_new", size="xs")
            ui.label(_("check on {site}").format(site=SOURCE_SITES[b.source]))


def _rank_editor(s: PubStat, done, venue_scope, for_venue, venue_id, step: _Step) -> None:
    def note_input(box) -> ui.input:
        """Why the rank of this paper differs from its venue's (asked for paper-level ranks)."""
        note = (
            ui.input(_("Why (required for this paper only)"), value=s.rank_note or "")
            .props("dense outlined")
            .classes("w-full")
            .mark("rank-note-input")
        )
        if box is not None:
            note.bind_visibility_from(box, "value")
        return note

    def paper_override(box, note: ui.input, override: dict, msg: str) -> None:
        if not note.value.strip():
            ui.notify(_("Say why this paper's rank differs from its venue's"), type="warning")
            return
        annotations.set_rank_override(s.id, override, note.value.strip())
        done(msg)

    with ui.tabs().props("dense align=left") as tabs:
        ui.tab("record", _("Pick a ranking record"))
        ui.tab("level", _("Set a level"))
    with ui.tab_panels(tabs, value="record").classes("w-full"):
        with ui.tab_panel("record").classes("p-0"):
            seen: dict[str, Badge] = {}
            for m in s.members:
                if m.venue:
                    for b in service.candidates(m.venue_name or m.venue, limit=30):
                        if b.recordKey and b.recordKey not in seen:
                            seen[b.recordKey] = b
            # Records from the lists that fit the kind: CORE for conferences, Scimago / JCR
            # for journals (all lists for other kinds, or when asked).
            lists = (
                {"core"}
                if s.kind in CONFERENCE_LIKE
                else {"scimago", "jcr"}
                if s.kind.endswith("journal")
                else None
            )
            rec_box = venue_scope()
            rec_note = note_input(rec_box)
            query = {"text": ""}
            all_lists = (
                ui.switch(_("all ranking lists"), value=lists is None)
                .props("dense")
                .tooltip(
                    _("Only {lists} records are listed for a {kind}").format(
                        lists="CORE" if lists == {"core"} else "Scimago / JCR",
                        kind=KINDS[s.kind].lower(),
                    )
                    if lists
                    else ""
                )
            )
            all_lists.visible = lists is not None
            results = ui.column().classes("w-full gap-1")

            def pick(key: str | None) -> None:
                if for_venue(rec_box):
                    venues.update_venue(venue_id, record_key=key)
                    annotations.set_rank_override(s.id, None)
                    done(_("Ranking record saved on the venue"))
                else:
                    paper_override(
                        rec_box,
                        rec_note,
                        {"record_key": key},
                        _("Ranking record saved for this paper"),
                    )

            def show(badges: list[Badge]) -> None:
                if lists and not all_lists.value:
                    badges = [b for b in badges if b.source in lists]
                results.clear()
                with results:
                    if not badges:
                        ui.label(_("No candidate")).classes("text-grey")
                    for b in sorted(badges, key=lambda b: -b.score)[:12]:
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            rank_chip(b)
                            ui.label(
                                f"{b.name} · {b.source} · {round(b.score * 100)}%"
                                + (" · " + _("acronym") if b.extra.get("acronym") else "")
                            ).classes("text-sm")
                            _check_link(b)
                            ui.button(icon="check", on_click=lambda k=b.recordKey: pick(k)).props(
                                "dense flat round size=sm"
                            ).tooltip(_("Use this record"))

            def update() -> None:
                text = query["text"]
                show(service.search(text, 60) if len(text) > 2 else list(seen.values()))

            def set_query(e) -> None:
                query["text"] = e.value or ""
                update()

            ui.input(_("search the ranking datasets"), on_change=set_query).props(
                "dense outlined debounce=300"
            ).classes("w-full").move(target_index=0)
            all_lists.on_value_change(lambda _e: update())
            update()
            step.cancel_button()

        with ui.tab_panel("level").classes("p-0"):
            with ui.row().classes("items-center gap-2"):
                vtype = ui.select(
                    {"conference": _("Conference (CORE)"), "journal": _("Journal (quartile)")},
                    value="journal" if s.kind.endswith("journal") else "conference",
                ).props("dense outlined")
                start = QUARTILES if vtype.value == "journal" else CORE_RANKS
                rank = ui.select(
                    level_options(start),
                    value=start[0] if start is QUARTILES else "A",
                    new_value_mode="add-unique",
                    with_input=True,
                ).props("dense outlined")
                vtype.on_value_change(
                    lambda e: rank.set_options(
                        level_options(CORE_RANKS if e.value == "conference" else QUARTILES),
                        value="A" if e.value == "conference" else "Q1",
                    )
                )
            level_hint(rank)
            lvl_box = venue_scope()
            lvl_note = note_input(lvl_box)

            def save_level() -> None:
                if for_venue(lvl_box):
                    venues.update_venue(venue_id, level_type=vtype.value, level_rank=rank.value)
                    annotations.set_rank_override(s.id, None)
                    done(_("Level saved on the venue"))
                else:
                    level = {"type": vtype.value, "rank": rank.value, "name": s.venue}
                    paper_override(lvl_box, lvl_note, level, _("Manual level saved for this paper"))

            with ui.row().classes("gap-2"):
                step.cancel_button()
                ui.button(_("Save"), on_click=save_level).props("dense")
            level_legend()


def person_publications(person_id: int) -> list[Publication]:
    with session_scope() as s:
        return list(s.scalars(select(Publication).where(Publication.person_id == person_id)))
