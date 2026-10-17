"""The tree of folders (collapsible, with their people; expanded or not: sticky), on the
Reports page: a click selects a folder; drag them to nest and move them; each one's menu
renames it, adds a folder within it, moves it, chooses its settings (named, those of the
Parameters), and deletes it."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import annotations, folders
from ..i18n import _, ngettext
from .dialogs import actions, confirm, ok_handler, transient_dialog
from .dnd import draggable, drop_zone

TOP = 0  # "parent" value of the top level
ALL = 0  # "folder" value of the All people (cleanup) view (on the Reports page)
EXPANDED = "ui.folders.expanded"  # the folders shown expanded in the tree (sticky)


def expand_to(folder_id: int) -> None:
    """Show a folder expanded, and the folders it is in (sticky)."""
    parents = {n.id: n.parent_id for n in folders.tree()}
    shown = set(annotations.ui_state(EXPANDED) or [])
    f: int | None = folder_id
    while f in parents and f not in shown:
        shown.add(f)
        f = parents[f]
    annotations.save_ui_state(EXPANDED, sorted(shown))


def categories_added(n: int) -> None:
    """Says how many categories were added to settings, for the excerpts filed in them."""
    if n:
        ui.notify(
            ngettext(
                "{n} category added there, for the excerpts filed in it",
                "{n} categories added there, for the excerpts filed in them",
                n,
            ).format(n=n)
        )


def folders_tree(
    current: int,
    select: Callable[[int], None],
    changed: Callable[[], None],
    people: Callable[[folders.FolderNode, int], None] | None = None,
    counts: dict[int, int] | None = None,
    person_dropped: Callable[[int, folders.FolderNode], None] | None = None,
) -> None:
    """The folders as a tree, ``current`` (a folder id, or ALL) shown selected; ``select``:
    called with the folder clicked (ALL: the All people view); ``changed``: once something
    changed (the tree is shown again by it); ``people``: shows an expanded folder's people
    (after the folders within it), at a depth; ``counts``: the number of people of each
    folder (shown once collapsed); ``person_dropped``: a person (of ``people``: their
    period's id, see dnd "person") dropped onto a folder."""
    expanded: set[int] = set(annotations.ui_state(EXPANDED) or [])
    counts = counts or {}

    def move(folder_id: int, parent: int | None, then: Callable[[], None] | None = None) -> None:
        """``then``: before the tree is shown again (e.g. closing the dialog)."""
        try:
            folders.set_parent(folder_id, parent or None)
        except ValueError as e:
            ui.notify(str(e), type="warning")
        if then:
            then()
        changed()

    def dropped(folder_id: int, target: folders.FolderNode | None, where: str) -> None:
        """Onto the middle of a folder: within it; its top or bottom: next to it (in its
        parent); the bottom zone (no target): at the top level."""
        parent = None
        if target is not None:
            parent = target.id if where == "inside" else target.parent_id
        if folder_id != (target.id if target else None):
            move(folder_id, parent)

    def show(ids: set[int]) -> None:
        expanded.clear()
        expanded.update(ids)
        annotations.save_ui_state(EXPANDED, sorted(expanded))
        listing.refresh()

    def toggle(folder_id: int) -> None:
        show(expanded ^ {folder_id})

    def subfolder(parent: int) -> None:
        select(folders.save_folder(None, _("New folder"), parent_id=parent))  # (expanded)

    def deleted(folder_id: int) -> None:
        if folder_id == current:
            select(ALL)
        else:
            changed()

    nodes = folders.tree()
    host = ui.element("div")  # (the dialogs: not in a menu, gone once closed)
    with ui.row().classes("w-full items-center no-wrap gap-1"):
        ui.icon("account_tree", color="grey-8")
        ui.label(_("Folders")).classes("font-medium grow")
        if nodes:
            ui.button(icon="unfold_more", on_click=lambda: show({n.id for n in nodes})).props(
                "flat dense round size=sm"
            ).tooltip(_("Expand all")).mark("folders-expand-all")
            ui.button(icon="unfold_less", on_click=lambda: show(set())).props(
                "flat dense round size=sm"
            ).tooltip(_("Collapse all")).mark("folders-collapse-all")
        ui.icon("help_outline", color="grey").tooltip(
            _(
                "Click a folder to show it. Drag a folder onto another one to put it within it "
                "(onto its top or bottom: next to it); a person onto a folder to move them "
                "there. A folder uses the settings chosen in its "
                "menu (its categories, the citations in its notes…; named, in the Parameters), "
                "else the default ones (those not: marked)."
            )
        )
    _entry(
        "groups",
        _("All people (cleanup)"),
        current == ALL,
        lambda: select(ALL),
    ).mark("folder-all")

    def node(n: folders.FolderNode) -> None:
        """A folder's row, then (expanded) the folders within it and its people."""
        opened = n.id in expanded
        row = (
            ui.row()
            .classes(
                "w-full items-center no-wrap gap-0 rounded cursor-pointer vr-row"
                + (" bg-amber-2" if n.id == current else "")
            )
            .style(f"padding-left:{1.1 * n.depth}rem")
            .mark(f"folder-node-{n.id}")
        )
        draggable(row, "folder", n.id)
        drop_zone(
            row,
            "folder",
            lambda i, w, n=n: dropped(i, n, w),
            middle="inside",
            others={"person": lambda i, n=n: person_dropped(i, n)} if person_dropped else None,
        )
        row.on(
            "click",
            lambda n=n: select(n.id),
            js_handler="(e) => { if (!e.target.closest('button, .q-btn')) emit(); }",
        )
        with row:
            if n.children or (people and counts.get(n.id)):
                ui.button(
                    icon="expand_more" if opened else "chevron_right",
                    on_click=lambda n=n: toggle(n.id),
                ).props("flat dense round size=sm").tooltip(
                    _("Hide its folders and people") if opened else _("Show its folders and people")
                ).mark(f"folder-toggle-{n.id}")
            else:
                ui.element("div").classes("w-6 shrink-0")
            ui.icon(
                "folder_open" if opened else "folder", color="grey" if n.hidden else "amber-8"
            ).classes("cursor-move mr-1")
            ui.label(n.name).classes(
                "grow min-w-0 ellipsis text-sm"
                + (" text-grey" if n.hidden else "")
                + (" font-medium" if n.id == current else "")
            ).tooltip(n.path).mark(f"folder-tree-name-{n.id}")
            if people and not opened and counts.get(n.id):
                ui.badge(str(counts[n.id]), color="grey-4", text_color="grey-9").props(
                    "rounded"
                ).classes("shrink-0 mr-1").tooltip(
                    ngettext("{n} person", "{n} people", counts[n.id]).format(n=counts[n.id])
                ).mark(f"folder-count-{n.id}")
            if not n.default_settings:
                ui.icon("tune", size="xs", color="grey-7").classes("shrink-0").tooltip(
                    _("Settings: {name}").format(name=n.settings)
                ).mark(f"folder-settings-name-{n.id}")
            with (
                ui.button(icon="more_vert")
                .props("flat dense round size=sm")
                .classes("shrink-0")
                .mark(f"folder-menu-{n.id}"),
                ui.menu().props("auto-close"),
            ):
                _menu(host, n, nodes, move, subfolder, deleted, changed)
        if opened:
            for c in n.children:
                node(c)
            if people:
                people(n, n.depth + 1)

    @ui.refreshable
    def listing() -> None:
        if not nodes:
            ui.label(_("No folder yet")).classes("text-sm text-grey")
        ids = {n.id for n in nodes}
        for n in nodes:
            if n.parent_id not in ids:
                node(n)
        if nodes:  # (dropped here: at the top level)
            end_zone = (
                ui.label(_("Drop here: at the top level"))
                .classes("w-full text-xs text-grey text-center rounded p-1 mt-1")
                .style("border: 1px dashed #bbb")
                .mark("folder-drop-top")
            )
            drop_zone(end_zone, "folder", lambda i, w: dropped(i, None, w), zoned=False)

    with ui.column().classes("w-full gap-0"):
        listing()


def _entry(icon: str, text: str, selected: bool, on_click: Callable[[], None]) -> ui.row:
    """A row of the tree that is not a folder (e.g. All people)."""
    with (
        ui.row()
        .classes(
            "w-full items-center no-wrap gap-1 rounded cursor-pointer vr-row px-1"
            + (" bg-amber-2 font-medium" if selected else "")
        )
        .on("click", on_click) as row
    ):
        ui.icon(icon, color="grey-8").classes("ml-6")
        ui.label(text).classes("text-sm grow")
    return row


def _menu(
    host: ui.element,
    n: folders.FolderNode,
    nodes: list[folders.FolderNode],
    move: Callable[..., None],
    subfolder: Callable[[int], None],
    deleted: Callable[[int], None],
    changed: Callable[[], None],
) -> None:
    """A folder's menu (in the tree; closed on a click, in the browser); its dialogs in
    ``host``."""

    def item(text: str, mark: str, act: Callable[..., None], *args, **kwargs) -> None:
        def run() -> None:
            with host:
                act(*args, **kwargs)

        ui.menu_item(text, run, auto_close=False).mark(f"{mark}-{n.id}")

    item(_("Rename…"), "folder-rename", _rename_dialog, n, changed)
    item(_("Add a folder within it"), "folder-sub", subfolder, n.id)
    item(_("Move to…"), "folder-move", _move_dialog, n, nodes, move)
    item(_("Its settings…"), "folder-settings", _settings_dialog, n, changed)
    ui.separator()
    item(
        _("Delete…"),
        "folder-delete",
        delete_dialog,
        n.id,
        n.name,
        n.hidden,
        lambda: deleted(n.id),
        changed,
    )


def delete_dialog(
    folder_id: int,
    name: str,
    hidden: bool,
    deleted: Callable[[], None],
    archived: Callable[[], None],
) -> None:
    """Delete a folder: an empty one once confirmed; else once its name is typed, archiving
    it offered instead (``deleted``, ``archived``: once done)."""
    what = folders.contents(folder_id)

    def delete() -> None:
        folders.delete_folder(folder_id)
        deleted()

    if what.empty:
        confirm(
            _("Delete the empty folder “{folder}”?").format(folder=name),
            _("Delete"),
            delete,
            mark="folder-delete-ok",
        )
        return
    lost = []
    if what.people:
        lost.append(
            ngettext(
                "{n} person, with their period in it (paper tags, notes, documents, report)",
                "{n} people, with their periods in it (paper tags, notes, documents, reports)",
                what.people,
            ).format(n=what.people)
        )
    if what.notes:
        lost.append(_("its notes"))
    with transient_dialog(
        _("Delete the folder “{folder}”").format(folder=name), width="w-[32rem] max-w-full"
    ) as (dlg, _card):
        if lost:
            ui.label(_("Deleted with it (the people themselves are kept):")).classes("text-sm")
            with ui.column().classes("gap-0 pl-4"):
                for x in lost:
                    ui.label(f"• {x}").classes("text-sm")
        if what.folders:
            ui.label(
                ngettext(
                    "The folder within it goes up a level.",
                    "The {n} folders within it go up a level.",
                    what.folders,
                ).format(n=what.folders)
            ).classes("text-sm")
        if not hidden:
            ui.label(
                _(
                    "To put it out of the way while keeping everything, better archive it "
                    "(greyed, its periods no longer offered; unarchived at any time)."
                )
            ).classes("text-sm text-grey")
        typed = (
            ui.input(_("Type “{folder}” to confirm").format(folder=name))
            .classes("w-full")
            .props("autofocus")
            .mark("folder-delete-name")
        )

        def ok() -> bool | None:
            if typed.value.strip() != name.strip():
                return False
            delete()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            if not hidden:
                ui.button(
                    _("Archive instead"),
                    icon="visibility_off",
                    on_click=ok_handler(
                        dlg, lambda: (folders.set_hidden(folder_id, True), archived())
                    ),
                ).props("outline").mark("folder-archive-instead")
            go = ui.button(_("Delete"), on_click=ok_handler(dlg, ok), color="negative").mark(
                "folder-delete-ok"
            )
            go.bind_enabled_from(typed, "value", lambda v: (v or "").strip() == name.strip())


def _rename_dialog(n: folders.FolderNode, changed: Callable[[], None]) -> None:
    with transient_dialog(_("Rename the folder"), width="w-96") as (dlg, _card):
        name = ui.input(_("Name"), value=n.name).classes("w-full").props("autofocus")
        name.mark("folder-rename-input")

        def ok() -> None:
            folders.rename(n.id, name.value)
            changed()

        name.on("keydown.enter", ok_handler(dlg, ok))
        actions(dlg, _("Rename"), ok, mark="folder-rename-ok")


def _move_dialog(
    n: folders.FolderNode,
    nodes: list[folders.FolderNode],
    move: Callable[..., None],
) -> None:
    """Where a folder is: the top level, or another folder (not within itself)."""
    within = set(folders.within(n.id))
    with transient_dialog(_("Move “{folder}” to").format(folder=n.name), width="w-96") as (
        dlg,
        _card,
    ):

        def chosen(e) -> None:
            move(n.id, e.value, dlg.close)

        ui.select(
            {TOP: _("(top level)"), **{x.id: x.path for x in nodes if x.id not in within}},
            value=n.parent_id or TOP,
            label=_("In"),
            on_change=chosen,
        ).props("dense outlined options-dense").classes("w-full").mark(f"folder-parent-{n.id}")
        actions(dlg)


def _settings_dialog(n: folders.FolderNode, changed: Callable[[], None]) -> None:
    """The settings a folder uses (named, those of the Parameters): its people's excerpts go
    to the categories of the same names there (added if missing)."""
    from .folder_settings import settings_choice

    chosen = next((f.settings_id for f in folders.folders() if f.id == n.id), None)
    with transient_dialog(_("Settings of “{folder}”").format(folder=n.path), width="w-[32rem]") as (
        dlg,
        _card,
    ):
        ui.label(
            _(
                "Its people's excerpts go to the categories of the same names in the settings "
                "chosen (added if missing), their starred papers to their star lists."
            )
        ).classes("text-sm text-grey")
        choice = settings_choice(chosen)

        def ok() -> None:
            categories_added(folders.use_settings(n.id, choice.value))
            changed()

        actions(dlg, _("Use them"), ok, mark="folder-settings-apply")
