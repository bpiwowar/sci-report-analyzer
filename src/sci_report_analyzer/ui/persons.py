"""Main page (Reports): the folders and their people, or all the people."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from html import escape

from nicegui import ui

from .. import annotations, folders, i18n, manual, persons, pubview, reports, source_settings
from ..db.models import Person
from ..i18n import N_, _, ngettext
from ..sources import ADAPTERS
from ..sync import (
    any_running,
    default_search_sources,
    discover,
    is_syncing,
    normalize_orcid,
    set_link_status,
    set_orcid,
    start_sync,
)
from .categories_editor import categories_dialog
from .dialogs import close_then, confirm, ok_handler, transient_dialog
from .folder_notes import NOTES_TIP, notes_url
from .folders_editor import ALL, delete_dialog, folders_tree
from .person import (
    candidate_details,
    link_url,
    move_to_folder_dialog,
    orcid_badge,
    purge_dialog,
    remove_from_folder_dialog,
)
from .theme import STATUS_COLOUR, fmt_dt, frame, int_or_none, refresh_alive, source_tag

LAST_FOLDER = "ui.people.folder"  # the folder shown on the Reports page (sticky)


def register() -> None:
    @ui.page("/")
    def index(folder: int | None = None) -> None:
        known = {f.id: f for f in folders.folders()}
        if folder is None:
            folder = annotations.ui_state(LAST_FOLDER)
        if folder not in known:
            folder = ALL
        annotations.save_ui_state(LAST_FOLDER, folder)
        title = known[folder].name if folder in known else _("All people")
        with frame(title):

            @ui.refreshable
            def body() -> None:  # (again once the tree changed: names, places, settings)
                _reports(folder, body.refresh)

            body()
            ui.timer(2.0, lambda: _refresh_if_running())


def _reports(folder: int, changed) -> None:
    """The tree of folders (on the left; above, on narrow screens), and the folder selected
    (else All people)."""
    known = {f.id: f for f in folders.folders()}
    current = known.get(folder)
    with ui.element("div").classes("w-full flex flex-col md:flex-row md:flex-nowrap gap-4"):
        with (
            ui.column()
            .classes(
                "w-full md:w-72 shrink-0 gap-1 self-start md:sticky md:top-16"
                " md:max-h-[calc(100vh-5rem)] md:overflow-auto"
            )
            .mark("folders-pane")
        ):
            folders_tree(current.id if current else ALL, _goto, changed)
        with ui.column().classes("w-full min-w-0 grow gap-3"):
            _header(current, known)
            if current is None:
                _cleanup_view(known)
            else:
                _folder_view(current)


def _refresh_if_running() -> None:
    if any_running():
        refresh_alive(_folder_cards)


def _goto(folder_id: int) -> None:
    ui.navigate.to(f"/?folder={folder_id}")


def _header(current: folders.FolderView | None, known: dict[int, folders.FolderView]) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.icon("folder" if current else "groups", size="md", color="amber-8" if current else "")
        ui.label(current.path if current else _("All people")).classes("text-2xl").mark(
            "people-title"
        )
        if current and current.date:
            ui.label(current.date.isoformat()).classes("text-grey")
        if current and current.hidden:
            ui.badge(_("archived"), color="grey").tooltip(_(ARCHIVED_TIP))
        if current:
            refresh = ui.navigate.reload
            ui.button(icon="edit", on_click=lambda: folder_dialog(current)).props(
                "flat round dense"
            ).tooltip(_("Edit the folder")).mark("folder-edit")
            ui.button(icon="category", on_click=lambda: categories_dialog(current.id)).props(
                "flat round dense"
            ).tooltip(_("Categories (where excerpts of the people's documents are filed)")).mark(
                "folder-categories"
            )
            ui.button(
                icon="visibility" if current.hidden else "visibility_off",
                on_click=lambda: (folders.set_hidden(current.id, not current.hidden), refresh()),
            ).props("flat round dense").tooltip(
                _("Unarchive") if current.hidden else _("Archive")
            ).mark(f"hide-folder-{current.id}")
            ui.button(
                icon="delete_sweep",
                on_click=lambda: purge_dialog(
                    [m.person_id for m in current.members],
                    _("everyone in “{folder}”").format(folder=current.name),
                    refresh,
                ),
            ).props("flat round dense color=negative").tooltip(
                _("Purge: remove all papers of the folder's people and re-sync")
            ).mark("purge-folder")
        ui.space()
        ui.button(
            _("New folder"),
            icon="create_new_folder",
            on_click=lambda: folder_dialog(None),
        ).props("flat")
        ui.button(
            _("Sync out-of-date"),
            icon="sync",
            on_click=lambda: _sync([m.person_id for m in current.members] if current else None),
        ).props("flat")
        ui.button(_("Add person"), icon="person_add", on_click=lambda: _add_dialog(current)).mark(
            "add-person"
        )


def _sync(person_ids: list[int] | None) -> None:
    started = 0
    for person, _status in persons.people_with_counts():
        if person_ids is not None and person.id not in person_ids:
            continue
        if any(ln.is_stale or ln.sync_state == "error" for ln in person.links):
            started += start_sync(person.id, only_stale=True)
    ui.notify(
        ngettext("Started {n} sync", "Started {n} syncs", started).format(n=started)
        if started
        else _("Everything is up to date")
    )


def _add_dialog(current: folders.FolderView | None) -> None:
    """Add a person: their name and a few settings, then their profiles searched and the
    right ones ticked, all in the dialog."""
    title = (
        _("Add a person to “{folder}”").format(folder=current.name)
        if current
        else _("Add a person")
    )
    with transient_dialog(title, width="w-[40rem] max-w-full") as (dialog, _card):
        with ui.column().classes("w-full gap-1") as form:
            name = ui.input(_("Full name")).classes("w-full").props("autofocus")
            aff = ui.input(_("Affiliation (optional, helps matching)")).classes("w-full")
            orcid = (
                ui.input(_("ORCID (optional)"), placeholder="0000-0002-1825-0097")
                .classes("w-full")
                .tooltip(_("Helps matching: profiles with this ORCID are shown in green"))
                .mark("add-person-orcid")
            )
            where = (
                ui.select(
                    {
                        n: a.label
                        for n, a in ADAPTERS.items()
                        if a.linkable and source_settings.enabled(n)
                    },
                    value=default_search_sources(),
                    multiple=True,
                    label=_("Search in"),
                )
                .props("dense use-chips")
                .classes("w-full")
                .mark("add-person-sources")
            )
            if current:
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    ui.label(_("Period in the folder")).classes("text-sm")
                    start = (
                        ui.number(_("from"), format="%d")
                        .props("dense outlined")
                        .classes("w-24")
                        .mark("add-person-start")
                    )
                    end = (
                        ui.number(_("to"), format="%d")
                        .props("dense outlined")
                        .classes("w-24")
                        .mark("add-person-end")
                    )
                tags = (
                    ui.select(
                        folders.tags_of(current),
                        value=[],
                        label=_("Tags"),
                        multiple=True,
                        new_value_mode="add-unique",
                        with_input=True,
                    )
                    .props("dense use-chips")
                    .classes("w-full")
                    .mark("add-person-tags")
                )
        found = ui.column().classes("w-full gap-2")
        buttons = ui.row().classes("w-full justify-end")

        async def search() -> None:
            if not name.value.strip():
                return
            given = normalize_orcid(orcid.value)
            if orcid.value.strip() and given is None:
                ui.notify(_("Not an ORCID (e.g. 0000-0002-1825-0097)"), type="warning")
                return
            pid = persons.create(name.value, aff.value)
            if given:
                set_orcid(pid, given)
            if current:
                period = folders.add_person(
                    current.id, pid, int_or_none(start.value), int_or_none(end.value)
                )
                if tags.value:
                    folders.set_tags(period, tags.value)
            # The person exists now: closing the dialog goes to their page.
            dialog.props("persistent")
            form.set_visibility(False)
            buttons.clear()
            with found, ui.row().classes("items-center gap-2"):
                ui.spinner()
                ui.label(_("Searching sources for {name}…").format(name=name.value))
            errors = await discover(pid, where.value or [])
            if dialog.is_deleted:
                return
            _candidates(pid, given, errors, found, buttons, dialog)

        name.on("keydown.enter", search)
        with buttons:
            ui.button(_("Cancel"), on_click=dialog.close).props("flat")
            ui.button(_("Add & search sources"), icon="search", on_click=search).mark(
                "add-person-ok"
            )


def _candidates(
    pid: int,
    orcid: str | None,
    errors: dict[str, str],
    found: ui.column,
    buttons: ui.row,
    dialog: ui.dialog,
) -> None:
    """The profiles found for a new person, to tick (the likely ones are)."""
    links = sorted(
        (ln for ln in persons.load(pid).links if ln.status == "candidate"),
        key=lambda ln: (-ln.score, ln.source),
    )
    picked: dict[int, ui.checkbox] = {}
    found.clear()
    with found:
        if errors:
            ui.label(
                _("Some sources failed: {sources}").format(
                    sources="; ".join(f"{k}: {v}" for k, v in errors.items())
                )
            ).classes("text-sm text-orange-9")
        if not links:
            ui.label(
                _("No candidate profile found: add their profiles by URL in the Sources tab.")
            ).classes("text-grey")
        else:
            ui.label(_("Tick the person's profiles:")).classes("text-sm")
        with ui.column().classes("w-full gap-1 max-h-[60vh] overflow-auto"):
            for ln in links:
                with ui.row().classes("w-full items-start gap-2 no-wrap"):
                    picked[ln.id] = ui.checkbox(value=ln.score >= 0.9).mark(
                        f"add-person-pick-{ln.id}"
                    )
                    with ui.column().classes("gap-0 grow"):
                        with ui.row().classes("items-center gap-2"):
                            source_tag(ln.source, url=link_url(ln))
                            ui.link(
                                ln.display_name or ln.external_id, ln.url or "#", new_tab=True
                            ).classes("font-medium")
                            ui.badge(
                                f"{ln.score:.2f}",
                                color="positive" if ln.score >= 0.9 else "grey",
                            ).tooltip(
                                _(
                                    "match score (name, ORCID, affiliation, known titles, "
                                    "papers in common)"
                                )
                            )
                            orcid_badge(ln, orcid)
                        if details := candidate_details(ln):
                            ui.label(" · ".join(details)).classes("text-xs text-grey")

    def done() -> None:
        chosen = [i for i, box in picked.items() if box.value]
        for i in chosen:
            set_link_status(i, "validated")
        if chosen:
            start_sync(pid)
            ui.notify(
                ngettext(
                    "{n} profile validated — syncing",
                    "{n} profiles validated — syncing",
                    len(chosen),
                ).format(n=len(chosen)),
                type="positive",
            )
        ui.navigate.to(f"/person/{pid}" if chosen else f"/person/{pid}?tab=sources")
        dialog.close()

    buttons.clear()
    with buttons:
        ui.button(_("Later, in the Sources tab"), on_click=lambda: _later(pid, dialog)).props(
            "flat"
        ).mark("add-person-later")
        ui.button(_("Validate the ticked ones"), icon="check", on_click=done).mark(
            "add-person-validate"
        )


def _later(pid: int, dialog: ui.dialog) -> None:
    ui.navigate.to(f"/person/{pid}?tab=sources")
    dialog.close()


# ---- a folder ------------------------------------------------------------------------------


def _folder_view(f: folders.FolderView) -> None:
    if f.notes:
        ui.label(f.notes).classes("text-sm text-grey")
    if within := [n for n in folders.tree() if n.parent_id == f.id]:
        with ui.row().classes("items-center gap-2"):
            ui.label(_("Folders within it:")).classes("text-sm text-grey")
            for n in within:
                ui.button(n.name, icon="folder", on_click=lambda n=n: _goto(n.id)).props(
                    "flat dense no-caps color=amber-9"
                ).mark(f"subfolder-{n.id}")
    _folder_cards(f.id)
    members = {m.person_id for m in f.members}
    others = {pid: n for pid, n in folders.all_people().items() if pid not in members}
    if others:
        with ui.row().classes("items-center gap-2"):
            pick = (
                ui.select(others, multiple=True, with_input=True, label=_("Add people"))
                .props("dense outlined use-chips")
                .classes("w-80")
                .mark(f"folder-add-{f.id}")
            )

            def add() -> None:
                for pid in pick.value or []:
                    folders.add_person(f.id, pid)
                ui.navigate.reload()

            ui.button(_("Add"), icon="person_add", on_click=add).props("dense").mark(
                f"folder-add-btn-{f.id}"
            )
    ui.label(
        _("Each person has their own period in the folder (set it in their Profile tab).")
    ).classes("text-xs text-grey")


@ui.refreshable
def _folder_cards(folder_id: int, only: tuple[str, ...] = ()) -> None:
    """The folder's people; with `only`, those having all these tags."""
    f = next((x for x in folders.folders() if x.id == folder_id), None)
    if f is None:
        return
    if not f.members:
        ui.label(_("No one in this folder yet.")).classes("text-grey")
        return
    tags = folders.tags_of(f)
    only = tuple(t for t in only if t in tags)
    if tags:
        ui.select(
            tags,
            value=list(only),
            multiple=True,
            label=_("Only people tagged"),
            on_change=lambda e: refresh_alive(_folder_cards, folder_id, tuple(e.value or ())),
        ).props("dense outlined use-chips clearable").classes("w-80").mark(
            f"folder-tag-filter-{folder_id}"
        )
    everyone = {p.id: (p, n) for p, n in persons.people_with_counts()}
    years = pubview.problem_years(m.person_id for m in f.members)
    problems = {
        m.person_id: pubview.count_in_period(years[m.person_id], m.start_year, m.end_year)
        for m in f.members
        if m.person_id in years
    }
    if total := sum(problems.values()):
        ui.label(
            ngettext(
                "{n} paper with problems in the people's periods",
                "{n} papers with problems in the people's periods",
                total,
            ).format(n=total)
        ).classes("text-sm text-orange-9").tooltip(_(_PROBLEMS_TIP)).mark("folder-problems")
    with ui.grid(columns="repeat(auto-fill, minmax(340px, 1fr))").classes("w-full"):
        for m in f.members:
            if m.person_id in everyone and set(only) <= set(m.tags):
                _card(
                    *everyone[m.person_id],
                    member=m,
                    folder_id=f.id,
                    problems=problems.get(m.person_id),
                    folder_tags=tags,
                )


