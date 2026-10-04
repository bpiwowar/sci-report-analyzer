"""Paper tags (global or per period) and notes: chips, the tags manager, a paper's editor."""

from __future__ import annotations

import time
from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING

from nicegui import ui

from .. import annotations
from ..db.models import Tag
from ..i18n import _
from ..pubview import tag_order
from .colours import ColourInput
from .mdedit import MarkdownEditor
from .theme import chip_style, chip_text, span

if TYPE_CHECKING:
    from ..db.models import Period
    from ..pubview import PubStat

DEFAULT_COLOUR = "#0969da"
# A note is saved once typing pauses for NOTE_IDLE seconds (checked every NOTE_TICK).
NOTE_IDLE = 1.0
NOTE_TICK = 0.5

# Drag and drop of the papers of a tag (ordering them): dropped before / after another one
# (its top / bottom half). Their own type of data: not text, dropped into an editor.
_PAPER = "application/x-vr-paper"
_WHERE = (
    "const r = e.currentTarget.getBoundingClientRect();"
    " const w = e.clientY - r.top < r.height / 2 ? 'before' : 'after';"
)
_DRAG = (
    "(e) => { e.stopPropagation(); e.dataTransfer.setData('" + _PAPER + "', '%d');"
    " e.dataTransfer.effectAllowed = 'move'; }"
)
_OVER = (
    "(e) => { if (!e.dataTransfer.types.includes('" + _PAPER + "')) return;"
    " e.preventDefault(); " + _WHERE + " e.currentTarget.style.boxShadow = w === 'before'"
    " ? 'inset 0 2px 0 #1976d2' : 'inset 0 -2px 0 #1976d2'; }"
)
_LEAVE = "(e) => { e.currentTarget.style.boxShadow = 'none'; }"
_DROP = (
    "(e) => { const d = e.dataTransfer.getData('" + _PAPER + "'); if (!d) return;"
    " e.preventDefault(); " + _WHERE + " e.currentTarget.style.boxShadow = 'none';"
    " emit({id: +d, where: w}); }"
)


def tag_chip(
    t: Tag,
    number: int | None = None,
    on_number: Callable[[int | None], None] | None = None,
    mark: str = "",
) -> None:
    """A tag, with the paper's number in the list it was put from (``on_number``: that
    number edited by Alt-clicking it; a plain click goes on, e.g. to the paper's row)."""
    n = f" #{number}" if number is not None and on_number is None else ""
    kind = _("tag within the period") if t.per_period else _("tag")
    span(
        f'<span class="vr-chip" style="background:{t.colour};color:{chip_text(t.colour)}">'
        f"{'⏱ ' if t.per_period else ''}{escape(t.name)}{n}</span>"
    ).tooltip(
        _("{tag}, number {number} in its list").format(tag=kind, number=number) if n else kind
    )
    if on_number is not None and number is not None:
        number_edit(number, on_number, mark=mark or f"tag-number-{t.id}", plain=False)


def number_edit(
    number: int | None,
    save: Callable[[int | None], None],
    *,
    mark: str,
    tip: str = "",
    plain: bool | Callable[[], None] = True,
    show: Callable[[int | None], ui.element] | None = None,
) -> None:
    """A paper's number within a tag ("#3"; "#": none), Alt-clicked: typed (Enter or leaving
    it saves, Escape cancels, empty: no number). ``plain``: what a plain click does (True:
    the same; False: nothing here, the click going on to what is around, e.g. a row opening
    the paper's details; else that function, e.g. citing the paper); ``show``: what shows
    the number (with its own mark and tooltip; else "#3", marked ``mark``)."""
    box = ui.element("span").classes("vr-num inline-flex items-center")
    state = {"number": number}
    js = (
        "(e) => { e.stopPropagation(); emit({altKey: e.altKey}); }"
        if plain is not False
        else "(e) => { if (!e.altKey) return; e.stopPropagation(); e.preventDefault();"
        " emit({altKey: true}); }"
    )

    def clicked(e) -> None:
        if (isinstance(e.args, dict) and e.args.get("altKey")) or plain is True:
            edit()
        elif callable(plain):
            plain()

    def display() -> None:
        box.clear()
        n = state["number"]
        with box:
            if show is not None:
                shown = show(n)
            else:
                shown = (
                    ui.label(f"#{n}" if n is not None else "#")
                    .classes(
                        "cursor-pointer text-sm px-1 rounded hover:bg-grey-3"
                        + ("" if n is not None else " text-grey")
                    )
                    .tooltip(
                        tip
                        or (
                            _("Its number in the tag's list (click to change it)")
                            if plain is True
                            else _("Its number in the tag's list (Alt-click to change it)")
                        )
                    )
                    .mark(mark)
                )
            shown.on("click", clicked, js_handler=js)

    def edit() -> None:
        box.clear()
        done = {"yes": False}
        with box:
            n = state["number"]
            field = (
                ui.input(value="" if n is None else str(n))
                .props("dense autofocus hide-bottom-space input-class=text-sm")
                .classes("w-14 vr-num-edit")
                .tooltip(_("Enter: save · Escape: cancel · empty: no number"))
                .mark(f"{mark}-input")
            )

        def commit() -> None:
            if done["yes"]:
                return
            done["yes"] = True
            text = (field.value or "").strip().lstrip("#").strip()
            if text and not text.isdigit():
                ui.notify(_("A number, please"), type="warning")
                display()
                return
            value = int(text) if text else None
            changed = value != state["number"]
            state["number"] = value
            display()
            if changed:
                save(value)

        def cancel() -> None:
            done["yes"] = True
            display()

        field.on("keydown.enter", commit)
        field.on("blur", commit)
        field.on("keydown.escape", cancel)

    display()


