"""Person page: publications panel, sources (auto-match validation), periods, theses."""

from __future__ import annotations

from fastapi import Request
from nicegui import background_tasks, ui
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import annotations, folders, manual, theses
from ..db.models import Person, SourceLink, Thesis
from ..db.session import session_scope
from ..source_settings import active_links
from ..sources import ADAPTERS
from ..sources.base import SourceError
from ..sources.scholar import parse_profile, profile_id_from_html
from ..sources.thesesfr import ROLE_LABELS
from ..sync import (
    add_link,
    discover,
    finish_link,
    is_syncing,
    normalize_orcid,
    probe_overlap,
    set_link_status,
    set_orcid,
    start_sync,
    suggested_orcid,
    sync_link,
)
from .documents_page import documents_view
from .panel import PublicationsPanel, period_label
from .theme import STATUS_COLOUR, fmt_dt, frame, source_tag


def _refresh(r) -> None:
    """Refresh a module-level refreshable, skipping instances of pages that are gone."""

    def alive(target) -> bool:
        try:
            target.container.client  # noqa: B018 - raises once the client was deleted
        except RuntimeError:
            return False
        return not target.container.is_deleted

    r.targets = [t for t in r.targets if alive(t)]
    r.refresh()


def _load(person_id: int) -> Person | None:
    with session_scope() as s:
        return s.scalar(
            select(Person).where(Person.id == person_id).options(selectinload(Person.links))
        )


def register() -> None:
    @ui.page("/person/{person_id}")
    def person_page(
        request: Request, person_id: int, tab: str = "publications", period: int | None = None
    ) -> None:
        _person_page(request, person_id, tab, period)

    @ui.page("/person/{person_id}/{period_id}")
    def person_period_page(
        request: Request, person_id: int, period_id: int, tab: str = "publications"
    ) -> None:
        """A person's page with a period selected (e.g. opened from a folder)."""
        _person_page(request, person_id, tab, period_id)


def _person_page(request: Request, person_id: int, tab: str, period: int | None) -> None:
    person = _load(person_id)
    if person is None:
        with frame("Not found"):
            ui.label("Unknown person")
        return
    # The folder the person is seen from: that of the period, else the People page's one.
    state_period = period or annotations.panel_state(person_id).get("period_id")
    folder = folders.folder_of_period(state_period)
    if folder is None:
        last = annotations.ui_state("ui.people.folder")
        folder = next(
            ((f.id, f.name) for f in folders.folders() if f.id == last),
            None,
        )
        if folder and not any(
            p.folder_id == folder[0]
            for p in annotations.periods(person_id, include_hidden_folders=True)
        ):
            folder = None
    with frame(person.name):
        tabs = _header(person, tab, folder)
        stale_banner(person_id)

        def tab_changed(e) -> None:
            panel.url_extra = {} if e.value == "publications" else {"tab": e.value}
            panel.push_url()

        tabs.on_value_change(tab_changed)
        with ui.tab_panels(tabs, value=tab).classes("w-full"):
            with ui.tab_panel("publications"):
                panel = PublicationsPanel(person_id, period, dict(request.query_params))
                if tab != "publications":
                    panel.url_extra = {"tab": tab}
                panel.build()
            with ui.tab_panel("sources"):
                panel.names_box = ui.column().classes("w-full").mark("name-variants")
                panel.render_names()
                sources_view(person_id, on_change=panel.reload)
            with ui.tab_panel("periods"):
                periods_view(person_id, on_change=panel.reload_periods)
            with ui.tab_panel("theses"):
                ThesesView(person_id, panel)
            with ui.tab_panel("documents"):
                documents_view(person_id)


