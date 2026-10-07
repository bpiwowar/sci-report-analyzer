"""The text of a category's section in a person's excerpts within a folder (Markdown, e.g. a
summary of its items: after its heading in the notes' ``[]{.excerpts}``): shown under the
category in the categories' tab, edited with its pencil (on hover) or a click on it. The
section can be summarised by its text only: its excerpts not listed in the notes, but in a
comment after the text (``%% … %%``, not shown) for reference; still listed in the tab."""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakSet

from nicegui import ui

from .. import categories, reports
from ..copy_filters import strip_comments
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


def show(side: Side, node: categories.Node, text: str, hidden: int | None = None) -> None:
    """Under a category's header row: its section's text, rendered without its comments
    (nothing without one), then, summarised by it only, how many excerpts are not listed in
    the notes (``hidden``); clicked: edited."""
    indent = f"margin-left:{1.2 * node.depth + 0.6}rem; width:auto"
    if (shown := strip_comments(_rendered(side, text))).strip():
        ui.markdown(shown, extras=NOTE_EXTRAS).classes(
            "w-full vr-note vr-section-text text-sm text-grey-9"
        ).style(indent).tooltip(_("The text of this section (click to edit it)")).on(
            "click", lambda: edit(side, node)
        ).mark(f"section-text-{node.id}")
    if hidden is not None:
        ui.label(
            _("Summarised by its text, its excerpts not listed ({count})").format(count=hidden)
        ).classes("vr-section-text text-xs text-grey italic").style(indent).tooltip(
            _("In the notes and the copy, they follow the text in a comment (%% … %%)")
        ).on("click", lambda: edit(side, node)).mark(f"section-summary-only-{node.id}")


def _rendered(side: Side, text: str) -> str:
    """The text with its citations substituted (as in the notes)."""
    return reports.render(text, reports.folder_context(side.stats, side.period_id)).text


def edit(side: Side, node: categories.Node) -> None:
    """A dialog: the section's text, in the notes' Markdown editor (with its preview; empty:
    removed), and whether the section is summarised by it only (its excerpts then in a
    comment after it in the notes, see categories.markdown)."""
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
        editor.open_cited = side.open_cited
        ui.label(
            _(
                "Put after the heading of the category in the excerpts of the notes, before its "
                "items. Empty: none."
            )
        ).classes("text-xs text-grey")
        if listed := categories.section_excerpts(period_id, node.id):
            with (
                ui.expansion(_("Its excerpts (and those of its subsections)"), value=True)
                .props("dense")
                .classes("w-full text-sm")
                .mark("section-text-excerpts")
            ):
                ui.markdown(listed, extras=NOTE_EXTRAS).classes(
                    "w-full vr-note text-sm text-grey-9 max-h-60 overflow-auto"
                )

        only = (
            ui.checkbox(
                _("Only this text, its excerpts not listed"),
                value=node.id in categories.summarised(period_id),
            )
            .tooltip(
                _(
                    "The section summarised by its text: in the notes and the copy, its "
                    "excerpts follow it between %% … %% for reference (not shown)"
                )
            )
            .mark("section-summary-only")
        )

        def save() -> None:
            categories.set_section_text(
                period_id, node.id, editor.value, summary_only=bool(only.value)
            )
            side.refresh_excerpts()
            if side.folder_refresh is not None:  # (the notes' preview)
                side.folder_refresh()

        actions(dlg, _("Save"), save, mark="section-text-save")
    editor.focus()
