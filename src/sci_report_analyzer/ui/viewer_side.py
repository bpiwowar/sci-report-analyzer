"""Next to a PDF (a paper's, or a document's): the details of a paper (as in the
publications panel), bookmarks, and the paper of a selection."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from .. import categories, documents, folders, manual, reflist
from ..i18n import N_, _
from ..sources.base import SourceError
from .panel import PublicationsPanel
from .pdf_viewer import changed, watch
from .pub_details import show_details

if TYPE_CHECKING:
    from ..pubview import PubStat


class _Host(PublicationsPanel):
    """The person's papers, for their details next to a PDF (as a publications panel
    without its view)."""

    def __init__(self, person_id: int, period_id: int | None, on_render) -> None:
        super().__init__(person_id)
        self._load_periods(period_id)
        self.period_id = period_id if any(p.id == period_id for p in self.periods) else None
        self._on_render = on_render
        self.dialogs = ui.element("div")
        self.container = self.dialogs
        watch(self)

    def render(self) -> None:
        self._on_render()

    def push_url(self) -> None:
        pass

    async def reload(self) -> None:
        """Reload after a change made here: the person's other windows too."""
        await self.reload_quietly()
        changed(self.person_id, but=self)

    async def reload_quietly(self) -> None:
        await PublicationsPanel.reload(self)


class Side:
    """The column next to a PDF: tabs (filled by the page: papers, categories, bookmarks,
    notes…), or the details of a paper (with the way back)."""

    def __init__(self, person_id: int, period_id: int | None, source: tuple[str, int]) -> None:
        self.person_id = person_id
        self.period_id = period_id
        self.source = source  # ("doc", document id) or ("pub", paper id): the PDF shown
        # The folder of the period, whose categories excerpts are filed in.
        self.folder = folders.folder_of_period(period_id)
        self.on_render: list[Callable[[], None]] = []
        self.host = _Host(person_id, period_id, lambda: [f() for f in self.on_render])
        self.bookmarks: Callable[[], None] | None = None
        self.wide: set[str] = set()  # (tabs shown wider, as the details of a paper)
        self.categories: Callable[[], None] | None = None
        # A link from a selection to a paper (documents): (pub id, page, rects, text).
        self.link: Callable[[int, int, list, str], None] | None = None

    def attach(self, box: ui.column) -> None:
        self.box = box
        with box:
            self.overview = ui.column().classes("w-full gap-1")
            with self.overview:
                self.tab_bar = (
                    ui.tabs(on_change=self._fit).props("dense align=left").classes("w-full")
                )
                self.panels = ui.tab_panels(self.tab_bar).classes("w-full")
            self.details = ui.column().classes("w-full gap-2 vr-details").mark("pdf-details")
            self.details.visible = False

    def section(self, name: str, icon: str, tip: str, *, wide: bool = False) -> ui.tab_panel:
        """A tab of the side column (the first one is shown)."""
        if wide:
            self.wide.add(name)
        with self.tab_bar:
            ui.tab(name, label="", icon=icon).tooltip(tip).mark(f"side-tab-{name}")
        if self.tab_bar.value is None:
            self.tab_bar.value = name
        with self.panels:
            return ui.tab_panel(name).classes("p-0 gap-2")

    def select(self, name: str) -> None:
        self.back()
        self.box.set_visibility(True)
        self.tab_bar.value = name

    @property
    def stats(self) -> list[PubStat]:
        return self.host.stats

    async def load(self) -> None:
        await self.host.reload_quietly()

    def show_paper(self, pub_id: int, header: Callable[[], None] | None = None) -> None:
        """The details of a paper (as in the publications panel), instead of the overview."""
        s = next((x for x in self.host.stats if x.id == pub_id), None)
        if s is None:
            ui.notify(_("This paper is no longer in the database"), type="warning")
            return
        self.overview.visible = False
        self.details.visible = True
        self.box.set_visibility(True)
        self._fit()
        self.details.clear()
        with self.details:
            ui.button(_("Back"), icon="arrow_back", on_click=self.back).props("flat dense").mark(
                "pdf-side-back"
            )
            if header:
                header()
            card = ui.column().classes("w-full gap-2")
        show_details(self.host, s, card, self.back)

    def back(self) -> None:
        self.details.visible = False
        self.details.clear()
        self.overview.visible = True
        self._fit()

    def _fit(self) -> None:
        """Wider for the details of a paper."""
        if self.details.visible or self.tab_bar.value in self.wide:
            self.box.style("width:40rem")
        else:
            self.box.style(remove="width:40rem")

    async def add_bookmark(self, kind: str, key: int) -> None:
        loc = await ui.run_javascript("vrPdf.location()")
        if not loc:
            ui.notify(_("The PDF is not shown yet"), type="warning")
            return
        name = " ".join((loc.get("text") or "").split())[:80]
        documents.add_bookmark(kind, key, name, int(loc["p"]), loc.get("y"))
        if self.bookmarks:
            self.bookmarks()
        where = name or _("page {page}").format(page=loc["p"])
        ui.notify(_("Bookmarked: {name}").format(name=where))

    async def add_excerpt(self) -> None:
        """File the selected text in one of the folder's categories."""
        if self.folder is None:
            ui.notify(
                _("Excerpts are filed in a folder's categories: open the PDF from a folder"),
                type="warning",
            )
            return
        sel = await ui.run_javascript("vrPdf.selection() || vrPdf.highlighted()")
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(
                _("Select the text to file (or an area with text, or click a highlight) first"),
                type="warning",
            )
            return
        page, rects = sel.get("p"), sel.get("rects") or []
        category_picker(
            self,
            sel["text"],
            lambda cat_id, **props: self.file_excerpt(cat_id, sel["text"], page, rects, **props),
            properties=True,
            merge=lambda lead, ref_only: self.merge_excerpt(
                lead, sel["text"], page, rects, ref_only=ref_only
            ),
        )

    def file_excerpt(
        self, category_id: int, text: str, page: int | None, rects: list, **props
    ) -> None:
        """``props``: its years and influence flag (see categories.add_excerpt)."""
        kind, key = self.source
        categories.add_excerpt(
            category_id,
            self.period_id,
            text,
            page,
            rects,
            document_id=key if kind == "doc" else None,
            publication_id=key if kind == "pub" else None,
            **props,
        )
        self.refresh_excerpts()

    def merge_excerpt(
        self,
        lead: categories.ExcerptView,
        text: str,
        page: int | None,
        rects: list,
        *,
        ref_only: bool = False,
    ) -> None:
        """File a passage with an excerpt (``ref_only``: only its place is cited)."""
        kind, key = self.source
        new = categories.add_excerpt(
            lead.category_id,
            self.period_id,
            text,
            page,
            rects,
            document_id=key if kind == "doc" else None,
            publication_id=key if kind == "pub" else None,
        )
        if why := categories.merge_excerpts(new, lead.id, ref_only=ref_only):
            ui.notify(why, type="warning")
        else:
            ui.notify(_("Merged with the excerpt"))
        self.refresh_excerpts()

    def refresh_excerpts(self) -> None:
        """The categories' tab, and the excerpts of this PDF tinted on its pages."""
        if self.categories:
            self.categories()
        kind, key = self.source
        if self.period_id is None or kind != "doc":
            return
        names = {n.id: n.path for n in categories.tree(self.folder[0])} if self.folder else {}
        marks = categories.tints(self.period_id, key, names)
        self.box.client.run_javascript(f"vrDoc.mark({json.dumps(marks)})")

    async def find_selection(self) -> None:
        sel = await ui.run_javascript("vrPdf.selection()")
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(_("Select the reference (or its title) in the PDF first"), type="warning")
            return
        find_dialog(self, sel["text"], sel.get("p"), sel.get("rects") or [])