def _header(person: Person, tab: str, folder: tuple[int, str] | None = None) -> ui.tabs:
    """Name, tabs and edit / delete buttons on one row (wrapping on narrow screens)."""
    with ui.row().classes("w-full items-center justify-between gap-2"):
        with ui.column().classes("gap-0"):
            if folder:
                with (
                    ui.link(target=f"/?folder={folder[0]}")
                    .classes("flex items-center gap-1 text-sm no-underline")
                    .mark("folder-title")
                ):
                    ui.icon("folder", color="amber-8")
                    ui.label(folder[1])
            ui.label(person.name).classes("text-2xl")
            if person.affiliation:
                ui.label(person.affiliation).classes("text-grey")
            if person.orcid:
                ui.link(
                    f"ORCID {person.orcid}", f"https://orcid.org/{person.orcid}", new_tab=True
                ).classes("text-xs").mark("person-orcid")
        with ui.tabs(value=tab).props("inline-label dense").classes("grow") as tabs:
            ui.tab("publications", "Publications", icon="article")
            ui.tab("sources", "Sources", icon="hub")
            ui.tab("periods", "Periods", icon="date_range")
            ui.tab("theses", "Theses", icon="school")
            ui.tab("documents", "Documents", icon="description").mark("tab-documents")
        with ui.row():
            ui.button(
                icon="delete_sweep",
                on_click=lambda: purge_dialog([person.id], person.name, ui.navigate.reload),
            ).props("flat round color=negative").tooltip(
                "Purge: remove all papers and re-sync"
            ).mark("purge-person")
            ui.button(icon="edit", on_click=lambda: edit.open()).props("flat round")
            ui.button(icon="delete", on_click=lambda: confirm.open()).props(
                "flat round color=negative"
            )

    with ui.dialog() as edit, ui.card().classes("w-96"):
        name = ui.input("Name", value=person.name).classes("w-full")
        aff = ui.input("Affiliation", value=person.affiliation or "").classes("w-full")
        orcid = (
            ui.input("ORCID", value=person.orcid or "", placeholder="0000-0002-1825-0097")
            .classes("w-full")
            .tooltip("Helps matching: profiles with this ORCID are shown in green")
            .mark("person-orcid-input")
        )
        aliases = (
            ui.textarea("Name aliases (one per line)", value="\n".join(person.aliases or []))
            .classes("w-full")
            .tooltip("Other spellings used in author lists, e.g. “B. Doe”")
        )
        notes = ui.textarea("Notes", value=person.notes or "").classes("w-full")

        def save() -> None:
            value = normalize_orcid(orcid.value)
            if orcid.value.strip() and value is None:
                ui.notify("Not an ORCID (e.g. 0000-0002-1825-0097)", type="warning")
                return
            with session_scope() as s:
                p = s.get(Person, person.id)
                p.name, p.affiliation = name.value.strip(), aff.value.strip() or None
                p.aliases = [a.strip() for a in aliases.value.splitlines() if a.strip()]
                p.notes = notes.value or None
            set_orcid(person.id, value)  # and rescore the candidates
            ui.navigate.reload()

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=edit.close).props("flat")
            ui.button("Save", on_click=save)

    with ui.dialog() as confirm, ui.card():
        ui.label(f"Delete {person.name} and all their data (flags, stars, periods)?")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=confirm.close).props("flat")

            def delete() -> None:
                with session_scope() as s:
                    s.delete(s.get(Person, person.id))
                ui.navigate.to("/")

            ui.button("Delete", color="negative", on_click=delete)
    return tabs


def purge_dialog(person_ids: list[int], who: str, after=None) -> None:
    """Big confirmation, then remove all papers of these people and re-sync them."""
    from ..sync import purge, purge_counts

    n = purge_counts(person_ids)
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("warning", size="lg", color="negative")
            ui.label(f"Purge the papers of {who}?").classes("text-xl font-medium")
        with ui.column().classes("gap-1 bg-red-1 rounded p-3 w-full"):
            ui.label(f"This removes, for {n['people']} person(s):").classes("font-medium")
            ui.label(f"• {n['papers']} paper(s) and {n['records']} source record(s)")
            ui.label(
                f"• everything set on these papers: {n['annotated']} paper(s) have flags, "
                "stars, a venue / rank / kind set by hand, corrections, a note, are hidden "
                "or were merged / split by hand"
            ).classes("text-negative" if n["annotated"] else "")
            ui.label(
                "Then all their validated sources are synced again from scratch. Sources, "
                "periods, name aliases, PhD students and venue decisions are kept."
            ).classes("text-sm")
            ui.label("This cannot be undone.").classes("text-negative font-medium")
        word = (
            ui.input("Type PURGE to confirm")
            .props("dense outlined")
            .classes("w-full")
            .mark("purge-word")
        )

        def ok() -> None:
            if (word.value or "").strip().upper() != "PURGE":
                ui.notify("Type PURGE to confirm", type="warning")
                return
            purge(person_ids)
            for pid in person_ids:
                start_sync(pid)
            ui.notify(f"Papers purged; re-syncing {n['people']} person(s)", type="positive")
            if after:
                after()
            dlg.close()  # last: a closed dialog is deleted, with its UI context

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Purge and re-sync", icon="delete_forever", on_click=ok).props(
                "color=negative"
            ).mark("purge-confirm")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


