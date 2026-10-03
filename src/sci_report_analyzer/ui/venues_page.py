"""Venue pages: conferences and journals with their variants and manual decisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html import escape

from nicegui import background_tasks, ui
from sqlalchemy.orm import selectinload

from .. import venue_match, venues
from ..db.models import Venue
from ..db.session import session_scope
from ..i18n import N_, _, ngettext
from ..ranking.badge import TRACK_LABEL, category_of, core_periods, sjr_periods, text_colour
from ..ranking.kinds import KIND_SHORT, KINDS, VENUE_KINDS, WORKSHOP_KINDS
from ..ranking.service import service
from ..sources import ADAPTERS
from .pub_details import VIA_LABEL, venue_rule_dialog
from .theme import (
    badge_details,
    frame,
    level_hint,
    level_options,
    merge_direction,
    rank_chip,
    source_tag,
    span,
)

TABS = {
    "conferences": (N_("Conferences"), venues.CONFERENCE_KINDS),
    "workshops": (N_("Workshops"), WORKSHOP_KINDS),
    "journals": (N_("Journals"), venues.JOURNAL_KINDS),
    "other": (N_("Other & preprints"), ("preprint", "other")),
}
LEVELS = ["A*", "A", "B", "C", "Q1", "Q2", "Q3", "Q4"]


def _papers(n: int) -> str:
    return ngettext("{n} paper", "{n} papers", n).format(n=n)


def _chip_html(row: venues.VenueRow) -> str:
    cat = category_of(row.badge, None, row.kind)
    label = cat.label if row.badge or cat.base_key.startswith("k_") else _("not ranked")
    colour = cat.colour
    style = f"background:{colour};color:{text_colour(colour)}"
    return f'<span class="vr-chip" style="{style}">{escape(label)}</span>' + (
        f' <span title="{escape(_("manual decision"))}">✎</span>' if row.manual else ""
    )


def _core_history(badge, host: str | None = None) -> None:
    """A conference's CORE ranks over the editions (e.g. A until 2021, A* since 2023)."""
    if badge is None or not badge.coreHistory:
        return
    with ui.row().classes("items-center gap-1 text-sm").mark("venue-core-history"):
        ui.label(f"{host}: CORE" if host else "CORE").classes("text-grey")
        for i, (a, b, rank) in enumerate(core_periods(badge.coreHistory)):
            if i:
                ui.icon("arrow_forward", size="xs", color="grey")
            ui.label(rank or _("unranked")).classes("font-medium")
            ui.label(f"({a}–{b})" if a != b else f"({a})").classes("text-grey")
        ui.icon("info", size="xs", color="grey").tooltip(
            _(
                "A paper takes the rank of the CORE edition in force in its year (Settings: or "
                "the latest one)"
            )
        )


def _sjr_history(badge) -> None:
    """A journal's Scimago quartiles over the years (e.g. Q2 2015–2019, Q1 2020–2024)."""
    if badge is None or not badge.sjrHistory:
        return
    with ui.row().classes("items-center gap-1 text-sm").mark("venue-sjr-history"):
        ui.label("Scimago").classes("text-grey")
        for i, (a, b, q) in enumerate(sjr_periods(badge.sjrHistory)):
            if i:
                ui.icon("arrow_forward", size="xs", color="grey")
            ui.label(q).classes("font-medium")
            ui.label(f"({a}–{b})" if a != b else f"({a})").classes("text-grey")
        ui.icon("info", size="xs", color="grey").tooltip(
            _(
                "A paper takes the quartile of its year (else of the closest year before); "
                "years missing: Settings › Data & cache, “Scimago, past years”"
            )
        )


def register() -> None:
    @ui.page("/venues")
    async def venues_page(tab: str = "conferences", focus: int | None = None) -> None:
        with frame(_("Venues")):
            ui.label(_("Venues")).classes("text-2xl")
            ui.label(
                _(
                    "Every venue seen in the validated sources. A source's venue text belongs "
                    "to a venue by its variants (same cleaned text), its rules (regex) or its "
                    "ISSN. Click a venue to set its kind or rank by hand, or to change what it "
                    "matches; manual decisions are kept on re-sync and shared when exporting "
                    "the settings."
                )
            ).classes("text-grey text-sm")
            _lookup()
            with ui.row().classes("gap-2"):
                ui.button(
                    _("Add a venue"), icon="add", on_click=lambda: _add_venue_dialog(view)
                ).props("dense outline").mark("venue-add")
                ui.button(
                    _("Propose merges…"), icon="merge", on_click=lambda: _proposals_dialog(view)
                ).props("dense outline").tooltip(
                    _("Venues that look alike, one pair at a time: merge them or say they differ")
                ).mark("venue-propose-merges")
            conflicts_box = ui.column().classes("w-full")
            container = ui.column().classes("w-full")
            with container:
                ui.spinner(size="lg")
            # Outside the list, which is rebuilt after a change.
            dialogs = ui.element("div")
            # The tab and the filter are kept when the list is rebuilt (after a merge...).
            view = _View(tab=tab, dialogs=dialogs)

            def show_conflicts() -> None:
                conflicts_box.clear()
                with conflicts_box:
                    _conflicts(view)

            async def reload() -> None:
                rows = await venues.venue_rows()
                if container.is_deleted:
                    return
                show_conflicts()
                view.rows = rows
                container.clear()
                with container:
                    _tabs(rows, view)

            view.reload = lambda: background_tasks.create(reload())
            show_conflicts()
            rows = view.rows = await venues.venue_rows()
            container.clear()
            with container:
                _tabs(rows, view, focus)


def _lookup() -> None:
    with ui.expansion(_("Test a venue string"), icon="search").classes("w-full"):
        out = ui.column().classes("w-full gap-0")

        async def run(e) -> None:
            out.clear()
            if not e.value:
                return
            badge = await service.resolve(e.value)
            with out:
                ui.label(_("cleaned as: “{text}”").format(text=service.clean(e.value))).classes(
                    "text-sm text-grey font-mono"
                )
                with ui.row().classes("items-center gap-2"):
                    rank_chip(badge)
                    ui.label(
                        badge_details(badge).replace("\n", " · ") if badge else _("not ranked")
                    )

        ui.input(_("Venue text"), on_change=run).props("debounce=500 clearable").classes(
            "w-full"
        ).move(target_index=0)


def _add_venue_dialog(view: _View) -> None:
    def added(vid: int, created: bool) -> None:
        view.reload()

        async def show() -> None:
            with view.dialogs:
                await open_venue(vid, view.reload)

        background_tasks.create(show())

    with view.dialogs:
        _new_venue_dialog(added)


def _new_venue_dialog(on_added: Callable[[int, bool], None], kind: str = "intl_conference") -> None:
    """Add a venue by hand; ``on_added(venue id, created)`` (False: one has this name)."""
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl"):
        ui.label(_("Add a venue")).classes("text-lg font-medium")
        name = ui.input(_("Name")).classes("w-full").mark("venue-add-name")
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            short = ui.input(_("Short name (acronym)")).classes("w-48").mark("venue-add-short")
            kind_select = (
                ui.select(VENUE_KINDS, value=kind, label=_("Kind"))
                .props("dense outlined")
                .classes("grow")
                .mark("venue-add-kind")
            )
        url = (
            ui.input(_("Website"), placeholder="https://…").classes("w-full").mark("venue-add-url")
        )
        ui.label(
            _(
                "The sources' texts with this name belong to it; add variants or rules in the "
                "venue to match other texts."
            )
        ).classes("text-xs text-grey")

        def ok() -> None:
            text = (name.value or "").strip()
            if not text:
                ui.notify(_("Give the venue a name"), type="warning")
                return
            vid, created = venues.add_venue(
                text, kind_select.value, (short.value or "").strip(), url.value
            )
            ui.notify(_("Venue added") if created else _("A venue already has this name"))
            dlg.close()
            on_added(vid, created)

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Add"), icon="add", on_click=ok).mark("venue-add-confirm")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def _conflicts(view: _View) -> None:
    """Texts that several venues match: a button opening a dialog to resolve them."""
    found = venues.conflicts()
    if not found:
        return
    with ui.row().classes("w-full items-center gap-2 bg-orange-1 rounded p-2"):
        ui.icon("warning", color="orange-8")
        ui.label(
            ngettext(
                "{n} venue text is matched by several venues.",
                "{n} venue texts are matched by several venues.",
                len(found),
            ).format(n=len(found))
        ).classes("grow")
        ui.button(_("Resolve…"), icon="rule", on_click=lambda: _conflict_dialog(view)).props(
            "dense"
        ).mark("venue-conflicts")


