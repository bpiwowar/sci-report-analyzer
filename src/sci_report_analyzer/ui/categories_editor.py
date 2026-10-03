"""Editing a folder's categories (an ordered tree, e.g. a committee's grid), where excerpts
of its people's PDFs are filed."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import categories, folders
from ..i18n import _, ngettext

# Drag and drop: where a category is dropped (onto the top / middle / bottom of another).
_WHERE = (
    "const r = e.currentTarget.getBoundingClientRect(), f = (e.clientY - r.top) / r.height;"
    " const w = f < 0.3 ? 'before' : f > 0.7 ? 'after' : 'inside';"
)
_DRAG = (
    "(e) => { e.dataTransfer.setData('text/plain', '%d'); e.dataTransfer.effectAllowed = 'move'; }"
)
_OVER = (
    "(e) => { e.preventDefault(); " + _WHERE + " const s = e.currentTarget.style;"
    " s.boxShadow = w === 'before' ? 'inset 0 2px 0 #1976d2'"
    " : w === 'after' ? 'inset 0 -2px 0 #1976d2' : 'none';"
    " s.background = w === 'inside' ? 'rgba(25, 118, 210, 0.12)' : ''; }"
)
_LEAVE = (
    "(e) => { e.currentTarget.style.boxShadow = 'none'; e.currentTarget.style.background = ''; }"
)
_DROP = (
    "(e) => { e.preventDefault(); " + _WHERE + " e.currentTarget.style.boxShadow = 'none';"
    " e.currentTarget.style.background = '';"
    " emit({id: +e.dataTransfer.getData('text/plain'), where: w}); }"
)


def _int(v) -> int | None:
    return int(v) if v not in (None, "") else None


def categories_dialog(folder_id: int, changed: Callable[[], None] | None = None) -> None:
    """Add, rename, order, nest (subcategories), date and delete the categories."""
    name = next((f.name for f in folders.folders() if f.id == folder_id), "")

    def done() -> None:
        listing.refresh()
        if changed:
            changed()

    with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl"):
        ui.label(_("Categories of {folder}").format(folder=name)).classes("text-lg font-medium")
        ui.label(
            _(
                "Excerpts of the people's documents (a passage selected in a PDF) are filed in "
                "them; in this order in the reports. Drag to order and nest them. Years "
                "(optional): those a category is about."
            )
        ).classes("text-sm text-grey")

        @ui.refreshable
        def listing() -> None:
            nodes = categories.tree(folder_id)
            if not nodes:
                ui.label(_("No category yet")).classes("text-grey")
            for n in nodes:
                row = (
                    ui.row()
                    .classes("w-full items-center no-wrap gap-1 rounded")
                    .style(f"padding-left:{1.5 * n.depth}rem")
                    .props("draggable=true")
                    .mark(f"category-{n.id}")
                )
                row.on("dragstart", js_handler=_DRAG % n.id)
                row.on("dragover", js_handler=_OVER)
                row.on("dragleave", js_handler=_LEAVE)
                row.on("drop", lambda e, n=n: dropped(e.args, n.id), js_handler=_DROP)
                with row:
                    ui.icon("drag_indicator", color="grey").classes("cursor-move").tooltip(
                        _(
                            "Drag: onto the top / bottom of a category (before / after it), or "
                            "its middle (inside it)"
                        )
                    )
                    title = (
                        ui.input(value=n.name)
                        .props("dense borderless")
                        .classes("grow font-medium" if not n.depth else "grow")
                        .mark(f"category-name-{n.id}")
                    )
                    start = (
                        ui.number(placeholder=_("from"), value=n.start_year, format="%d")
                        .props("dense borderless")
                        .classes("w-16")
                        .mark(f"category-from-{n.id}")
                    )
                    end = (
                        ui.number(placeholder=_("to"), value=n.end_year, format="%d")
                        .props("dense borderless")
                        .classes("w-16")
                        .mark(f"category-to-{n.id}")
                    )

                    def save(_e=None, n=n, t=title, a=start, b=end) -> None:
                        categories.update(n.id, t.value, _int(a.value), _int(b.value))
                        if changed:
                            changed()

                    for field in (title, start, end):
                        field.on("blur", save)
                        field.on("keydown.enter", save)
                    ui.button(
                        icon="subdirectory_arrow_right",
                        on_click=lambda n=n: (categories.add(folder_id, _("New"), n.id), done()),
                    ).props("flat dense round size=sm").tooltip(_("Add a subcategory")).mark(
                        f"category-sub-{n.id}"
                    )
                    ui.button(icon="delete", on_click=lambda n=n: delete(n)).props(
                        "flat dense round size=sm color=negative"
                    ).mark(f"category-delete-{n.id}")
            if nodes:  # (dropped here: the last one at the top level)
                end_zone = (
                    ui.label(_("Drop here: last, at the top level"))
                    .classes("w-full text-xs text-grey text-center rounded p-1 border-dashed")
                    .style("border: 1px dashed #bbb")
                    .mark("category-drop-end")
                )
                end_zone.on("dragover", js_handler=_OVER)
                end_zone.on("dragleave", js_handler=_LEAVE)
                last = next(n for n in reversed(nodes) if n.depth == 0)
                end_zone.on(
                    "drop",
                    lambda e, last=last: dropped({**(e.args or {}), "where": "after"}, last.id),
                    js_handler=_DROP,
                )

        def dropped(args, target: int) -> None:
            if not isinstance(args, dict) or not args.get("id"):
                return
            if categories.place(int(args["id"]), target, args.get("where") or "inside"):
                done()
            else:
                ui.notify(_("A category cannot go within itself"), type="warning")

        def delete(n: categories.Node) -> None:
            """Once confirmed; with excerpts (there or below): to move them first."""
            count = categories.excerpt_count(n.id)
            within = set(categories.subtree(n.id))
            targets = {c.id: c.path for c in categories.tree(folder_id) if c.id not in within}
            with ui.dialog() as confirm, ui.card().classes("max-w-lg"):
                if not count:
                    ui.label(
                        _("Delete “{name}” and its subcategories?").format(name=n.name)
                        if n.children
                        else _("Delete “{name}”?").format(name=n.name)
                    )
                else:
                    ui.label(
                        ngettext(
                            "“{name}” has an excerpt (with its subcategories): move it first.",
                            "“{name}” has {n} excerpts (with its subcategories): move them first.",
                            count,
                        ).format(name=n.name, n=count)
                    ).mark("category-delete-blocked")
                    target = (
                        ui.select(targets, label=_("Move them to"))
                        .props("dense outlined options-dense")
                        .classes("w-full")
                        .mark("category-delete-target")
                    )
                    if not targets:
                        ui.label(_("No other category: create one first")).classes(
                            "text-sm text-grey"
                        )

                def ok() -> None:
                    if count:
                        if not target.value:
                            ui.notify(_("Choose where to move them"), type="warning")
                            return
                        categories.move_excerpts(n.id, target.value)
                    if not categories.delete(n.id):
                        ui.notify(_("Excerpts were filed there meanwhile"), type="warning")
                    confirm.close()
                    done()

                with ui.row().classes("w-full justify-end"):
                    ui.button(_("Cancel"), on_click=confirm.close).props("flat")
                    ui.button(
                        _("Move, then delete") if count else _("Delete"),
                        color="negative",
                        on_click=ok,
                    ).mark("category-delete-ok")
            confirm.on_value_change(lambda e: None if e.value else confirm.delete())
            confirm.open()

        with ui.column().classes("w-full gap-0 max-h-[60vh] overflow-auto"):
            listing()
        with ui.row().classes("w-full items-center gap-2"):
            new = (
                ui.input(placeholder=_("New category"))
                .props("dense outlined")
                .classes("w-64")
                .mark("category-new")
            )

            def add() -> None:
                if (new.value or "").strip():
                    categories.add(folder_id, new.value)
                    new.value = ""
                    done()

            new.on("keydown.enter", add)
            ui.button(_("Add"), icon="add", on_click=add).props("flat dense").mark("category-add")
            others = {
                f.id: f.name
                for f in folders.folders()
                if f.id != folder_id and categories.tree(f.id)
            }
            if others:
                source = (
                    ui.select(others, label=_("Copy those of"))
                    .props("dense outlined")
                    .classes("w-48")
                    .mark("category-copy-from")
                )

                def copy() -> None:
                    if source.value:
                        n = categories.copy_tree(source.value, folder_id)
                        ui.notify(
                            ngettext("{n} category copied", "{n} categories copied", n).format(n=n)
                        )
                        done()

                ui.button(icon="content_copy", on_click=copy).props("flat dense round").tooltip(
                    _("Add the categories of another folder")
                ).mark("category-copy")
            ui.space()
            ui.button(_("Close"), on_click=dlg.close).props("flat")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()
