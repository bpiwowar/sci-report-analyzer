"""Paper tags (global or per period) and notes: chips, the tags manager, a paper's editor."""

from __future__ import annotations

import time
from collections.abc import Callable
from html import escape

from nicegui import ui

from .. import annotations
from ..db.models import Tag
from ..i18n import _
from .mdedit import MarkdownEditor
from .theme import chip_style, chip_text, span

DEFAULT_COLOUR = "#0969da"


def tag_chip(t: Tag, number: int | None = None) -> None:
    """A tag, with the paper's number in the list it was put from."""
    n = f" #{number}" if number is not None else ""
    kind = _("tag within the period") if t.per_period else _("tag")
    span(
        f'<span class="vr-chip" style="background:{t.colour};color:{chip_text(t.colour)}">'
        f"{'⏱ ' if t.per_period else ''}{escape(t.name)}{n}</span>"
    ).tooltip(
        _("{tag}, number {number} in its list").format(tag=kind, number=number) if n else kind
    )


def tags_section(on_change: Callable[[], None] | None = None) -> None:
    """Every tag: rename, recolour, delete (the built-in "starred" is kept)."""

    def changed() -> None:
        listing.refresh()
        if on_change:
            on_change()

    @ui.refreshable
    def listing() -> None:
        tags = annotations.all_tags()
        for t in tags:
            with ui.row().classes("items-center gap-2"):
                name = ui.input(_("Name"), value=t.name).props("dense").mark(f"tag-name-{t.id}")
                colour = (
                    ui.color_input(_("Colour"), value=t.colour, preview=True)
                    .props("dense")
                    .classes("w-36")
                    .mark(f"tag-colour-{t.id}")
                )
                ui.label(_("within a period") if t.per_period else _("global")).classes(
                    "text-sm text-grey w-28"
                )

                def save(tid=t.id, n=name, c=colour) -> None:
                    if n.value.strip():
                        annotations.save_tag(n.value, c.value, tag_id=tid)
                        ui.notify(_("Saved"))
                        changed()

                ui.button(icon="save", on_click=save).props("flat round dense").mark(
                    f"tag-save-{t.id}"
                )
                if t.key is None:
                    ui.button(
                        icon="delete",
                        on_click=lambda tid=t.id: (annotations.delete_tag(tid), changed()),
                    ).props("flat round dense color=negative").tooltip(
                        _("Delete the tag (from every paper)")
                    )

    ui.label(
        _(
            "Tags are put on papers from their details. A global tag sticks to the paper; a tag "
            "within a period (⏱, e.g. “starred”) is set separately in each period / folder."
        )
    ).classes("text-grey")
    listing()
    with ui.row().classes("items-center gap-2 mt-2"):
        name = ui.input(_("New tag")).props("dense").mark("new-tag-name")
        colour = (
            ui.color_input(_("Colour"), value=DEFAULT_COLOUR, preview=True)
            .props("dense")
            .classes("w-36")
        )
        per_period = ui.checkbox(_("within a period")).mark("new-tag-period")

        def add() -> None:
            if name.value.strip():
                annotations.save_tag(name.value, colour.value, per_period.value)
                name.value = ""
                changed()

        ui.button(_("Add"), on_click=add).mark("new-tag-add")


def tags_dialog(on_change: Callable[[], None]) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-full max-w-2xl"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(_("Tags")).classes("text-lg font-medium")
            ui.button(icon="close", on_click=dlg.close).props("flat round")
        tags_section(on_change)
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def note_editor(
    label: str,
    value: str | None,
    save: Callable[[str], bool | None],
    *,
    mark: str,
    mode: str = "edit",
    stacked: bool = False,
    height: str = "12rem",
    render: Callable[[str], str] | None = None,
    toolbar: Callable[[], None] | None = None,
    fill: bool = False,
    side: Callable[[], None] | None = None,
    side_tip: str = "",
) -> MarkdownEditor:
    """A Markdown note, always open, saved as it is typed (once typing pauses, when the
    editor is left, and when the page is closed); ``save`` returning False: refused (the text
    is kept as unsaved, until typed again); ``mode``, ``stacked``, ``render``, ``toolbar``,
    ``side``, ``side_tip``: those of the editor."""
    state = {"saved": (value or "").strip(), "typed": None, "at": 0.0, "refused": False}

    def flush() -> None:
        text = state["typed"]
        if text is None:
            return
        state["typed"] = None
        text = text.strip()
        if text != state["saved"]:
            if save(text) is False:
                state["refused"] = True
                status.text = _("Not saved")
                return
            state["saved"] = text
        state["refused"] = False
        status.text = _("Saved")

    def typed(text: str) -> None:
        state.update(typed=text, at=time.monotonic(), refused=False)
        status.text = _("Editing…")

    def tick() -> None:
        if state["typed"] is not None and time.monotonic() - state["at"] > 1.0:
            flush()

    with ui.column().classes("w-full gap-0" + (" grow min-h-0" if fill else "")) as box:
        if fill:  # (at least ``height``, else the room left in the column)
            box.style(f"flex:1 0 {height}")
        with ui.row().classes("w-full items-center gap-1"):
            ui.label(label).classes("text-sm text-grey")
            ui.space()
            status = ui.label("").classes("text-xs text-grey").mark(f"{mark}-status")
        editor = MarkdownEditor(
            value or "",
            on_change=typed,
            mark=mark,
            height=height,
            mode=mode,
            stacked=stacked,
            render=render,
            toolbar=toolbar,
            fill=fill,
            side=side,
            side_tip=side_tip,
        )

    def adopt(text: str) -> None:  # (saved by another window)
        state.update(saved=text.strip(), typed=None, refused=False)
        editor.value = text
        state["typed"] = None
        status.text = _("Saved")

    editor.is_dirty = lambda: state["typed"] is not None or state["refused"]
    editor.adopt = adopt
    box.on("focusout", flush)
    ui.timer(0.5, tick)
    ui.context.client.on_disconnect(flush)
    return editor