_PROBLEMS_TIP = N_(
    "Papers needing a look (warning icon in the list), as of the last time the person's "
    "papers were shown"
)


def _period_editor(member: folders.Member) -> None:
    """The member's period in the folder; click to edit it in place."""
    text = (
        _("period {start}–{end}").format(start=member.start_year or "…", end=member.end_year or "…")
        if member.start_year or member.end_year
        else _("no period set")
    )
    with (
        ui.label(text)
        .classes("text-sm text-primary cursor-pointer border-b border-dashed")
        .on("click.stop", lambda: None)
        .tooltip(_("Click to set the period"))
        .mark(f"period-{member.period_id}"),
        ui.menu() as menu,
        ui.row().classes("items-center gap-2 p-2 no-wrap"),
    ):
        start = (
            ui.number(_("from"), value=member.start_year, format="%d")
            .props("dense outlined")
            .classes("w-24")
            .mark(f"period-start-{member.period_id}")
        )
        end = (
            ui.number(_("to"), value=member.end_year, format="%d")
            .props("dense outlined")
            .classes("w-24")
            .mark(f"period-end-{member.period_id}")
        )

        def save() -> None:
            folders.set_period(
                member.period_id,
                int_or_none(start.value),
                int_or_none(end.value),
            )
            menu.close()
            refresh_alive(_folder_cards)

        for el in (start, end):
            el.on("keydown.enter", save)
        ui.button(icon="check", on_click=save).props("flat round dense").mark(
            f"period-save-{member.period_id}"
        )


