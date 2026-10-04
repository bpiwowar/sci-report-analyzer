"""The notes of a person within a folder: one Markdown text for all their documents and
papers in it, edited next to any of their PDFs (possibly in several windows at once, kept in
sync)."""

from __future__ import annotations

from collections.abc import Callable
from weakref import WeakSet

from nicegui import app, ui

from .. import folders
from ..i18n import _
from .mdedit import MarkdownEditor
from .tags import note_editor


def _editors() -> dict[int, WeakSet[MarkdownEditor]]:
    """Period id (a person within a folder) -> the open editors of its notes. Kept on the app
    (one registry)."""
    if not hasattr(app.state, "folder_notes"):
        app.state.folder_notes = {}
    return app.state.folder_notes


def saved(period_id: int, text: str, but: MarkdownEditor) -> None:
    """The notes of a person within a folder were saved from ``but``: the other open editors
    show them (or, with unsaved changes of their own, say that they changed)."""
    for e in list(_editors().get(period_id, ())):
        if e is but or e.editor.is_deleted or e.value.strip() == text.strip():
            continue
        try:
            if e.is_dirty():
                with e.box.client:
                    ui.notify(_("The notes of the folder were changed in another window"))
            else:
                e.adopt(text)
        except RuntimeError:  # (its window is gone)
            _editors()[period_id].discard(e)


def folder_notes_editor(
    period_id: int,
    render: Callable[[str], str] | None = None,
    toolbar: Callable[[], None] | None = None,
    side: Callable[[], None] | None = None,
) -> MarkdownEditor:
    """The editor of a person's notes within a folder, saved as typed (as the other notes);
    ``side``: the pane of the papers to cite (in place of the preview)."""
    holder: list[MarkdownEditor] = []

    def save(text: str) -> None:
        folders.set_notes(period_id, text)
        saved(period_id, text, holder[0])

    editor = note_editor(
        _("Notes of the folder"),
        folders.notes_of(period_id),
        save,
        mark="folder-note",
        mode="split",
        stacked=True,
        height="14rem",
        render=render,
        toolbar=toolbar,
        fill=True,
        side=side,
        side_tip=_("or beside the papers to discuss (cited or not)"),
    )
    holder.append(editor)
    _editors().setdefault(period_id, WeakSet()).add(editor)
    return editor