@ui.refreshable
def stale_banner(person_id: int) -> None:
    person = _load(person_id)
    stale = [ln for ln in person.links if ln.is_stale or ln.sync_state == "error"]
    if is_syncing(person_id):
        with ui.row().classes("w-full items-center bg-blue-1 text-blue-9 rounded p-2"):
            ui.spinner(size="sm")
            running = [
                ADAPTERS[ln.source].label for ln in person.links if ln.sync_state == "running"
            ]
            ui.label("Updating " + (", ".join(running) or "…"))
        return
    if stale:
        with ui.row().classes("w-full items-center bg-amber-1 text-amber-10 rounded p-2 gap-3"):
            ui.icon("warning")
            names = ", ".join(f"{ADAPTERS[ln.source].label} ({ln.status_label})" for ln in stale)
            ui.label(f"Some sources are not up to date: {names}")
            ui.button(
                "Sync now", icon="sync", on_click=lambda: _sync(person_id, only_stale=True)
            ).props("dense")


def _sync(person_id: int, *, only_stale: bool = False) -> None:
    if not start_sync(person_id, only_stale=only_stale):
        ui.notify("A sync is already running")
    _refresh(stale_banner)
    _refresh(sources_list)


# ---- sources -------------------------------------------------------------------------------


def sources_view(person_id: int, on_change) -> None:
    state = {"was_running": False}

    with ui.row().classes("w-full items-center gap-2"):
        ui.button("Re-sync all", icon="sync", on_click=lambda: _sync(person_id))

        async def search_again() -> None:
            ui.notify("Searching every source…")
            errors = await discover(person_id)
            if errors:
                ui.notify(
                    "Failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()),
                    type="warning",
                    multi_line=True,
                )
            _refresh(sources_list)

        ui.button("Search sources again", icon="search", on_click=search_again).props("outline")
        url = ui.input("Add a profile by URL or id").classes("w-96").props("dense clearable")
        src = ui.select(
            {"": "auto-detect", **{k: a.label for k, a in ADAPTERS.items() if a.linkable}}, value=""
        ).props("dense")

        def add() -> None:
            text = (url.value or "").strip()
            candidates = [src.value] if src.value else list(ADAPTERS)
            for name in candidates:
                ext = ADAPTERS[name].parse_url(text)
                if ext:
                    add_link(person_id, name, ext)
                    ui.notify(f"Added {ADAPTERS[name].label} profile {ext} — syncing")
                    url.value = ""
                    background_tasks.create(_sync_one(person_id, None, name, ext))
                    _refresh(sources_list)
                    _refresh(stale_banner)
                    return
            ui.notify("Could not recognise this URL / id", type="warning")

        ui.button("Add", on_click=add).props("dense")

    sources_list(person_id)

    def tick() -> None:
        running = is_syncing(person_id) or any(
            ln.sync_state == "running" for ln in _load(person_id).links
        )
        if running or state["was_running"]:
            _refresh(sources_list)
            _refresh(stale_banner)
        state["was_running"] = running
        # Reload the publications whenever a merge happened since they were loaded (a quick
        # sync can start and finish between two ticks).
        with session_scope() as s:
            merged = s.get(Person, person_id).last_merged_at
        if merged != state.get("merged_at"):
            if "merged_at" in state:
                _refresh(sources_list)
                _refresh(stale_banner)
                background_tasks.create(on_change())
            state["merged_at"] = merged

    ui.timer(1.5, tick)


async def _sync_one(
    person_id: int, link_id: int | None, source: str | None = None, ext: str | None = None
) -> None:
    if link_id is None:
        with session_scope() as s:
            link_id = s.scalar(
                select(SourceLink.id).where(
                    SourceLink.person_id == person_id,
                    SourceLink.source == source,
                    SourceLink.external_id == ext,
                )
            )
    await sync_link(link_id)


def _link_url(ln: SourceLink) -> str | None:
    return ln.url or ADAPTERS[ln.source].profile_url(ln.external_id)