# ---- all people (cleanup) ------------------------------------------------------------------


def _cleanup_view(known: dict[int, folders.FolderView]) -> None:
    ui.label(
        _(
            "Every person, with the folders they are in: select people to delete them or to put "
            "them in a folder. Pick a folder in the tree to work with it."
        )
    ).classes("text-sm text-grey")
    rows = folders.people_rows()
    columns = [
        {"name": "name", "label": _("Name"), "field": "name", "align": "left", "sortable": True},
        {"name": "affiliation", "label": _("Affiliation"), "field": "affiliation", "align": "left"},
        {"name": "n_folders", "label": _("Folders"), "field": "n_folders", "sortable": True},
        {"name": "folders", "label": _("In"), "field": "folders", "align": "left"},
        {"name": "pubs", "label": _("Papers"), "field": "pubs", "sortable": True},
        {"name": "sources", "label": _("Sources"), "field": "sources", "sortable": True},
    ]
    data = [
        {
            "id": r.id,
            "name": r.name,
            "affiliation": r.affiliation or "",
            "n_folders": len(r.folders),
            "folders": ", ".join(r.folders),
            "pubs": r.publications,
            "sources": r.sources,
        }
        for r in rows
    ]
    with ui.row().classes("items-center gap-2 w-full"):
        filt = ui.input(placeholder=_("filter")).props("dense outlined clearable").classes("w-64")
        only_orphans = ui.switch(_("in no folder only")).mark("orphans-only")
        ui.space()
        target = (
            ui.select({f.id: f.name for f in known.values()}, label=_("folder"))
            .props("dense outlined")
            .classes("w-56")
        )
        add_btn = ui.button(_("Add to folder"), icon="drive_file_move").props("dense flat")
        del_btn = ui.button(_("Delete"), icon="delete").props("dense flat color=negative")
        del_btn.mark("delete-people")
    table = (
        ui.table(columns=columns, rows=data, row_key="id", selection="multiple", pagination=50)
        .classes("w-full")
        .props("dense flat")
        .mark("people-table")
    )
    filt.bind_value(table, "filter")
    table.on(
        "rowClick",
        lambda e: ui.navigate.to(f"/person/{e.args[1]['id']}"),
    )

    def orphans(e) -> None:
        table.rows = [r for r in data if not e.value or not r["n_folders"]]

    only_orphans.on_value_change(orphans)

    def selected() -> list[int]:
        return [r["id"] for r in table.selected]

    def add() -> None:
        if not target.value or not selected():
            ui.notify(_("Select people and a folder"), type="warning")
            return
        for pid in selected():
            folders.add_person(target.value, pid)
        ui.navigate.reload()

    def delete() -> None:
        ids = selected()
        if not ids:
            ui.notify(_("Select people first"), type="warning")
            return

        def ok() -> None:
            folders.delete_people(ids)
            ui.navigate.reload()

        confirm(
            ngettext(
                "Delete {n} person and all their data (sources, publications, "
                "tags, stars, periods)?",
                "Delete {n} people and all their data (sources, publications, "
                "tags, stars, periods)?",
                len(ids),
            ).format(n=len(ids)),
            _("Delete"),
            ok,
            mark="confirm-delete",
        )

    add_btn.on_click(add)
    del_btn.on_click(delete)