def bookmarks_section(kind: str, key: int) -> Callable[[], None]:
    """The bookmarks of a PDF (to jump to, rename, delete); returns its refresh."""
    ui.label(_("Bookmarks")).classes("font-medium")

    @ui.refreshable
    def listing() -> None:
        marks = documents.bookmarks(kind, key)
        if not marks:
            ui.label(
                _("None: ‘bookmark’ in the header adds one (here, or at a selection)")
            ).classes("text-sm text-grey")
        for i, b in enumerate(marks):
            with ui.row().classes("w-full items-center no-wrap gap-1").mark(f"bookmark-{i}"):
                ui.icon("bookmark", size="xs", color="primary")
                y = json.dumps(b.get("y"))
                ui.label(b["name"]).classes(
                    "grow min-w-0 ellipsis cursor-pointer text-primary hover:underline"
                ).on("click", js_handler=f"() => vrPdf.go({b['p']}, {y})").tooltip(b["name"])
                ui.label(_("p. {page}").format(page=b["p"])).classes("text-xs text-grey shrink-0")
                ui.button(icon="edit", on_click=lambda i=i: rename(i)).props(
                    "flat dense round size=xs"
                ).mark(f"bookmark-rename-{i}")
                ui.button(icon="close", on_click=lambda i=i: remove(i)).props(
                    "flat dense round size=xs color=grey"
                ).mark(f"bookmark-remove-{i}")

    def remove(i: int) -> None:
        marks = documents.bookmarks(kind, key)
        marks.pop(i)
        documents.set_bookmarks(kind, key, marks)
        listing.refresh()

    def rename(i: int) -> None:
        marks = documents.bookmarks(kind, key)
        with ui.dialog() as dlg, ui.card().classes("w-96"):
            name = (
                ui.input(_("Name"), value=marks[i]["name"]).classes("w-full").mark("bookmark-name")
            )

            def ok() -> None:
                if name.value.strip():
                    marks[i]["name"] = name.value.strip()
                    documents.set_bookmarks(kind, key, marks)
                dlg.close()
                listing.refresh()

            name.on("keydown.enter", ok)
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                ui.button(_("Rename"), on_click=ok).mark("bookmark-rename-ok")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    listing()
    return listing.refresh