@ui.refreshable
def sources_list(person_id: int) -> None:
    person = _load(person_id)
    links = [ln for ln in person.links if not manual.is_hal_document(ln)]
    validated = [ln for ln in links if ln.status == "validated"]
    candidates = sorted(
        (ln for ln in links if ln.status == "candidate"), key=lambda ln: (ln.source, -ln.score)
    )
    rejected = [ln for ln in links if ln.status == "rejected"]

    if not person.orcid and (found := suggested_orcid(person)):
        with (
            ui.row()
            .classes("w-full items-center gap-2 bg-green-1 rounded p-2")
            .mark("orcid-suggestion")
        ):
            ui.icon("badge", color="positive")
            ui.label(f"The validated profiles give the ORCID {found}.").classes("grow")

            def use(o=found) -> None:
                ui.notify(f"ORCID {o} set: candidates with it are shown in green")
                set_orcid(person_id, o)
                _refresh(sources_list)

            ui.button("Use it", icon="check", on_click=use).props("dense").mark("orcid-use")
    ui.label("Validated sources").classes("text-lg mt-2")
    if not validated:
        ui.label("None yet: validate candidates below, or add a profile by URL.").classes(
            "text-grey"
        )
    with ui.column().classes("w-full gap-2"):
        for ln in validated:
            _validated_card(person_id, ln)

    if items := manual.added(person_id):
        _added_list(person_id, items)

    ui.label("Candidates to review").classes("text-lg mt-4")
    if not candidates:
        ui.label("No pending candidate.").classes("text-grey")
    with ui.grid(columns="repeat(auto-fill, minmax(360px, 1fr))").classes("w-full"):
        for ln in candidates:
            _candidate_card(person_id, ln, person.orcid or suggested_orcid(person), bool(validated))

    if rejected:
        with ui.expansion(f"Rejected ({len(rejected)})").classes("w-full"):
            for ln in rejected:
                with ui.row().classes("items-center gap-2"):
                    source_tag(ln.source, url=_link_url(ln))
                    ui.link(ln.display_name or ln.external_id, ln.url or "#", new_tab=True)
                    ui.button(
                        "restore", on_click=lambda i=ln.id: _set(person_id, i, "candidate")
                    ).props("flat dense")


def _added_list(person_id: int, items: list[manual.AddedItem]) -> None:
    """Publications added by hand (Publications tab → +), with their source record."""
    ui.label("Added by hand").classes("text-lg mt-4")
    with ui.column().classes("w-full gap-1").mark("added-publications"):
        for it in items:
            with ui.row().classes("w-full items-center gap-3 px-2"):
                source_tag(it.kind, url=it.url)
                ui.link(it.title or it.id, it.url, new_tab=True)
                ui.label(it.id).classes("text-grey text-xs")
                if it.state and it.state != "up to date":
                    ui.badge(it.state, color=STATUS_COLOUR.get(it.state, "grey"))
                ui.space()

                async def remove(i=it) -> None:
                    await manual.remove(person_id, i)
                    ui.notify(f"Removed {i.id}")
                    _refresh(sources_list)

                ui.button(icon="delete", on_click=remove).props(
                    "flat round dense color=negative"
                ).tooltip("Remove (the paper stays if another source has it)").mark(
                    f"remove-added-{it.id}"
                )


def _set(person_id: int, link_id: int, status: str) -> None:
    set_link_status(link_id, status)
    _refresh(sources_list)
    _refresh(stale_banner)


def _validated_card(person_id: int, ln: SourceLink) -> None:
    adapter = ADAPTERS[ln.source]
    with ui.card().classes("w-full py-2"), ui.row().classes("w-full items-center gap-3"):
        source_tag(ln.source, url=_link_url(ln))
        ui.link(
            ln.display_name or ln.external_id,
            ln.url or adapter.profile_url(ln.external_id),
            new_tab=True,
        ).classes("font-medium")
        ui.label(ln.external_id).classes("text-grey text-xs")
        ui.badge(ln.status_label, color=STATUS_COLOUR.get(ln.status_label, "grey"))
        if ln.sync_state == "running":
            ui.spinner(size="xs")
        ui.label(f"last sync {fmt_dt(ln.last_synced_at)}").classes("text-sm text-grey")
        if ln.record_count is not None:
            ui.label(f"{ln.record_count} records").classes("text-sm text-grey")
        ui.space()
        if ln.source == "scholar":
            _scholar_upload(person_id, ln)
        ui.button(
            icon="sync", on_click=lambda i=ln.id: background_tasks.create(_sync_one(person_id, i))
        ).props("flat round dense").tooltip("Sync this source")
        ui.button(icon="link_off", on_click=lambda i=ln.id: _set(person_id, i, "rejected")).props(
            "flat round dense color=negative"
        ).tooltip("Unlink (reject)")
    if ln.sync_state == "error" and ln.last_error:
        ui.label(ln.last_error).classes("text-negative text-sm -mt-2 ml-4")


