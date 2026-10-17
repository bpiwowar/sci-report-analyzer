"""Main page (Reports): the folders and their people, or all the people."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial
from html import escape

from fastapi.responses import RedirectResponse
from nicegui import ui

from .. import (
    annotations,
    folders,
    i18n,
    llm,
    manual,
    persons,
    pubview,
    reports,
    source_settings,
    urls,
)
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
from .dialogs import close_then, ok_handler, transient_dialog
from .folder_notes import NOTES_TIP, notes_url
from .folders_editor import ALL, delete_dialog, expand_to, folders_tree
from .person import (
    candidate_details,
    deletion_dialog,
    link_url,
    move_to_folder_dialog,
    orcid_badge,
    purge_dialog,
    remove_from_folder_dialog,
)
from .theme import STATUS_COLOUR, fmt_dt, frame, int_or_none, refresh_alive, source_tag

LAST_FOLDER = "ui.people.folder"  # the folder shown on the Reports page (sticky)
LAST_IN_TREE = "ui.people.tree"  # the last folder selected in the tree (sticky)


def register() -> None:
    @ui.page("/")
    def index(folder: str | None = None) -> RedirectResponse | None:
        """The folder chosen (``folder``: its path, see urls.folder_url; the former ids:
        redirected), else the last one."""
        known = {f.id: f for f in folders.folders()}
        last = annotations.ui_state(LAST_FOLDER)
        if folder is not None:
            chosen = urls.resolve_folder(folder)
            if chosen in known and (url := urls.folder_url(chosen)) != f"/?folder={folder}":
                return RedirectResponse(url)
        folder = last if folder is None else chosen
        if folder not in known:
            folder = ALL
        if folder != last:
            annotations.save_ui_state(LAST_FOLDER, folder)
            if folder != ALL:  # (newly selected: shown expanded, where it is)
                annotations.save_ui_state(LAST_IN_TREE, folder)
                expand_to(folder)
        title = known[folder].name if folder in known else _("All people")
        with frame(title):

            @ui.refreshable
            def body() -> None:  # (again once the tree changed: names, places, settings)
                _reports(folder, body.refresh)

            body()
            ui.timer(2.0, lambda: _refresh_if_running())
        return None


def _reports(folder: int, changed) -> None:
    """The folder selected (its actions, in a bar staying on top) and the tree of folders with
    their people; else All people."""
    known = {f.id: f for f in folders.folders()}
    current = known.get(folder)
    view = {"only": ()}  # (the selected folder's people: those having all these tags)
    with (
        ui.column()
        .classes("w-full gap-1 sticky top-12 z-10 py-2 vr-toolbar")
        .mark("folder-toolbar")
    ):
        _header(current, known)
        if current is not None:
            if current.notes:
                ui.label(current.notes).classes("text-sm text-grey")
            _tag_filter(current, view)
    if current is None:
        _cleanup_view(known)
    else:
        _tree(current.id, changed, view)


def _refresh_if_running() -> None:
    if any_running():
        refresh_alive(_tree)


def _goto(folder_id: int) -> None:
    ui.navigate.to(urls.folder_url(folder_id))


def _header(current: folders.FolderView | None, known: dict[int, folders.FolderView]) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.icon("folder" if current else "groups", size="md", color="amber-8" if current else "")
        ui.label(current.path if current else _("All people")).classes("text-2xl").mark(
            "people-title"
        )
        if current is None and folders.folders():
            back = annotations.ui_state(LAST_IN_TREE)
            ui.button(
                _("Folders"),
                icon="account_tree",
                on_click=lambda: _goto(back if back in known else next(iter(known))),
            ).props("flat dense no-caps").tooltip(_("Back to the folders and their people")).mark(
                "folders-back"
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
        ui.navigate.to(urls.person_url(pid, tab=None if chosen else "sources"))
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
    ui.navigate.to(urls.person_url(pid, tab="sources"))
    dialog.close()


# ---- a folder: the tree, with the people --------------------------------------------------


def _tag_filter(f: folders.FolderView, view: dict) -> None:
    if tags := folders.tags_of(f):
        ui.select(
            tags,
            value=[],
            multiple=True,
            label=_("Only people tagged"),
            on_change=lambda e: (view.update(only=tuple(e.value or ())), refresh_alive(_tree)),
        ).props("dense outlined use-chips clearable").classes("w-80").mark(
            f"folder-tag-filter-{f.id}"
        )


@ui.refreshable
def _tree(current: int, changed: Callable[[], None], view: dict) -> None:
    """The folders, with the people of those expanded (of the ``current`` one, those having
    all the tags ``view["only"]``)."""
    known = {f.id: f for f in folders.folders()}
    everyone = {p.id: (p, n) for p, n in persons.people_with_counts()}
    members = [m for f in known.values() for m in f.members]
    years = pubview.problem_years({m.person_id for m in members})
    problems = {
        m.period_id: pubview.count_in_period(years[m.person_id], m.start_year, m.end_year)
        for m in members
        if m.person_id in years
    }

    def people(n: folders.FolderNode, depth: int) -> None:
        f = known[n.id]
        indent = f"padding-left:{1.1 * depth + 1.5}rem"
        if not f.members and (f.id == current or not n.children):
            ui.label(_("No one in this folder yet.")).classes("text-sm text-grey").style(indent)
        tags = folders.tags_of(f)
        only = set(view["only"]) if f.id == current else set()
        if total := sum(problems.get(m.period_id, 0) for m in f.members):
            ui.label(
                ngettext(
                    "{n} paper with problems in the people's periods",
                    "{n} papers with problems in the people's periods",
                    total,
                ).format(n=total)
            ).classes("text-xs text-orange-9").style(indent).tooltip(_(_PROBLEMS_TIP)).mark(
                "folder-problems"
            )
        for m in f.members:
            if m.person_id in everyone and only <= set(m.tags):
                _person_row(
                    *everyone[m.person_id],
                    member=m,
                    folder_id=f.id,
                    problems=problems.get(m.period_id),
                    folder_tags=tags,
                    depth=depth,
                )
        if f.id == current:
            _add_people(f, indent)

    folders_tree(
        current,
        _goto,
        changed,
        people=people,
        counts={f.id: len(f.members) for f in known.values()},
    )
    ui.label(_("Each person has their own period in the folder (click it to set it).")).classes(
        "text-xs text-grey"
    )


def _add_people(f: folders.FolderView, indent: str) -> None:
    """People already known, added to the folder."""
    members = {m.person_id for m in f.members}
    others = {pid: n for pid, n in folders.all_people().items() if pid not in members}
    if not others:
        return
    with ui.row().classes("items-center gap-2 py-1").style(indent):
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

        ui.button(_("Add"), icon="person_add", on_click=add).props("dense flat").mark(
            f"folder-add-btn-{f.id}"
        )


_PROBLEMS_TIP = N_(
    "Papers needing a look (warning icon in the list), as of the last time the person's "
    "papers were shown"
)


def _period_editor(member: folders.Member) -> None:
    """The member's period in the folder; click to edit it in place."""
    text = (
        _("{start}–{end}").format(start=member.start_year or "…", end=member.end_year or "…")
        if member.start_year or member.end_year
        else _("no period set")
    )
    with (
        ui.label(text)
        .classes(
            "text-xs text-primary cursor-pointer border-b border-dashed whitespace-nowrap shrink-0"
        )
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
            refresh_alive(_tree)

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
        lambda e: ui.navigate.to(urls.person_url(e.args[1]["id"])),
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

        deletion_dialog(
            ngettext("Delete {n} person?", "Delete {n} people?", len(ids)).format(n=len(ids)),
            folders.deletion_counts(person_ids=ids),
            ok,
            word=_("DELETE"),
        )

    add_btn.on_click(add)
    del_btn.on_click(delete)


def _person_row(
    person: Person,
    n_pubs: int,
    *,
    member: folders.Member,
    folder_id: int,
    problems: int | None,
    folder_tags: list[str],
    depth: int,
) -> None:
    """A person in the tree (one line; two on narrow screens): their name, period, stars and
    tags in the folder, their sources, what needs a look, and their actions."""
    validated = [
        ln for ln in person.links if ln.status == "validated" and not manual.is_hal_document(ln)
    ]
    pending = [ln for ln in person.links if ln.status == "candidate"]
    stale = [ln for ln in validated if ln.is_stale or ln.sync_state == "error"]
    url = partial(urls.person_url, person.id, member.period_id)
    with (
        ui.row()
        .classes("w-full items-center gap-x-3 gap-y-0 rounded cursor-pointer vr-row py-0.5 pr-1")
        .style(f"padding-left:{1.1 * depth + 1.5}rem")
        .on(
            "click",
            lambda: ui.navigate.to(url()),
            js_handler=(
                "(e) => { if (!e.target.closest('a, button, .q-btn, .q-field, .q-menu')) emit(); }"
            ),
        )
        .mark(f"person-row-{member.period_id}")
    ):
        with ui.row().classes("items-center gap-x-2 gap-y-0 min-w-0 grow"):
            ui.icon("person", color="grey-7").classes("shrink-0")
            ui.label(person.name).classes("text-sm font-medium whitespace-nowrap")
            if person.affiliation:
                ui.label(person.affiliation).classes(
                    "text-xs text-grey ellipsis min-w-0 max-w-xs"
                ).tooltip(person.affiliation)
            _period_editor(member)
            if member.stars:
                ui.label(f"★ {member.stars}").classes(
                    "text-amber-8 text-xs whitespace-nowrap"
                ).tooltip(_("Starred papers"))
            _tags_editor(member, folder_tags)
        with ui.row().classes("items-center gap-1 no-wrap ml-auto"):
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
                ui.label(_("no validated source")).classes("text-xs text-grey")
            elif synced := persons.last_sync(person):
                ui.icon("schedule", size="xs", color="grey").tooltip(
                    _("last sync {when}").format(when=fmt_dt(synced))
                ).mark(f"last-sync-{person.id}")
            ui.label(str(n_pubs)).classes("text-xs text-grey ml-1").tooltip(
                ngettext("{n} publication", "{n} publications", n_pubs).format(n=n_pubs)
            )
            if pending:
                ui.badge(str(len(pending)), color="orange").tooltip(
                    ngettext(
                        "{n} candidate to review", "{n} candidates to review", len(pending)
                    ).format(n=len(pending))
                )
            if stale:
                ui.icon("sync_problem", size="xs", color="warning").tooltip(
                    ngettext(
                        "{n} source not up to date", "{n} sources not up to date", len(stale)
                    ).format(n=len(stale))
                )
            if is_syncing(person.id):
                ui.spinner(size="xs").tooltip(_("syncing…"))
            if problems:
                ui.badge(
                    ngettext("{n} problem", "{n} problems", problems).format(n=problems),
                    color="orange-8",
                ).classes("cursor-pointer").on(
                    "click.stop", lambda: ui.navigate.to(url(problems=1))
                ).tooltip(_("{tip} — click to list them").format(tip=_(_PROBLEMS_TIP))).mark(
                    f"problems-{person.id}"
                )
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
            ).props("flat round dense size=sm").tooltip(_("Move to another folder")).mark(
                f"move-{person.id}"
            )
            ui.button(
                icon="close",
                on_click=lambda: remove_from_folder_dialog(
                    folder_id, person.id, ui.navigate.reload
                ),
            ).props("flat round dense size=sm").tooltip(_("Remove from the folder")).mark(
                f"remove-{person.id}"
            )


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
        f'dense borderless use-chips options-dense placeholder="{escape(_("+ tag"), quote=True)}" '
        "hide-dropdown-icon"
    ).classes("min-w-16 shrink").tooltip(_("Tags of the person in this folder")).mark(
        f"tags-{member.period_id}"
    )


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