def paper_tags_and_notes(
    s,
    period,
    tags: list[Tag],
    reload: Callable[[], None],
    manage: Callable[[], None],
    *,
    quote_tool: Callable[[Callable[[], MarkdownEditor | None]], None] | None = None,
) -> None:
    """A paper's tags (global, and within ``period``) and its notes (its own, and within
    the period), in its details; ``quote_tool``: a button of the notes' toolbars, given their
    editor (next to the PDF: quoting the selected text)."""
    editors: dict[str, MarkdownEditor] = {}

    def tools(name: str) -> Callable[[], None] | None:
        return (lambda: quote_tool(lambda: editors.get(name))) if quote_tool else None

    ui.label(_("Tags")).classes("font-medium")
    pid = period.id if period else None

    @ui.refreshable
    def chips() -> None:
        current = s.tags_in(pid)
        with ui.row().classes("items-center gap-1"):
            for t in tags:
                if t.per_period and period is None:
                    continue
                on = t.id in current

                def toggle(tid=t.id, per=t.per_period) -> None:
                    added = annotations.toggle_tag(s.id, tid, pid)
                    target = s.period_tags.setdefault(pid, set()) if per else s.tags
                    (target.add if added else target.discard)(tid)
                    chips.refresh()
                    reload()

                ui.chip(
                    ("⏱ " if t.per_period else "") + t.name,
                    selectable=True,
                    selected=on,
                    on_click=toggle,
                ).style(chip_style(t.colour, on)).props(
                    "dense " + ("color=primary" if on else "")
                ).mark(f"tag-{t.name}").tooltip(
                    _("within {period}").format(period=period.name)
                    if t.per_period
                    else _("on the paper (every period)")
                )
            new = (
                ui.input(placeholder=_("+ new tag"))
                .props("dense borderless")
                .classes("w-32")
                .mark("paper-new-tag")
            )
            per = (
                ui.checkbox("⏱", value=False)
                .props("dense")
                .tooltip(_("Within the period only"))
                .mark("paper-new-tag-period")
            )
            if period is None:
                per.disable()
                per.tooltip(_("Choose a period to make a tag within the period"))

            def create() -> None:
                name = (new.value or "").strip()
                if not name:
                    return
                existing = next((t for t in tags if t.name == name), None)
                tid = existing.id if existing else annotations.save_tag(name, None, per.value)
                if existing is None:
                    tags[:] = annotations.all_tags()
                tag = next(t for t in tags if t.id == tid)
                if tag.per_period and period is None:
                    ui.notify(_("Choose a period first"), type="warning")
                    return
                if tid not in s.tags_in(pid):
                    annotations.toggle_tag(s.id, tid, pid)
                    target = s.period_tags.setdefault(pid, set()) if tag.per_period else s.tags
                    target.add(tid)
                chips.refresh()
                reload()

            new.on("keydown.enter", create)
            ui.button(icon="settings", on_click=manage).props("flat round dense size=sm").tooltip(
                _("Manage tags (names, colours)")
            ).mark("manage-tags")

    chips()
    ui.label(_("Notes")).classes("font-medium mt-2")

    def save_note(text: str) -> None:
        annotations.set_note(s.id, text)
        s.note = text or None
        reload()

    editors["paper"] = note_editor(
        _("Note (on the paper)"),
        s.note,
        save_note,
        mark="paper-note",
        mode="split",
        stacked=True,
        height="8rem",
        toolbar=tools("paper"),
    )
    if period is not None:

        def save_period_note(text: str) -> None:
            annotations.set_note(s.id, text, pid)
            if text:
                s.period_notes[pid] = text
            else:
                s.period_notes.pop(pid, None)
            reload()

        editors["period"] = note_editor(
            _("Note within {period}").format(period=period.name),
            s.period_notes.get(pid),
            save_period_note,
            mark="period-note",
            mode="split",
            stacked=True,
            height="8rem",
            toolbar=tools("period"),
        )
