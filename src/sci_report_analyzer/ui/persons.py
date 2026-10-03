"""Main page: the list of people."""

from __future__ import annotations

import contextlib
from datetime import date

from nicegui import background_tasks, ui
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import annotations, folders, manual, pubview
from ..db.models import Person, Publication
from ..db.session import session_scope
from ..sources import ADAPTERS
from ..sync import discover, is_syncing, start_sync
from .categories_editor import categories_dialog
from .person import purge_dialog
from .theme import STATUS_COLOUR, fmt_dt, frame, source_tag


def _people() -> list[tuple[Person, int]]:
    with session_scope() as s:
        counts = dict(
            s.execute(
                select(Publication.person_id, func.count())
                .where(Publication.missing.is_(False))
                .group_by(Publication.person_id)
            ).all()
        )
        people = list(
            s.scalars(select(Person).options(selectinload(Person.links)).order_by(Person.name))
        )
        return [(p, counts.get(p.id, 0)) for p in people]


async def _create(name: str, affiliation: str, folder_id: int | None = None) -> None:
    with session_scope() as s:
        person = Person(name=name.strip(), affiliation=affiliation.strip() or None)
        s.add(person)
        s.flush()
        pid = person.id
    if folder_id:
        folders.add_person(folder_id, pid)
    ui.notify(f"Searching sources for {name}…")

    async def run() -> None:
        errors = await discover(pid)
        msg = "Candidate profiles found — validate them in the Sources tab."
        if errors:
            msg += " Some sources failed: " + ", ".join(errors)
        with contextlib.suppress(RuntimeError):  # the page may have been left
            ui.notify(msg, type="warning" if errors else "positive", multi_line=True)

    background_tasks.create(run())
    ui.navigate.to(f"/person/{pid}?tab=sources")


LAST_FOLDER = "ui.people.folder"  # the folder shown on the People page (sticky)
ALL = 0  # "folder" value of the All people (cleanup) view


def register() -> None:
    @ui.page("/")
    def index(folder: int | None = None) -> None:
        known = {f.id: f for f in folders.folders()}
        if folder is None:
            folder = annotations.ui_state(LAST_FOLDER)
        if folder not in known:
            folder = ALL
        annotations.save_ui_state(LAST_FOLDER, folder)
        current = known.get(folder)
        title = current.name if current else "All people"
        with frame(title):
            _header(current, known)
            if current is None:
                _cleanup_view(known)
            else:
                _folder_view(current)
            ui.timer(2.0, lambda: _refresh_if_running())


def _refresh_if_running() -> None:
    if _any_running():
        _folder_cards.refresh()


def _goto(folder_id: int) -> None:
    ui.navigate.to(f"/?folder={folder_id}")


def _header(current: folders.FolderView | None, known: dict[int, folders.FolderView]) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.icon("folder" if current else "groups", size="md", color="amber-8" if current else "")
        ui.label(current.name if current else "All people").classes("text-2xl").mark("people-title")
        if current and current.date:
            ui.label(current.date.isoformat()).classes("text-grey")
        if current and current.hidden:
            ui.badge("hidden", color="grey")
        options = {
            f.id: f.name + (" (hidden)" if f.hidden else "")
            for f in sorted(known.values(), key=lambda f: f.hidden)
        }
        ui.select(
            {**options, ALL: "All people (cleanup)"},
            value=current.id if current else ALL,
            label="folder",
            on_change=lambda e: _goto(e.value),
        ).props("dense outlined options-dense").classes("w-64").mark("folder-select")
        if current:
            refresh = ui.navigate.reload
            ui.button(icon="edit", on_click=lambda: folder_dialog(current)).props(
                "flat round dense"
            ).tooltip("Edit the folder")
            ui.button(icon="category", on_click=lambda: categories_dialog(current.id)).props(
                "flat round dense"
            ).tooltip("Categories (where excerpts of the people's documents are filed)").mark(
                "folder-categories"
            )
            ui.button(
                icon="visibility" if current.hidden else "visibility_off",
                on_click=lambda: (folders.set_hidden(current.id, not current.hidden), refresh()),
            ).props("flat round dense").tooltip("Show" if current.hidden else "Hide").mark(
                f"hide-folder-{current.id}"
            )
            ui.button(
                icon="delete_sweep",
                on_click=lambda: purge_dialog(
                    [m.person_id for m in current.members],
                    f"everyone in “{current.name}”",
                    refresh,
                ),
            ).props("flat round dense color=negative").tooltip(
                "Purge: remove all papers of the folder's people and re-sync"
            ).mark("purge-folder")
        ui.space()
        ui.button(
            "New folder",
            icon="create_new_folder",
            on_click=lambda: folder_dialog(None),
        ).props("flat")
        ui.button(
            "Sync out-of-date",
            icon="sync",
            on_click=lambda: _sync([m.person_id for m in current.members] if current else None),
        ).props("flat")
        ui.button("Add person", icon="person_add", on_click=lambda: _add_dialog(current)).mark(
            "add-person"
        )