def _store(s: PubStat, tag: Tag, period_id: int | None, number: int | None) -> None:
    """The paper's number within the tag, as loaded (the tag on it)."""
    if tag.per_period:
        s.period_tags.setdefault(period_id, set()).add(tag.id)
        numbers = s.period_numbers.setdefault(period_id, {})
    else:
        s.tags.add(tag.id)
        numbers = s.numbers
    if number is None:
        numbers.pop(tag.id, None)
    else:
        numbers[tag.id] = number


def save_number(s: PubStat, tag: Tag, period_id: int | None, number: int | None) -> None:
    """Set (None: remove) a paper's number within a tag, within the period for a per-period
    tag."""
    annotations.set_tag_number(s.id, tag.id, number, period_id if tag.per_period else None)
    _store(s, tag, period_id, number)


def save_order(rows: list[PubStat], tag: Tag, period_id: int | None) -> None:
    """Number the papers of a tag 1, 2… in this order."""
    where = period_id if tag.per_period else None
    annotations.number_in_order(tag.id, [r.id for r in rows], where)
    for i, r in enumerate(rows, 1):
        _store(r, tag, period_id, i)


def clear_numbers(rows: list[PubStat], tag: Tag, period_id: int | None) -> None:
    where = period_id if tag.per_period else None
    annotations.clear_tag_numbers(tag.id, [r.id for r in rows], where)
    for r in rows:
        _store(r, tag, period_id, None)


def moved(ids: list[int], src: int, target: int, where: str = "before") -> list[int]:
    """``ids`` with ``src`` moved before / after ``target``."""
    if src == target or src not in ids or target not in ids:
        return list(ids)
    out = [i for i in ids if i != src]
    at = out.index(target) + (where == "after")
    return [*out[:at], src, *out[at:]]


def reorder(
    stats: list[PubStat], tag: Tag, period_id: int | None, src: int, target: int, where: str
) -> bool:
    """A paper of the tag moved before / after another one: the papers of the tag numbered
    in the new order; whether it moved."""
    rows = tag_order(stats, tag.id, period_id)
    ids = [r.id for r in rows]
    new = moved(ids, src, target, where)
    if new == ids and all(r.number_of(tag.id, period_id) == i for i, r in enumerate(rows, 1)):
        return False
    by_id = {r.id: r for r in rows}
    save_order([by_id[i] for i in new], tag, period_id)
    return True


def step(stats: list[PubStat], tag: Tag, period_id: int | None, pub_id: int, delta: int) -> bool:
    """A paper of the tag moved up (-1) or down (+1)."""
    ids = [r.id for r in tag_order(stats, tag.id, period_id)]
    if pub_id not in ids:
        return False
    j = ids.index(pub_id) + delta
    if not 0 <= j < len(ids):
        return False
    return reorder(stats, tag, period_id, pub_id, ids[j], "before" if delta < 0 else "after")


def draggable(row: ui.element, pub_id: int, dropped: Callable[[int, str], None]) -> None:
    """A paper's row, dragged and dropped onto (``dropped``: the paper dropped, and
    "before" / "after")."""
    row.props("draggable=true")
    row.on("dragstart", js_handler=_DRAG % pub_id)
    row.on("dragover", js_handler=_OVER)
    row.on("dragleave", js_handler=_LEAVE)
    row.on(
        "drop",
        lambda e: (
            dropped(int(e.args["id"]), e.args.get("where") or "before")
            if isinstance(e.args, dict) and e.args.get("id")
            else None
        ),
        js_handler=_DROP,
    )