def _scholar_upload(person_id: int, ln: SourceLink) -> None:
    with ui.dialog() as dlg, ui.card():
        ui.label("Upload a saved Google Scholar profile page").classes("text-lg")
        ui.markdown(
            "Open the profile, click **Show more** until every paper is listed, then "
            "save the page (HTML) and upload it here."
        )

        async def handle(e) -> None:
            html = await e.file.text()
            sid = profile_id_from_html(html)
            if sid and sid != ln.external_id:
                ui.notify(f"This page is for profile {sid}, not {ln.external_id}", type="warning")
                return
            result = parse_profile(html, [ln.display_name or ""])
            if not result.publications:
                ui.notify("No publications found in this page", type="warning")
                return
            finish_link(ln.id, result)
            ui.notify(f"Imported {len(result.publications)} publications")
            _refresh(sources_list)
            dlg.close()
            _refresh(stale_banner)

        ui.upload(on_upload=handle, auto_upload=True, max_files=1).props('accept=".html,.htm"')
    ui.button(icon="upload_file", on_click=dlg.open).props("flat round dense").tooltip(
        "Upload a saved profile page"
    )


def _candidate_card(
    person_id: int, ln: SourceLink, orcid: str | None = None, can_probe: bool = False
) -> None:
    """``orcid``: the person's, or that of the validated profiles (a candidate with the same
    is shown in green, another in red). ``can_probe``: there are validated sources to compare
    the candidate's papers with."""
    ev = ln.evidence or {}
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2 w-full"):
            source_tag(ln.source, url=_link_url(ln))
            ui.link(ln.display_name or ln.external_id, ln.url or "#", new_tab=True).classes(
                "font-medium"
            )
            ui.space()
            ui.badge(f"{ln.score:.2f}", color="positive" if ln.score >= 0.9 else "grey").tooltip(
                "match score (name, ORCID, affiliation, known titles, papers in common)"
            )
        details = []
        if ev.get("affiliation"):
            details.append(ev["affiliation"])
        if ev.get("works_count") is not None:
            details.append(f"{ev['works_count']} works")
        own_id = ln.external_id if ln.source == "orcid" else ""
        theirs = normalize_orcid(ev.get("orcid") or own_id)
        if theirs:
            same = orcid is not None and theirs == orcid
            ui.badge(
                f"ORCID {theirs}" + (" ✓" if same else " ≠" if orcid else ""),
                color="positive" if same else "negative" if orcid else "grey-6",
            ).props("outline" if not orcid else "").tooltip(
                "the person's ORCID"
                if same
                else f"not the person's ORCID ({orcid})"
                if orcid
                else "set the person's ORCID (edit) to check it"
            ).mark(f"candidate-orcid-{ln.id}")
        if ev.get("topics"):
            details.append(", ".join(t for t in ev["topics"] if t))
        if ev.get("roles"):
            details.append(", ".join(f"{k.split(' /')[0]}: {v}" for k, v in ev["roles"].items()))
        for d in details:
            ui.label(d).classes("text-sm text-grey")
        for t in ev.get("sample_titles") or []:
            ui.label(f"• {t}").classes("text-xs")
        if ov := ev.get("overlap"):
            share = ov["matched"] / ov["total"] if ov["total"] else 0
            ui.badge(
                f"{ov['matched']}/{ov['total']} papers already known ({share:.0%})",
                color="positive" if share >= 0.3 else "warning" if share > 0 else "negative",
            ).tooltip("its papers found in the validated sources").mark(
                f"candidate-overlap-{ln.id}"
            )
        with ui.row():
            ui.button(
                "Validate", icon="check", on_click=lambda i=ln.id: _validate(person_id, i)
            ).props("dense")
            ui.button(
                "Reject", icon="close", on_click=lambda i=ln.id: _set(person_id, i, "rejected")
            ).props("dense flat color=negative")
            if can_probe and ADAPTERS[ln.source].provides_publications:
                ui.button(icon="join_inner", on_click=lambda i=ln.id: _probe(person_id, i)).props(
                    "dense flat"
                ).tooltip("Fetch its papers and check how many the validated sources have").mark(
                    f"candidate-probe-{ln.id}"
                )