def _sync(person_ids: list[int] | None) -> None:
    started = 0
    for person, _ in _people():
        if person_ids is not None and person.id not in person_ids:
            continue
        if any(ln.is_stale or ln.sync_state == "error" for ln in person.links):
            started += start_sync(person.id, only_stale=True)
    ui.notify(f"Started {started} sync(s)" if started else "Everything is up to date")


def _add_dialog(current: folders.FolderView | None) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Add a person" + (f" to “{current.name}”" if current else "")).classes("text-lg")
        name = ui.input("Full name").classes("w-full").props("autofocus")
        aff = ui.input("Affiliation (optional, helps matching)").classes("w-full")

        async def ok() -> None:
            if not name.value.strip():
                return
            # Before closing: a closed dialog is deleted, with the context its UI calls need.
            await _create(name.value, aff.value, current.id if current else None)
            dialog.close()

        name.on("keydown.enter", ok)
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Add & search sources", on_click=ok)
    dialog.on_value_change(lambda e: None if e.value else dialog.delete())
    dialog.open()


# ---- a folder ------------------------------------------------------------------------------


def _folder_view(f: folders.FolderView) -> None:
    if f.notes:
        ui.label(f.notes).classes("text-sm text-grey")
    _folder_cards(f.id)
    members = {m.person_id for m in f.members}
    others = {pid: n for pid, n in folders.all_people().items() if pid not in members}
    if others:
        with ui.row().classes("items-center gap-2"):
            pick = (
                ui.select(others, multiple=True, with_input=True, label="Add people")
                .props("dense outlined use-chips")
                .classes("w-80")
                .mark(f"folder-add-{f.id}")
            )

            def add() -> None:
                for pid in pick.value or []:
                    folders.add_person(f.id, pid)
                ui.navigate.reload()

            ui.button("Add", icon="person_add", on_click=add).props("dense").mark(
                f"folder-add-btn-{f.id}"
            )
    ui.label(
        "Each person has their own period in the folder (set it in their Periods tab)."
    ).classes("text-xs text-grey")


@ui.refreshable
def _folder_cards(folder_id: int, only: tuple[str, ...] = ()) -> None:
    """The folder's people; with `only`, those having all these tags."""
    f = next((x for x in folders.folders() if x.id == folder_id), None)
    if f is None:
        return
    if not f.members:
        ui.label("No one in this folder yet.").classes("text-grey")
        return
    tags = folders.tags_of(f)
    only = tuple(t for t in only if t in tags)
    if tags:
        ui.select(
            tags,
            value=list(only),
            multiple=True,
            label="Only people tagged",
            on_change=lambda e: _folder_cards.refresh(folder_id, tuple(e.value or ())),
        ).props("dense outlined use-chips clearable").classes("w-80").mark(
            f"folder-tag-filter-{folder_id}"
        )
    people = {p.id: (p, n) for p, n in _people()}
    years = pubview.problem_years(m.person_id for m in f.members)
    problems = {
        m.person_id: pubview.count_in_period(years[m.person_id], m.start_year, m.end_year)
        for m in f.members
        if m.person_id in years
    }
    if total := sum(problems.values()):
        ui.label(f"{total} paper(s) with problems in the people's periods").classes(
            "text-sm text-orange-9"
        ).tooltip(_PROBLEMS_TIP).mark("folder-problems")
    with ui.grid(columns="repeat(auto-fill, minmax(340px, 1fr))").classes("w-full"):
        for m in f.members:
            if m.person_id in people and set(only) <= set(m.tags):
                _card(
                    *people[m.person_id],
                    member=m,
                    folder_id=f.id,
                    problems=problems.get(m.person_id),
                    folder_tags=tags,
                )