def _card(
    person: Person,
    n_pubs: int,
    *,
    member: folders.Member | None = None,
    folder_id: int | None = None,
    problems: int | None = None,
    folder_tags: list[str] | None = None,
) -> None:
    validated = [
        ln for ln in person.links if ln.status == "validated" and not manual.is_hal_document(ln)
    ]
    pending = [ln for ln in person.links if ln.status == "candidate"]
    stale = [ln for ln in validated if ln.is_stale or ln.sync_state == "error"]
    url = f"/person/{person.id}" + (f"/{member.period_id}" if member else "")
    with (
        ui.card()
        .classes("cursor-pointer")
        .on(
            "click",
            lambda: ui.navigate.to(url),
            js_handler="(e) => { if (!e.target.closest('a, button, .q-btn')) emit(); }",
        )
    ):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label(person.name).classes("text-lg font-medium")
            ui.label(
                ngettext("{n} publication", "{n} publications", n_pubs).format(n=n_pubs)
            ).classes("text-grey text-sm")
        if person.affiliation:
            ui.label(person.affiliation).classes("text-sm text-grey -mt-2")
        with ui.row().classes("gap-1 items-center"):
            for ln in validated:
                with source_tag(
                    ln.source, url=ln.url or ADAPTERS[ln.source].profile_url(ln.external_id)
                ):
                    ui.tooltip(
                        _("{name} — {status} (last sync {when})").format(
                            name=ln.display_name or ln.external_id,
                            status=_(ln.status_label),
                            when=fmt_dt(ln.last_synced_at),
                        )
                    )
                colour = STATUS_COLOUR.get(ln.status_label, "grey")
                ui.icon("circle", size="8px", color=colour).classes("-ml-1 mr-1")
            if not validated:
                ui.label(_("no validated source")).classes("text-sm text-grey")
            elif synced := persons.last_sync(person):
                ui.label(_("last sync {when}").format(when=fmt_dt(synced))).classes(
                    "text-xs text-grey ml-1"
                ).mark(f"last-sync-{person.id}")
        with ui.row().classes("gap-2"):
            if pending:
                ui.badge(
                    ngettext(
                        "{n} candidate to review", "{n} candidates to review", len(pending)
                    ).format(n=len(pending)),
                    color="orange",
                )
            if stale:
                ui.badge(
                    ngettext(
                        "{n} source not up to date", "{n} sources not up to date", len(stale)
                    ).format(n=len(stale)),
                    color="warning",
                )
            if is_syncing(person.id):
                ui.badge(_("syncing…"), color="info")
            if problems:
                ui.badge(
                    ngettext("{n} problem", "{n} problems", problems).format(n=problems),
                    color="orange-8",
                ).classes("cursor-pointer").on(
                    "click.stop", lambda: ui.navigate.to(f"{url}?problems=1")
                ).tooltip(_("{tip} — click to list them").format(tip=_(_PROBLEMS_TIP))).mark(
                    f"problems-{person.id}"
                )
        if member is not None:
            with ui.row().classes("items-center gap-2 w-full"):
                _period_editor(member)
                if member.stars:
                    ui.label(f"★ {member.stars}").classes("text-amber-8 text-sm")
                ui.space()
                ui.button(
                    icon="edit_note", on_click=lambda: ui.navigate.to(notes_url(member.period_id))
                ).props("flat round dense size=sm").tooltip(_(NOTES_TIP)).mark(
                    f"notes-{member.period_id}"
                )

                ui.button(
                    icon="drive_file_move",
                    on_click=lambda: move_to_folder_dialog(
                        folder_id, person.id, lambda _to: ui.navigate.reload()
                    ),
                ).props("flat round dense size=sm").on("click.stop", lambda: None).tooltip(
                    _("Move to another folder")
                ).mark(f"move-{person.id}")

                def remove() -> None:
                    remove_from_folder_dialog(folder_id, person.id, ui.navigate.reload)

                ui.button(icon="close", on_click=remove).props("flat round dense size=sm").on(
                    "click.stop", lambda: None
                ).tooltip(_("Remove from the folder")).mark(f"remove-{person.id}")
            _tags_editor(member, folder_tags or [])