def find_dialog(side: Side, text: str, page: int | None, rects: list) -> None:
    """The paper of a selection (a reference, a title): among the person's papers (to show,
    or to link the selection to), else on HAL (to add)."""
    item = documents.selection_item(text)
    state: dict = {"found": None}
    with side.host.dialogs, ui.dialog() as dlg, ui.card().classes("w-full max-w-2xl"):
        ui.label(_("Find the paper")).classes("text-lg font-medium")
        ui.label(reflist.label(item, 300)).classes("text-sm text-grey").mark("find-text")
        busy = ui.spinner(size="sm")
        busy.visible = False

        def show(pub_id: int) -> None:
            dlg.close()
            side.show_paper(pub_id)

        def link(pub_id: int) -> None:
            dlg.close()
            assert side.link is not None and page is not None
            side.link(pub_id, page, rects, text)

        @ui.refreshable
        def body() -> None:
            untitled = _("(untitled)")
            rows = [r for r in side.stats if not r.hidden]
            [m] = reflist.match([item], rows)
            if m.candidates:
                ui.label(_("Among the papers")).classes("font-medium")
            else:
                ui.label(_("None of the papers matches")).classes("text-grey").mark("find-none")
            for c in m.candidates:
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.label(f"{c.title or untitled} ({c.year or '?'})").classes("grow min-w-0")
                    ui.label(_("its id") if c.by_id else f"{round(100 * c.score)}%").classes(
                        "text-xs text-grey shrink-0"
                    )
                    ui.button(_("Show"), icon="article", on_click=lambda c=c: show(c.pub_id)).props(
                        "flat dense"
                    ).mark(f"find-show-{c.pub_id}")
                    if side.link and page:
                        ui.button(
                            _("Link here"), icon="link", on_click=lambda c=c: link(c.pub_id)
                        ).props("flat dense").tooltip(
                            _("Link the selection to this paper (in this document)")
                        ).mark(f"find-link-{c.pub_id}")
            with ui.row().classes("items-center gap-2"):
                if item.ref and not any(c.by_id for c in m.candidates):
                    ui.button(
                        _("Add {ref}").format(ref=item.ref),
                        icon="add",
                        on_click=lambda: add(item.ref),
                    ).props("flat dense").mark("find-add-ref")
                ui.button(_("Search HAL"), icon="travel_explore", on_click=search).props(
                    "flat dense"
                ).mark("find-search")
            found = state["found"]
            if found is not None and not found:
                ui.label(_("Nothing found on HAL")).classes("text-xs text-grey")
            for f in found or []:
                with ui.row().classes("w-full items-center no-wrap gap-1 text-xs"):
                    ui.link(f"{f.title} ({f.year or '?'})", f.url, new_tab=True).classes(
                        "grow min-w-0"
                    )
                    ui.label(f"{', '.join(f.authors[:3])} · {round(100 * f.score)}%").classes(
                        "text-grey"
                    )
                    ui.button(icon="add", on_click=lambda r=f.ref: add(r)).props(
                        "dense flat round size=sm"
                    ).tooltip(_("Add it to the papers")).mark("find-add-found")

        async def search() -> None:
            busy.visible = True
            try:
                state["found"] = await reflist.search(item)
            except SourceError as e:
                ui.notify(f"HAL: {e}", type="warning")
            finally:
                busy.visible = False
            body.refresh()

        async def add(ref: str) -> None:
            if busy.visible:
                return
            busy.visible = True
            try:
                title = await manual.add_publication(side.person_id, ref)
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return
            finally:
                busy.visible = False
            ui.notify(_("Added: {title}").format(title=title))
            state["found"] = None
            await side.host.reload()
            body.refresh()

        body()
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Close"), on_click=dlg.close).props("flat")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


# An excerpt's colours (its tint on the PDF; None: the default, amber).
COLOURS = {
    None: "#ffc107",
    "#4caf50": N_("green"),
    "#2196f3": N_("blue"),
    "#e91e63": N_("pink"),
    "#9c27b0": N_("purple"),
    "#ff5722": N_("orange"),
}


def excerpt_properties(
    start: int | None = None,
    end: int | None = None,
    influence: bool = False,
    colour: str | None = None,
) -> Callable[[], dict]:
    """The fields of an excerpt's years, influence flag and colour; returns their values
    (the keywords of categories.add_excerpt / update_excerpt)."""
    chosen = {"colour": colour if colour in COLOURS else None}

    def year(v: float | None) -> int | None:
        return int(v) if v else None

    with ui.row().classes("items-center gap-2"):
        start_in = ui.number(_("From (year)"), value=start, format="%d").props("dense outlined")
        start_in.classes("w-32").mark("excerpt-start")
        end_in = ui.number(_("To (year)"), value=end, format="%d").props("dense outlined")
        end_in.classes("w-32").mark("excerpt-end")
        flag = ui.checkbox(_("Influence"), value=influence).mark("excerpt-influence")
        flag.tooltip(_("Shows the person's influence (“rayonnement”: invited talks, prizes…)"))
        ui.icon("public", size="xs", color="teal").classes("-ml-2")

    @ui.refreshable
    def swatches() -> None:
        with ui.row().classes("items-center gap-1"):
            ui.label(_("Colour")).classes("text-sm text-grey")
            for c in COLOURS:
                ring = "ring-2 ring-offset-1 ring-grey-8" if c == chosen["colour"] else ""
                ui.element("div").classes(f"w-5 h-5 rounded-full cursor-pointer {ring}").style(
                    f"background: {c or COLOURS[None]}"
                ).on("click", lambda c=c: (chosen.update(colour=c), swatches.refresh())).tooltip(
                    _(COLOURS[c]) if c else _("amber (the default)")
                ).mark(f"excerpt-colour-{(c or 'default').lstrip('#')}")

    swatches()
    return lambda: {
        "start": year(start_in.value),
        "end": year(end_in.value),
        "influence": bool(flag.value),
        "colour": chosen["colour"],
    }


