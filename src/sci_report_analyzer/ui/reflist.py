"""Tag from a pasted list: find the person's papers of a numbered list (e.g. a report's,
copied from a PDF) and put a tag on them, with their numbers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from .. import annotations, manual, reflist
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
    state: dict = {"matches": [], "chosen": {}, "found": {}, "editing": set()}

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
            ui.button(_("Find the papers"), icon="search", on_click=lambda: find()).mark(
                "reflist-find"
            )

        @ui.refreshable
        def review() -> None:
            matches: list[Match] = state["matches"]
            if not matches:
                return
            n = sum(1 for i in range(len(matches)) if state["chosen"].get(i, NONE) != NONE)
            ui.label(_("{n} of {total} items matched").format(n=n, total=len(matches))).classes(
                "font-medium mt-2"
            ).mark("reflist-count")
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

        def _edit_item(i: int, m: Match) -> None:
            """The item's text, to correct (e.g. a title cut by the copy) and match again."""
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
        state["matches"] = reflist.match(items, panel.stats)
        state["chosen"] = {
            i: m.best.pub_id for i, m in enumerate(state["matches"]) if m.best is not None
        }
        state["found"] = {}
        state["editing"] = set()
        review.refresh()

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