def _tags_editor(member: folders.Member, options: list[str]) -> None:
    """The member's tags in the folder (any new tag can be typed)."""

    def save(e) -> None:
        folders.set_tags(member.period_id, e.value or [])
        member.tags = list(e.value or [])

    ui.select(
        sorted(set(options) | set(member.tags), key=str.lower),
        value=list(member.tags),
        multiple=True,
        new_value_mode="add-unique",
        with_input=True,
        on_change=save,
    ).props(
        f'dense borderless use-chips placeholder="{escape(_("+ tag"), quote=True)}" '
        "hide-dropdown-icon"
    ).classes("w-full -mt-2").on("click.stop", lambda: None).tooltip(
        _("Tags of the person in this folder")
    ).mark(f"tags-{member.period_id}")


# ---- folders -------------------------------------------------------------------------------

INFLUENCE_TIP = N_("In [[excerpts]] (and the copied excerpts), the excerpts flagged “rayonnement”")
INFLUENCE_MODES = {
    "copy": N_("Also in the Rayonnement section"),
    "move": N_("Only in the Rayonnement section, by category"),
    "mark": N_("Only in their categories, marked"),
}


def markers_section(folder_id: int) -> Callable[[], dict[str, reports.Marker]]:
    """The tags of an excerpt's markers in the reports (blank: the default, in their
    language), and whether the excerpts marked are first in their categories; returns them."""
    from .viewer_side import MARKER_ICONS

    current = reports.markers(folder_id)
    fields = {}
    with ui.row().classes("items-center gap-4"):
        for key, m in current.items():
            label, _tip, icon, colour = MARKER_ICONS[key]
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon(icon, size="xs", color=colour)
                tag = (
                    ui.input(_(label), value=m.tag)
                    .props('dense prefix="#"')
                    .classes("w-32")
                    .tooltip(_("Its tag in the reports (e.g. turned into an icon when copied)"))
                    .mark(f"folder-marker-tag-{key}")
                )
                first = (
                    ui.checkbox(_("First"), value=m.first)
                    .tooltip(
                        _(
                            "An excerpt marked moves first in its category (unmarked: "
                            "after those marked)"
                        )
                    )
                    .mark(f"folder-marker-first-{key}")
                )
                fields[key] = (tag, first)
    return lambda: {
        k: reports.Marker((t.value or "").strip("# "), bool(f.value))
        for k, (t, f) in fields.items()
    }