def similar_excerpts(
    side: Side, text: str, merge: Callable[[categories.ExcerptView, bool], None]
) -> None:
    """The excerpts a passage might be already (a warning), or found by their words: to merge
    it with one (keeping its text, or only its place)."""
    names = {n.id: n.path for n in categories.tree(side.folder[0])} if side.folder else {}
    found = categories.similar_excerpts(side.period_id, text)
    with (
        ui.expansion(
            _("Might be already an excerpt ({n}): merge with it?").format(n=len(found))
            if found
            else _("Merge with an excerpt"),
            icon="warning" if found else "call_merge",
            value=bool(found),
        )
        .classes("w-full" + (" bg-amber-1 rounded" if found else ""))
        .props("dense" + (" header-class=text-amber-10" if found else ""))
        .mark("excerpt-similar")
    ):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            search = (
                ui.input(placeholder=_("Find an excerpt (its words, its PDF)"))
                .props("dense outlined clearable")
                .classes("grow")
                .mark("excerpt-similar-search")
            )
            mode = (
                ui.toggle({False: _("Keep its text"), True: _("Only its place")}, value=False)
                .props("dense no-caps size=sm")
                .mark("excerpt-similar-mode")
            )
            mode.tooltip(
                _("Merged, the passage is quoted too, or only its page is cited (as a reference)")
            )

        @ui.refreshable
        def listing() -> None:
            q = (search.value or "").strip()
            shown = categories.similar_excerpts(side.period_id, text, q) if q else found
            if not shown:
                ui.label(
                    _("No excerpt has these words") if q else _("Type to find an excerpt")
                ).classes("text-sm text-grey")
            for e in shown[:8]:
                with (
                    ui.row()
                    .classes("w-full items-start no-wrap gap-1")
                    .mark(f"excerpt-similar-{e.id}")
                ):
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(e.quoted).classes("text-sm line-clamp-2")
                        where = ", ".join(sorted({x.source for x in e.parts}))
                        ui.label(f"{names.get(e.category_id, '')} · {where}").classes(
                            "text-xs text-grey ellipsis"
                        )
                    ui.button(
                        icon="call_merge", on_click=lambda e=e: merge(e, bool(mode.value))
                    ).props("flat dense round size=sm color=primary").tooltip(
                        _("Merge with it (in its category)")
                    ).mark(f"excerpt-similar-merge-{e.id}")

        search.on_value_change(listing.refresh)
        with ui.column().classes("w-full gap-1 max-h-64 overflow-auto"):
            listing()