async def _probe(person_id: int, link_id: int) -> None:
    ui.notify("Fetching the candidate's papers…")
    try:
        await probe_overlap(link_id)
    except Exception as e:
        ui.notify(f"Could not fetch its papers: {e}", type="warning")
        return
    _refresh(sources_list)


def _validate(person_id: int, link_id: int) -> None:
    # Before refreshing the list: it holds the clicked button (deleted with it).
    ui.notify("Validated — syncing this source", type="positive")
    _set(person_id, link_id, "validated")
    background_tasks.create(_sync_one(person_id, link_id))


# ---- periods -------------------------------------------------------------------------------


def periods_view(person_id: int, on_change) -> None:
    @ui.refreshable
    def listing() -> None:
        items = [p for p in annotations.periods(person_id) if p.folder_id is None]
        if not items:
            ui.label(
                "No period yet. Periods are named year ranges (e.g. “HDR 2015–2024”); "
                "papers can be starred, tagged and annotated within each period."
            ).classes("text-grey")
        for p in items:
            with ui.row().classes("items-center gap-2"):
                name = ui.input("Name", value=p.name).props("dense")
                start = (
                    ui.number("From", value=p.start_year, format="%d")
                    .props("dense")
                    .classes("w-24")
                )
                end = ui.number("To", value=p.end_year, format="%d").props("dense").classes("w-24")
                ui.label(f"★ {len(p.stars)}").classes("text-amber-8")

                def save(pid=p.id, n=name, a=start, b=end) -> None:
                    annotations.save_period(person_id, n.value, _int(a.value), _int(b.value), pid)
                    ui.notify("Period saved")
                    on_change()

                def remove(pid=p.id) -> None:
                    annotations.delete_period(pid)
                    listing.refresh()
                    on_change()

                ui.button(icon="save", on_click=save).props("flat round dense")
                ui.button(icon="delete", on_click=remove).props("flat round dense color=negative")

    listing()
    with ui.row().classes("items-center gap-2 mt-4"):
        name = ui.input("New period name").props("dense")
        start = ui.number("From", format="%d").props("dense").classes("w-24")
        end = ui.number("To", format="%d").props("dense").classes("w-24")

        def add() -> None:
            if not name.value:
                return
            annotations.save_period(person_id, name.value, _int(start.value), _int(end.value))
            name.value = ""
            listing.refresh()
            on_change()

        ui.button("Add period", icon="add", on_click=add)

    folders_section(person_id, on_change)


def folders_section(person_id: int, on_change) -> None:
    """The person's period in each of their folders."""
    ui.label("In folders").classes("text-lg mt-6")
    ui.label(
        "A folder (e.g. a committee) groups people; each person has their own period in it."
    ).classes("text-sm text-grey")

    @ui.refreshable
    def listing() -> None:
        mine = [
            p
            for p in annotations.periods(person_id, include_hidden_folders=True)
            if p.folder_id is not None
        ]
        for p in mine:
            with ui.row().classes("items-center gap-2"):
                ui.icon("folder", color="amber-8")
                ui.link(p.folder.name, f"/?folder={p.folder_id}").classes("w-56")
                if p.folder.date:
                    ui.label(p.folder.date.isoformat()).classes("text-sm text-grey")
                if p.folder.hidden:
                    ui.badge("hidden", color="grey")
                start = (
                    ui.number("From", value=p.start_year, format="%d")
                    .props("dense")
                    .classes("w-24")
                )
                end = ui.number("To", value=p.end_year, format="%d").props("dense").classes("w-24")
                ui.label(f"★ {len(p.stars)}").classes("text-amber-8")
                tags = (
                    ui.select(
                        sorted(set(p.tags or []), key=str.lower),
                        value=list(p.tags or []),
                        label="Tags",
                        multiple=True,
                        new_value_mode="add-unique",
                        with_input=True,
                    )
                    .props("dense use-chips")
                    .classes("w-56")
                    .mark(f"folder-tags-{p.id}")
                )

                def save(pid=p.id, a=start, b=end, t=tags) -> None:
                    folders.set_period(pid, _int(a.value), _int(b.value))
                    folders.set_tags(pid, t.value or [])
                    ui.notify("Period saved")
                    on_change()

                def leave(fid=p.folder_id) -> None:
                    folders.remove_person(fid, person_id)
                    listing.refresh()
                    on_change()

                ui.button(icon="save", on_click=save).props("flat round dense")
                ui.button(icon="folder_off", on_click=leave).props(
                    "flat round dense color=negative"
                ).tooltip("Remove from this folder (its stars, tags and notes on papers are lost)")
        joined = {p.folder_id for p in mine}
        others = {f.id: f.name for f in folders.folders() if f.id not in joined}
        if others:
            with ui.row().classes("items-center gap-2 mt-2"):
                pick = (
                    ui.select(others, label="Add to a folder")
                    .props("dense outlined")
                    .classes("w-64")
                )

                def join() -> None:
                    if pick.value:
                        folders.add_person(pick.value, person_id)
                        listing.refresh()
                        on_change()

                ui.button("Add", icon="create_new_folder", on_click=join).props("dense")

    listing()