def levels_section(folder_id: int) -> Callable[[], tuple[list[reports.Level], str | None]]:
    """The levels an excerpt can have (e.g. local, national, international), in order: their
    tags in the reports (blank: the default, in their language; a new one: added below,
    removed with its button), and the default level of the excerpts without one; returns
    them."""
    rows = [{"key": lv.key, "tag": lv.tag} for lv in reports.levels(folder_id)]
    state = {"default": reports.default_level(folder_id) or ""}
    fields: dict[str, ui.select] = {}

    def options() -> dict[str, str]:
        return {
            "": _("No level"),
            **{r["key"] or r["tag"]: f"#{r['tag']}" for r in rows if (r["key"] or r["tag"])},
        }

    @ui.refreshable
    def listing() -> None:
        with ui.row().classes("items-center gap-2"):
            ui.label(_("Levels")).classes("text-sm").tooltip(
                _(
                    "An excerpt's level (e.g. national; none: the default one), written as "
                    "a #tag in the reports (e.g. turned into a text when copied)"
                )
            )
            for i, r in enumerate(rows):
                with ui.row().classes("items-center gap-0 no-wrap"):
                    ui.input(value=r["tag"]).props('dense prefix="#"').classes("w-28").on(
                        "blur", renamed
                    ).bind_value(r, "tag").mark(f"folder-level-tag-{i}")
                    ui.button(icon="close", on_click=lambda i=i: drop(i)).props(
                        "flat dense round size=xs color=grey-7"
                    ).tooltip(_("Remove this level")).mark(f"folder-level-remove-{i}")
            ui.button(icon="add", on_click=add).props("flat dense round size=sm").tooltip(
                _("Add a level")
            ).mark("folder-level-add")
            fields["default"] = (
                ui.select(options(), label=_("Default level"))
                .bind_value(state, "default")
                .props("dense")
                .classes("w-40")
                .tooltip(_("That of the excerpts without one"))
                .mark("folder-level-default")
            )

    def renamed() -> None:
        """(the default's options: the tags as typed; a new level's, its tag)"""
        shown, default = options(), fields["default"]
        default.set_options(shown, value=default.value if default.value in shown else "")

    def add() -> None:
        rows.append({"key": "", "tag": ""})
        listing.refresh()

    def drop(i: int) -> None:
        if rows[i]["key"] and state["default"] == rows[i]["key"]:
            state["default"] = ""
        del rows[i]
        listing.refresh()

    listing()

    def values() -> tuple[list[reports.Level], str | None]:
        kept = [r for r in rows if (r["tag"] or "").strip("# ") or r["key"]]
        return (
            [reports.Level(r["key"], (r["tag"] or "").strip("# ")) for r in kept],
            state["default"] or None,
        )

    return values