def category_picker(
    side: Side,
    text: str,
    chosen: Callable[..., None],
    *,
    title: str = N_("Add to a category"),
    done: str = N_("Added to {category}"),
    current: int | None = None,
    properties: bool = False,
    merge: Callable[[categories.ExcerptView, bool], None] | None = None,
) -> None:
    """Choose the category of an excerpt (``chosen`` is called with it): click it, or type to
    find it (Enter: the first one), or name a new one. ``title``, ``done`` (its
    ``{category}`` named): marked with ``N_``, translated here. ``current``: its category, shown;
    ``properties``: also its years and influence flag (passed to ``chosen`` as keywords);
    ``merge``: or merge it with an excerpt (called with it, and whether only its place is
    cited), the similar ones shown with a warning."""
    folder_id, folder_name = side.folder
    with side.host.dialogs, ui.dialog() as dlg, ui.card().classes("w-full max-w-xl"):
        ui.label(_(title)).classes("text-lg font-medium")
        short = text if len(text) <= 300 else text[:299] + "…"
        ui.label(f"“{' '.join(short.split())}”").classes("text-sm text-grey").mark("excerpt-text")
        if merge is not None and side.period_id is not None:
            similar_excerpts(
                side, text, lambda lead, ref_only: (dlg.close(), merge(lead, ref_only))
            )
        props = excerpt_properties() if properties else dict
        search = (
            ui.input(
                placeholder=_("Find a category of {folder} (Enter: the first one)").format(
                    folder=folder_name
                )
            )
            .props("dense outlined clearable autofocus")
            .classes("w-full")
            .mark("category-search")
        )

        def matching() -> list[categories.Node]:
            q = (search.value or "").lower().strip()
            nodes = categories.tree(folder_id)
            return [n for n in nodes if q in n.path.lower()] if q else nodes

        def pick(cat_id: int) -> None:
            if cat_id == current:
                return
            chosen(cat_id, **props())
            dlg.close()
            name = next((n.path for n in categories.tree(folder_id) if n.id == cat_id), "")
            ui.notify(_(done).format(category=name))

        @ui.refreshable
        def listing() -> None:
            nodes = matching()
            if not nodes:
                ui.label(
                    _("No category yet: type a name, then Enter (or ‘create’)")
                    if not search.value
                    else _("No such category: Enter (or ‘create’) creates it")
                ).classes("text-sm text-grey").mark("category-none")
            q = (search.value or "").strip()
            for n in nodes:
                indent = 0 if q else n.depth
                with (
                    ui.row()
                    .classes("w-full items-center no-wrap gap-1 cursor-pointer hover:bg-grey-2")
                    .style(f"padding-left:{1.2 * indent}rem")
                    .on("click", lambda n=n: pick(n.id))
                    .mark(f"pick-category-{n.id}")
                ):
                    ui.icon("label" if n.depth else "folder_special", size="xs", color="primary")
                    ui.label(n.path if q else n.name).classes(
                        "grow" + (" text-grey" if n.id == current else "")
                    )
                    if n.id == current:
                        ui.label(_("(its category)")).classes("text-xs text-grey")
                    if n.years:
                        ui.label(n.years).classes("text-xs text-grey")

        def enter() -> None:
            nodes = matching()
            if nodes:
                pick(nodes[0].id)
            elif (search.value or "").strip():
                create()

        def create() -> None:
            """A new category (the dialog stays open: click it to file the excerpt)."""
            name = (search.value or "").strip()
            if not name:
                return
            categories.add(folder_id, name, parent.value or None)
            search.value = ""
            listing.refresh()
            parent.set_options(parents(), value=parent.value)
            ui.notify(_("Category “{name}” created").format(name=name))

        def parents() -> dict[int, str]:
            return {0: _("(top level)"), **{n.id: n.path for n in categories.tree(folder_id)}}

        search.on_value_change(lambda: listing.refresh())
        search.on("keydown.enter", enter)
        with ui.column().classes("w-full gap-0 max-h-96 overflow-auto"):
            listing()
        with ui.row().classes("w-full items-center gap-2"):
            parent = (
                ui.select(parents(), value=0, label=_("Create under"))
                .props("dense outlined options-dense")
                .classes("w-56")
                .mark("category-parent")
            )
            ui.button(_("Create"), icon="add", on_click=create).props("flat dense").tooltip(
                _("Create a category named as typed above")
            ).mark("category-create")
            ui.space()
            ui.button(_("Close"), on_click=dlg.close).props("flat").mark("category-close")
    dlg.on_value_change(lambda e: None if e.value else dlg.delete())
    dlg.open()


