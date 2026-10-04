"""The notes of a person within a folder: one Markdown text for all their documents and
papers in it, edited next to any of their PDFs (possibly in several windows at once, kept in
sync)."""

from __future__ import annotations

from collections.abc import Callable
from weakref import WeakSet

from nicegui import app, ui

from .. import folders, reports
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
        if e is but or e.editor.is_deleted:
            continue
        try:
            if e.value.strip() == text.strip():
                e.rebase(text)  # (up to date: its next save is not refused)
            elif e.is_dirty():
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
    ``side``: the pane of the papers to cite (in place of the preview). Empty notes start
    with the folder's skeleton (saved once edited).

    A save is refused if the notes changed since the editor loaded them (saved elsewhere:
    another window, the old reports appended…), and clearing them all asks first."""
    holder: list[MarkdownEditor] = []
    base = {"text": folders.notes_of(period_id)}  # (the notes as last loaded or saved here)
    text = base["text"]
    if not text.strip() and (found := folders.folder_of_period(period_id)):
        text = reports.skeleton(found[0]) or text

    def store(text: str) -> bool:
        if not folders.set_notes(period_id, text, base=base["text"]):
            conflict.set_visibility(True)
            return False
        base["text"] = text
        saved(period_id, text, holder[0])
        return True

    def save(text: str) -> bool:
        if text.strip() or not base["text"].strip():
            return store(text)
        if holder[0].box.client.has_socket_connection:  # (not when the window is closed)
            confirm_clear()
        return False

    def confirm_clear() -> None:
        def clear() -> None:
            dlg.close()
            if not holder[0].value.strip() and store(""):  # (unless typed since)
                holder[0].adopt("")

        def restore() -> None:
            dlg.close()
            holder[0].adopt(base["text"])

        with holder[0].box, ui.dialog().props("persistent") as dlg, ui.card():
            ui.label(_("Clear all the notes of the folder on this person?"))
            with ui.row().classes("justify-end w-full"):
                ui.button(_("Restore them"), on_click=restore).props("flat").mark(
                    "folder-note-restore"
                )
                ui.button(_("Clear the notes"), on_click=clear).props("flat color=negative").mark(
                    "folder-note-clear"
                )
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    editor = note_editor(
        _("Notes of the folder"),
        text,
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

    def reload() -> None:
        editor.adopt(folders.notes_of(period_id))

    def copy() -> None:
        ui.clipboard.write(editor.value)
        ui.notify(_("Copied"))

    with ui.row().classes("w-full items-center gap-2 bg-amber-1 rounded px-2 py-1") as conflict:
        ui.icon("warning", color="warning")
        ui.label(
            _("Not saved: the notes were changed elsewhere since this window loaded them")
        ).classes("text-sm grow")
        ui.button(_("Copy my text"), icon="content_copy", on_click=copy).props(
            "flat dense no-caps"
        ).mark("folder-note-copy-unsaved")
        ui.button(_("Reload"), icon="refresh", on_click=reload).props("flat dense no-caps").tooltip(
            _("Show the notes as saved (the text typed here is lost: copy it first)")
        ).mark("folder-note-reload")
    conflict.mark("folder-note-conflict").move(editor.box, target_index=0)
    conflict.set_visibility(False)
    inner = editor.adopt

    def adopt(text: str) -> None:  # (the notes as saved: no more conflict)
        base["text"] = text
        conflict.set_visibility(False)
        inner(text)

    editor.adopt = adopt
    editor.rebase = lambda text: base.update(text=text)
    _editors().setdefault(period_id, WeakSet()).add(editor)
    return editor