def _conflict_dialog(view: _View) -> None:
    """One problem at a time: merge the venues, or keep the text in one of them."""
    state = {"i": 0, "changed": False}
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-2xl"):
        body = ui.column().classes("w-full gap-2")

    def close() -> None:
        if state["changed"]:
            view.reload()
        dlg.close()

    def act(fn, message: str) -> None:
        fn()
        state["changed"] = True
        ui.notify(message, type="positive")
        show()

    def show() -> None:
        found = venues.conflicts()
        body.clear()
        with body:
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label(_("Venues matching the same text")).classes("text-lg font-medium grow")
                ui.button(icon="close", on_click=close).props("flat round dense")
            if not found:
                ui.label(_("No conflict left.")).classes("text-positive")
                ui.button(_("Close"), on_click=close).mark("conflicts-done")
                return
            state["i"] %= len(found)
            source, raw, ids = found[state["i"]]
            names = venues.venue_names()
            ui.label(_("Problem {i} of {n}").format(i=state["i"] + 1, n=len(found))).classes(
                "text-xs text-grey"
            )
            with ui.row().classes("items-center gap-2"):
                source_tag(source)
                ui.label(f"“{raw}”").classes("font-mono")
            ui.label(_("It is matched by:")).classes("text-sm")
            for n, vid in enumerate(ids):
                rules = [r.pattern for r in venues.venue_patterns(vid) if r.applies(source, raw)]
                with ui.row().classes("items-center gap-2 no-wrap pl-2"):
                    ui.label(names.get(vid, "?")).classes("font-medium")
                    ui.label(
                        (_("belongs to it now") if n == 0 else _("also matches"))
                        + (
                            _(" — rule {rules}").format(rules=", ".join(rules))
                            if rules
                            else _(" — same cleaned text")
                        )
                    ).classes("text-xs text-grey")
                    with ui.link(target=f"/venues?focus={vid}", new_tab=True):
                        ui.icon("visibility", size="xs").tooltip(_("Open the venue"))
            ui.label(_("Solutions")).classes("font-medium mt-2")
            for vid in ids:
                name = names.get(vid, "?")
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.button(
                        _("Merge all into “{name}”").format(name=name),
                        icon="merge",
                        on_click=lambda v=vid, n=name: act(
                            lambda: venues.merge_venues(v, [o for o in ids if o != v]),
                            _("Merged into “{name}”").format(name=n),
                        ),
                    ).props("dense outline no-caps").mark(f"conflict-merge-{vid}")
                    ui.button(
                        _("Only in “{name}”").format(name=name),
                        icon="push_pin",
                        on_click=lambda v=vid, n=name: act(
                            lambda: venues.add_variant(v, raw, source),
                            _("“{text}” now belongs to “{name}” (a variant set by hand)").format(
                                text=raw, name=n
                            ),
                        ),
                    ).props("dense flat no-caps").tooltip(
                        _(
                            "Keep the venues apart: this text becomes a variant of this venue, "
                            "which wins over the other venues' rules"
                        )
                    ).mark(f"conflict-keep-{vid}")
            with ui.row().classes("w-full justify-between mt-2"):
                ui.button(
                    _("Previous"),
                    icon="chevron_left",
                    on_click=lambda: (state.update(i=state["i"] - 1), show()),
                ).props("flat dense")
                ui.button(
                    _("Skip"),
                    icon="chevron_right",
                    on_click=lambda: (state.update(i=state["i"] + 1), show()),
                ).props("flat dense").mark("conflict-skip")

    show()
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def _proposals_dialog(view: _View) -> None:
    """Venues that look alike, one pair at a time: choose the primary venue, then what the
    secondary one is for it (the same venue, one of its tracks, a joint conference including
    it, one of its workshops; guessed), or say they are not the same."""
    proposals = venues.merge_proposals(view.rows)
    state = {"i": 0, "changed": False}
    with view.dialogs, ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl"):
        body = ui.column().classes("w-full gap-2")

    def close() -> None:
        if state["changed"]:
            view.reload()
        dlg.close()

    def merge(keep: venues.VenueRow, other: venues.VenueRow, names: _MergeNames) -> None:
        name, short, kind = names.values(keep)
        venues.merge_venues(keep.id, [other.id], name, short, kind)
        state["changed"] = True
        ui.notify(_("Merged into “{name}”").format(name=name or keep.name), type="positive")
        # The merged venue is gone: its other proposals too (the list is recomputed when
        # the dialog is opened again).
        proposals[:] = [p for p in proposals if other not in p[:2]]
        show()

    def relate(row: venues.VenueRow, other: venues.VenueRow, relation: str) -> None:
        kept = venues.relate_venues(row.id, [other.id], relation)
        state["changed"] = True
        ui.notify(_("Relation recorded"), type="positive")
        # A track is merged into its venue: the proposals of the merged venue go too.
        gone = {row.id, other.id} - {kept} if relation.lstrip("~").startswith("track") else set()
        proposals[:] = [
            p for i, p in enumerate(proposals) if i != state["i"] and not gone & {p[0].id, p[1].id}
        ]
        show()

    def not_same(a: venues.VenueRow, b: venues.VenueRow) -> None:
        venues.set_not_same(a.id, b.id)
        proposals.pop(state["i"])
        show()

    def card(r: venues.VenueRow, primary: bool, make_primary) -> None:
        with (
            ui.card()
            .props("flat bordered")
            .classes("grow basis-0 gap-1")
            .style("border-color: var(--q-primary); border-width: 2px" if primary else "")
        ):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                if primary:
                    ui.badge(_("primary"), color="primary").mark(f"proposal-primary-{r.id}")
                else:
                    ui.badge(_("secondary"), color="grey")
                ui.badge(KINDS[r.kind], color="blue-grey").props("outline").mark(
                    f"proposal-kind-{r.id}"
                )
                ui.space()
                if not primary:
                    ui.button(_("Make it primary"), icon="push_pin", on_click=make_primary).props(
                        "dense flat no-caps"
                    ).mark(f"proposal-make-primary-{r.id}")
            with ui.row().classes("items-center gap-2 no-wrap"):
                span(_chip_html(r))
                ui.label(r.name).classes("font-medium")
                with ui.link(target=f"/venues?focus={r.id}", new_tab=True):
                    ui.icon("open_in_new", size="xs").tooltip(_("Open the venue"))
            ui.label(
                " · ".join(
                    (
                        r.short_name or "—",
                        _papers(r.publications),
                        ngettext("{n} person", "{n} people", len(r.people)).format(n=len(r.people)),
                    )
                )
            ).classes("text-xs text-grey")
            texts = sorted(r.variants, key=lambda v: -v[2])
            for _key, example, n, *_rest in texts[:4]:
                ui.label(f"“{example}” ({n})").classes("text-xs font-mono")
            if len(texts) > 4:
                ui.label(
                    ngettext("… {n} more variant", "… {n} more variants", len(texts) - 4).format(
                        n=len(texts) - 4
                    )
                ).classes("text-xs text-grey")

    def show() -> None:
        body.clear()
        with body:
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label(_("Venues that look alike")).classes("text-lg font-medium grow")
                ui.button(icon="close", on_click=close).props("flat round dense")
            if not proposals:
                ui.label(_("No merge to propose.")).classes("text-positive")
                ui.button(_("Close"), on_click=close).mark("proposals-done")
                return
            state["i"] %= len(proposals)
            pair = list(proposals[state["i"]][:2])  # the primary venue first
            # The guessed relation, from the primary venue (the main one for a track…).
            guess = venues.guess_relation(*pair)
            if guess.startswith("~"):
                pair.reverse()
                guess = guess[1:]
            ui.label(_("Proposal {i} of {n}").format(i=state["i"] + 1, n=len(proposals))).classes(
                "text-xs text-grey"
            )
            ui.label(_("1. Which venue is the primary one?")).classes("font-medium")
            cards = ui.row().classes("w-full no-wrap items-stretch")
            secondary = ui.label().classes("font-medium")
            rel = (
                ui.select(
                    {
                        k: _secondary_label(k)
                        for k in venues.relation_choices(guess)
                        if not k.startswith("~")
                    },
                    value=guess,
                )
                .props("dense outlined options-dense")
                .classes("w-full")
                .mark("proposal-relation")
            )
            with ui.column().classes("w-full gap-2") as merging:
                ui.label(
                    _(
                        "Merging moves the texts, rules, ISSNs and papers of the secondary "
                        "venue into the primary one; the primary's decisions win, the "
                        "secondary's fill what it lacks."
                    )
                ).classes("text-xs text-grey")
                names = _MergeNames(pair, "proposal")
            relating = ui.label().classes("text-sm").mark("proposal-relation-effect")

            def swap() -> None:
                pair.reverse()
                names.set_target(pair[0])
                show_cards()

            def show_cards() -> None:
                same = rel.value == "same"
                merging.set_visibility(same)
                relating.set_visibility(not same)
                if not same:
                    relating.set_text(_relation_sentence(pair[0], pair[1:], rel.value))
                secondary.set_text(
                    _("2. What is “{name}” for the primary venue?").format(
                        name=pair[1].short_name or pair[1].name
                    )
                )
                cards.clear()
                with cards:
                    for n, r in enumerate(pair):
                        card(r, n == 0, swap)

            def apply() -> None:
                if rel.value == "same":
                    merge(*pair, names)
                else:
                    relate(*pair, rel.value)

            rel.on_value_change(lambda: show_cards())
            show_cards()
            with ui.row().classes("w-full items-center gap-2"):
                ui.button(_("Apply"), icon="check", on_click=apply).props("dense").mark(
                    "proposal-merge"
                )
                ui.button(
                    _("Not the same"), icon="call_split", on_click=lambda: not_same(*pair)
                ).props("dense flat no-caps color=negative").tooltip(
                    _("Never propose this pair again")
                ).mark("proposal-not-same")
            with ui.row().classes("w-full justify-between mt-2"):
                ui.button(
                    _("Previous"),
                    icon="chevron_left",
                    on_click=lambda: (state.update(i=state["i"] - 1), show()),
                ).props("flat dense")
                ui.button(
                    _("Skip"),
                    icon="chevron_right",
                    on_click=lambda: (state.update(i=state["i"] + 1), show()),
                ).props("flat dense").mark("proposal-skip")

    show()
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


