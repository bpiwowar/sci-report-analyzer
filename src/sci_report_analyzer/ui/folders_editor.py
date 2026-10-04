"""The tree of folders (collapsible), on the Reports page: a click shows a folder; drag them
to nest and move them; each one's menu renames it, adds a folder within it, chooses its
settings (its parent's, or its own) and moves or copies them to another folder."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import annotations, folders
from ..i18n import _, ngettext
from .categories_editor import _DRAG, _DROP, _LEAVE, _OVER

TOP = 0  # "parent" value of the top level
ALL = 0  # "folder" value of the All people (cleanup) view (on the Reports page)
COLLAPSED = "ui.folders.collapsed"  # the folders shown collapsed in the tree (sticky)


def _added(n: int) -> None:
    if n:
        ui.notify(
            ngettext(
                "{n} category added there, for the excerpts filed in it",
                "{n} categories added there, for the excerpts filed in them",
                n,
            ).format(n=n)
        )


def folders_tree(current: int, select: Callable[[int], None], changed: Callable[[], None]) -> None:
    """The folders as a tree, ``current`` (a folder id, or ALL) shown selected; ``select``:
    called with the folder clicked (ALL: the All people view); ``changed``: once something
    changed (the tree is shown again by it)."""
    collapsed: set[int] = set(annotations.ui_state(COLLAPSED) or [])

    def move(folder_id: int, parent: int | None, then: Callable[[], None] | None = None) -> None:
        """``then``: before the tree is shown again (e.g. closing the dialog)."""
        try:
            _added(folders.set_parent(folder_id, parent or None))
        except ValueError as e:
            ui.notify(str(e), type="warning")
        if then:
            then()
        changed()

    def dropped(args, target: folders.FolderNode | None) -> None:
        """Onto the middle of a folder: within it; its top or bottom: next to it (in its
        parent); the bottom zone (no target): at the top level."""
        if not isinstance(args, dict) or not args.get("id"):
            return
        parent = None
        if target is not None:
            parent = target.id if args.get("where") == "inside" else target.parent_id
        if int(args["id"]) != (target.id if target else None):
            move(int(args["id"]), parent)

    def toggle(folder_id: int) -> None:
        collapsed.symmetric_difference_update({folder_id})
        annotations.save_ui_state(COLLAPSED, sorted(collapsed))
        listing.refresh()

    def subfolder(parent: int) -> None:
        new = folders.save_folder(None, _("New folder"), parent_id=parent)
        if parent in collapsed:
            toggle(parent)
        select(new)

    nodes = folders.tree()
    host = ui.element("div")  # (the dialogs: not in a menu, gone once closed)
    with ui.row().classes("w-full items-center no-wrap gap-1"):
        ui.icon("account_tree", color="grey-8")
        ui.label(_("Folders")).classes("font-medium grow")
        ui.icon("help_outline", color="grey").tooltip(
            _(
                "Click a folder to show it. Drag a folder onto another one to put it within it "
                "(onto its top or bottom: next to it). A folder uses the settings of the folder "
                "it is in (its categories, the citations in its notes: all of them), or its own "
                "ones: see its menu."
            )
        )
    _entry(
        "groups",
        _("All people (cleanup)"),
        current == ALL,
        lambda: select(ALL),
    ).mark("folder-all")

    @ui.refreshable
    def listing() -> None:
        if not nodes:
            ui.label(_("No folder yet")).classes("text-sm text-grey")
        unseen: set[int] = set()  # (within a collapsed folder)
        for n in nodes:
            if n.parent_id in collapsed or n.parent_id in unseen:
                unseen.add(n.id)
                continue
            row = (
                ui.row()
                .classes(
                    "w-full items-center no-wrap gap-0 rounded cursor-pointer vr-row"
                    + (" bg-amber-2" if n.id == current else "")
                )
                .style(f"padding-left:{1.1 * n.depth}rem")
                .props("draggable=true")
                .mark(f"folder-node-{n.id}")
            )
            row.on("dragstart", js_handler=_DRAG % n.id)
            row.on("dragover", js_handler=_OVER)
            row.on("dragleave", js_handler=_LEAVE)
            row.on("drop", lambda e, n=n: dropped(e.args, n), js_handler=_DROP)
            row.on(
                "click",
                lambda n=n: select(n.id),
                js_handler="(e) => { if (!e.target.closest('button, .q-btn')) emit(); }",
            )
            with row:
                if n.children:
                    ui.button(
                        icon="chevron_right" if n.id in collapsed else "expand_more",
                        on_click=lambda n=n: toggle(n.id),
                    ).props("flat dense round size=sm").tooltip(
                        _("Show the folders within it")
                        if n.id in collapsed
                        else _("Hide the folders within it")
                    ).mark(f"folder-toggle-{n.id}")
                else:
                    ui.element("div").classes("w-6 shrink-0")
                ui.icon("folder", color="grey" if n.hidden else "amber-8").classes(
                    "cursor-move mr-1"
                )
                ui.label(n.name).classes(
                    "grow min-w-0 ellipsis text-sm"
                    + (" text-grey" if n.hidden else "")
                    + (" font-medium" if n.id == current else "")
                ).tooltip(n.path).mark(f"folder-tree-name-{n.id}")
                if n.parent_id is not None and n.own:
                    ui.icon("tune", size="xs", color="grey-7").classes("shrink-0").tooltip(
                        _("Its own settings")
                    ).mark(f"folder-own-{n.id}")
                if n.shared_with:
                    ui.icon("share", size="xs", color="primary").classes("shrink-0").tooltip(
                        _("Same settings as: {folders}").format(folders=", ".join(n.shared_with))
                    ).mark(f"folder-shared-{n.id}")
                with (
                    ui.button(icon="more_vert")
                    .props("flat dense round size=sm")
                    .classes("shrink-0")
                    .mark(f"folder-menu-{n.id}"),
                    ui.menu().props("auto-close"),
                ):
                    _menu(host, n, nodes, move, subfolder, changed)
        if nodes:  # (dropped here: at the top level)
            end_zone = (
                ui.label(_("Drop here: at the top level"))
                .classes("w-full text-xs text-grey text-center rounded p-1 mt-1")
                .style("border: 1px dashed #bbb")
                .mark("folder-drop-top")
            )
            end_zone.on("dragover", js_handler=_OVER)
            end_zone.on("dragleave", js_handler=_LEAVE)
            end_zone.on("drop", lambda e: dropped(e.args, None), js_handler=_DROP)

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
    ui.separator()
    if n.parent_id is not None:
        if n.own:
            item(_("Use its parent's settings"), "folder-settings", _parent_settings, n, changed)
        else:
            item(
                _("Use settings of its own (a copy)"), "folder-settings", _own_settings, n, changed
            )
        item(
            _("Move its settings to…"),
            "folder-move-settings",
            _settings_dialog,
            n,
            nodes,
            changed,
            copy=False,
        )
    if len(nodes) > 1:
        item(
            _("Copy its settings to…"),
            "folder-copy-settings",
            _settings_dialog,
            n,
            nodes,
            changed,
            copy=True,
        )


def _rename_dialog(n: folders.FolderNode, changed: Callable[[], None]) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-96"):
        ui.label(_("Rename the folder")).classes("text-lg")
        name = ui.input(_("Name"), value=n.name).classes("w-full").props("autofocus")
        name.mark("folder-rename-input")

        def ok() -> None:
            folders.rename(n.id, name.value)
            dlg.close()
            changed()

        name.on("keydown.enter", ok)
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Rename"), on_click=ok).mark("folder-rename-ok")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def _move_dialog(
    n: folders.FolderNode,
    nodes: list[folders.FolderNode],
    move: Callable[..., None],
) -> None:
    """Where a folder is: the top level, or another folder (not within itself)."""
    within = set(folders.within(n.id))
    with ui.dialog() as dlg, ui.card().classes("w-96"):
        ui.label(_("Move “{folder}” to").format(folder=n.name)).classes("text-lg")

        def chosen(e) -> None:
            move(n.id, e.value, dlg.close)

        ui.select(
            {TOP: _("(top level)"), **{x.id: x.path for x in nodes if x.id not in within}},
            value=n.parent_id or TOP,
            label=_("In"),
            on_change=chosen,
        ).props("dense outlined options-dense").classes("w-full").mark(f"folder-parent-{n.id}")
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def _own_settings(n: folders.FolderNode, changed: Callable[[], None]) -> None:
    folders.use_own_settings(n.id)
    ui.notify(_("Its own settings: a copy of its parent's"))
    changed()


def _parent_settings(n: folders.FolderNode, changed: Callable[[], None]) -> None:
    with ui.dialog() as confirm, ui.card().classes("max-w-lg"):
        ui.label(
            _(
                "Use the settings of the folder it is in? Its own ones (categories, "
                "citations in the notes) are dropped, unless other folders use them; its "
                "people's excerpts go to the categories of the same names there (added if "
                "missing)."
            )
        )

        def ok() -> None:
            _added(folders.use_parent_settings(n.id))
            confirm.close()  # (before: a closed dialog loses its client)
            changed()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=confirm.close).props("flat")
            ui.button(_("Use the parent's"), color="negative", on_click=ok).mark(
                "folder-settings-confirm"
            )
    confirm.on_value_change(lambda e: None if e.value else confirm.delete())
    confirm.open()


def _settings_dialog(
    n: folders.FolderNode,
    nodes: list[folders.FolderNode],
    changed: Callable[[], None],
    *,
    copy: bool,
) -> None:
    """Move the settings a folder uses to a folder it is in (e.g. its parent, for all the
    folders within it to share them), or copy them to any other folder; the folders whose
    settings change are named before."""
    by_id = {x.id: x for x in nodes}
    if copy:
        targets = {x.id: x.path for x in nodes if x.id != n.id}
    else:
        targets, up = {}, n.parent_id
        while up in by_id and up not in targets:
            targets[up] = by_id[up].path
            up = by_id[up].parent_id
    with ui.dialog() as dlg, ui.card().classes("w-[36rem] max-w-full"):
        ui.label(
            (
                _("Copy the settings of “{folder}”")
                if copy
                else _("Move the settings of “{folder}”")
            ).format(folder=n.path)
        ).classes("text-lg")
        ui.label(
            _(
                "The folder gets its own copy of these settings (its categories, the citations "
                "in its notes); its people's excerpts, and those of the folders using its "
                "settings, go to the categories of the same names there (added if missing)."
            )
            if copy
            else _(
                "The folder then uses these settings (its categories, the citations in its "
                "notes), as do the folders that used its own, and “{folder}” (with those "
                "between them) uses them from it. Settings no folder uses any more are dropped; "
                "excerpts go to the categories of the same names (added if missing)."
            ).format(folder=n.name)
        ).classes("text-sm text-grey")
        target = (
            ui.select(
                targets,
                value=None if copy else n.parent_id,
                label=_("To"),
                with_input=copy,
                on_change=lambda: who.refresh(),
            )
            .props("dense outlined options-dense")
            .classes("w-full")
            .mark("folder-settings-target")
        )

        @ui.refreshable
        def who() -> None:
            if target.value is None:
                return
            names = folders.affected(n.id, target.value, copy=copy)
            ui.label(
                _("The settings of these folders change: {folders}").format(
                    folders=", ".join(names)
                )
                if names
                else _("No folder's settings change")
            ).classes("text-sm").mark("folder-settings-affected")

        who()

        def ok() -> None:
            if target.value is None:
                return
            try:
                if copy:
                    n_added = folders.copy_settings_to(n.id, target.value)
                else:
                    n_added = folders.move_settings(n.id, target.value)
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return
            _added(n_added)
            dlg.close()  # (before: a closed dialog loses its client)
            changed()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Copy") if copy else _("Move"), color="negative", on_click=ok).mark(
                "folder-settings-apply"
            )
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()
