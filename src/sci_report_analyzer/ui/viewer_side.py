"""Next to a PDF (a paper's, or a document's): the details of a paper (as in the
publications panel), bookmarks, and the paper of a selection."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from .. import annotations, categories, documents, folders, manual, pdftext, reflist, reports
from ..i18n import N_, _
from ..sources.base import SourceError
from . import section_text
from .categories_editor import categories_dialog
from .dialogs import actions, close, confirm, ok_handler, transient_dialog
from .dnd import draggable, drop_zone
from .mdedit import MarkdownEditor, quote
from .panel import PublicationsPanel
from .pdf_viewer import changed, page_url, watch
from .pub_details import show_details
from .reflist import tag_from_list
from .theme import int_or_none

ui.add_css(
    ".vr-excerpt-on { background: rgba(255, 160, 0, 0.15); border-radius: 4px;"
    " box-shadow: 0 0 0 2px #ffa000; }",
    shared=True,
)

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
        # ("doc", document id) or ("pub", paper id): the PDF shown; ("notes", 0): none (the
        # person's notes and excerpts in the folder, on their own page)
        self.source = source
        # The folder of the period, whose categories excerpts are filed in.
        self.folder = folders.folder_of_period(period_id)
        self.on_render: list[Callable[[], None]] = []
        self.host = _Host(person_id, period_id, lambda: [f() for f in self.on_render])
        self.bookmarks: Callable[[], None] | None = None
        self.wide: set[str] = set()  # (tabs shown wider, as the details of a paper)
        self.categories: Callable[[], None] | None = None
        # The excerpt selected (clicked: its tint on the PDF, or its entry), and the entries of
        # the categories' tab (by excerpt: highlighted when selected).
        self.excerpt: int | None = None
        self.excerpt_rows: dict[int, ui.element] = {}
        # A link from a selection to a paper (documents): (pub id, page, rects, text).
        self.link: Callable[[int, int, list, str], None] | None = None
        # The notes' editors (the selected text quoted into them), the last one used first.
        self.notes: list[MarkdownEditor] = []
        self.folder_editor: MarkdownEditor | None = None  # (the folder-wide notes, if any)
        # The papers cited first when citing: those of the PDF shown (a paper: itself; a
        # document: the page sets those it found in it).
        self.here: Callable[[], set[int]] = lambda: (
            {self.source[1]} if self.source[0] == "pub" else set()
        )
        # In another window (the pane: see pdf_viewer.viewer_frame) meanwhile, its tabs here
        # hidden; once back, what the page shows again up to date.
        self.detached = False
        self.on_attach: list[Callable[[], object]] = []
        self.folder_refresh: Callable[[], None] | None = None  # (their preview and status)

    def attach(self, box: ui.column) -> None:
        self.box = box
        with box:
            self.overview = ui.column().classes("w-full gap-1 no-wrap grow min-h-0")
            with self.overview:
                self.tab_bar = (
                    ui.tabs(on_change=self._fit).props("dense align=left").classes("w-full")
                )
                self.panels = ui.tab_panels(self.tab_bar).classes("w-full grow min-h-0")
            self.details = ui.column().classes("w-full gap-2 vr-details").mark("pdf-details")
            self.details.visible = False

    def section(
        self, name: str, icon: str, tip: str, *, wide: bool = False, fill: bool = False
    ) -> ui.tab_panel:
        """A tab of the side column (the first one is shown); ``fill``: its content can take
        the height of the column."""
        if wide:
            self.wide.add(name)
        with self.tab_bar:
            ui.tab(name, label="", icon=icon).tooltip(tip).mark(f"side-tab-{name}")
        if self.tab_bar.value is None:
            self.tab_bar.value = name
        with self.panels:
            return ui.tab_panel(name).classes(
                "p-0 gap-2" + (" flex flex-col no-wrap h-full" if fill else "")
            )

    def select(self, name: str) -> None:
        self.back()
        self.box.set_visibility(True)
        self.tab_bar.value = name

    @property
    def has_pdf(self) -> bool:
        return self.source[0] in ("doc", "pub")

    @property
    def stats(self) -> list[PubStat]:
        return self.host.stats

    async def load(self) -> None:
        await self.host.reload_quietly()
        if self.folder_refresh is not None:  # (its citations, now the papers are known)
            self.folder_refresh()

    async def reattached(self) -> None:
        """Back from another window: up to date again (the papers, bookmarks, excerpts, the
        page's own: e.g. its notes, edited there)."""
        await self.load()
        if self.bookmarks:
            self.bookmarks()
        self.refresh_excerpts()
        for f in self.on_attach:
            if inspect.isawaitable(done := f()):
                await done

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
            self.box.style("min-width:40rem")
        else:
            self.box.style(remove="min-width:40rem")

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
        sel = await ui.run_javascript("vrPdf.excerpted()")
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(
                _("Select the text to file (or an area with text, or click a highlight) first"),
                type="warning",
            )
            return
        sel["text"] = pdftext.clean_pdf_text(sel["text"], one_paragraph=True)
        page, rects = sel.get("p"), sel.get("rects") or []
        category_picker(
            self,
            sel["text"],
            lambda cat_id, text, **props: self.file_excerpt(cat_id, text, page, rects, **props),
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
        self.unhighlight(page, rects)
        self.refresh_excerpts()

    def unhighlight(self, page: int | None, rects: list) -> None:
        """The highlights of PDF.js under an excerpt just filed: removed (its tint instead)."""
        if rects:
            self.box.client.run_javascript(
                f"vrPdf.unhighlight({json.dumps(rects)}, {json.dumps(page)})"
            )

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
        why = categories.merge_excerpts(new, lead.id, ref_only=ref_only)
        self.unhighlight(page, rects)
        with self.box:  # (not the picker's dialog, closed: its elements are gone)
            if why:
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

    def select_excerpt(self, excerpt_id: int | None) -> None:
        """An excerpt selected (else none): highlighted on the PDF, and its entry in the
        categories' tab (shown, scrolled to)."""
        self.excerpt = excerpt_id
        for i, row in self.excerpt_rows.items():
            if i == excerpt_id:
                row.classes(add="vr-excerpt-on")
            else:
                row.classes(remove="vr-excerpt-on")
        js = f"vrDoc.select({json.dumps(excerpt_id)});"
        row = self.excerpt_rows.get(excerpt_id) if excerpt_id is not None else None
        if row is not None:
            if not self.details.visible and self.tab_bar.value != "categories":
                self.tab_bar.value = "categories"
                self._fit()
            js += (  # (once the tab is shown)
                f" setTimeout(() => document.getElementById('c{row.id}')"
                "?.scrollIntoView({block: 'nearest', behavior: 'smooth'}), 100);"
            )
        self.box.client.run_javascript(js)

    def folder_notes(self) -> None:
        """The person's notes within the folder (one text for all their documents and papers
        in it), if the PDF is in a folder: in their own tab, and where quotes go by default."""
        if self.folder is None or self.period_id is None:
            return
        from .citations import CitationStatus, folder_citations_dialog
        from .folder_notes import folder_notes_editor
        from .papers_pane import PapersPane

        folder_id, name = self.folder
        tip = _("Notes of the folder {folder} on this person (all their documents and papers)")
        status: list[CitationStatus] = []

        def tools() -> None:
            self.folder_tools()
            status.append(CitationStatus(lambda: folder_citations_dialog(folder_id, refresh)))

        def render(text: str) -> str:
            ctx = reports.folder_context(self.stats, self.period_id)
            out = reports.render(text, ctx)
            status[0].update(ctx, out)
            return out.text

        pane: list[PapersPane] = []

        def papers() -> None:
            period_id = self.period_id
            pane.append(
                PapersPane(
                    lambda: self.folder_editor,
                    lambda: reports.folder_context(self.stats, period_id),
                    lambda: reports.load_templates(folder_id),
                    period_id,
                    mark="folder-papers",
                    on_numbers=numbers_changed,
                )
            )

        def numbers_changed() -> None:  # (reordered in the pane: here and elsewhere)
            refresh()
            changed(self.host.person_id, but=self.host)

        def shown() -> bool:
            return bool(pane) and self.folder_editor.mode.value == "side"

        def refresh() -> None:  # (the preview, the status, the papers: also when hidden)
            render(self.folder_editor.value)
            self.folder_editor.refresh_preview()
            if shown():
                pane[0].refresh(force=True)

        def typed() -> None:
            if self.folder_editor.mode.value not in ("split", "preview"):
                render(self.folder_editor.value)  # (the status)
            if shown():
                pane[0].refresh()

        with self.section("folder-notes", "folder_open", tip.format(folder=name), fill=True):
            self.folder_editor = folder_notes_editor(
                self.period_id, render, toolbar=tools, side=papers
            )
            self.folder_editor.editor.on_value_change(typed)
            pane[0].track(self.folder_editor)
            self.folder_editor.on_mode.append(
                lambda mode: pane[0].refresh(force=True) if mode == "side" else None
            )
        self.folder_refresh = refresh
        self.on_render.append(refresh)  # (the papers edited: their tags, notes…)

    def folder_tools(self) -> None:
        """The toolbar of the folder's notes: quote, cite a paper, copy with the references
        (numbered as the folder says), insert a block (the publications, the excerpts)."""
        if self.has_pdf:
            self.quote_tool(lambda: self.folder_editor, first=True)
        self.cite_tools(
            lambda: self.folder_editor,
            "folder-note",
            lambda: reports.folder_context(self.stats, self.period_id),
        )
        for name, icon, tip in (
            (
                "publications",
                "summarize",
                _(
                    "Insert the summary of the period’s publications (kept up to date, as set "
                    "in the Summary)"
                ),
            ),
            ("excerpts", "format_list_bulleted", _("Insert the excerpts (kept up to date)")),
        ):
            ui.button(
                icon=icon,
                on_click=lambda name=name: self.folder_editor.insert_block(f"[]{{.{name}}}"),
            ).props("flat dense round size=sm").tooltip(tip).mark(f"folder-note-{name}")

    def note_context(self) -> reports.Context:
        """The papers a note cites, by their keys (for the preview and the copy)."""
        return reports.note_context(self.stats, reports.citation_keys(self.stats))

    def cite_tools(
        self,
        editor: Callable[[], MarkdownEditor | None],
        mark: str,
        context: Callable[[], reports.Context] | None = None,
    ) -> None:
        """In the toolbar of a note's ``editor``: cite a paper (``[@key]`` at the cursor), and
        copy the note with its citations numbered and the papers cited listed (``context``: of
        its citations, by default ``note_context``). The buttons are marked ``{mark}-cite`` and
        ``{mark}-copy``."""
        context = context or self.note_context

        def copy() -> None:
            if (e := editor()) is not None:
                ui.clipboard.write(reports.with_references(e.value, context()))
                ui.notify(_("Copied (with the references)"))

        ui.button(icon="format_quote", on_click=lambda: self.cite_dialog(editor, mark)).props(
            "flat dense round size=sm"
        ).tooltip(_("Cite a paper ([@key], numbered when copied)")).mark(f"{mark}-cite")
        ui.button(icon="content_copy", on_click=copy).props("flat dense round size=sm").tooltip(
            _("Copy the note, its citations numbered and the papers cited listed")
        ).mark(f"{mark}-copy")

    def cite_dialog(self, editor: Callable[[], MarkdownEditor | None], mark: str) -> None:
        """Pick one of the person's papers (those of the PDF first): cited at the cursor."""
        stats = [s for s in self.stats if not s.hidden]
        keys = reports.citation_keys(self.stats)
        here = self.here()
        stats.sort(key=lambda s: (s.id not in here, -(s.year or 0), (s.title or "").lower()))
        untitled = _("(untitled)")
        options = {
            keys[s.id]: f"{s.title or untitled} ({s.year or '?'})"
            + (_(" · in the document") if s.id in here else "")
            for s in stats
        }

        def chosen(e) -> bool | None:
            if not e.value:
                return False
            if (target := editor()) is not None:
                target.insert(f"[@{e.value}]")

        with transient_dialog(width="w-[40rem] max-w-full") as (dialog, _card):
            ui.label(_("Cite a paper")).classes("font-medium")
            ui.select(
                options,
                with_input=True,
                label=_("Title (type to search)"),
                on_change=ok_handler(dialog, chosen),
            ).props("dense outlined autofocus options-dense").classes("w-full").mark(
                f"{mark}-cite-paper"
            )

    def tab_of(self, editor: MarkdownEditor) -> str:
        """The tab a notes' editor is in."""
        return "folder-notes" if editor is self.folder_editor else "notes"

    def cite_hint(self, s: PubStat) -> str:
        """The hover of a paper cited in the PDF: what clicking does, and (in a folder) its
        tags there."""
        lines = [
            s.title or _("(untitled)"),
            _("Click: details · Shift-click: cite in the notes"),
        ]
        if self.folder:
            names = {t.id: t.name for t in annotations.all_tags()}
            tags = sorted(names[t] for t in s.tags_in(self.period_id) if t in names)
            lines.append(
                _("In {folder}: {tags}").format(folder=self.folder[1], tags=", ".join(tags))
                if tags
                else _("In {folder}: no tag").format(folder=self.folder[1])
            )
        return "\n".join(lines)

    def cite(self, pub_id: int) -> None:
        """Insert the citation of a paper (``[@key]``) in the notes last used, at the cursor."""
        notes = [e for e in self.notes if not e.editor.is_deleted]
        key = reports.citation_keys(self.stats).get(pub_id)
        if not notes or key is None:
            ui.notify(_("No notes to cite in"), type="warning")
            return
        self.select(self.tab_of(notes[0]))
        notes[0].insert(f"[@{key}]")

    def source_link(self, page: int | None) -> tuple[str, str]:
        """(short name, url at ``page``) of the PDF shown: where a quote in the folder's notes
        is from."""
        kind, key = self.source
        if kind == "doc":
            info = documents.info(key)
            return (info.name if info else _("Document")), f"/doc/{key}" + (
                f"?page={page}" if page else ""
            )
        s = next((x for x in self.stats if x.id == key), None)
        return short_title(s), page_url(key, self.period_id, page=page)

    def quote_tool(
        self, editor: Callable[[], MarkdownEditor | None], *, first: bool = False
    ) -> None:
        """In the toolbar of a note's ``editor`` (made after it: hence a function): quote the
        selected text there; Q quotes into the note last used (``first``: by default, before
        any is used)."""

        def used() -> None:
            if (e := editor()) is not None:
                self.notes[:] = [e, *(x for x in self.notes if x is not e)]

        async def click() -> None:
            used()
            if (e := editor()) is not None:
                await self.quote(e)

        ui.button(icon="post_add", on_click=click).props("flat dense round size=sm").tooltip(
            _("Quote the text selected in the PDF (or the highlight clicked), with its page (Q)")
        ).mark("note-quote")

        def made() -> None:  # (the note's editor: listed, last; first once used)
            if (e := editor()) is not None:
                self.notes.insert(0, e) if first else self.notes.append(e)
                e.box.on("focusin", used)

        ui.timer(0, made, once=True)

    async def quote(self, editor: MarkdownEditor | None = None) -> None:
        """Quote the text selected (in the PDF; else on the page) into ``editor`` (else the
        note last used), as a Markdown quote with its page."""
        if editor is None:  # (Q: the notes' tab shown)
            notes = [e for e in self.notes if not e.editor.is_deleted]
            if not notes:
                return
            editor = notes[0]
            self.select(self.tab_of(editor))
        sel = await ui.run_javascript("vrPdf.quoted()")
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(
                _("Select the text to quote (or an area with text, or click a highlight) first"),
                type="warning",
            )
            return
        page = sel.get("p")
        where = _("p. {page}").format(page=page) if page else ""
        if editor is self.folder_editor:  # (the notes of all: names its source, linked)
            name, url = self.source_link(page)
            name = name.replace("[", "(").replace("]", ")")
            where = f"[{name}, {where}]({url})" if where else f"[{name}]({url})"
        editor.insert_block(quote(pdftext.clean_pdf_text(sel["text"], one_paragraph=True), where))

    async def tag_selection(self) -> None:
        """Tag the papers of the list selected in the PDF (e.g. an area over numbered
        references), as in the publications panel."""
        sel = await ui.run_javascript("vrPdf.selection()")
        text = (sel or {}).get("text") or ""
        if not text.strip():
            ui.notify(_("Select the list (or an area over it) in the PDF first"), type="warning")
            return
        tag_from_list(self.host, text, show_tagged=False)

    async def find_selection(self) -> None:
        sel = await ui.run_javascript("vrPdf.selection()")
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(_("Select the reference (or its title) in the PDF first"), type="warning")
            return
        find_dialog(self, sel["text"], sel.get("p"), sel.get("rects") or [])


def short_title(s: PubStat | None, width: int = 40) -> str:
    """A short name for a paper: first author's surname and year, else a truncated title."""
    if s is None:
        return _("Paper")
    names = (s.authors[0] if s.authors else "").replace(",", " ").split()
    if names and s.year:
        return f"{names[-1]} {s.year}" if len(s.authors) < 2 else f"{names[-1]} et al. {s.year}"
    title = " ".join((s.title or _("(untitled)")).split())
    return title if len(title) <= width else title[: width - 1] + "…"


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
        with transient_dialog(width="w-96") as (dlg, _card):
            name = (
                ui.input(_("Name"), value=marks[i]["name"]).classes("w-full").mark("bookmark-name")
            )

            def ok() -> None:
                if name.value.strip():
                    marks[i]["name"] = name.value.strip()
                    documents.set_bookmarks(kind, key, marks)
                listing.refresh()

            name.on("keydown.enter", ok_handler(dlg, ok))
            actions(dlg, _("Rename"), ok, mark="bookmark-rename-ok")

    listing()
    return listing.refresh


def find_dialog(side: Side, text: str, page: int | None, rects: list) -> None:
    """The paper of a selection (a reference, a title): among the person's papers (to show,
    or to link the selection to), else on HAL (to add)."""
    item = documents.selection_item(text)
    state: dict = {"found": None}
    with (
        side.host.dialogs,
        transient_dialog(_("Find the paper"), width="w-full max-w-2xl") as (dlg, _card),
    ):
        ui.label(reflist.label(item, 300)).classes("text-sm text-grey").mark("find-text")
        busy = ui.spinner(size="sm")
        busy.visible = False

        def show(pub_id: int) -> None:
            side.show_paper(pub_id)
            close(dlg)

        def link(pub_id: int) -> None:
            assert side.link is not None and page is not None
            side.link(pub_id, page, rects, text)
            close(dlg)

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
        actions(dlg, cancel=_("Close"))


def excerpt_properties(
    start: int | None = None,
    end: int | None = None,
    influence: bool = False,
    *,
    cleared: Callable[[], None] | None = None,
) -> Callable[[], dict]:
    """The fields of an excerpt's years (a button clears them, then calls ``cleared``) and
    influence flag; returns their values (the keywords of categories.add_excerpt /
    update_excerpt)."""

    def clear() -> None:
        start_in.set_value(None)
        end_in.set_value(None)
        if cleared:
            cleared()

    with ui.row().classes("items-center gap-2"):
        start_in = ui.number(_("From (year)"), value=start, format="%d").props("dense outlined")
        start_in.classes("w-32").mark("excerpt-start")
        end_in = ui.number(_("To (year)"), value=end, format="%d").props("dense outlined")
        end_in.classes("w-32").mark("excerpt-end")
        ui.button(icon="event_busy", on_click=clear).props("flat dense round size=sm").classes(
            "-ml-1"
        ).tooltip(_("Clear the years")).mark("excerpt-years-clear")
        flag = ui.checkbox(_("Influence"), value=influence).mark("excerpt-influence")
        flag.tooltip(_("Shows the person's influence (“rayonnement”: invited talks, prizes…)"))
        ui.icon("public", size="xs", color="teal").classes("-ml-2")

    return lambda: {
        "start": int_or_none(start_in.value),
        "end": int_or_none(end_in.value),
        "influence": bool(flag.value),
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
    ``properties``: also its years (those found in the text, then taken out of it if leading
    or ending it: see categories.split_years; cleared, the text as it was) and influence flag
    (passed to ``chosen`` as keywords, with ``text``, so taken out or not);
    ``merge``: or merge it with an excerpt (called with it, and whether only its place is
    cited), the similar ones shown with a warning."""
    folder_id, folder_name = side.folder
    with side.host.dialogs, transient_dialog(_(title), width="w-full max-w-xl") as (dlg, _card):
        years, filed = categories.split_years(text) if properties else (None, text)
        quote = ui.label().classes("text-sm text-grey").mark("excerpt-text")

        def show(t: str) -> None:
            nonlocal filed
            filed = t
            short = t if len(t) <= 300 else t[:299] + "…"
            quote.set_text(f"“{' '.join(short.split())}”")

        show(filed)
        if merge is not None and side.period_id is not None:
            similar_excerpts(side, text, lambda lead, ref_only: (merge(lead, ref_only), close(dlg)))
        props = (
            excerpt_properties(*(years or (None, None)), cleared=lambda: show(text))
            if properties
            else dict
        )
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
            chosen(cat_id, **({"text": filed} if properties else {}), **props())
            name = next((n.path for n in categories.tree(folder_id) if n.id == cat_id), "")
            ui.notify(_(done).format(category=name))
            close(dlg)

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


class _ExcerptsSection:
    """The excerpts of the person (in the folder) by category: to go to (in this PDF, or
    another one), edit, move, merge (drag one onto another, or its merge icon then a click
    on the other), remove, copy as Markdown."""

    def __init__(self, side: Side) -> None:
        self.side = side
        self.merging: categories.ExcerptView | None = None  # (merged with the one clicked)
        self.listing()

    @ui.refreshable_method
    def listing(self) -> None:
        if self.side.folder is None or self.side.period_id is None:
            ui.label(
                _("Excerpts are filed in the categories of a folder: open the PDF from one.")
            ).classes("text-sm text-grey")
            return
        folder_id, folder_name = self.side.folder
        nodes = categories.tree(folder_id)
        with ui.row().classes("w-full items-center gap-1"):
            ui.label(_("Categories of {folder}").format(folder=folder_name)).classes(
                "font-medium grow"
            )
            ui.button(
                icon="content_copy",
                on_click=lambda: (
                    ui.clipboard.write(categories.markdown(folder_id, self.side.period_id)),
                    ui.notify(_("Copied (Markdown)")),
                ),
            ).props("flat dense round size=sm").tooltip(_("Copy the excerpts, as Markdown")).mark(
                "excerpts-copy"
            )
            ui.button(icon="badge", on_click=self.name_documents).props(
                "flat dense round size=sm"
            ).tooltip(_("Name the documents (as cited in the copied excerpts)")).mark(
                "excerpts-sources"
            )
            ui.button(icon="edit", on_click=lambda: self.edit_categories(folder_id)).props(
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
        self.side.excerpt_rows.clear()
        for e in categories.excerpts(self.side.period_id, grouped=True):
            by_cat.setdefault(e.category_id, []).append(e)
        texts = categories.section_texts(self.side.period_id)
        merging = self.merging
        if merging:
            with ui.row().classes("w-full items-center no-wrap gap-1 bg-amber-1 p-1 rounded"):
                short = merging.text if len(merging.text) <= 60 else merging.text[:59] + "…"
                ui.label(_("Click the excerpt to merge “{text}” with").format(text=short)).classes(
                    "text-sm grow"
                )
                ui.button(icon="close", on_click=lambda: self.merge_mode(None)).props(
                    "flat dense round size=xs"
                ).tooltip(_("Cancel")).mark("excerpt-merge-cancel")
        for n in nodes:
            items = by_cat.get(n.id, [])
            with (
                ui.row()
                .classes("w-full items-center gap-1 rounded")
                .style(f"padding-left:{1.2 * n.depth}rem")
                .mark(f"category-header-{n.id}") as header
            ):
                # (an excerpt dropped onto its heading: first in it, from any category)
                drop_zone(
                    header,
                    "excerpt",
                    lambda source_id, _where, n=n: self.drop_on_category(source_id, n.id),
                    zoned=False,
                )
                ui.element("div").classes("w-3 h-3 rounded-full shrink-0").style(
                    f"background: {n.colour}"
                ).tooltip(_("The colour of its excerpts (edit the categories to change it)")).mark(
                    f"category-dot-{n.id}"
                )
                ui.label(n.name).classes("font-medium text-sm" if not n.depth else "text-sm")
                if n.influence:
                    ui.icon("campaign", size="xs", color="amber-9").tooltip(
                        _("The “rayonnement” category: it also lists the influence excerpts")
                    )
                if n.years:
                    ui.label(n.years).classes("text-xs text-grey")
                if items:
                    ui.badge(str(len(items))).props("rounded color=amber-8")
                section_text.edit_button(self.side, n)
            section_text.show(self.side, n, texts.get(n.id, ""))
            if n.influence:  # (after its own: those flagged elsewhere, as copied)
                paths = {c.id: c.path for c in nodes}
                flagged = [
                    e for c in nodes if c.id != n.id for e in by_cat.get(c.id, []) if e.influence
                ]
                for e in flagged:
                    ui.label(f"{paths[e.category_id]} · {e.quoted}").classes(
                        "text-xs text-grey italic line-clamp-2"
                    ).style(f"padding-left:{1.2 * n.depth + 0.6}rem").tooltip(
                        _("Flagged “influence” in {category} (copied here too)").format(
                            category=paths[e.category_id]
                        )
                    ).mark(f"excerpt-influence-copy-{e.id}")
            for e in items:  # (an excerpt, or a group: its lead, then the others)
                with (
                    ui.column()
                    .classes("w-full gap-0")
                    .style(f"padding-left:{1.2 * n.depth + 0.6}rem")
                    .mark(f"excerpt-{e.id}") as block
                ):
                    # (onto its top or bottom: placed before / after it; its middle: merged)
                    draggable(block, "excerpt", e.id)
                    drop_zone(
                        block,
                        "excerpt",
                        lambda source_id, zone, e=e: self.dropped(source_id, zone, e),
                        middle="merge",
                    )
                    if e.group_text:
                        ui.label(e.group_text).classes("text-sm line-clamp-4").style(
                            f"border-left: 3px solid {e.colour}; padding-left: 0.4rem"
                        ).tooltip(_("The text of the group (edit it with its pencil)")).mark(
                            f"excerpt-group-text-{e.id}"
                        )
                    for x in e.parts:
                        self.quote(x, e, merging)

    def quote(
        self,
        x: categories.ExcerptView,
        lead: categories.ExcerptView,
        merging: categories.ExcerptView | None,
    ) -> None:
        """An excerpt's row (``lead``: that of its group): its text, where, the group's
        years, influence and actions (the others of a group: taken out, removed)."""
        kind, key = self.side.source
        here = (x.document_id if kind == "doc" else x.publication_id) == key
        member = x.id != lead.id
        cited = bool(lead.group_text) or x.ref_only  # (only its place, in the group)
        with (
            ui.row().classes("w-full items-start no-wrap gap-1").mark(f"excerpt-part-{x.id}") as row
        ):
            self.side.excerpt_rows[x.id] = row
            if x.id == self.side.excerpt:
                row.classes("vr-excerpt-on")
            ui.icon("subdirectory_arrow_right" if member else "format_quote", size="xs").classes(
                "mt-1"
            ).style(f"color: {lead.colour}")
            with ui.column().classes("gap-0 grow min-w-0"):
                label = ui.label(x.text).classes(
                    "cursor-pointer hover:underline "
                    + ("text-xs text-grey italic line-clamp-1" if cited else "text-sm line-clamp-3")
                )
                if x.ref_only:
                    label.tooltip(_("Only its place is cited (merged as a reference)"))
                if merging and merging.id != lead.id:
                    label.on("click", lambda: self.merge(merging.id, lead))
                elif here and x.page:
                    on_page = [r for r in x.rects if len(r) < 5 or r[4] == x.page]
                    top = max((r[3] for r in on_page), default=None)
                    y = json.dumps(top + 20 if top is not None else None)
                    label.on(
                        "click",
                        lambda: self.side.select_excerpt(x.id),
                        js_handler=f"() => {{ vrPdf.go({x.page}, {y}); vrDoc.select({x.id}); "
                        "emit(); }",
                    )
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
                                    self.side.refresh_excerpts(),
                                ),
                            )
                        ]
                        if lead.members and not lead.group_text
                        else []
                    )
                    buttons = (
                        [
                            *cite,
                            ("split", "call_split", _("Take it out of the group"), self.split),
                            ("remove", "delete", _("Remove"), self.remove),
                        ]
                        if member
                        else [
                            (
                                "edit",
                                "edit",
                                _("Edit (text, years, influence…)"),
                                self.edit_excerpt,
                            ),
                            *cite,
                            ("move", "drive_file_move", _("Move to another category"), self.move),
                            (
                                "merge",
                                "call_merge",
                                _(
                                    "Merge with another excerpt: click it then (or drag this "
                                    "one onto it; onto its top or bottom edge: placed before or "
                                    "after; onto a category's heading: first in it)"
                                ),
                                self.merge_mode,
                            ),
                            ("remove", "delete", _("Remove"), self.remove),
                        ]
                    )
                    for mark, icon, tip, act in buttons:
                        ui.button(icon=icon, on_click=lambda act=act: act(x)).props(
                            "flat dense round size=xs color="
                            + ("negative" if mark == "remove" else "grey-7")
                        ).classes("shrink-0").tooltip(tip).mark(f"excerpt-{mark}-{x.id}")

    def dropped(self, source_id: int, zone: str, target: categories.ExcerptView) -> None:
        """An excerpt dropped on another one: placed before / after it, or merged."""
        if zone in ("before", "after"):
            categories.place_excerpt(source_id, target.id, zone)
            self.side.refresh_excerpts()
        else:
            self.merge(source_id, target)

    def drop_on_category(self, source_id: int, category_id: int) -> None:
        """An excerpt dropped onto a category's heading: moved there, first."""
        categories.file_excerpt_first(source_id, category_id)
        self.side.refresh_excerpts()

    def split(self, x: categories.ExcerptView) -> None:
        categories.split_excerpt(x.id)
        self.side.refresh_excerpts()

    def edit_categories(self, folder_id: int) -> None:
        with self.side.host.dialogs:  # (outside the listing, refreshed after each change)
            categories_dialog(folder_id, self.side.refresh_excerpts)

    def name_documents(self) -> None:
        """Rename the documents the excerpts come from (e.g. "001038323Dossier-…_1.0.0" as
        "Application"), their names being their references in the copied excerpts."""
        docs = categories.cited_documents(self.side.period_id) if self.side.period_id else []
        if not docs:
            ui.notify(_("No excerpt from a document yet"))
            return
        with self.side.host.dialogs, transient_dialog(width="w-[32rem]") as (dlg, _card):
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
                self.side.refresh_excerpts()

            actions(dlg, _("Save"), ok, mark="doc-names-ok")

    def merge_mode(self, e: categories.ExcerptView | None) -> None:
        self.merging = e
        self.listing.refresh()

    def merge(self, source_id: int, target: categories.ExcerptView) -> None:
        """Merge an excerpt into ``target``, once confirmed."""
        self.merging = None
        source = next(
            (x for x in categories.excerpts(self.side.period_id) if x.id == source_id), None
        )
        if source is None or source.id == target.id:
            self.listing.refresh()
            return

        def yes() -> None:
            if why := categories.merge_excerpts(source.id, target.id, ref_only=bool(mode.value)):
                ui.notify(why, type="warning")
            self.side.refresh_excerpts()

        def short(t: str) -> str:
            return t if len(t) <= 80 else t[:79] + "…"

        with self.side.host.dialogs, transient_dialog() as (dialog, _card):
            # Closed: out of the merge mode.
            dialog.on_value_change(lambda ev: None if ev.value else self.listing.refresh())
            ui.label(
                _("Merge “{first}” with “{second}”?").format(
                    first=short(source.text), second=short(target.text)
                )
            )
            ui.label(
                _(
                    "They are on one item (in the order of their PDFs), with the second one's "
                    "category, years, influence and text (its pencil edits the text of "
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
            actions(dialog, _("Merge"), yes, mark="excerpt-merge-confirm")

    def remove(self, e: categories.ExcerptView) -> None:
        def yes() -> None:
            categories.remove_excerpt(e.id)
            self.side.refresh_excerpts()

        short = e.text if len(e.text) <= 120 else e.text[:119] + "…"
        with self.side.host.dialogs:
            confirm(
                _("Remove the excerpt “{text}”?").format(text=short),
                _("Remove"),
                yes,
                mark="excerpt-remove-confirm",
            )

    def move(self, e: categories.ExcerptView) -> None:
        def to(cat_id: int, **_props) -> None:
            categories.move_excerpt(e.id, cat_id)
            self.side.refresh_excerpts()

        category_picker(
            self.side,
            e.text,
            to,
            title=N_("Move to a category"),
            done=N_("Moved to {category}"),
            current=e.category_id,
        )

    def originals(self, e: categories.ExcerptView, text: ui.textarea) -> None:
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

    def edit_excerpt(self, e: categories.ExcerptView) -> None:
        """Its text (that of a group), years and "influence" flag; the passages as selected
        shown."""
        with (
            self.side.host.dialogs,
            transient_dialog(
                _("Merged excerpts") if e.members else _("Excerpt"), width="w-[32rem]"
            ) as (dialog, _card),
        ):
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
            self.originals(e, text)
            props = excerpt_properties(e.start_year, e.end_year, e.influence)

            def save() -> None:
                if e.members:
                    joined = " […] ".join(x.text for x in e.parts if not x.ref_only)
                    edited = " ".join((text.value or "").split())
                    categories.set_group_text(e.id, None if edited == joined else edited)
                categories.update_excerpt(e.id, text=None if e.members else text.value, **props())
                self.side.refresh_excerpts()

            actions(dialog, _("Save"), save, mark="excerpt-edit-save")


def categories_section(side: Side) -> Callable[[], None]:
    """The excerpts by category (``_ExcerptsSection``); returns its refresh."""
    return _ExcerptsSection(side).listing.refresh


def excerpt_url(e: categories.ExcerptView) -> str:
    """The PDF of an excerpt, at its page."""
    page = f"page={e.page}" if e.page else ""
    if e.document_id:
        return f"/doc/{e.document_id}" + (f"?{page}" if page else "")
    return f"/pdf/{e.publication_id}" + (f"?{page}" if page else "")