@dataclass
class _View:
    """What the venue list shows, kept across rebuilds."""

    tab: str
    dialogs: ui.element
    filter: str = ""
    reload: Callable[[], object] = lambda: None
    rows: list[venues.VenueRow] = field(default_factory=list)


def _tabs(rows: list[venues.VenueRow], view: _View, focus: int | None = None) -> None:
    tab = view.tab
    if focus is not None:
        focused = next((r for r in rows if r.id == focus), None)
        if focused:
            tab = next((t for t, (_label, kinds) in TABS.items() if focused.kind in kinds), tab)
    view.tab = tab
    undecided = [r for r in rows if _undecided_joint(r)]
    if tab == "joint" and not undecided:
        view.tab = tab = "conferences"
    with ui.tabs(value=tab, on_change=lambda e: setattr(view, "tab", e.value)).classes(
        "w-full"
    ) as tabs:
        for name, (label, kinds) in TABS.items():
            n = sum(r.kind in kinds for r in rows)
            ui.tab(name, f"{_(label)} ({n})")
        if undecided:
            ui.tab("joint", _("Multiple conferences ({n})").format(n=len(undecided))).classes(
                "text-orange-9"
            ).mark("venue-tab-joint")
    with ui.tab_panels(tabs, value=tab).classes("w-full"):
        for name, (_label, kinds) in TABS.items():
            with ui.tab_panel(name):
                _table([r for r in rows if r.kind in kinds], rows, view)
        if undecided:
            with ui.tab_panel("joint"):
                _joint_list(undecided, rows, view)
    if focus is not None and (r := next((r for r in rows if r.id == focus), None)):
        with view.dialogs:
            venue_dialog(r, rows, view.reload)


def _undecided_joint(r: venues.VenueRow) -> bool:
    """A joint venue whose parts have different levels, without a decision."""
    # (A level set by hand on the venue is not that of its parts.)
    from_parts = r.badge is None or r.badge.extra.get("joint_differ")
    return r.joint_differ and r.joint_use is None and bool(from_parts)