ARCHIVED_TIP = N_(
    "Greyed in the folders, its periods no longer offered when choosing a person's period"
)


def folder_dialog(f: folders.FolderView | None) -> None:
    """A folder's own settings: its name, date, notes…; and (an existing one) those of its
    reports, possibly shared with other folders (marked: an icon on their tab), in another tab:
    their language, the papers numbered, the templates…"""
    with transient_dialog(
        _("New folder") if f is None else _("Edit folder"),
        width="w-96" if f is None else "w-full max-w-4xl",
    ) as (dlg, _card):
        with ui.tabs().props("dense inline-label align=left").classes("w-full") as tabs:
            general_tab = ui.tab("general", _("Folder"), icon="folder").mark("folder-tab-general")
            if f is not None:
                others = folders.sharing(f.id)
                notes_tab = ui.tab(
                    "notes", _("Reports"), icon="share" if others else "description"
                ).mark("folder-tab-notes")
                notes_tab.tooltip(
                    _("Shared with {folders}: changed for them too").format(
                        folders=", ".join(others)
                    )
                    if others
                    else _("The settings of the reports (can be shared with other folders)")
                )
        citations_save = None
        with ui.tab_panels(tabs, value=general_tab).classes("w-full"):
            with ui.tab_panel("general").classes("p-0 gap-2"):
                name = (
                    ui.input(_("Name"), value=f.name if f else "")
                    .classes("w-full")
                    .mark("folder-name")
                )
                with ui.input(_("Date"), value=f.date.isoformat() if f and f.date else "").classes(
                    "w-full"
                ) as day:
                    with ui.menu().props("no-parent-event") as menu, ui.date().bind_value(day):
                        ui.button(_("Close"), on_click=menu.close).props("flat")
                    with day.add_slot("append"):
                        ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
                edited = {"notes": f is None}  # (saved only if edited: the copy shown may be stale)
                notes = (
                    ui.textarea(
                        _("Notes"),
                        value=f.notes if f else "",
                        on_change=lambda: edited.update(notes=True),
                    )
                    .classes("w-full")
                    .mark("folder-own-notes")
                )
                hidden = ui.checkbox(_("Archived"), value=f.hidden if f else False).tooltip(
                    _(ARCHIVED_TIP)
                )
            influence = lang = primary = marker_fields = level_fields = None
            if f is not None:
                with ui.tab_panel("notes").classes("p-0 gap-2"):
                    from .citations import folder_citations_section

                    lang = (
                        ui.select(
                            {"": _("That of the interface"), **i18n.LANGUAGES},
                            value=reports.language(f.id) or "",
                            label=_("Language"),
                        )
                        .classes("w-64")
                        .tooltip(_("Of what is generated: references, excerpts, summary…"))
                        .mark("folder-language")
                    )
                    influence = (
                        ui.select(
                            {k: _(v) for k, v in INFLUENCE_MODES.items()},
                            value=reports.influence_mode(f.id),
                            label=_("Rayonnement excerpts"),
                        )
                        .classes("w-96")
                        .tooltip(_(INFLUENCE_TIP))
                        .mark("folder-influence-mode")
                    )
                    marker_fields = markers_section(f.id)
                    level_fields = levels_section(f.id)
                    default = source_settings.default_primary()
                    primary = (
                        ui.select(
                            {
                                None: _("Default ({source})").format(
                                    source=ADAPTERS[default].label
                                    if default in ADAPTERS
                                    else _("none")
                                ),
                                source_settings.NO_PRIMARY: _("None"),
                                **{
                                    n: a.label
                                    for n, a in ADAPTERS.items()
                                    if a.linkable and a.provides_publications
                                },
                            },
                            value=folders.primary_source(f.id),
                            label=_("Primary source"),
                        )
                        .classes("w-full")
                        .tooltip(_("Papers this source doesn't list for a person are not counted"))
                        .mark("folder-primary")
                    )
                    citations_save = folder_citations_section(f.id)

        def save() -> bool | None:
            if not name.value.strip():
                tabs.value = "general"
                return False
            try:
                d = date.fromisoformat(day.value) if day.value else None
            except ValueError:
                ui.notify(_("Invalid date (YYYY-MM-DD)"), type="warning")
                tabs.value = "general"
                return False
            if citations_save is not None and citations_save() is False:
                tabs.value = "notes"
                return False
            if influence is not None:
                reports.save_influence_mode(f.id, influence.value)
            if marker_fields is not None:
                reports.save_markers(f.id, marker_fields())
            if level_fields is not None:
                reports.save_levels(f.id, *level_fields())
            if lang is not None:
                reports.save_language(f.id, lang.value or None)
            if primary is not None:
                folders.set_primary_source(f.id, primary.value)
            fid = folders.save_folder(
                f.id if f else None,
                name.value.strip(),
                d,
                hidden.value,
                notes.value if edited["notes"] else ...,
            )
            _goto(fid)

        def delete() -> None:
            close_then(
                dlg,
                lambda: delete_dialog(
                    f.id, f.name, f.hidden, lambda: _goto(ALL), lambda: _goto(f.id)
                ),
            )

        with ui.row().classes("justify-end w-full"):
            if f is not None:
                ui.button(_("Delete"), on_click=delete).props("flat color=negative").tooltip(
                    _(
                        "Deletes the folder and its periods (people are kept); to keep them, "
                        "better archive it"
                    )
                ).mark("folder-delete")
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Save"), on_click=ok_handler(dlg, save)).mark("folder-save")