def _int(v) -> int | None:
    return int(v) if v not in (None, "") else None


# ---- theses --------------------------------------------------------------------------------


def student_aliases_view(person_id: int, rows: list[Thesis]) -> None:
    """Aliases of supervised PhD students, used to highlight them in author lists."""
    students = sorted(
        {n for t in rows if t.role == "director" for n in (t.student or "").split(", ") if n}
    )
    if not students:
        return
    with session_scope() as s:
        current = dict(s.get(Person, person_id).student_aliases or {})
    with ui.expansion("PhD student name aliases (for highlighting in author lists)").classes(
        "w-full"
    ):
        inputs = {}
        for name in students:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(name).classes("w-56")
                inputs[name] = (
                    ui.input(
                        placeholder="other spellings, comma-separated",
                        value=", ".join(current.get(name, [])),
                    )
                    .props("dense outlined")
                    .classes("grow")
                )

        def save() -> None:
            with session_scope() as s:
                s.get(Person, person_id).student_aliases = {
                    n: [a.strip() for a in i.value.split(",") if a.strip()]
                    for n, i in inputs.items()
                    if i.value.strip()
                }
            ui.notify("Aliases saved (reload the publications to see them)")

        ui.button("Save aliases", icon="save", on_click=save)


class ThesesView:
    """The person's theses by role; within the years of the publications panel (its
    period) unless "all" is asked."""

    def __init__(self, person_id: int, panel: PublicationsPanel) -> None:
        self.person_id = person_id
        self.panel = panel
        self.show_all = False
        self.shown: tuple | None = None
        with session_scope() as s:
            self.rows = list(
                s.scalars(
                    select(Thesis)
                    .join(SourceLink)
                    .where(SourceLink.person_id == person_id, active_links())
                )
            )
        if not self.rows:
            ui.label("No thesis: validate a theses.fr profile in the Sources tab.").classes(
                "text-grey"
            )
            return
        student_aliases_view(person_id, self.rows)
        self.container = ui.column().classes("w-full gap-2").mark("theses")
        self.render()
        panel.on_render.append(self.refresh)

    def refresh(self) -> None:
        """Re-render when the period changed."""
        if self.container.is_deleted:
            self.panel.on_render.remove(self.refresh)
        elif self.shown != (self.panel.lo, self.panel.hi):
            self.render()

    def render(self) -> None:
        lo, hi = self.panel.lo, self.panel.hi
        self.shown = (lo, hi)
        bounded = lo is not None or hi is not None
        rows = (
            self.rows
            if self.show_all or not bounded
            else [t for t in self.rows if theses.in_period(t, lo, hi)]
        )
        self.container.clear()
        with self.container:
            if bounded:
                period = next((p for p in self.panel.periods if p.id == self.panel.period_id), None)
                rng = f"{lo or '…'}–{hi or '…'}"
                where = (
                    period_label(period)
                    if period
                    and (lo, hi)
                    == (
                        period.start_year,
                        period.end_year,
                    )
                    else rng
                )
                with ui.row().classes("items-center gap-4"):
                    ui.label(
                        f"{len(rows)} of {len(self.rows)} theses in {where}"
                        if not self.show_all
                        else f"All {len(self.rows)} theses (period: {where})"
                    ).classes("text-grey").mark("theses-period")

                    def toggle(e) -> None:
                        self.show_all = e.value
                        self.render()

                    ui.switch("show all", value=self.show_all, on_change=toggle).tooltip(
                        "Also the theses outside the period (supervision overlapping it; "
                        "juries: the defence within it)"
                    ).mark("theses-all")
            with session_scope() as s:
                outcomes = dict(s.get(Person, self.person_id).student_outcomes or {})
            for role in ["director", "rapporteur", "examiner", "president", "author", "other"]:
                items = [t for t in rows if t.role == role]
                if items:
                    _theses_table(role, items, self.person_id, outcomes)


