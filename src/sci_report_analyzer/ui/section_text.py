"""The text of a category's section in a person's excerpts within a folder (Markdown, e.g. a
summary of its items: after its heading in the notes' ``[]{.excerpts}``): shown under the
category in the categories' tab, edited with its pencil (on hover) or a click on it."""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakSet

from nicegui import ui

from .. import categories, reports
from ..i18n import _
from .dialogs import actions, transient_dialog
from .mdedit import MarkdownEditor
from .theme import NOTE_EXTRAS

if TYPE_CHECKING:
    from .viewer_side import Side

_CSS = (
    ".vr-section-edit { opacity: 0; transition: opacity 0.15s; }"
    " .vr-section-head:hover .vr-section-edit, .vr-section-edit:focus { opacity: 1; }"
    " .vr-section-text { cursor: pointer; }"
    " .vr-section-text p { margin: 0.1rem 0; }"
)
_styled: WeakSet = WeakSet()  # (the pages given the CSS)


def edit_button(side: Side, node: categories.Node) -> None:
    """In a category's header row: its pencil (shown on hover), to edit its section's text."""
    if (client := ui.context.client) not in _styled:
        _styled.add(client)
        ui.add_css(_CSS)
    if (row := ui.context.slot.parent) is not None:
        row.classes(add="vr-section-head")
    ui.button(icon="notes", on_click=lambda: edit(side, node)).props(
        "flat dense round size=xs color=grey-7"
    ).classes("vr-section-edit").tooltip(
        _("The text of this section (Markdown, e.g. a summary of its items)")
    ).mark(f"section-text-edit-{node.id}")


def show(side: Side, node: categories.Node, text: str) -> None:
    """Under a category's header row: its section's text, rendered (nothing without one);
    clicked: edited."""
    if not text.strip():
        return
    ui.markdown(_rendered(side, text), extras=NOTE_EXTRAS).classes(
        "w-full vr-note vr-section-text text-sm text-grey-9"
    ).style(f"margin-left:{1.2 * node.depth + 0.6}rem; width:auto").tooltip(
        _("The text of this section (click to edit it)")
    ).on("click", lambda: edit(side, node)).mark(f"section-text-{node.id}")


def _rendered(side: Side, text: str) -> str:
    """The text with its citations substituted (as in the notes)."""
    return reports.render(text, reports.folder_context(side.stats, side.period_id)).text


def edit(side: Side, node: categories.Node) -> None:
    """A dialog: the section's text, in the notes' Markdown editor (with its preview; empty:
    removed)."""
    if side.period_id is None:
        return
    period_id = side.period_id
    title = _("Text of the section “{category}”").format(category=node.path)
    with side.host.dialogs, transient_dialog(title, width="w-[56rem] max-w-full") as (dlg, _c):
        editor = MarkdownEditor(
            categories.section_text(period_id, node.id),
            render=lambda t: _rendered(side, t),
            mode="split",
            mark="section-text-input",
            height="14rem",
        )
        ui.label(
            _(
                "Put after the heading of the category in the excerpts of the notes, before its "
                "items. Empty: none."
            )
        ).classes("text-xs text-grey")

        def save() -> None:
            categories.set_section_text(period_id, node.id, editor.value)
            side.refresh_excerpts()
            if side.folder_refresh is not None:  # (the notes' preview)
                side.folder_refresh()

        actions(dlg, _("Save"), save, mark="section-text-save")
    editor.focus()
