"""Tag from a pasted list: find the person's papers of a numbered list (e.g. a report's,
copied from a PDF) and put a tag on them, with their numbers. The list split by its lines, or
read by the local LLM into references (see reflist_read; optional)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nicegui import background_tasks, ui

from .. import annotations, llm, manual, reflist, reflist_read
from ..i18n import _, ngettext
from ..reflist import Match
from ..sources.base import SourceError
from .dialogs import transient_dialog

if TYPE_CHECKING:
    from .panel import PublicationsPanel

NONE = 0  # the "no paper" choice of an item


def tag_from_list(panel: PublicationsPanel, text: str = "", *, show_tagged: bool = True) -> None:
    """The dialog, its list ``text`` given (e.g. selected in a PDF: its papers found at once);
    ``show_tagged``: the tagged papers shown afterwards (the panel filtered by the tag; a
    star list's: in the Starred tab)."""
    period = panel.period
    pid = period.id if period else None
    tags = {t.id: ("★ " if t.per_period else "") + t.name for t in panel.usable_tags()}
    state: dict = {"matches": [], "chosen": {}, "found": {}, "editing": set(), "read": None}
    # Reading with the LLM (see read): the part read, of how many; its phase, its characters.
    reading: dict = {"task": None, "part": 0, "parts": 0, "phase": "", "chars": 0}
    model = reflist_read.model()

    with panel.dialogs, transient_dialog(width="w-full max-w-5xl") as (dlg, _card):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(_("Tag from a list")).classes("text-lg font-medium")
            ui.button(icon="close", on_click=dlg.close).props("flat round")
        ui.label(
            _(
                "Paste a list of publications (e.g. numbered, copied from a PDF): each item is "
                "matched with the papers by its HAL id or DOI, else by its title. Check the "
                "matches, then put a tag on the papers, or put them in a star list (★), with "
                "their numbers in the list."
            )
        ).classes("text-sm text-grey")
        text_box = (
            ui.textarea(
                value=text,
                placeholder=_("1- Title. Authors. Venue, 2023. Lien : https://hal.science/…"),
            )
            .props("outlined")
            .classes("w-full font-mono text-sm")
            .mark("reflist-text")
        )
        with ui.row().classes("w-full items-center gap-3"):
            default = panel.starred_id if pid and panel.starred_id in tags else None
            tag = (
                ui.select(
                    tags,
                    value=default,
                    label=_("Tag to put"),
                    new_value_mode="add-unique",
                    with_input=True,
                )
                .props("dense outlined")
                .classes("min-w-48")
                .mark("reflist-tag")
            )
            if period is None:
                ui.label(_("(choose a period for a tag within a period, e.g. starred)")).classes(
                    "text-sm text-grey"
                )
            ui.space()
            with ui.element("span").tooltip(  # (a disabled button shows no tooltip)
                _(
                    "The local LLM ({model}) reads the list into references (title, authors, "
                    "venue, year, DOI), matched by their title: for a messy list (lines "
                    "broken, items run together)"
                ).format(model=model)
                if model
                else _("Turn on the local LLM and choose a model (Settings → Local LLM)")
            ):
                llm_button = (
                    ui.button(_("Read with the LLM"), icon="auto_awesome", on_click=lambda: read())
                    .props("flat color=purple")
                    .mark("reflist-llm")
                )
                llm_button.set_enabled(bool(model))
            ui.button(_("Find the papers"), icon="search", on_click=lambda: find()).mark(
                "reflist-find"
            )
        if not model:  # (said, with where to turn it on)
            with ui.row().classes("w-full items-center no-wrap gap-1").mark("reflist-llm-off"):
                ui.icon("auto_awesome", size="xs", color="grey-5")
                off = not llm.settings().enabled
                ui.label(
                    _("No reading by the local AI: the local LLM is off")
                    if off
                    else _("No reading by the local AI: no model chosen")
                ).classes("text-xs text-grey-6")
                ui.link(
                    _("Settings"),
                    "/settings?tab=llm" if off else "/settings?tab=reflist",
                    new_tab=True,
                ).classes("text-xs").mark("reflist-llm-settings")

        @ui.refreshable
        def progress() -> None:
            """The list being read by the LLM: its part, and a Stop button."""
            if reading["task"] is None:
                return
            with ui.row().classes("w-full items-center no-wrap gap-2").mark("reflist-reading"):
                ui.spinner(size="sm", color="purple")
                status = ui.label().classes("text-sm grow").mark("reflist-reading-status")
                ui.button(_("Stop"), icon="stop", on_click=stop).props(
                    "flat dense color=negative"
                ).tooltip(_("Stop reading (the parts not read yet split by their lines)")).mark(
                    "reflist-stop"
                )

            def tick() -> None:
                phase = {
                    "thinking": _("thinking ({n} characters)"),
                    "writing": _("writing ({n} characters)"),
                }.get(reading["phase"], _("reading"))
                status.set_text(
                    _("Reading the list with {model}: part {part} of {parts}, {phase}").format(
                        model=model,
                        part=min(reading["part"] + 1, reading["parts"]),
                        parts=reading["parts"],
                        phase=phase.format(n=reading["chars"]),
                    )
                )

            tick()
            ui.timer(0.5, tick)

        progress()

        @ui.refreshable
        def review() -> None:
            matches: list[Match] = state["matches"]
            if not matches:
                return
            n = sum(1 for i in range(len(matches)) if state["chosen"].get(i, NONE) != NONE)
            ui.label(_("{n} of {total} items matched").format(n=n, total=len(matches))).classes(
                "font-medium mt-2"
            ).mark("reflist-count")
            if read := state["read"]:
                _read_note(read)
            with ui.column().classes("w-full gap-1"):
                for i, m in enumerate(matches):
                    _item_row(i, m)

        def _item_row(i: int, m: Match) -> None:
            chosen = state["chosen"].get(i, NONE)
            with ui.row().classes("w-full items-start no-wrap gap-2 border-b py-1"):
                ui.icon(
                    "check_circle" if chosen != NONE else "help",
                    color="positive" if chosen != NONE else "orange-8",
                ).classes("mt-2")
                with ui.column().classes("gap-0 w-1/2 min-w-0"):
                    if i in state["editing"]:
                        _edit_item(i, m)
                    else:
                        with ui.row().classes("w-full items-start no-wrap gap-1"):
                            if m.item.title:
                                _parts(m.item)
                            else:
                                ui.label(reflist.label(m.item)).classes("text-sm grow min-w-0")
                            ui.button(icon="edit", on_click=lambda i=i: edit(i)).props(
                                "flat round dense size=sm"
                            ).tooltip(_("Edit the text to match")).mark(f"reflist-edit-{i}")
                    if m.item.url:
                        ui.link(m.item.url, m.item.url, new_tab=True).classes("text-xs")
                with ui.column().classes("gap-1 w-1/2 min-w-0"):
                    options = {NONE: _("— none")}
                    for c in m.candidates:
                        how = _("by id") if c.by_id else f"{round(100 * c.score)}%"
                        options[c.pub_id] = f"{c.title} ({c.year or '?'}) · {how}"

                    def choose(e, i=i) -> None:
                        state["chosen"][i] = e.value or NONE
                        review.refresh()

                    ui.select(options, value=chosen, on_change=choose).props(
                        "dense outlined options-dense"
                    ).classes("w-full").mark(f"reflist-choice-{i}")
                    if chosen == NONE:
                        _missing(i, m)

        def _read_note(read: dict) -> None:
            """The list read by the LLM: said (its model; the parts split by their lines)."""
            with ui.row().classes("items-center gap-1 text-xs text-grey-8").mark("reflist-read"):
                ui.icon("auto_awesome", size="xs", color="purple")
                ui.label(_("Read by the local LLM ({model})").format(model=read["model"]))
                if n := read["split"]:
                    ui.label(
                        ngettext(
                            "· {n} part of {parts} split by its lines (no valid answer{why})",
                            "· {n} parts of {parts} split by their lines (no valid answer{why})",
                            n,
                        ).format(
                            n=n,
                            parts=read["parts"],
                            why=f": {read['error']}" if read["error"] else "",
                        )
                    ).classes("text-orange-9").mark("reflist-read-split")
                if n := read["stopped"]:
                    ui.label(
                        ngettext(
                            "· stopped: {n} part split by its lines",
                            "· stopped: {n} parts split by their lines",
                            n,
                        ).format(n=n)
                    ).classes("text-orange-9").mark("reflist-read-stopped")

        def _parts(item: reflist.Item) -> None:
            """An item read by the LLM: its title, then its authors, venue and year."""
            with ui.column().classes("gap-0 grow min-w-0"):
                number = f"{item.number}. " if item.number is not None else ""
                ui.label(f"{number}{item.title}").classes("text-sm font-medium")
                authors = ", ".join(item.authors[:3]) + (" et al." if len(item.authors) > 3 else "")
                year = min(item.years) if item.years else None
                rest = " · ".join(str(x) for x in (authors, item.venue, year) if x)
                if rest:
                    ui.label(rest).classes("text-xs text-grey-8")

        def _edit_item(i: int, m: Match) -> None:
            """The item's text, to correct (e.g. a title cut by the copy) and match again."""
            if m.item.title:
                _edit_parts(i, m.item)
                return
            box = (
                ui.textarea(value=m.item.text)
                .props("outlined dense autogrow autofocus")
                .classes("w-full text-sm")
                .mark(f"reflist-item-text-{i}")
            )
            with ui.row().classes("items-center gap-1"):
                ui.button(
                    _("Match again"), icon="search", on_click=lambda: rematch(i, box.value)
                ).props("dense flat").mark(f"reflist-rematch-{i}")
                ui.button(_("Cancel"), on_click=lambda: edit(i, False)).props("dense flat")

        def _edit_parts(i: int, item: reflist.Item) -> None:
            """The parts of an item read by the LLM, to correct and match again."""
            title = (
                ui.input(_("Title"), value=item.title or "")
                .props("outlined dense autofocus")
                .classes("w-full text-sm")
                .mark(f"reflist-item-title-{i}")
            )
            authors = (
                ui.input(_("Authors"), value=", ".join(item.authors))
                .props("outlined dense")
                .classes("w-full text-sm")
            )
            with ui.row().classes("w-full no-wrap gap-1"):
                venue = (
                    ui.input(_("Venue"), value=item.venue or "")
                    .props("outlined dense")
                    .classes("grow text-sm")
                )
                year = (
                    ui.number(_("Year"), value=min(item.years) if item.years else None, step=1)
                    .props("outlined dense")
                    .classes("w-24 text-sm")
                )
            doi = (
                ui.input(_("DOI, HAL id or link"), value=item.doi or item.url or item.hal or "")
                .props("outlined dense")
                .classes("w-full text-sm")
            )

            def again() -> None:
                y = int(year.value) if year.value else None
                rematch_parts(
                    i, title.value, authors.value, venue.value, y, (doi.value or "").strip()
                )

            with ui.row().classes("items-center gap-1"):
                ui.button(_("Match again"), icon="search", on_click=again).props("dense flat").mark(
                    f"reflist-rematch-{i}"
                )
                ui.button(_("Cancel"), on_click=lambda: edit(i, False)).props("dense flat")

        def _missing(i: int, m: Match) -> None:
            """An item matching none of the papers: add it by its id, or search for it."""
            with ui.row().classes("items-center gap-2"):
                if m.item.ref:
                    ui.button(
                        _("Add {ref}").format(ref=m.item.ref),
                        icon="add",
                        on_click=lambda i=i, r=m.item.ref: add(i, r),
                    ).props("dense flat").mark(f"reflist-add-{i}")
                ui.button(
                    _("Search HAL"), icon="travel_explore", on_click=lambda i=i: search(i)
                ).props("dense flat").mark(f"reflist-search-{i}")
            found = state["found"].get(i)
            if found is not None and not found:
                ui.label(_("Nothing found on HAL")).classes("text-xs text-grey")
            for f in found or []:
                with ui.row().classes("items-center no-wrap gap-1 text-xs"):
                    ui.link(f"{f.title} ({f.year or '?'})", f.url, new_tab=True)
                    ui.label(f"{', '.join(f.authors[:3])} · {round(100 * f.score)}%").classes(
                        "text-grey"
                    )
                    ui.button(icon="add", on_click=lambda i=i, r=f.ref: add(i, r)).props(
                        "dense flat round size=sm"
                    ).tooltip(_("Add it to the papers")).mark(f"reflist-add-found-{i}")

        busy = ui.spinner(size="sm")
        busy.visible = False
        review()
        with ui.row().classes("w-full items-center"):
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Put the tag"), icon="sell", on_click=lambda: apply()).mark("reflist-apply")

    def find() -> None:
        items = reflist.split_items(text_box.value or "")
        if not items:
            ui.notify(_("No item in the text"), type="warning")
            return
        show(items)

    def show(items: list[reflist.Item], read: dict | None = None) -> None:
        """The items' matches (``read``: by the LLM, see read), the best ones chosen."""
        state["read"] = read
        state["matches"] = reflist.match(items, panel.stats)
        state["chosen"] = {
            i: m.best.pub_id for i, m in enumerate(state["matches"]) if m.best is not None
        }
        state["found"] = {}
        state["editing"] = set()
        review.refresh()

    async def read() -> None:
        """The list read by the LLM into references (see reflist_read), part after part, then
        matched; stopped: the parts not read split by their lines."""
        if reading["task"] is not None:
            return
        text = text_box.value or ""
        out = reflist_read.Reading(reflist_read.parts(text))
        if not out.parts:
            ui.notify(_("No item in the text"), type="warning")
            return

        def part(done: int, total: int) -> None:
            reading.update(part=done, parts=total, phase="", chars=0)

        def phase(name: str, chars: int) -> None:
            reading.update(phase=name, chars=chars)

        async def run() -> Exception | None:
            """(an error returned, not raised: said here, not logged as the task's)"""
            try:
                await reflist_read.read(text, out=out, progress=part, phase=phase)
            except Exception as e:
                return e
            return None

        task = background_tasks.create(run(), name="reflist-read")
        reading.update(task=task, part=0, parts=len(out.parts), phase="", chars=0, stopped=False)
        dlg.props("persistent")  # (a click outside: not closed while reading)
        llm_button.disable()
        progress.refresh()
        stopped, error = False, None
        try:
            error = await task
        except asyncio.CancelledError:
            stopped = reading.get("stopped", False)
            if not stopped:  # (the dialog closed)
                return
        finally:
            reading["task"] = None
            if not dlg.is_deleted:
                dlg.props(remove="persistent")
                llm_button.enable()
                progress.refresh()
        if error is not None:
            ui.notify(
                _("The LLM could not read the list: {error}").format(error=error), type="warning"
            )
            return
        rest = out.rest() if stopped else []
        show(
            out.items + rest,
            {
                "model": model,
                "parts": len(out.parts),
                "split": len(out.failed),
                "error": out.error,
                "stopped": len(out.parts) - out.done if stopped else 0,
            },
        )

    def stop() -> None:
        if (task := reading["task"]) is not None:
            reading["stopped"] = True
            task.cancel()

    dlg.on_value_change(
        lambda e: None if e.value or reading["task"] is None else reading["task"].cancel()
    )

    def edit(i: int, on: bool = True) -> None:
        (state["editing"].add if on else state["editing"].discard)(i)
        review.refresh()

    def rematch(i: int, text: str) -> None:
        """Match item ``i`` again, from its corrected ``text`` (its number kept)."""
        if not (text or "").strip():
            ui.notify(_("No item in the text"), type="warning")
            return
        item = reflist.item_of(state["matches"][i].item.number, text.strip())
        again = reflist.match([item], panel.stats)[0]
        state["matches"][i] = again
        state["chosen"][i] = again.best.pub_id if again.best else NONE
        state["found"].pop(i, None)
        state["editing"].discard(i)
        review.refresh()

    def rematch_parts(
        i: int, title: str, authors: str, venue: str, year: int | None, ref: str
    ) -> None:
        """Match item ``i`` (read by the LLM) again, from its corrected parts."""
        number = state["matches"][i].item.number
        item = reflist_read.structured(title, authors, venue, year, ref, number)
        if item is None:
            ui.notify(_("No title"), type="warning")
            return
        again = reflist.match([item], panel.stats)[0]
        state["matches"][i] = again
        state["chosen"][i] = again.best.pub_id if again.best else NONE
        state["found"].pop(i, None)
        state["editing"].discard(i)
        review.refresh()

    async def search(i: int) -> None:
        busy.visible = True
        try:
            state["found"][i] = await reflist.search(state["matches"][i].item)
        except SourceError as e:
            ui.notify(f"HAL: {e}", type="warning")
        finally:
            busy.visible = False
        review.refresh()

    async def add(i: int, ref: str) -> None:
        if busy.visible:
            return
        busy.visible = True
        try:
            title = await manual.add_publication(panel.person_id, ref)
        except ValueError as e:
            ui.notify(str(e), type="warning")
            return
        finally:
            busy.visible = False
        ui.notify(_("Added: {title}").format(title=title))
        await panel.reload()
        m = state["matches"][i]
        # The added paper: by the reference it was added by, else the item's own match.
        again = reflist.match([reflist.Item(m.item.number, m.item.text, hal=ref)], panel.stats)
        again = again[0] if again[0].best else reflist.match([m.item], panel.stats)[0]
        state["matches"][i] = Match(m.item, again.candidates)
        if again.best:
            state["chosen"][i] = again.best.pub_id
        state["found"].pop(i, None)
        review.refresh()

    async def apply() -> None:
        if not state["matches"]:
            ui.notify(_("Find the papers first"), type="warning")
            return
        value = tag.value
        if isinstance(value, str):  # a new tag
            value = annotations.save_tag(value, None)
            panel.tags = annotations.all_tags()
        chosen = next((t for t in panel.tags if t.id == value), None)
        if chosen is None:
            ui.notify(_("Choose a tag"), type="warning")
            return
        numbers: dict[int, int | None] = {}
        for i, m in enumerate(state["matches"]):
            pub = state["chosen"].get(i, NONE)
            if pub != NONE:
                numbers.setdefault(pub, m.item.number)
        if chosen.per_period:
            annotations.tag_numbered(chosen.id, numbers, pid)
        else:
            annotations.put_tag(chosen.id, list(numbers))
        dlg.close()
        # The tagged papers shown: a tag's by filtering on it, a star list's in the Starred tab.
        filtered = show_tagged and not chosen.per_period
        if filtered:
            panel.tag_filter = [chosen.id]
            panel.sels = []
            panel._save_state()
        await panel.reload()
        if show_tagged and chosen.per_period and panel.show_starred is not None:
            panel.show_starred()
        # The tagged papers the panel does not show (e.g. outside the period's years).
        shown = {s.id for s in panel.base_rows()} if filtered else set(numbers)
        rest = [s for s in panel.stats if s.id in numbers and s.id not in shown]
        with panel.dialogs:
            ui.notify(
                ngettext(
                    "“{tag}” put on {n} paper (not shown: {hidden})",
                    "“{tag}” put on {n} papers (not shown: {hidden})",
                    len(numbers),
                ).format(tag=chosen.name, n=len(numbers), hidden=panel.not_shown(rest))
                if rest
                else ngettext(
                    "“{tag}” put on {n} paper", "“{tag}” put on {n} papers", len(numbers)
                ).format(tag=chosen.name, n=len(numbers)),
                type="warning" if rest else None,
            )

    if text.strip():
        find()