def categories_section(side: Side) -> Callable[[], None]:
    """The excerpts of the person (in the folder) by category: to go to (in this PDF, or
    another one), edit, move, merge (drag one onto another, or its merge icon then a click
    on the other), remove, copy as Markdown; returns its refresh."""
    state: dict[str, categories.ExcerptView | None] = {"merging": None}
    ui.add_css(
        ".vr-drop { outline: 2px dashed #ffa000; outline-offset: 1px; }"
        " .vr-drop-before { box-shadow: inset 0 2px #ffa000; }"
        " .vr-drop-after { box-shadow: inset 0 -2px #ffa000; }"
    )

    @ui.refreshable
    def listing() -> None:
        if side.folder is None or side.period_id is None:
            ui.label(
                _("Excerpts are filed in the categories of a folder: open the PDF from one.")
            ).classes("text-sm text-grey")
            return
        folder_id, folder_name = side.folder
        nodes = categories.tree(folder_id)
        with ui.row().classes("w-full items-center gap-1"):
            ui.label(_("Categories of {folder}").format(folder=folder_name)).classes(
                "font-medium grow"
            )
            ui.button(
                icon="content_copy",
                on_click=lambda: (
                    ui.clipboard.write(categories.markdown(folder_id, side.period_id)),
                    ui.notify(_("Copied (Markdown)")),
                ),
            ).props("flat dense round size=sm").tooltip(_("Copy the excerpts, as Markdown")).mark(
                "excerpts-copy"
            )
            ui.button(icon="badge", on_click=name_documents).props(
                "flat dense round size=sm"
            ).tooltip(_("Name the documents (as cited in the copied excerpts)")).mark(
                "excerpts-sources"
            )
            ui.button(icon="edit", on_click=lambda: edit(folder_id)).props(
                "flat dense round size=sm"
            ).tooltip(_("Edit the categories")).mark("categories-edit")
        if not nodes:
            ui.label(
                _(
                    "None yet: select a passage, then ‘add to a category’ in the header (or "
                    "edit the categories)."
                )
            ).classes("text-sm text-grey")
            return
        by_cat: dict[int, list[categories.ExcerptView]] = {}
        for e in categories.excerpts(side.period_id, grouped=True):
            by_cat.setdefault(e.category_id, []).append(e)
        merging = state["merging"]
        if merging:
            with ui.row().classes("w-full items-center no-wrap gap-1 bg-amber-1 p-1 rounded"):
                short = merging.text if len(merging.text) <= 60 else merging.text[:59] + "…"
                ui.label(_("Click the excerpt to merge “{text}” with").format(text=short)).classes(
                    "text-sm grow"
                )
                ui.button(icon="close", on_click=lambda: merge_mode(None)).props(
                    "flat dense round size=xs"
                ).tooltip(_("Cancel")).mark("excerpt-merge-cancel")
        for n in nodes:
            items = by_cat.get(n.id, [])
            with (
                ui.row()
                .classes("w-full items-center gap-1")
                .style(f"padding-left:{1.2 * n.depth}rem")
            ):
                ui.label(n.name).classes("font-medium text-sm" if not n.depth else "text-sm")
                if n.years:
                    ui.label(n.years).classes("text-xs text-grey")
                if items:
                    ui.badge(str(len(items))).props("rounded color=amber-8")
            for e in items:  # (an excerpt, or a group: its lead, then the others)
                with (
                    ui.column()
                    .classes("w-full gap-0")
                    .style(f"padding-left:{1.2 * n.depth + 0.6}rem")
                    .props('draggable="true"')
                    .mark(f"excerpt-{e.id}") as block
                ):
                    _droppable(block, e, lambda source_id, zone, e=e: dropped(source_id, zone, e))
                    if e.group_text:
                        ui.label(e.group_text).classes("text-sm line-clamp-4").style(
                            f"border-left: 3px solid {e.colour or COLOURS[None]}; "
                            "padding-left: 0.4rem"
                        ).tooltip(_("The text of the group (edit it with its pencil)")).mark(
                            f"excerpt-group-text-{e.id}"
                        )
                    for x in e.parts:
                        quote(x, e, merging)

    def quote(
        x: categories.ExcerptView,
        lead: categories.ExcerptView,
        merging: categories.ExcerptView | None,
    ) -> None:
        """An excerpt's row (``lead``: that of its group): its text, where, the group's
        years, influence and actions (the others of a group: taken out, removed)."""
        kind, key = side.source
        here = (x.document_id if kind == "doc" else x.publication_id) == key
        member = x.id != lead.id
        cited = bool(lead.group_text) or x.ref_only  # (only its place, in the group)
        with ui.row().classes("w-full items-start no-wrap gap-1").mark(f"excerpt-part-{x.id}"):
            ui.icon("subdirectory_arrow_right" if member else "format_quote", size="xs").classes(
                "mt-1"
            ).style(f"color: {lead.colour or COLOURS[None]}")
            with ui.column().classes("gap-0 grow min-w-0"):
                label = ui.label(x.text).classes(
                    "cursor-pointer hover:underline "
                    + ("text-xs text-grey italic line-clamp-1" if cited else "text-sm line-clamp-3")
                )
                if x.ref_only:
                    label.tooltip(_("Only its place is cited (merged as a reference)"))
                if merging and merging.id != lead.id:
                    label.on("click", lambda: merge(merging.id, lead))
                elif here and x.page:
                    on_page = [r for r in x.rects if len(r) < 5 or r[4] == x.page]
                    top = max((r[3] for r in on_page), default=None)
                    y = json.dumps(top + 20 if top is not None else None)
                    label.on("click", js_handler=f"() => vrPdf.go({x.page}, {y})")
                else:
                    url = excerpt_url(x)
                    label.on("click", js_handler=f"() => window.open({json.dumps(url)})")
                where = (
                    _("p. {page}").format(page=x.page)
                    if here
                    else _("{source}, p. {page}").format(source=x.source, page=x.page or "?")
                )
                with ui.row().classes("w-full items-center no-wrap gap-1"):
                    ui.label(where).classes("text-xs text-grey ellipsis")
                    if not member and lead.years:
                        ui.label(lead.years).classes("text-xs text-grey-8 shrink-0").mark(
                            f"excerpt-years-{x.id}"
                        )
                    if not member and lead.influence:
                        ui.icon("public", size="xs", color="teal").classes("shrink-0").tooltip(
                            _("Influence")
                        ).mark(f"excerpt-influence-{x.id}")
                    cite = (  # (in a group, its text not edited: quote it, or only cite it)
                        [
                            (
                                "cite",
                                "format_quote" if x.ref_only else "link",
                                _("Quote its text too")
                                if x.ref_only
                                else _("Only cite its place (a reference)"),
                                lambda x: (
                                    categories.set_ref_only(x.id, not x.ref_only),
                                    side.refresh_excerpts(),
                                ),
                            )
                        ]
                        if lead.members and not lead.group_text
                        else []
                    )
                    actions = (
                        [
                            *cite,
                            ("split", "call_split", _("Take it out of the group"), split),
                            ("remove", "delete", _("Remove"), remove),
                        ]
                        if member
                        else [
                            ("edit", "edit", _("Edit (text, years, influence…)"), edit_excerpt),
                            *cite,
                            ("move", "drive_file_move", _("Move to another category"), move),
                            (
                                "merge",
                                "call_merge",
                                _(
                                    "Merge with another excerpt: click it then (or drag this "
                                    "one onto it; onto its top or bottom edge: placed before or "
                                    "after)"
                                ),
                                merge_mode,
                            ),
                            ("remove", "delete", _("Remove"), remove),
                        ]
                    )
                    for mark, icon, tip, act in actions:
                        ui.button(icon=icon, on_click=lambda act=act: act(x)).props(
                            "flat dense round size=xs color="
                            + ("negative" if mark == "remove" else "grey-7")
                        ).classes("shrink-0").tooltip(tip).mark(f"excerpt-{mark}-{x.id}")

    def dropped(source_id: int, zone: str, target: categories.ExcerptView) -> None:
        """An excerpt dropped on another one: placed before / after it, or merged."""
        if zone in ("before", "after"):
            categories.place_excerpt(source_id, target.id, zone)
            side.refresh_excerpts()
        else:
            merge(source_id, target)

    def split(x: categories.ExcerptView) -> None:
        categories.split_excerpt(x.id)
        side.refresh_excerpts()

    def edit(folder_id: int) -> None:
        with side.host.dialogs:  # (outside the listing, refreshed after each change)
            categories_dialog(folder_id, side.refresh_excerpts)

    def name_documents() -> None:
        """Rename the documents the excerpts come from (e.g. "001038323Dossier-…_1.0.0" as
        "Application"), their names being their references in the copied excerpts."""
        docs = categories.cited_documents(side.period_id) if side.period_id else []
        if not docs:
            ui.notify(_("No excerpt from a document yet"))
            return
        with side.host.dialogs, ui.dialog() as dlg, ui.card().classes("w-[32rem]"):
            ui.label(_("Names of the documents")).classes("font-medium")
            ui.label(_("As cited in the copied excerpts: %% Application, p. 4 %%")).classes(
                "text-sm text-grey"
            )
            inputs = [
                (i, ui.input(value=name).props("dense").classes("w-full").mark(f"doc-name-{i}"))
                for i, name in docs
            ]

            def ok() -> None:
                for i, field in inputs:
                    documents.rename(i, field.value)
                dlg.close()
                side.refresh_excerpts()

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                ui.button(_("Save"), on_click=ok).mark("doc-names-ok")
        dlg.on_value_change(lambda e: None if e.value else dlg.delete())
        dlg.open()

    def merge_mode(e: categories.ExcerptView | None) -> None:
        state["merging"] = e
        listing.refresh()

    def merge(source_id: int, target: categories.ExcerptView) -> None:
        """Merge an excerpt into ``target``, once confirmed."""
        state["merging"] = None
        source = next((x for x in categories.excerpts(side.period_id) if x.id == source_id), None)
        if source is None or source.id == target.id:
            listing.refresh()
            return

        def yes() -> None:
            dialog.close()
            if why := categories.merge_excerpts(source.id, target.id, ref_only=bool(mode.value)):
                ui.notify(why, type="warning")
            side.refresh_excerpts()

        def short(t: str) -> str:
            return t if len(t) <= 80 else t[:79] + "…"

        with side.host.dialogs, ui.dialog() as dialog, ui.card():
            ui.label(
                _("Merge “{first}” with “{second}”?").format(
                    first=short(source.text), second=short(target.text)
                )
            )
            ui.label(
                _(
                    "They are on one item (in the order of their PDFs), with the second one's "
                    "category, years, influence, colour and text (its pencil edits the text of "
                    "the group)."
                )
            ).classes("text-sm text-grey")
            mode = (
                ui.radio(
                    {
                        False: _("Keep both texts (two quotes)"),
                        True: _("Keep only the place of the first one (a reference)"),
                    },
                    value=False,
                )
                .props("dense")
                .mark("excerpt-merge-mode")
            )
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dialog.close).props("flat")
                ui.button(_("Merge"), on_click=yes).mark("excerpt-merge-confirm")
        dialog.on("hide", lambda: (dialog.delete(), listing.refresh()))
        dialog.open()

    def remove(e: categories.ExcerptView) -> None:
        def yes() -> None:
            categories.remove_excerpt(e.id)
            dialog.close()
            side.refresh_excerpts()

        short = e.text if len(e.text) <= 120 else e.text[:119] + "…"
        with side.host.dialogs, ui.dialog() as dialog, ui.card():
            ui.label(_("Remove the excerpt “{text}”?").format(text=short))
            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dialog.close).props("flat")
                ui.button(_("Remove"), on_click=yes).props("color=negative").mark(
                    "excerpt-remove-confirm"
                )
        dialog.on("hide", dialog.delete)
        dialog.open()

    def move(e: categories.ExcerptView) -> None:
        def to(cat_id: int, **_props) -> None:
            categories.move_excerpt(e.id, cat_id)
            side.refresh_excerpts()

        category_picker(
            side,
            e.text,
            to,
            title=N_("Move to a category"),
            done=N_("Moved to {category}"),
            current=e.category_id,
        )

    def originals(e: categories.ExcerptView, text: ui.textarea) -> None:
        """The passages as selected (of each excerpt of a group): where, how edited since; to
        copy into the text (that of a group: added to it)."""
        ui.label(_("As selected") if not e.members else _("The excerpts, as selected")).classes(
            "text-sm font-medium"
        )
        with ui.column().classes("w-full gap-1 max-h-64 overflow-auto"):
            for x in e.parts:
                original = x.original or x.text

                def use(original: str = original) -> None:
                    current = (text.value or "").strip()
                    text.value = f"{current} […] {original}" if e.members and current else original

                with (
                    ui.row()
                    .classes("w-full items-start no-wrap gap-1")
                    .mark(f"excerpt-original-{x.id}")
                ):
                    ui.icon("link" if x.ref_only else "format_quote", size="xs").classes(
                        "mt-1 text-grey"
                    )
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(original).classes("text-sm")
                        where = _("{source}, p. {page}").format(source=x.source, page=x.page or "?")
                        if x.ref_only:
                            where += " · " + _("cited by its place only")
                        ui.label(where).classes("text-xs text-grey")
                        if x.text != original:
                            ui.label(_("Edited: {text}").format(text=x.text)).classes(
                                "text-xs text-grey-8 italic"
                            )
                    ui.button(icon="content_paste_go", on_click=use).props(
                        "flat dense round size=xs color=grey-7"
                    ).tooltip(
                        _("Add it to the text") if e.members else _("Back to it (as selected)")
                    ).mark(f"excerpt-original-use-{x.id}")

    def edit_excerpt(e: categories.ExcerptView) -> None:
        """Its text (that of a group), years and "influence" flag; the passages as selected
        shown."""
        with side.host.dialogs, ui.dialog() as dialog, ui.card().classes("w-[32rem]"):
            ui.label(_("Merged excerpts") if e.members else _("Excerpt")).classes("text-lg")
            text = (
                ui.textarea(_("Text of the group") if e.members else _("Text"), value=e.quoted)
                .props("outlined autogrow")
                .classes("w-full")
            )
            text.mark("excerpt-edit-text")
            if e.members:
                ui.label(
                    _(
                        "Its quotes, joined; edited, it is the text of the group (its excerpts: "
                        "their places only). Blank: its quotes again."
                    )
                ).classes("text-xs text-grey")
            originals(e, text)
            props = excerpt_properties(e.start_year, e.end_year, e.influence, e.colour)

            def save() -> None:
                if e.members:
                    joined = " […] ".join(x.text for x in e.parts if not x.ref_only)
                    edited = " ".join((text.value or "").split())
                    categories.set_group_text(e.id, None if edited == joined else edited)
                categories.update_excerpt(e.id, text=None if e.members else text.value, **props())
                dialog.close()
                side.refresh_excerpts()

            with ui.row().classes("w-full justify-end"):
                ui.button(_("Cancel"), on_click=dialog.close).props("flat")
                ui.button(_("Save"), on_click=save).mark("excerpt-edit-save")
        dialog.on_value_change(lambda ev: None if ev.value else dialog.delete())
        dialog.open()

    listing()
    return listing.refresh