_PROBLEMS_TIP = (
    "Papers needing a look (warning icon in the list), as of the last time the person's "
    "papers were shown"
)


def _period_editor(member: folders.Member) -> None:
    """The member's period in the folder; click to edit it in place."""
    text = (
        f"period {member.start_year or '…'}–{member.end_year or '…'}"
        if member.start_year or member.end_year
        else "no period set"
    )
    with (
        ui.label(text)
        .classes("text-sm text-primary cursor-pointer border-b border-dashed")
        .on("click.stop", lambda: None)
        .tooltip("Click to set the period")
        .mark(f"period-{member.period_id}"),
        ui.menu() as menu,
        ui.row().classes("items-center gap-2 p-2 no-wrap"),
    ):
        start = (
            ui.number("from", value=member.start_year, format="%d")
            .props("dense outlined")
            .classes("w-24")
            .mark(f"period-start-{member.period_id}")
        )
        end = (
            ui.number("to", value=member.end_year, format="%d")
            .props("dense outlined")
            .classes("w-24")
            .mark(f"period-end-{member.period_id}")
        )

        def save() -> None:
            folders.set_period(
                member.period_id,
                int(start.value) if start.value else None,
                int(end.value) if end.value else None,
            )
            menu.close()
            _folder_cards.refresh()

        for el in (start, end):
            el.on("keydown.enter", save)
        ui.button(icon="check", on_click=save).props("flat round dense").mark(
            f"period-save-{member.period_id}"
        )


# ---- all people (cleanup) ------------------------------------------------------------------


def _cleanup_view(known: dict[int, folders.FolderView]) -> None:
    ui.label(
        "Every person, with the folders they are in: select people to delete them or to put "
        "them in a folder. Pick a folder above to work with it."
    ).classes("text-sm text-grey")
    rows = folders.people_rows()
    columns = [
        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
        {"name": "affiliation", "label": "Affiliation", "field": "affiliation", "align": "left"},
        {"name": "n_folders", "label": "Folders", "field": "n_folders", "sortable": True},
        {"name": "folders", "label": "In", "field": "folders", "align": "left"},
        {"name": "pubs", "label": "Papers", "field": "pubs", "sortable": True},
        {"name": "sources", "label": "Sources", "field": "sources", "sortable": True},
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
        filt = ui.input(placeholder="filter").props("dense outlined clearable").classes("w-64")
        only_orphans = ui.switch("in no folder only").mark("orphans-only")
        ui.space()
        target = (
            ui.select({f.id: f.name for f in known.values()}, label="folder")
            .props("dense outlined")
            .classes("w-56")
        )
        add_btn = ui.button("Add to folder", icon="drive_file_move").props("dense flat")
        del_btn = ui.button("Delete", icon="delete").props("dense flat color=negative")
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
            ui.notify("Select people and a folder", type="warning")
            return
        for pid in selected():
            folders.add_person(target.value, pid)
        ui.navigate.reload()

    def delete() -> None:
        ids = selected()
        if not ids:
            ui.notify("Select people first", type="warning")
            return
        with ui.dialog() as dlg, ui.card():
            ui.label(
                f"Delete {len(ids)} person(s) and all their data (sources, publications, "
                "flags, stars, periods)?"
            )
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=dlg.close).props("flat")

                def confirm() -> None:
                    folders.delete_people(ids)
                    ui.navigate.reload()
                    dlg.close()

                ui.button("Delete", color="negative", on_click=confirm).mark("confirm-delete")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    add_btn.on_click(add)
    del_btn.on_click(delete)