def _joint_list(rows: list[venues.VenueRow], all_rows: list[venues.VenueRow], view: _View) -> None:
    """Joint venues whose parts have different levels: which level to use."""
    ui.label(
        _(
            "These venues are joint conferences (their texts name several conferences) whose "
            "conferences have different levels. Until you choose, their papers take the lowest "
            "one. You can also set a level by hand in the venue."
        )
    ).classes("text-sm text-grey")

    def use(row: venues.VenueRow, part_id: int, name: str) -> None:
        venues.set_joint_use(row.id, part_id)
        ui.notify(
            _("“{venue}” takes the level of “{name}”").format(venue=row.name, name=name),
            type="positive",
        )
        view.reload()

    def edit(row: venues.VenueRow) -> None:
        with view.dialogs:
            venue_dialog(row, all_rows, view.reload)

    for row in rows:
        with ui.card().classes("w-full q-pa-sm").mark(f"joint-{row.id}"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(row.name).classes("font-medium grow")
                ui.label(_papers(row.publications)).classes("text-xs text-grey")
                ui.button(_("Open"), icon="edit", on_click=lambda r=row: edit(r)).props(
                    "dense flat size=sm"
                ).mark(f"joint-open-{row.id}")
            for pid, name, badge in row.parts:
                with ui.row().classes("items-center gap-2 no-wrap pl-2"):
                    rank_chip(badge)
                    ui.label(name).classes("text-sm")
                    ui.button(
                        _("Use this level"),
                        icon="check",
                        on_click=lambda r=row, p=pid, n=name: use(r, p, n),
                    ).props("dense flat size=sm no-caps color=primary").mark(
                        f"joint-use-{row.id}-{pid}"
                    )


# A venue picker's option: its acronym ("[EMNLP] …", see venues.venue_choices) in bold.
_ACRONYM_OPTION = """
<q-item v-bind="props.itemProps">
  <q-item-section><q-item-label>
    <b v-if="props.opt.label.startsWith('[')">{{
      props.opt.label.slice(0, props.opt.label.indexOf(']') + 1) }}</b>{{
      props.opt.label.startsWith('[')
        ? props.opt.label.slice(props.opt.label.indexOf(']') + 1) : props.opt.label }}
  </q-item-label></q-item-section>
</q-item>
"""


def _new_choice(vid: int) -> dict[int, str]:
    """The picker label of a venue just added."""
    return {vid: venues.venue_choices().get(vid, "?")}


def _venue_select(options: dict[int, str], value: int | None, on_change) -> ui.select:
    """A venue picker searching names and acronyms (shown in bold)."""
    sel = ui.select(options, value=value, with_input=True, on_change=on_change)
    sel.add_slot("option", _ACRONYM_OPTION)
    return sel.props("dense outlined")


def _joint_editor(row: venues.VenueRow, finish, go) -> None:
    """A joint venue's parts (found automatically or set by hand), and the level it takes."""
    options = {vid: name for vid, name in venues.venue_choices().items() if vid != row.id}
    parts = [pid for pid, *_rest in row.parts]
    ui.label(_("Joint venue")).classes("font-medium mt-2")
    ui.label(
        _(
            "A venue made of several conferences (e.g. CORIA-TALN), found from the acronyms "
            "of its texts. Its papers take the level its conferences share; when they "
            "differ, the one you choose (else the lowest). A level set by hand below wins."
        )
    ).classes("text-xs text-grey")

    def use(part_id: int | None) -> None:
        venues.set_joint_use(row.id, part_id)
        ui.notify(_("Level saved"), type="positive")
        finish()

    for pid, name, badge in row.parts:
        with ui.row().classes("items-center gap-2 no-wrap pl-2").mark(f"venue-part-{pid}"):
            rank_chip(badge)
            ui.label(name).classes("text-sm")
            _open_button(go, pid, f"venue-part-open-{pid}")
            if row.joint_use == pid:
                ui.badge(_("its level is used"), color="positive")
                ui.button(_("Undo"), on_click=lambda: use(None)).props("dense flat size=sm")
            elif row.joint_differ or row.joint_use is not None:
                ui.button(_("Use this level"), icon="check", on_click=lambda p=pid: use(p)).props(
                    "dense flat size=sm no-caps color=primary"
                ).mark(f"venue-part-use-{pid}")

    with (
        ui.expansion(
            _("Change the conferences") if parts else _("Make it a joint venue"), icon="edit"
        )
        .classes("w-full")
        .mark("venue-parts-edit")
    ):

        @ui.refreshable
        def lines() -> None:
            for i, pid in enumerate(parts):
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    _venue_select(
                        options, pid, lambda e, i=i: parts.__setitem__(i, e.value)
                    ).classes("grow").mark(f"venue-part-select-{i}")
                    ui.button(
                        icon="delete", on_click=lambda i=i: (parts.pop(i), lines.refresh())
                    ).props("flat round dense size=sm")

        lines()

        def save(value: list[int] | None) -> None:
            venues.set_joint_parts(row.id, value)
            ui.notify(_("Conferences saved"), type="positive")
            finish()

        with ui.row().classes("items-center gap-2"):
            ui.button(
                _("Add a conference"),
                icon="add",
                on_click=lambda: (parts.append(None), lines.refresh()),
            ).props("dense flat size=sm").mark("venue-part-add")

            def new_part(vid: int, _created: bool) -> None:
                options.update(_new_choice(vid))
                parts.append(vid)
                lines.refresh()

            ui.button(
                _("New venue"), icon="add_circle", on_click=lambda: _new_venue_dialog(new_part)
            ).props("dense flat size=sm").tooltip(
                _("Add a venue not in the list, as a conference of this one")
            ).mark("venue-part-new")
            ui.button(_("Save"), icon="save", on_click=lambda: save([p for p in parts if p])).props(
                "dense unelevated size=sm color=primary"
            ).mark("venue-parts-save")
            if row.parts_manual:
                ui.button(_("Automatic"), on_click=lambda: save(None)).props(
                    "dense flat size=sm"
                ).tooltip(_("Use the conferences found in its texts")).mark("venue-parts-auto")


def _table(rows: list[venues.VenueRow], all_rows: list[venues.VenueRow], view: _View) -> None:
    columns = [
        {"name": "short", "label": _("Short"), "field": "short", "align": "left", "sortable": True},
        {"name": "name", "label": _("Venue"), "field": "name", "align": "left", "sortable": True},
        {"name": "kind", "label": _("Kind"), "field": "kind", "sortable": True},
        {"name": "rank", "label": _("Rank"), "field": "rank"},
        {"name": "pubs", "label": _("Papers"), "field": "pubs", "sortable": True},
        {"name": "people", "label": _("People"), "field": "people", "sortable": True},
        {"name": "variants", "label": _("Variants"), "field": "variants", "sortable": True},
    ]
    data = [
        {
            "id": r.id,
            "short": (r.short_name or "") + (" ✎" if r.short_manual else ""),
            "name": r.name,
            "url": r.url,
            "kind": KIND_SHORT[r.kind] + ("" if not r.kind_manual else " ✎"),
            "rank": _chip_html(r),
            "pubs": r.publications,
            "people": len(r.people),
            "variants": len(r.variants),
        }
        for r in sorted(rows, key=lambda r: (-r.publications, r.name.lower()))
    ]
    by_id = {r.id: r for r in rows}
    table = (
        ui.table(columns=columns, rows=data, row_key="id", pagination=50)
        .classes("w-full")
        .props("dense flat")
    )
    # A row opens the venue; dropped onto another row, it is merged into it. The paper /
    # people counts open the list of papers.
    table.add_slot("body", _ROW_SLOT)
    filt = (
        ui.input(placeholder=_("filter venues"), value=view.filter)
        .props("dense outlined clearable")
        .classes("w-64")
    )
    filt.bind_value(table, "filter")
    filt.on_value_change(lambda e: setattr(view, "filter", e.value or ""))
    filt.move(target_index=0)
    ui.label(_("Drag a venue onto another one to merge it into that one.")).classes(
        "text-xs text-grey"
    ).move(target_index=1)

    def open_row(e) -> None:
        with view.dialogs:
            venue_dialog(by_id[e.args["id"]], all_rows, view.reload)

    table.on("open_venue", open_row)
    table.on("show_pubs", lambda e: papers_dialog(by_id[e.args["id"]]))
    table.on("show_people", lambda e: papers_dialog(by_id[e.args["id"]], people=True))

    def dropped(e) -> None:
        src, dst = by_id.get(e.args.get("src")), by_id.get(e.args.get("dst"))
        if src is not None and dst is not None and src.id != dst.id:
            with view.dialogs:
                _confirm_merge(dst, [src], lambda _vid: view.reload(), swappable=True)

    table.on("venue_drop", dropped)


_ROW_SLOT = """
<q-tr :props="props" draggable="true" class="cursor-pointer"
  @dragstart="e => { e.dataTransfer.setData('text/plain', String(props.row.id));
                     e.dataTransfer.effectAllowed = 'move' }"
  @dragover.prevent="e => e.currentTarget.classList.add('vr-drop')"
  @dragleave="e => e.currentTarget.classList.remove('vr-drop')"
  @drop.prevent="e => { e.currentTarget.classList.remove('vr-drop');
    $parent.$emit('venue_drop',
                  {src: Number(e.dataTransfer.getData('text/plain')), dst: props.row.id}) }"
  @click="$parent.$emit('open_venue', props.row)">
  <q-td v-for="col in props.cols" :key="col.name" :props="props">
    <span v-if="col.name === 'rank'" v-html="col.value"></span>
    <a v-else-if="col.name === 'pubs' || col.name === 'people'" class="vr-count-link"
       @click.stop="$parent.$emit(col.name === 'pubs' ? 'show_pubs' : 'show_people',
                                  props.row)">{{ col.value }}</a>
    <span v-else-if="col.name === 'name'">{{ col.value }}
      <a v-if="props.row.url" :href="props.row.url" target="_blank" rel="noopener"
         @click.stop :title="props.row.url"><q-icon name="open_in_new" size="xs"
         color="primary" /></a></span>
    <span v-else>{{ col.value }}</span>
  </q-td>
</q-tr>
"""


def _hosts_editor(row: venues.VenueRow, finish, go) -> None:
    """A workshop's main conferences (with years), which give its papers their rank."""
    options = {vid: name for vid, name in venues.venue_choices().items() if vid != row.id}
    hosts = [{"venue_id": h[0], "from": h[2], "to": h[3]} for h in row.hosts]
    ui.label(_("Main conference")).classes("font-medium mt-2")
    ui.label(
        _(
            "Papers of this workshop take the rank of its main conference in their year "
            "(a workshop can move: give the years of each one)."
        )
    ).classes("text-xs text-grey")

    @ui.refreshable
    def lines() -> None:
        if not hosts:
            ui.label(_("No main conference: its papers are not ranked.")).classes(
                "text-sm text-grey"
            )
        for i, h in enumerate(hosts):
            with ui.row().classes("items-center gap-2 no-wrap w-full").mark(f"venue-host-{i}"):
                _venue_select(
                    options, h["venue_id"], lambda e, h=h: h.update(venue_id=e.value)
                ).classes("grow").mark(f"venue-host-select-{i}")
                for end, label in (("from", _("from")), ("to", _("to"))):
                    ui.number(
                        label,
                        value=h[end],
                        format="%d",
                        on_change=lambda e, h=h, end=end: h.update(
                            {end: int(e.value) if e.value else None}
                        ),
                    ).props("dense outlined").classes("w-24")
                if h["venue_id"]:
                    _open_button(go, h["venue_id"], f"venue-host-open-{i}")
                ui.button(
                    icon="delete", on_click=lambda i=i: (hosts.pop(i), lines.refresh())
                ).props("flat round dense size=sm")

    lines()
    with ui.row().classes("items-center gap-2"):

        def add(vid: int | None = None) -> None:
            hosts.append({"venue_id": vid, "from": None, "to": None})
            lines.refresh()

        ui.button(_("Add a main conference"), icon="add", on_click=lambda: add()).props(
            "dense flat size=sm"
        ).mark("venue-host-add")
        ui.button(
            _("New venue"),
            icon="add_circle",
            on_click=lambda: _new_venue_dialog(
                lambda vid, _created: (options.update(_new_choice(vid)), add(vid))
            ),
        ).props("dense flat size=sm").tooltip(
            _("Add a venue not in the list, as its main conference")
        ).mark("venue-host-new")
        if not hosts and (sugg := venues.host_suggestion(row.id)):
            ui.button(
                _("Use {venue}").format(venue=sugg[1]),
                icon="auto_fix_high",
                on_click=lambda: add(sugg[0]),
            ).props("dense flat size=sm color=primary").tooltip(
                _("Found in the workshop's venue texts")
            ).mark("venue-host-suggest")

        def save() -> None:
            venues.save_hosts(row.id, [h for h in hosts if h["venue_id"]])
            ui.notify(_("Main conferences saved"), type="positive")
            finish()

        ui.button(_("Save main conferences"), icon="save", on_click=save).props(
            "dense unelevated size=sm color=primary"
        ).mark("venue-hosts-save")


def _open_button(go, venue_id: int, mark: str) -> None:
    """Opens a related venue (main conference, conference of a joint venue) in place of the
    current one, which its Back button reopens."""
    ui.button(icon="visibility", on_click=lambda: go(venue_id)).props(
        "flat round dense size=sm"
    ).tooltip(_("Open this venue (changes not saved here are lost)")).mark(mark)


def papers_dialog(row: venues.VenueRow, *, people: bool = False) -> None:
    """The papers of a venue, by person (``people``: only the people, with their count)."""
    papers = venues.venue_papers(row.pub_ids)
    by_person: dict[int, list[venues.VenuePaper]] = {}
    for p in papers:
        by_person.setdefault(p.person_id, []).append(p)
    with (
        ui.dialog() as dlg,
        ui.card().classes("w-full max-w-3xl").style("max-height: 90vh; overflow-y: auto"),
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            what = (
                ngettext("{n} person", "{n} people", len(by_person)).format(n=len(by_person))
                if people
                else _papers(len(papers))
            )
            ui.label(f"{row.name}: {what}").classes("text-lg")
            ui.button(icon="close", on_click=dlg.close).props("flat round")
        if not papers:
            ui.label(_("No paper.")).classes("text-grey")
        for pid, items in by_person.items():
            with ui.column().classes("w-full gap-0").mark(f"venue-person-{pid}"):
                ui.link(
                    f"{items[0].person} ({len(items)})", f"/person/{pid}?venue={row.id}"
                ).classes("font-medium").tooltip(_("Their papers at this venue"))
                if people:
                    continue
                for p in items:
                    with ui.row().classes("gap-2 no-wrap items-baseline pl-4 text-sm"):
                        ui.label(str(p.year or "—")).classes("w-10 shrink-0 text-grey")
                        ui.link(p.title or _("(untitled)"), f"/person/{pid}?pub={p.id}").classes(
                            "text-grey-8" if p.hidden else ""
                        ).mark(f"venue-paper-{p.id}")
                        if p.hidden:
                            ui.label(_("hidden")).classes("text-xs text-grey")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


# Reopens the venue a dialog was opened from.
Back = Callable[[], Awaitable[None]]


async def open_venue(
    venue_id: int, done=None, back: Back | None = None, tab: str | None = None
) -> None:
    """Open a venue's dialog over the current page (``done``: called after a change;
    ``back``: reopens the venue it was opened from; ``tab``: the one shown)."""
    rows = await venues.venue_rows(only={venue_id})
    row = next((r for r in rows if r.id == venue_id), None)
    if row is None:
        ui.notify(_("This venue has no paper any more"), type="warning")
        return
    venue_dialog(row, rows, done, back, tab)


def venue_dialog(
    row: venues.VenueRow,
    all_rows: list[venues.VenueRow],
    done=None,
    back: Back | None = None,
    tab: str | None = None,
) -> None:
    """``done``: called after a change (default: reload the page); ``back``: reopens the
    venue this one was opened from (a Back button)."""
    with session_scope() as s:
        v = s.get(Venue, row.id, options=[selectinload(Venue.keys)])
        if v is None:
            return
        state = {
            "name": v.name,
            "short_name": v.short_name or "",
            "url": v.url or "",
            "kind": v.kind if v.kind_manual else "",
            "level_type": v.level_type
            or ("journal" if row.kind.endswith("journal") else "conference"),
            "level_rank": v.level_rank or "",
            "record_key": v.record_key,
            "match_text": v.match_text or "",
            "issns": ", ".join((v.identifiers or {}).get("issn") or []),
        }
    with (
        ui.dialog() as dlg,
        ui.card()
        .classes("w-full max-w-4xl vr-details")
        .style("max-height: 90vh; overflow-y: auto"),
    ):
        # Related venues open next to this dialog (deleted once closed).
        parent = dlg.parent_slot.parent

        async def reopen(open_it: Back) -> None:
            with parent:
                await open_it()

        def go(venue_id: int) -> None:
            """Open a related venue instead, with a way back here."""
            dlg.close()
            background_tasks.create(
                reopen(lambda: open_venue(venue_id, done, lambda: open_venue(row.id, done, back)))
            )

        def go_back() -> None:
            dlg.close()
            background_tasks.create(reopen(back))

        def finish() -> None:
            # Refresh before closing: a closed dialog is deleted, along with its client context.
            if done is None:
                ui.navigate.reload()
            else:
                done()
            dlg.close()
            if back is not None and done is not None:
                background_tasks.create(reopen(back))

        def merged(target_id: int) -> None:
            """Merged: the page is refreshed and this dialog shows the venue kept."""
            if done is not None:
                done()

            async def update() -> None:
                with parent:
                    await open_venue(target_id, done, back, tabs.value)
                if not dlg.is_deleted:
                    dlg.close()

            background_tasks.create(update())

        if back is not None:
            ui.button(_("Back"), icon="arrow_back", on_click=go_back).props(
                "flat dense no-caps"
            ).mark("venue-back")
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            name = ui.input(_("Name"), value=state["name"]).classes("grow").mark("venue-name")
            short = (
                ui.input(
                    _("Short name"),
                    value=state["short_name"],
                    placeholder=(row.short_name or _("e.g. ICLR")) if not row.short_manual else "",
                )
                .classes("w-40")
                .tooltip(_("Empty: found automatically (ranking record acronym, venue texts)"))
                .mark("venue-short")
            )
            ui.button(icon="close", on_click=dlg.close).props("flat round")
        ui.label(
            _("Renamed: “{name}” stays a variant, so its texts keep matching.").format(
                name=row.name
            )
        ).classes("text-xs text-grey").bind_visibility_from(
            name, "value", lambda v: (v or "").strip() not in ("", row.name)
        )
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            url = (
                ui.input(_("Website"), value=state["url"], placeholder="https://…")
                .props("dense")
                .classes("grow")
                .mark("venue-url")
            )
            ui.button(
                icon="open_in_new",
                on_click=lambda: ui.navigate.to(venues.normalize_url(url.value), new_tab=True),
            ).props("flat round dense").tooltip(_("Open the website")).bind_visibility_from(
                url, "value", lambda v: bool((v or "").strip())
            )
        with ui.row().classes("items-center gap-2"):
            span(_chip_html(row))
            ui.label(
                _papers(row.publications)
                + " · "
                + ngettext("{n} person", "{n} people", len(row.people)).format(n=len(row.people))
            ).classes("text-sm text-grey")
        _core_history(row.badge)
        _sjr_history(row.badge)
        for vid, host, *_rest in row.hosts:  # (a workshop: its main conferences' ranks)
            r = next((r for r in all_rows if r.id == vid), None)
            _core_history(r.badge if r else None, host)
        if suggestions := venues.suggest_related(row, all_rows):
            _related_strip(row, suggestions, merged)

        with ui.tabs().classes("w-full").props("align=left dense") as tabs:
            t_rank = ui.tab("ranking", _("Name and ranking")).mark("venue-tab-ranking")
            ui.tab("matching", _("Matching (rules and variants)")).mark("venue-tab-matching")
            ui.tab("merge", _("Merge with other venues")).mark("venue-tab-merge")
        with ui.tab_panels(tabs, value=tab or t_rank).classes("w-full"):
            with ui.tab_panel("ranking").classes("q-px-none"):
                ui.label(_("Kind")).classes("font-medium")
                kind = (
                    ui.select(
                        {"": _("Automatic ({kind})").format(kind=KINDS[row.kind]), **VENUE_KINDS},
                        value=state["kind"] if state["kind"] in VENUE_KINDS else "",
                    )
                    .props("dense outlined")
                    .classes("w-80")
                )
                hosts_box = ui.column().classes("w-full gap-1")
                with hosts_box:
                    _hosts_editor(row, finish, go)
                hosts_box.bind_visibility_from(
                    kind,
                    "value",
                    lambda k: k in WORKSHOP_KINDS or (not k and row.kind in WORKSHOP_KINDS),
                )
                joint_box = ui.column().classes("w-full gap-1")
                with joint_box:
                    _joint_editor(row, finish, go)
                joint_box.bind_visibility_from(
                    kind,
                    "value",
                    lambda k: k not in WORKSHOP_KINDS and (k or row.kind not in WORKSHOP_KINDS),
                )

                ui.label(_("Level (by hand)")).classes("font-medium mt-2")
                with ui.row().classes("items-center gap-2"):
                    ltype = ui.select(
                        {"conference": _("Conference (CORE)"), "journal": _("Journal (quartile)")},
                        value=state["level_type"],
                    ).props("dense outlined")
                    lrank = (
                        ui.select(
                            {"": _("— automatic"), **level_options(LEVELS)},
                            value=state["level_rank"],
                            new_value_mode="add-unique",
                        )
                        .props("dense outlined")
                        .classes("w-72")
                    )
                level_hint(lrank).bind_visibility_from(lrank, "value")

                ui.label(_("Ranking record (by hand)")).classes("font-medium mt-2")
                chosen = {"key": state["record_key"]}

                @ui.refreshable
                def record_view() -> None:
                    b = service.badge_for_record(chosen["key"]) if chosen["key"] else None
                    with ui.row().classes("items-center gap-2 w-full no-wrap").mark("venue-record"):
                        if b:
                            rank_chip(b)
                            ui.label(b.name).classes("font-medium")
                            ui.label(f"{b.source} · {b.type}").classes("text-xs text-grey")
                        else:
                            if row.badge:
                                rank_chip(row.badge)
                            if not row.badge:
                                text = _("Automatic matching: no ranking record found")
                            elif row.badge.exact or row.badge.manual:
                                text = _("Automatic matching (currently: {name})").format(
                                    name=row.badge.name
                                )
                            else:
                                text = _(
                                    "Automatic matching (currently: {name}, fuzzy match {score}%)"
                                ).format(name=row.badge.name, score=round(row.badge.score * 100))
                            ui.label(text).classes("text-grey")
                        ui.space()
                        ui.button(
                            _("Search for a ranking"),
                            icon="search",
                            on_click=lambda: open_search(True),
                        ).props("dense flat").mark("venue-search-open")
                        if chosen["key"]:
                            ui.button(_("Automatic"), on_click=lambda: pick(None)).props(
                                "dense flat"
                            ).tooltip(_("Forget the chosen record: match automatically"))

                def pick(key: str | None) -> None:
                    chosen["key"] = key
                    open_search(False)
                    record_view.refresh()

                record_view()
                search_box = ui.card().classes("w-full q-pa-sm").props("flat bordered")
                search_box.visible = False
                with search_box:
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        query = (
                            ui.input(
                                _("Ranking record name"),
                                value=row.name,
                                on_change=lambda: search(),
                            )
                            .props("dense outlined debounce=300 autofocus")
                            .classes("grow")
                            .mark("venue-search")
                        )
                        ui.button(icon="close", on_click=lambda: open_search(False)).props(
                            "flat round dense"
                        )
                    results = ui.column().classes("w-full gap-1")

                def search() -> None:
                    results.clear()
                    text = (query.value or "").strip()
                    with results:
                        if len(text) < 3:
                            ui.label(_("Type at least 3 characters.")).classes("text-xs text-grey")
                            return
                        found = service.search(text, 10)
                        if not found:
                            ui.label(_("No ranking record found.")).classes("text-xs text-grey")
                        for i, b in enumerate(found):
                            with ui.row().classes("items-center gap-2 no-wrap w-full"):
                                rank_chip(b)
                                ui.label(b.name).classes("grow")
                                ui.label(f"{b.source} · {round(b.score * 100)}%").classes(
                                    "text-xs text-grey"
                                )
                                ui.button(_("Use"), on_click=lambda k=b.recordKey: pick(k)).props(
                                    "dense unelevated color=primary"
                                ).mark(f"venue-use-{i}")

                def open_search(on: bool) -> None:
                    search_box.visible = on
                    if on:
                        search()

            with ui.tab_panel("matching").classes("q-px-none"):
                match = (
                    ui.input(
                        _("Search rankings as (empty: the venue's texts)"),
                        value=state["match_text"],
                    )
                    .classes("w-full")
                    .tooltip(
                        _(
                            "Text the automatic ranking lookup searches for, instead of the "
                            "venue's texts (it does not change which texts belong to the venue)"
                        )
                    )
                )
                issns = (
                    ui.input(_("ISSNs (comma-separated)"), value=state["issns"])
                    .classes("w-full")
                    .tooltip(
                        _("Records with one of these ISSNs belong to this venue, whatever text")
                    )
                    .mark("venue-issns")
                )
                ui.label(_("Variants (raw texts, matched by their cleaned text)")).classes(
                    "font-medium mt-2"
                )
                tracks = {"": _("no track"), **TRACK_LABEL}
                workshop_keys = dict(venues.workshop_variants(row.id))
                if workshop_keys:
                    with (
                        ui.row()
                        .classes("items-center gap-2 w-full bg-orange-1 rounded p-1 no-wrap")
                        .mark("venue-workshop-variants")
                    ):
                        ui.icon("group_work", color="orange-9")
                        ui.label(
                            ngettext(
                                "{n} variant looks like a workshop of this venue. "
                                "A workshop is its own venue, ranked as its main conference.",
                                "{n} variants look like workshops of this venue. "
                                "A workshop is its own venue, ranked as its main conference.",
                                len(workshop_keys),
                            ).format(n=len(workshop_keys))
                        ).classes("text-sm grow")

                        def split_all() -> None:
                            for k in workshop_keys:
                                venues.split_as_workshop(k)
                            ui.notify(
                                ngettext(
                                    "{n} workshop venue created",
                                    "{n} workshop venues created",
                                    len(workshop_keys),
                                ).format(n=len(workshop_keys))
                            )
                            finish()

                        ui.button(_("Split each into a workshop"), on_click=split_all).props(
                            "dense flat color=primary"
                        ).mark("venue-split-workshops")
                for key, example, count, manual, track in row.variants:
                    with ui.row().classes("items-center gap-2 no-wrap w-full"):
                        ui.label(example or key).classes("text-sm")
                        ui.label(
                            f"“{key}” · "
                            + ngettext("{n} record", "{n} records", count).format(n=count)
                            + (" · " + _("set by hand") if manual else "")
                        ).classes("text-xs text-grey grow")
                        ui.select(
                            tracks,
                            value=track or "",
                            on_change=lambda e, k=key: (
                                venues.set_variant_track(k, e.value or None),
                                ui.notify(_("Track saved")),
                            ),
                        ).props("dense borderless").classes("w-28").tooltip(
                            _("Track of the papers with this variant")
                        )
                        if len(row.variants) > 1:
                            ui.button(
                                icon="call_split",
                                on_click=lambda k=key: (venues.split_key(k), finish()),
                            ).props("flat round dense size=sm").tooltip(
                                _("Split into its own venue")
                            )
                        if row.kind not in WORKSHOP_KINDS:
                            ui.button(
                                icon="group_work",
                                on_click=lambda k=key: (venues.split_as_workshop(k), finish()),
                            ).props(
                                "flat round dense size=sm color="
                                + ("orange-9" if key in workshop_keys else "grey")
                            ).tooltip(
                                _("Split into a workshop venue whose main conference is this one")
                            ).mark(f"venue-split-workshop-{key}")
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    new_raw = (
                        ui.input(_("Add a variant (a raw venue text)"))
                        .props("dense outlined")
                        .classes("grow")
                        .mark("venue-variant-new")
                    )
                    new_src = (
                        ui.select(
                            {"": _("any source"), **{k: a.label for k, a in ADAPTERS.items()}},
                            value="",
                        )
                        .props("dense outlined")
                        .classes("w-40")
                        .tooltip(_("The source's normalization rules apply to the text"))
                    )

                    def add_variant() -> None:
                        if (new_raw.value or "").strip():
                            venues.add_variant(row.id, new_raw.value.strip(), new_src.value or None)
                            finish()

                    ui.button(icon="add", on_click=add_variant).props("flat round dense").mark(
                        "venue-variant-add"
                    )
                rule_host = ui.element("div")  # rule dialogs live outside the refreshed list

                def edit_rule(index: int | None = None) -> None:
                    with rule_host:
                        venue_rule_dialog(rule_saved, venue_id=row.id, index=index)

                def rule_saved(message: str) -> None:
                    ui.notify(
                        _("{message} (applied everywhere)").format(message=message), type="positive"
                    )
                    rules_view.refresh()

                @ui.refreshable
                def rules_view() -> None:
                    uses = venues.pattern_uses(row.id)
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.label(_("Venue rules (regex)")).classes("font-medium")
                        ui.button(_("Add a rule"), icon="add", on_click=lambda: edit_rule()).props(
                            "dense flat size=sm"
                        ).mark("venue-rule-add")
                    ui.label(
                        _(
                            "Source venue texts matching a rule belong to this venue "
                            "(saved at once)."
                        )
                    ).classes("text-xs text-grey")
                    for use in uses:
                        r = use.rule
                        with ui.column().classes("gap-0 w-full").mark(f"venue-rule-{use.index}"):
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                ui.label(r.pattern).classes("font-mono text-sm")
                                ui.label(
                                    _("only {sources}").format(sources=", ".join(r.sources))
                                    if r.sources
                                    else _("all sources")
                                ).classes("text-xs text-grey")
                                if r.track:
                                    ui.label(f"→ {TRACK_LABEL.get(r.track, r.track)}").classes(
                                        "text-xs text-primary"
                                    )
                                if r.note:
                                    ui.label(r.note).classes("text-xs text-grey italic")
                                ui.button(
                                    icon="edit", on_click=lambda i=use.index: edit_rule(i)
                                ).props("flat round dense size=sm").mark(
                                    f"venue-rule-edit-{use.index}"
                                )
                            if not use.examples:
                                ui.label(_("captures no venue text")).classes(
                                    "text-xs text-orange-9 pl-4"
                                )
                            for source, raw, n in use.examples[:5]:
                                with ui.row().classes("items-center gap-2 no-wrap pl-4"):
                                    source_tag(source)
                                    ui.label(f"{raw} ({n})").classes("text-xs text-grey")
                            if len(use.examples) > 5:
                                ui.label(
                                    _("… and {n} more").format(n=len(use.examples) - 5)
                                ).classes("text-xs text-grey pl-4")
                            for source, raw, other in use.lost[:5]:
                                with ui.row().classes("items-center gap-2 no-wrap pl-4"):
                                    source_tag(source)
                                    ui.label(
                                        _("{text}: matched, but in “{venue}”").format(
                                            text=raw, venue=other
                                        )
                                    ).classes("text-xs text-orange-9").tooltip(
                                        _("A manual variant or a more specific rule wins")
                                    )

                rules_view()
                texts = venue_match.texts_of([row.id]).get(row.id, [])
                if texts:
                    counts = row.source_texts
                    with (
                        ui.expansion(_("Source texts ({n})").format(n=len(texts)))
                        .classes("w-full")
                        .mark("venue-texts")
                    ):
                        for t in sorted(texts, key=lambda t: -counts.get((t.source, t.raw), 0)):
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                source_tag(t.source)
                                ui.label(f"{t.raw} ({counts.get((t.source, t.raw), 0)})").classes(
                                    "text-xs"
                                )
                                ui.label(VIA_LABEL.get(t.via, t.via)).classes("text-xs text-grey")
                                if t.conflicts:
                                    ui.icon("warning", size="xs", color="orange-8").tooltip(
                                        _("Other venues' rules match this text too")
                                    )

            with ui.tab_panel("merge").classes("q-px-none"):
                _merge_tab(row, all_rows, merged)

        def save() -> None:
            venues.update_venue(
                row.id,
                name=name.value.strip() or row.name,
                short_name=short.value.strip() or None,
                url=venues.normalize_url(url.value),
                kind=kind.value or None,
                level_type=ltype.value if lrank.value else None,
                level_rank=lrank.value or None,
                record_key=chosen["key"],
                match_text=match.value.strip() or None,
            )
            if issns.value != state["issns"]:
                venues.save_issns(row.id, [i for i in (issns.value or "").split(",") if i.strip()])
            finish()

        with ui.row().classes("justify-end w-full"):
            ui.button(
                _("Clear manual decisions"),
                on_click=lambda: (venues.clear_manual(row.id), finish()),
            ).props("flat color=negative")
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Save"), on_click=save).mark("venue-save")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def _relation_label(relation: str) -> str:
    """What a suggested venue is, for the open one."""
    kind, _sep, track = relation.partition(":")
    track = TRACK_LABEL.get(track, track)
    return {
        "same": _("The same venue"),
        "track": _("This venue's {track} track").format(track=track),
        "~track": _("The main venue (this one is its {track} track)").format(track=track),
        "joint": _("A joint conference including this venue"),
        "~joint": _("A part of this joint conference"),
        "workshop": _("A workshop of this venue"),
        "~workshop": _("The main conference of this workshop"),
    }[kind]


def _secondary_label(relation: str) -> str:
    """What the secondary venue of a pair is, for the primary one."""
    kind, _sep, track = relation.partition(":")
    return {
        "same": _("The same venue (merged into the primary one)"),
        "track": _("Its {track} track (merged into it, its texts marked as such)").format(
            track=TRACK_LABEL.get(track, track)
        ),
        "joint": _("A joint conference including it"),
        "workshop": _("One of its workshops"),
    }[kind]


def _relation_sentence(row: venues.VenueRow, group: list[venues.VenueRow], relation: str) -> str:
    """What recording a relation does."""
    reverse = relation.startswith("~")
    kind, _sep, track = relation.lstrip("~").partition(":")
    sat, main = (row, group[0]) if reverse else (group[0], row)
    if kind == "track":
        text = _("“{sat}” is merged into “{main}”, its texts marked as its {track} track.")
    elif kind == "joint":
        text = _(
            "“{sat}” becomes a joint conference including “{main}”: its papers take its level."
        )
    else:
        text = _("“{sat}” becomes a workshop of “{main}”: its papers take its rank.")
    text = text.format(sat=sat.name, main=main.name, track=TRACK_LABEL.get(track, track))
    if len(group) > 1:
        first = _("First, {names} are merged into “{name}”.").format(
            names=", ".join(f"“{r.name}”" for r in group[1:]), name=group[0].name
        )
        text = f"{first} {text}"
    return text


def _related_strip(
    row: venues.VenueRow,
    suggestions: list[tuple[list[venues.VenueRow], str]],
    finish: Callable[[int], object],
) -> None:
    """Venues that look related to ``row``, each with its guessed relation (to change), to
    record it or to say they are not related."""

    def apply(group: list[venues.VenueRow], relation: str) -> None:
        if relation == "same":
            _confirm_merge(row, group, finish)
            return
        with ui.dialog() as dlg, ui.card().classes("min-w-96"):
            ui.label(_relation_label(relation)).classes("text-lg font-medium")
            ui.label(_relation_sentence(row, group, relation)).classes("text-sm")

            def ok() -> None:
                kept = venues.relate_venues(row.id, [r.id for r in group], relation)
                dlg.close()
                finish(kept)

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                ui.button(_("Apply"), icon="check", on_click=ok).mark("venue-relate-confirm")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    def unrelated(group: list[venues.VenueRow], line) -> None:
        for r in group:
            venues.set_not_same(row.id, r.id)
        line.delete()

    with ui.column().classes("w-full gap-1 bg-blue-1 rounded p-2").mark("venue-suggestions"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("merge", color="primary")
            ui.label(_("Related venues?")).classes("text-sm font-medium")
        for i, (group, guess) in enumerate(suggestions):
            with ui.row().classes("w-full items-center gap-2 no-wrap pl-2") as line:
                with ui.column().classes("grow gap-0"):
                    for r in group:
                        ui.label(f"{r.name} ({r.publications})").classes("text-sm")
                rel = (
                    ui.select(
                        {k: _relation_label(k) for k in venues.relation_choices(guess)},
                        value=guess,
                    )
                    .props("dense outlined options-dense")
                    .classes("w-72")
                    .mark(f"venue-suggest-relation-{i}")
                )
                ui.button(
                    _("Apply"),
                    icon="call_merge",
                    on_click=lambda g=group, rel=rel: apply(g, rel.value),
                ).props("dense flat no-caps color=primary").mark(f"venue-suggest-merge-{i}")
                ui.button(
                    icon="close", on_click=lambda g=group, line=line: unrelated(g, line)
                ).props("flat round dense size=sm").tooltip(
                    _("Not related: never propose it again")
                ).mark(f"venue-suggest-unrelated-{i}")


def _confirm_merge(
    row: venues.VenueRow,
    others: list[venues.VenueRow],
    finish: Callable[[int], object],
    *,
    swappable: bool = False,
) -> None:
    """Confirm merging ``others`` into ``row``; ``swappable``: the other way round too;
    ``finish``: called with the venue kept."""
    state = {"target": row, "others": others}

    def label(r: venues.VenueRow) -> str:
        short = f" [{r.short_name}]" if r.short_name else ""
        return f"{r.name}{short} — {KINDS[r.kind]} ({_papers(r.publications)})"

    with ui.dialog() as dlg, ui.card().classes("min-w-96"):
        ui.label(_("Merge venues")).classes("text-lg font-medium")
        view = ui.column().classes("w-full")

        def show() -> None:
            view.clear()
            with view:
                merge_direction(label(state["target"]), [label(r) for r in state["others"]])
                ui.label(
                    _(
                        "Their texts, rules, ISSNs and papers move to “{name}”; "
                        "its own decisions win, the others' fill what it lacks."
                    ).format(name=state["target"].name)
                ).classes("text-xs text-grey")

        names = _MergeNames([row, *others], "venue-merge")

        def swap() -> None:
            (other,) = state["others"]
            state["target"], state["others"] = other, [state["target"]]
            names.set_target(other)
            show()

        def ok() -> None:
            target = state["target"]
            name, short, kind = names.values(target)
            venues.merge_venues(target.id, [r.id for r in state["others"]], name, short, kind)
            dlg.close()
            finish(target.id)

        show()
        with ui.row().classes("w-full justify-end"):
            if swappable and len(others) == 1:
                ui.button(_("The other way round"), icon="swap_vert", on_click=swap).props(
                    "flat no-caps"
                ).mark("venue-merge-swap")
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Merge"), icon="merge", on_click=ok).mark("venue-merge-confirm")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


class _MergeNames:
    """The merged venue's name, short name and kind, those of the kept venue (``rows[0]``)
    to start with; another venue's name can be copied to edit it."""

    def __init__(self, rows: list[venues.VenueRow], mark: str) -> None:
        self.target = rows[0]
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            self.name = (
                ui.input(_("Name of the merged venue"), value=self.target.name)
                .props("dense")
                .classes("grow")
                .tooltip(_("The former names stay variants, so their texts keep matching"))
                .mark(f"{mark}-name")
            )
            self.short = (
                ui.input(_("Short name"), value=self._short(rows), placeholder=_("e.g. ICLR"))
                .props("dense clearable")
                .classes("w-40")
                .tooltip(_("Empty: found automatically (ranking record acronym, venue texts)"))
                .mark(f"{mark}-short")
            )
            self.kind = (
                ui.select(
                    VENUE_KINDS,
                    value=self.target.kind if self.target.kind in VENUE_KINDS else None,
                    label=_("Kind"),
                )
                .props("dense")
                .classes("w-52")
                .mark(f"{mark}-kind")
            )
        if len(kinds := dict.fromkeys(KINDS[r.kind] for r in rows)) > 1:
            with ui.row().classes("w-full items-center gap-2 bg-orange-1 rounded p-1 no-wrap"):
                ui.icon("warning", color="orange-8")
                ui.label(
                    _("Not the same type ({kinds}): check the merged venue's kind.").format(
                        kinds=" / ".join(kinds)
                    )
                ).classes("text-sm").mark(f"{mark}-kinds-differ")
        with ui.row().classes("items-center gap-1"):
            ui.label(_("Copy a name:")).classes("text-xs text-grey")
            for i, r in enumerate(rows):
                ui.chip(r.name, on_click=lambda r=r: self.name.set_value(r.name)).props(
                    "dense clickable outline no-caps"
                ).classes("text-xs").tooltip(_("Use this name (and edit it)")).mark(
                    f"{mark}-use-name-{i}"
                )
        self.rows = rows

    @staticmethod
    def _short(rows: list[venues.VenueRow]) -> str:
        return next((r.short_name for r in rows if r.short_name), "")

    def set_target(self, target: venues.VenueRow) -> None:
        """Another venue is kept: the fields left as they were follow it."""
        old = self.target
        if (self.name.value or "").strip() == old.name:
            self.name.set_value(target.name)
        if (self.short.value or "") == self._short([old, *self.rows]):
            self.short.set_value(self._short([target, *self.rows]))
        if self.kind.value == old.kind:
            self.kind.set_value(target.kind)
        self.target = target

    def values(self, target: venues.VenueRow) -> tuple[str | None, str | None, str | None]:
        """(new name, short name, kind) to set by hand: None when unchanged."""
        name = (self.name.value or "").strip() or None
        short = (self.short.value or "").strip() or None
        return (
            name if name != target.name else None,
            short if short != target.short_name else None,
            self.kind.value if self.kind.value != target.kind else None,
        )


def _merge_tab(row: venues.VenueRow, all_rows: list[venues.VenueRow], merged) -> None:
    """Search other venues (similar ones suggested) and merge them into this one."""
    ui.label(
        _(
            "Merged venues become variants of this one: their texts, rules, ISSNs and papers "
            "move here; this venue's decisions win, the others' fill what it lacks."
        )
    ).classes("text-xs text-grey")
    picked: set[int] = set()
    query = (
        ui.input(_("Search venues (name, acronym, source text)"))
        .props("dense outlined clearable debounce=300")
        .classes("w-full")
        .mark("venue-merge-search")
    )
    results = ui.column().classes("w-full gap-0")
    merge_btn = ui.button(_("Merge the selected venues into this one"), icon="merge")
    merge_btn.mark("venue-merge-selected").props("dense")

    def update_btn() -> None:
        merge_btn.text = ngettext(
            "Merge {n} venue into this one", "Merge {n} venues into this one", len(picked)
        ).format(n=len(picked))
        merge_btn.set_enabled(bool(picked))

    def toggle(vid: int, on: bool) -> None:
        (picked.add if on else picked.discard)(vid)
        update_btn()

    def show() -> None:
        found = venues.similar_venues(row, all_rows, query.value or "")
        results.clear()
        with results:
            if not found:
                ui.label(
                    _("No venue found.") if query.value else _("No similar venue: search one.")
                ).classes("text-xs text-grey")
            elif not query.value:
                ui.label(_("Similar venues")).classes("text-xs text-grey")
            for i, (r, _score) in enumerate(found):
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    ui.checkbox(
                        value=r.id in picked, on_change=lambda e, v=r.id: toggle(v, e.value)
                    ).mark(f"venue-merge-pick-{i}")
                    span(_chip_html(r))
                    ui.label(r.name).classes("text-sm grow")
                    ui.label(
                        f"{r.short_name or ''} · {KIND_SHORT[r.kind]} · {_papers(r.publications)}"
                    ).classes("text-xs text-grey")
                    ui.link(_("open"), f"/venues?focus={r.id}", new_tab=True).classes("text-xs")

    def confirm() -> None:
        by_id = {r.id: r for r in all_rows}
        _confirm_merge(row, [by_id[v] for v in sorted(picked) if v in by_id], merged)

    merge_btn.on_click(confirm)
    query.on_value_change(lambda _e: show())
    show()
    update_btn()