def models_section(folder_id: int) -> Callable[[], tuple[str, str]]:
    """The models the folder uses instead of the default ones (Settings → Local LLM): the LLM
    and the embedding model (blank: the default; those of the servers proposed); returns
    their values."""
    st = llm.settings()
    model, embed = llm.folder_models(folder_id)
    ui.label(_("Models")).classes("text-base mt-2")
    with ui.row().classes("w-full gap-2 no-wrap"):
        inputs = [
            ui.select(
                [value] if value else [],
                value=value or None,
                label=label,
                with_input=True,
                new_value_mode="add-unique",
                clearable=True,
            )
            .classes("grow")
            .tooltip(tip)
            .mark(mark)
            for value, label, tip, mark in (
                (
                    model,
                    _("LLM (default: {model})").format(model=st.model or _("none")),
                    _("For the features using the local LLM"),
                    "folder-llm-model",
                ),
                (
                    embed,
                    _("Embedding model (default: {model})").format(
                        model=st.embed_model or _("none")
                    ),
                    _("E.g. to suggest an excerpt's category (trained again if changed)"),
                    "folder-embed-model",
                ),
            )
        ]

    async def load() -> None:
        for select, listing in (
            (inputs[0], llm.list_models(st.url)),
            (
                inputs[1],
                llm.list_embed_models(
                    st.embed_api, st.embed_url if st.embed_api == "openai" else st.url
                ),
            ),
        ):
            try:
                found = await listing
            except Exception:
                continue  # (the server is not running: typed)
            value = select.value
            select.set_options(sorted({*found, *([value] if value else [])}), value=value)

    if st.enabled:
        ui.timer(0.1, load, once=True)
    return lambda: (inputs[0].value or "", inputs[1].value or "")


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
            influence = lang = primary = marker_fields = level_fields = model_fields = None
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
                    model_fields = models_section(f.id)

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
            if model_fields is not None:
                llm.save_folder_models(f.id, *model_fields())
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
