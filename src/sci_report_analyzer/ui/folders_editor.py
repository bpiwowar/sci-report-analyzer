"""Editing the tree of folders (collapsible): rename, nest and move them, and choose the
settings of each one (its parent's, or its own)."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import folders
from ..i18n import _, ngettext
from .categories_editor import _DRAG, _DROP, _LEAVE, _OVER

TOP = 0  # "parent" value of the top level
PARENT, OWN = "parent", "own"  # the settings of a folder


def folders_dialog(changed: Callable[[], None] | None = None) -> None:
    """The folders as a tree; ``changed``: called once closed, if something changed."""
    collapsed: set[int] = set()
    state = {"changed": False}

    def done() -> None:
        state["changed"] = True
        listing.refresh()

    def added(n: int) -> None:
        if n:
            ui.notify(
                ngettext(
                    "{n} category added there, for the excerpts filed in it",
                    "{n} categories added there, for the excerpts filed in them",
                    n,
                ).format(n=n)
            )

    def move(folder_id: int, parent: int | None) -> None:
        try:
            added(folders.set_parent(folder_id, parent or None))
        except ValueError as e:
            ui.notify(str(e), type="warning")
        done()

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

    def settings(n: folders.FolderNode, value: str) -> None:
        if value == OWN:
            folders.use_own_settings(n.id)
            ui.notify(_("Its own settings: a copy of its parent's"))
            done()
            return
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
                added(folders.use_parent_settings(n.id))
                state["changed"] = True
                confirm.close()  # (before: a closed dialog loses its client)

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=confirm.close).props("flat")
                ui.button(_("Use the parent's"), color="negative", on_click=ok).mark(
                    "folder-settings-confirm"
                )

        def closed(e) -> None:  # (cancelled: the choice shown again as it was)
            if not e.value:
                confirm.delete()
                listing.refresh()

        confirm.on_value_change(closed)
        confirm.open()

    def toggle(folder_id: int) -> None:
        collapsed.symmetric_difference_update({folder_id})
        listing.refresh()

    def subfolder(parent: int) -> None:
        folders.save_folder(None, _("New folder"), parent_id=parent)
        collapsed.discard(parent)
        done()

    with ui.dialog() as dlg, ui.card().classes("w-full max-w-4xl"):
        ui.label(_("Folders")).classes("text-lg font-medium")
        ui.label(
            _(
                "Drag a folder onto another one to put it within it (onto its top or bottom: "
                "next to it). A folder uses the settings of the folder it is in (its "
                "categories, the citations in its notes: all of them), or its own ones."
            )
        ).classes("text-sm text-grey")

        @ui.refreshable
        def listing() -> None:
            nodes = folders.tree()
            if not nodes:
                ui.label(_("No folder yet")).classes("text-grey")
            unseen: set[int] = set()  # (within a collapsed folder)
            for n in nodes:
                if n.parent_id in collapsed or n.parent_id in unseen:
                    unseen.add(n.id)
                    continue
                within = set(folders.within(n.id)) if n.children else {n.id}
                row = (
                    ui.row()
                    .classes("w-full items-center no-wrap gap-1 rounded")
                    .style(f"padding-left:{1.5 * n.depth}rem")
                    .props("draggable=true")
                    .mark(f"folder-node-{n.id}")
                )
                row.on("dragstart", js_handler=_DRAG % n.id)
                row.on("dragover", js_handler=_OVER)
                row.on("dragleave", js_handler=_LEAVE)
                row.on("drop", lambda e, n=n: dropped(e.args, n), js_handler=_DROP)
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
                        ui.element("div").classes("w-7")
                    ui.icon("folder", color="grey" if n.hidden else "amber-8").classes(
                        "cursor-move"
                    )
                    title = (
                        ui.input(value=n.name)
                        .props("dense borderless")
                        .classes("grow" + (" text-grey" if n.hidden else ""))
                        .mark(f"folder-tree-name-{n.id}")
                    )

                    def rename(_e=None, n=n, t=title) -> None:
                        if (t.value or "").strip() and t.value.strip() != n.name:
                            folders.rename(n.id, t.value)
                            n.name = t.value.strip()
                            state["changed"] = True

                    title.on("blur", rename)
                    title.on("keydown.enter", rename)
                    ui.select(
                        {
                            TOP: _("(top level)"),
                            **{x.id: x.path for x in nodes if x.id not in within},
                        },
                        value=n.parent_id or TOP,
                        label=_("In"),
                        on_change=lambda e, n=n: move(n.id, e.value),
                    ).props("dense outlined options-dense").classes("w-48").mark(
                        f"folder-parent-{n.id}"
                    )
                    if n.parent_id is None:
                        ui.label(_("Own settings")).classes("w-44 text-sm text-grey px-2")
                    else:
                        ui.select(
                            {PARENT: _("Its parent's settings"), OWN: _("Own settings")},
                            value=OWN if n.own else PARENT,
                            on_change=lambda e, n=n: settings(n, e.value),
                        ).props("dense outlined options-dense").classes("w-44").tooltip(
                            _("Its categories and the citations in its notes")
                        ).mark(f"folder-settings-{n.id}")
                    if n.shared_with:
                        ui.icon("share", color="primary").tooltip(
                            _("Same settings as: {folders}").format(
                                folders=", ".join(n.shared_with)
                            )
                        ).mark(f"folder-shared-{n.id}")
                    ui.button(icon="create_new_folder", on_click=lambda n=n: subfolder(n.id)).props(
                        "flat dense round size=sm"
                    ).tooltip(_("Add a folder within it")).mark(f"folder-sub-{n.id}")
            if nodes:  # (dropped here: at the top level)
                end_zone = (
                    ui.label(_("Drop here: at the top level"))
                    .classes("w-full text-xs text-grey text-center rounded p-1")
                    .style("border: 1px dashed #bbb")
                    .mark("folder-drop-top")
                )
                end_zone.on("dragover", js_handler=_OVER)
                end_zone.on("dragleave", js_handler=_LEAVE)
                end_zone.on("drop", lambda e: dropped(e.args, None), js_handler=_DROP)

        with ui.column().classes("w-full gap-0 max-h-[60vh] overflow-auto"):
            listing()
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Close"), on_click=dlg.close).props("flat").mark("folders-close")

    def closed(e) -> None:
        if not e.value:
            if state["changed"] and changed:
                changed()  # (before deleting the dialog: its client is needed)
            dlg.delete()

    dlg.on_value_change(closed)
    dlg.open()