def tag_order_dialog(
    tag: Tag, stats: list[PubStat], period: Period | None, on_change: Callable[[], None]
) -> None:
    """The papers of a tag (of the person; within the period for a per-period tag), in
    their order: dragged or moved up / down (numbering them all 1, 2… in the new order),
    a number typed (click it), numbered in the order shown, or their numbers removed."""
    pid = period.id if period else None
    if tag.per_period and pid is None:
        ui.notify(_("Choose a period first"), type="warning")
        return

    def changed() -> None:
        listing.refresh()
        on_change()

    with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl"):
        with ui.row().classes("w-full items-center no-wrap"):
            ui.label(
                _("Order the papers tagged “{tag}” within {period}").format(
                    tag=tag.name, period=period.name
                )
                if tag.per_period
                else _("Order the papers tagged “{tag}”").format(tag=tag.name)
            ).classes("text-lg font-medium grow")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        ui.label(
            _(
                "Their numbers (#) order them: in the publications, and in the citations of "
                "folder notes (those without a number come after). Drag a paper or use its "
                "arrows (all are then numbered 1, 2… in the new order), or click a number "
                "to type it."
            )
        ).classes("text-sm text-grey")

        @ui.refreshable
        def listing() -> None:
            rows = tag_order(stats, tag.id, pid)
            if not rows:
                ui.label(_("No paper with this tag")).classes("text-grey")
            with ui.column().classes("w-full gap-0 max-h-[60vh] overflow-auto"):
                for i, s in enumerate(rows):
                    row = ui.row().classes("w-full items-center no-wrap gap-2 py-1 border-b")
                    row.mark(f"tag-order-row-{s.id}")
                    draggable(
                        row,
                        s.id,
                        lambda src, where, tid=s.id: (
                            reorder(stats, tag, pid, src, tid, where) and changed()
                        ),
                    )
                    with row:
                        ui.icon("drag_indicator", color="grey").classes("cursor-move").tooltip(
                            _("Drag onto another paper (its top / bottom half: before / after it)")
                        )
                        with ui.element("span").classes("w-12 shrink-0"):
                            number_edit(
                                s.number_of(tag.id, pid),
                                lambda n, s=s: (save_number(s, tag, pid, n), changed()),
                                mark=f"tag-order-number-{s.id}",
                            )
                        with ui.column().classes("grow min-w-0 gap-0"):
                            ui.label(s.title or _("(untitled)")).classes(
                                "text-sm leading-tight" + (" text-grey" if s.hidden else "")
                            )
                            about = " · ".join(x for x in (s.venue, str(s.year or "")) if x)
                            ui.label(about).classes("text-xs text-grey ellipsis")
                        up = ui.button(
                            icon="arrow_upward",
                            on_click=lambda s=s: step(stats, tag, pid, s.id, -1) and changed(),
                        ).props("flat round dense size=sm")
                        up.tooltip(_("Up")).mark(f"tag-order-up-{s.id}")
                        down = ui.button(
                            icon="arrow_downward",
                            on_click=lambda s=s: step(stats, tag, pid, s.id, 1) and changed(),
                        ).props("flat round dense size=sm")
                        down.tooltip(_("Down")).mark(f"tag-order-down-{s.id}")
                        if i == 0:
                            up.disable()
                        if i == len(rows) - 1:
                            down.disable()

        listing()

        def number_all() -> None:
            save_order(tag_order(stats, tag.id, pid), tag, pid)
            changed()

        def clear_all() -> None:
            clear_numbers(tag_order(stats, tag.id, pid), tag, pid)
            changed()

        with ui.row().classes("w-full items-center gap-2"):
            numbered = ui.button(
                _("Number in this order"), icon="format_list_numbered", on_click=number_all
            )
            numbered.props("flat").tooltip(_("Number them 1, 2… as listed"))
            numbered.mark("tag-order-number-all")
            ui.button(_("Clear the numbers"), icon="backspace", on_click=clear_all).props(
                "flat color=negative"
            ).tooltip(_("Remove their numbers (the tag stays)")).mark("tag-order-clear")
            ui.space()
            ui.button(_("Close"), on_click=dlg.close).props("flat")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


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
                    ColourInput(_("Colour"), value=t.colour)
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
        colour = ColourInput(_("Colour"), value=DEFAULT_COLOUR).props("dense").classes("w-36")
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
        if state["typed"] is not None and time.monotonic() - state["at"] > NOTE_IDLE:
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
    ui.timer(NOTE_TICK, tick)
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
                    if not added:  # (its number goes with it)
                        numbers = s.period_numbers.get(pid, {}) if per else s.numbers
                        numbers.pop(tid, None)
                    chips.refresh()
                    reload()

                def renumber(n: int | None, t=t) -> None:
                    save_number(s, t, pid, n)
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
                if on:
                    number_edit(s.number_of(t.id, pid), renumber, mark=f"tag-number-{t.name}")
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