def _any_running() -> bool:
    return any(is_syncing(p.id) for p, _ in _people())


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
            ui.label(f"{n_pubs} publications").classes("text-grey text-sm")
        if person.affiliation:
            ui.label(person.affiliation).classes("text-sm text-grey -mt-2")
        with ui.row().classes("gap-1 items-center"):
            for ln in validated:
                with source_tag(
                    ln.source, url=ln.url or ADAPTERS[ln.source].profile_url(ln.external_id)
                ):
                    ui.tooltip(
                        f"{ln.display_name or ln.external_id} — {ln.status_label} "
                        f"(last sync {fmt_dt(ln.last_synced_at)})"
                    )
                colour = STATUS_COLOUR.get(ln.status_label, "grey")
                ui.icon("circle", size="8px", color=colour).classes("-ml-1 mr-1")
            if not validated:
                ui.label("no validated source").classes("text-sm text-grey")
        with ui.row().classes("gap-2"):
            if pending:
                ui.badge(f"{len(pending)} candidate(s) to review", color="orange")
            if stale:
                ui.badge(f"{len(stale)} source(s) not up to date", color="warning")
            if is_syncing(person.id):
                ui.badge("syncing…", color="info")
            if problems:
                ui.badge(f"{problems} problem(s)", color="orange-8").classes("cursor-pointer").on(
                    "click.stop", lambda: ui.navigate.to(f"{url}?problems=1")
                ).tooltip(_PROBLEMS_TIP + " — click to list them").mark(f"problems-{person.id}")
        if member is not None:
            with ui.row().classes("items-center gap-2 w-full"):
                _period_editor(member)
                if member.stars:
                    ui.label(f"★ {member.stars}").classes("text-amber-8 text-sm")
                ui.space()

                def remove() -> None:
                    folders.remove_person(folder_id, person.id)
                    ui.navigate.reload()

                ui.button(icon="close", on_click=remove).props("flat round dense size=sm").on(
                    "click.stop", lambda: None
                ).tooltip("Remove from the folder").mark(f"remove-{person.id}")
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
    ).props('dense borderless use-chips placeholder="+ tag" hide-dropdown-icon').classes(
        "w-full -mt-2"
    ).on("click.stop", lambda: None).tooltip("Tags of the person in this folder").mark(
        f"tags-{member.period_id}"
    )


# ---- folders -------------------------------------------------------------------------------


def folder_dialog(f: folders.FolderView | None) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-96"):
        ui.label("New folder" if f is None else "Edit folder").classes("text-lg")
        name = ui.input("Name", value=f.name if f else "").classes("w-full").mark("folder-name")
        with ui.input("Date", value=f.date.isoformat() if f and f.date else "").classes(
            "w-full"
        ) as day:
            with ui.menu().props("no-parent-event") as menu, ui.date().bind_value(day):
                ui.button("Close", on_click=menu.close).props("flat")
            with day.add_slot("append"):
                ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
        notes = ui.textarea("Notes", value=f.notes if f else "").classes("w-full")
        hidden = ui.checkbox("Hidden", value=f.hidden if f else False)

        def save() -> None:
            if not name.value.strip():
                return
            try:
                d = date.fromisoformat(day.value) if day.value else None
            except ValueError:
                ui.notify("Invalid date (YYYY-MM-DD)", type="warning")
                return
            fid = folders.save_folder(
                f.id if f else None, name.value.strip(), d, hidden.value, notes.value
            )
            _goto(fid)  # before closing: a closed dialog loses its client
            dlg.close()

        def delete() -> None:
            folders.delete_folder(f.id)
            _goto(ALL)
            dlg.close()

        with ui.row().classes("justify-end w-full"):
            if f is not None:
                ui.button("Delete", on_click=delete).props("flat color=negative").tooltip(
                    "Deletes the folder and its periods (people are kept)"
                )
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Save", on_click=save).mark("folder-save")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()
