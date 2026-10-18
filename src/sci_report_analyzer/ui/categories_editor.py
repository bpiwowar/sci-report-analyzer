"""Editing a folder's categories (an ordered tree, e.g. a committee's grid), where excerpts
of its people's PDFs are filed."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import categories, classifier, folders, llm
from ..i18n import N_, _, ngettext
from .colours import ColourMenu
from .dialogs import actions, transient_dialog
from .dnd import draggable, drop_zone


def shared_note(folder_id: int) -> None:
    """Says which other folders use the same settings as a folder (changed for them too; a
    folders.SettingsRef: the folders using them)."""
    if others := folders.sharing(folder_id):
        text = (
            _("Used by {folders}: changed for them too")
            if isinstance(folder_id, folders.SettingsRef)
            else _("Shared with {folders}: changed for them too")
        )
        ui.label(text.format(folders=", ".join(others))).classes("text-sm text-primary").mark(
            "settings-shared"
        )


HELP = N_(
    "Excerpts of the people's documents (a passage selected in a PDF) are filed in them; in "
    "this order in the reports. Drag to order and nest them. Its colour tints its excerpts."
)


def categories_dialog(folder_id: int, changed: Callable[[], None] | None = None) -> None:
    """Add, rename, order, nest (subcategories), merge and delete the categories (of a
    folder's settings; a folders.SettingsRef: of those), in a dialog."""
    if isinstance(folder_id, folders.SettingsRef):
        name = next((x.name for x in folders.named_settings() if x.id == folder_id), "")
    else:
        name = next((f.name for f in folders.folders() if f.id == folder_id), "")
    with transient_dialog(
        _("Categories of {folder}").format(folder=name), width="w-full max-w-3xl"
    ) as (dlg, _card):
        ui.label(_(HELP)).classes("text-sm text-grey")
        shared_note(folder_id)
        categories_editor(folder_id, changed)
        with ui.row().classes("w-full"):
            ui.space()
            ui.button(_("Close"), on_click=dlg.close).props("flat")


def categories_editor(folder_id: int, changed: Callable[[], None] | None = None) -> None:
    """The categories (see categories_dialog), changed at once; ``changed``: then."""

    def done() -> None:
        listing.refresh()
        if changed:
            changed()

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
                .mark(f"category-{n.id}")
            )
            draggable(row, "category", n.id)
            drop_zone(row, "category", lambda i, w, n=n: dropped(i, n.id, w), middle="inside")
            with row:
                ui.icon("drag_indicator", color="grey").classes("cursor-move").tooltip(
                    _(
                        "Drag: onto the top / bottom of a category (before / after it), or "
                        "its middle (inside it)"
                    )
                )
                with (
                    ui.button()
                    .props("round dense unelevated size=xs")
                    .style(f"background: {n.colour} !important")
                    .tooltip(_("The colour of its excerpts (their tint on the PDFs)"))
                    .mark(f"category-colour-{n.id}")
                ):
                    ColourMenu(
                        lambda c, n=n: (categories.set_colour(n.id, c), done()), n.colour
                    ).swatches.mark(f"category-colour-pick-{n.id}")
                title = (
                    ui.input(value=n.name)
                    .props("dense borderless")
                    .classes("grow font-medium" if not n.depth else "grow")
                    .mark(f"category-name-{n.id}")
                )

                def save(_e=None, n=n, t=title) -> None:
                    categories.rename(n.id, t.value)
                    if changed:
                        changed()

                title.on("blur", save)
                title.on("keydown.enter", save)
                key = (
                    ui.input(placeholder=_("id"), value=n.key or "")
                    .props("dense borderless prefix=#")
                    .classes("w-24")
                    .tooltip(
                        _(
                            "Its id in the reports: [[excerpts#id]] lists its excerpts (and "
                            "those below it) there, then left out of [[excerpts]]"
                        )
                    )
                    .mark(f"category-key-{n.id}")
                )

                def save_key(_e=None, n=n, k=key) -> None:
                    if not categories.set_key(n.id, k.value):
                        ui.notify(_("Another category has this id"), type="warning")
                        k.value = n.key or ""
                        return
                    n.key = categories.clean_key(k.value) or None
                    k.value = n.key or ""
                    if changed:
                        changed()

                key.on("blur", save_key)
                key.on("keydown.enter", save_key)
                ui.button(
                    icon="subdirectory_arrow_right",
                    on_click=lambda n=n: (categories.add(folder_id, _("New"), n.id), done()),
                ).props("flat dense round size=sm").tooltip(_("Add a subcategory")).mark(
                    f"category-sub-{n.id}"
                )
                ui.button(icon="call_merge", on_click=lambda n=n: merge(n)).props(
                    "flat dense round size=sm"
                ).tooltip(_("Merge it into another category")).mark(f"category-merge-{n.id}")
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
            last = next(n for n in reversed(nodes) if n.depth == 0)
            drop_zone(
                end_zone,
                "category",
                lambda i, _w, last=last: dropped(i, last.id, "after"),
                zoned=False,
            )

    def dropped(category_id: int, target: int, where: str) -> None:
        if categories.place(category_id, target, where):
            done()
        else:
            ui.notify(_("A category cannot go within itself"), type="warning")

    def merge(n: categories.Node) -> None:
        """Into another category (not within it), once chosen: its excerpts, texts and
        subcategories there, then it is gone."""
        within = set(categories.subtree(n.id))
        targets = {c.id: c.path for c in categories.tree(folder_id) if c.id not in within}
        if not targets:
            ui.notify(_("No other category: create one first"), type="warning")
            return
        with transient_dialog(width="max-w-lg") as (confirm, _card):
            ui.label(
                _(
                    "Merge “{name}” into another category: its excerpts, its sections' "
                    "texts and its subcategories go there, after its own; then “{name}” is "
                    "deleted."
                ).format(name=n.name)
            )
            target = (
                ui.select(targets, label=_("Merge into"), with_input=True)
                .props("dense outlined options-dense")
                .classes("w-full")
                .mark("category-merge-target")
            )

            def ok() -> bool | None:
                if not target.value:
                    ui.notify(_("Choose the category to merge it into"), type="warning")
                    return False
                if not categories.merge(n.id, target.value):
                    ui.notify(_("A category cannot go within itself"), type="warning")
                done()

            actions(confirm, _("Merge"), ok, mark="category-merge-ok")

    def delete(n: categories.Node) -> None:
        """Once confirmed; with excerpts (there or below): to move first, one by one (each
        with the category suggested for it, see classifier.py) or all at once."""
        from .classification import excerpt_card

        within = set(categories.subtree(n.id))
        names = {c.id: c.path for c in categories.tree(folder_id)}
        targets = {c: p for c, p in names.items() if c not in within}
        count = categories.excerpt_count(n.id)
        suggested: dict[int, int | None] = {}
        with transient_dialog(width="max-w-2xl") as (confirm, card):
            if not count:
                ui.label(
                    _("Delete “{name}” and its subcategories?").format(name=n.name)
                    if n.children
                    else _("Delete “{name}”?").format(name=n.name)
                )
            else:
                ui.label(
                    ngettext(
                        "“{name}” has an excerpt (with its subcategories): move it first, "
                        "where suggested or elsewhere.",
                        "“{name}” has {n} excerpts (with its subcategories): move them first, "
                        "one by one (where suggested, or elsewhere) or all at once.",
                        count,
                    ).format(name=n.name, n=count)
                ).mark("category-delete-blocked")
                note = ui.row().classes("w-full items-center gap-2")
                pending = ui.column().classes("w-full gap-1 max-h-[50vh] overflow-auto")
                target = (
                    ui.select(targets, label=_("Move them all to"))
                    .props("dense outlined options-dense")
                    .classes("w-full")
                    .mark("category-delete-target")
                )
                if not targets:
                    ui.label(_("No other category: create one first")).classes("text-sm text-grey")

            def ok() -> bool | None:
                if categories.excerpt_count(n.id):
                    if not target.value:
                        ui.notify(_("Choose where to move them"), type="warning")
                        return False
                    categories.move_excerpts(n.id, target.value)
                if not categories.delete(n.id):
                    ui.notify(_("Excerpts were filed there meanwhile"), type="warning")
                done()

            ok_button = actions(
                confirm,
                _("Move, then delete") if count else _("Delete"),
                ok,
                danger=True,
                mark="category-delete-ok",
            )

        if not count:
            return

        def listed() -> None:
            """The excerpts left, each with the category suggested; none: to delete."""
            pending.clear()
            left = categories.filed_below(n.id)
            with pending:
                for i, cat, text in left:
                    excerpt_card(
                        i,
                        text,
                        cat,
                        suggested.get(i),
                        names,
                        lambda c, i=i: move(i, c),
                        others=targets,
                    )
                if not left:
                    ui.label(_("All moved: “{name}” can be deleted").format(name=n.name)).classes(
                        "text-sm text-positive"
                    ).mark("category-delete-ready")
            target.set_visibility(bool(left))
            ok_button.set_text(_("Move, then delete") if left else _("Delete"))

        def move(excerpt_id: int, category_id: int) -> None:
            categories.move_excerpt(excerpt_id, category_id)
            listed()
            done()

        async def suggest() -> None:
            """(the category closest to each one, not within the one deleted)"""
            note.clear()
            if not llm.embed_model():
                with note:
                    ui.label(
                        _(
                            "No suggestion: turn on the local LLM, choose an embedding model "
                            "(Settings)"
                        )
                    ).classes("text-xs text-grey").mark("category-delete-no-suggestion")
                return
            if classifier.trained(folder_id) is None:
                with note:
                    ui.label(_("No suggestion (train first)")).classes("text-xs text-grey").mark(
                        "category-delete-no-suggestion"
                    )
                    ui.button(_("Train"), icon="model_training", on_click=train).props(
                        "flat dense size=sm no-caps color=purple"
                    ).mark("category-delete-train")
                return
            for i, _cat, text in categories.filed_below(n.id):
                try:
                    found = await classifier.suggest(folder_id, text, 1, within)
                except Exception:
                    found = []
                suggested[i] = found[0][0] if found else None
            listed()

        async def train() -> None:
            try:
                await classifier.train(folder_id)
            except Exception as e:
                ui.notify(_("Cannot train: {error}").format(error=e), type="negative")
                return
            await suggest()

        listed()
        with card:
            ui.timer(0.05, suggest, once=True)

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
        mine = folders.settings_of(folder_id)
        others = {
            x.id: x.name
            for x in folders.named_settings()
            if x.id != mine and categories.tree(folders.SettingsRef(x.id))
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
                _("Add the categories of other settings")
            ).mark("category-copy")