def _droppable(
    block: ui.element, e: categories.ExcerptView, dropped: Callable[[int, str], None]
) -> None:
    """An excerpt's block, dragged (its id) and dropped on: ``dropped`` with the id of the
    one dropped, and where: on its top or bottom quarter "before" / "after" it, else
    "merge"."""
    zone = (
        "const r = ev.currentTarget.getBoundingClientRect(), f = (ev.clientY - r.top) / r.height;"
        " const z = f < 0.25 ? 'before' : f > 0.75 ? 'after' : 'merge';"
    )
    clear = "ev.currentTarget.classList.remove('vr-drop', 'vr-drop-before', 'vr-drop-after');"
    block.on(
        "dragstart",
        js_handler="(ev) => { ev.stopPropagation(); "
        f"ev.dataTransfer.setData('text/plain', 'excerpt:{e.id}'); }}",
    )
    block.on(
        "dragover",
        js_handler=f"(ev) => {{ ev.preventDefault(); {zone} {clear} "
        "ev.currentTarget.classList.add(z === 'merge' ? 'vr-drop' : 'vr-drop-' + z); }",
    )
    block.on("dragleave", js_handler=f"(ev) => {{ {clear} }}")
    block.on(
        "drop",
        lambda ev: (
            dropped(int(ev.args[0].split(":")[1]), ev.args[1])
            if isinstance(ev.args, list) and str(ev.args[0]).startswith("excerpt:")
            else None
        ),
        js_handler=f"(ev) => {{ ev.preventDefault(); {zone} {clear} "
        "emit(ev.dataTransfer.getData('text/plain'), z); }",
    )


def excerpt_url(e: categories.ExcerptView) -> str:
    """The PDF of an excerpt, at its page."""
    page = f"page={e.page}" if e.page else ""
    if e.document_id:
        return f"/doc/{e.document_id}" + (f"?{page}" if page else "")
    return f"/pdf/{e.publication_id}" + (f"?{page}" if page else "")


def categories_dialog(folder_id: int, changed_cb: Callable[[], None] | None = None) -> None:
    from .categories_editor import categories_dialog as dialog

    dialog(folder_id, changed_cb)