def _theses_table(role: str, items: list[Thesis], person_id: int, outcomes: dict) -> None:
    spans = {t.id: theses.span(t) for t in items}
    items.sort(key=lambda t: spans[t.id].end or spans[t.id].start or "9999", reverse=True)
    with ui.expansion(f"{ROLE_LABELS[role]} ({len(items)})", value=role != "author").classes(
        "w-full"
    ):
        columns = [
            {"name": "start", "label": "Start", "field": "start", "sortable": True},
            {"name": "end", "label": "End", "field": "end", "sortable": True},
            {"name": "student", "label": "PhD student", "field": "student", "sortable": True},
            {"name": "title", "label": "Title", "field": "title", "align": "left"},
            {"name": "sup", "label": "Supervisors", "field": "sup", "align": "left"},
            {"name": "inst", "label": "Institution", "field": "inst", "align": "left"},
            {"name": "status", "label": "Status", "field": "status"},
        ]
        supervised = role == "director"
        if supervised:
            columns.append(
                {"name": "outcome", "label": "After the PhD", "field": "outcome", "align": "left"}
            )
        table = (
            ui.table(
                columns=columns,
                rows=[
                    {
                        "id": t.id,
                        "start": spans[t.id].start or "",
                        "estimated": spans[t.id].start_estimated,
                        "end": spans[t.id].end or "",
                        "student": t.student,
                        "title": t.title,
                        "sup": ", ".join(t.supervisors or []),
                        "inst": t.institution,
                        "status": "defended"
                        if t.status == "soutenue"
                        else "in progress"
                        if t.status == "enCours"
                        else t.status,
                        "url": t.url,
                        **(
                            {
                                "outcome": outcomes.get(t.student or "", ""),
                                "links": theses.search_links(t.student or ""),
                            }
                            if supervised
                            else {}
                        ),
                    }
                    for t in items
                ],
                row_key="id",
            )
            .classes("w-full")
            .props("dense flat wrap-cells")
            .mark(f"theses-{role}")
        )
        table.add_slot(
            "body-cell-start",
            f"""
            <q-td :props="props">
              <span v-if="props.row.estimated" class="text-grey">≈ {{{{ props.value }}}}
                <q-tooltip>Estimated: {theses.TYPICAL_YEARS} years before the defence
                (theses.fr only gives the start of the theses in progress)</q-tooltip>
              </span>
              <span v-else>{{{{ props.value }}}}</span>
            </q-td>""",
        )
        table.add_slot(
            "body-cell-title",
            """
            <q-td :props="props"><a :href="props.row.url" target="_blank">{{ props.value }}</a>
            </q-td>""",
        )
        if supervised:
            # Search links, and a note (click to edit) on what became of the student.
            table.add_slot(
                "body-cell-outcome",
                """
                <q-td :props="props" style="min-width: 14rem">
                  <div class="row items-center q-gutter-x-xs">
                    <q-btn v-for="l in props.row.links" :key="l.url" :href="l.url"
                           target="_blank" :label="l.label" flat dense no-caps size="sm"
                           color="primary">
                      <q-tooltip>Search {{ l.name }} on {{ l.label }}</q-tooltip>
                    </q-btn>
                  </div>
                  <div class="cursor-pointer" :class="props.value ? '' : 'text-grey'">
                    {{ props.value || 'add a note…' }}
                    <q-popup-edit v-model="props.row.outcome" buttons v-slot="scope"
                        @save="v => $parent.$emit('outcome',
                                                  {student: props.row.student, note: v})">
                      <q-input v-model="scope.value" type="textarea" autogrow dense autofocus
                               label="What became of them (position, employer…)" />
                    </q-popup-edit>
                  </div>
                </q-td>""",
            )

            def save_outcome(e) -> None:
                annotations.set_student_outcome(person_id, e.args["student"], e.args["note"])
                for r in table.rows:
                    if r["student"] == e.args["student"]:
                        r["outcome"] = e.args["note"].strip()
                ui.notify("Note saved")

            table.on("outcome", save_outcome)


__all__ = ["SourceError", "register"]
