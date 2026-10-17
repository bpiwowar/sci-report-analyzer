"""Next to a PDF (a paper's, or a document's): the details of a paper (as in the
publications panel), bookmarks, and the paper of a selection."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from weakref import WeakSet

from nicegui import app, background_tasks, ui

from .. import (
    annotations,
    career_extract,
    categories,
    classifier,
    documents,
    excerpt_rewrite,
    folders,
    llm,
    manual,
    pdftext,
    reflist,
    reports,
    urls,
)
from ..i18n import N_, _, ngettext
from ..sources.base import SourceError
from . import section_text
from .categories_editor import categories_dialog
from .copying import copy_button
from .dialogs import actions, close, confirm, ok_handler, transient_dialog
from .dnd import draggable, drop_zone
from .mdedit import MarkdownEditor, inline_quote, quote, tool_group
from .panel import PublicationsPanel
from .pdf_viewer import changed, gone, page_url, watch
from .pub_details import show_details
from .reflist import tag_from_list
from .theme import NOTE_EXTRAS, chip_html, fmt_dt, int_or_none, span

ui.add_css(
    ".vr-excerpt-on { background: rgba(255, 160, 0, 0.15); border-radius: 4px;"
    " box-shadow: 0 0 0 2px #ffa000; }"
    # (an excerpt in the preview of the folder's notes: its dialog when clicked)
    " .vr-report-preview li:is(:has(> [data-vr-excerpt]), :has(> p > [data-vr-excerpt]))"
    " { cursor: pointer; border-radius: 4px; }"
    " .vr-report-preview li:is(:has(> [data-vr-excerpt]), :has(> p > [data-vr-excerpt]))"
    ":hover { background: rgba(255, 160, 0, 0.1); }",
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


def _sides() -> dict[int, WeakSet[Side]]:
    """Period id -> the open sides showing its excerpts (in any window: one changed in one,
    the others show it). Kept on the app (one registry)."""
    if not hasattr(app.state, "excerpt_sides"):
        app.state.excerpt_sides = {}
    return app.state.excerpt_sides


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
        self.last_category: int | None = None  # (chosen last in category_picker, here)
        # (an excerpt's edit dialog, once the categories' tab is there: see _ExcerptsSection)
        self.edit_excerpt: Callable[[categories.ExcerptView], None] | None = None
        # (an excerpt not shown in the categories' tab, collapsed or searched: shown)
        self.reveal_excerpt: Callable[[int], None] | None = None
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
        # Within a person's page, beside it (see pdf_dock), its side column hidden: its quotes
        # and excerpts go to that page's.
        self.embedded = False
        # A person's page, the PDFs beside it (see dock): its source, the one shown; its quote
        # buttons, shown with one.
        self.docked = False
        self.quote_buttons: list[ui.button] = []
        if period_id is not None:
            _sides().setdefault(period_id, WeakSet()).add(self)

    def attach(self, box: ui.column) -> None:
        self.box = box
        with box:
            self.overview = ui.column().classes("w-full gap-1 no-wrap grow min-h-0")
            with self.overview:
                # (the tabs, then what the page adds on their row)
                with ui.row().classes("w-full items-center no-wrap gap-2") as self.bar:
                    self.tab_bar = (
                        ui.tabs(on_change=self._fit).props("dense align=left").classes("grow")
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
        if not self.embedded:  # (embedded: the person's page beside it, in its place)
            self.box.set_visibility(True)
        self.tab_bar.value = name

    @property
    def has_pdf(self) -> bool:
        return self.source[0] in ("doc", "pub")

    def dock(self, source: tuple[str, int] | None) -> None:
        """On a person's page: the PDF shown beside it (``None``: none), where its quotes and
        excerpts are from."""
        self.docked = True
        self.source = source or ("notes", 0)
        for b in self.quote_buttons:
            if not b.is_deleted:
                b.set_visibility(self.has_pdf)
        if self.categories:  # (those of the PDF shown: gone to there)
            self.categories()

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
        self.refresh_excerpts(others=False)
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
        if self.embedded:  # (beside a person's page: only a paper's details, over the PDF)
            self.box.set_visibility(False)

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
        """File the selected text in one of the folder's categories; already filed (the
        excerpt clicked, nothing selected; or its very text selected): edited instead."""
        if self.folder is None:
            ui.notify(
                _("Excerpts are filed in a folder's categories: open the PDF from a folder"),
                type="warning",
            )
            return
        sel = await ui.run_javascript("vrPdf.excerpted()")
        text = pdftext.clean_pdf_text((sel or {}).get("text") or "", one_paragraph=True)
        if (filed := self._filed(text)) is not None and self.edit_excerpt is not None:
            self.edit_excerpt(filed)
            return
        if not sel or not (sel.get("text") or "").strip():
            ui.notify(
                _("Select the text to file (or an area with text, or click a highlight) first"),
                type="warning",
            )
            return
        sel["text"] = pdftext.clean_pdf_text(sel["text"], one_paragraph=True)
        page, rects = sel.get("p"), sel.get("rects") or []
        dated, rewriting = categories.detect_years(sel["text"]), bool(llm.rewrite_model())
        before, after = await self._around(rects) if rewriting or not dated else ("", "")
        category_picker(
            self,
            sel["text"],
            lambda cat_id, text, **props: self.file_excerpt(cat_id, text, page, rects, **props),
            properties=True,
            around=None if dated else categories.context_years("", before, after),
            suggest=(lambda: excerpt_rewrite.rewrite(sel["text"], before, after))
            if rewriting
            else None,
            merge=lambda lead, ref_only: self.merge_excerpt(
                lead, sel["text"], page, rects, ref_only=ref_only
            ),
        )

    async def _around(self, rects: list) -> tuple[str, str]:
        """The text around a selection in the PDF (see documents.selection_context): in the
        document's lines, else in those of its pages, extracted."""
        kind, key = self.source
        lines = documents.lines_of(key) if kind == "doc" else None
        if lines is None and rects:
            from .documents_page import LINES_JS

            pages = sorted({int(r[4]) for r in (rects[0], rects[-1]) if len(r) > 4})
            try:
                lines = await ui.run_javascript(
                    LINES_JS + f"sraLines(vrPdf.app().pdfDocument, {json.dumps(pages)})"
                )
            except TimeoutError:
                lines = None
        return documents.selection_context(lines or [], rects)

    def _filed(self, text: str) -> categories.ExcerptView | None:
        """The excerpt (its group's lead) of this PDF with that text (none: the one selected,
        e.g. clicked on the page), if any."""
        if self.period_id is None:
            return None
        kind, key = self.source
        for e in categories.excerpts(self.period_id, grouped=True):
            for x in [e, *e.members]:
                here = (x.document_id if kind == "doc" else x.publication_id) == key
                if (x.id == self.excerpt and not text) or (
                    text and here and text in (x.text, x.original)
                ):
                    return e
        return None

    def file_excerpt(
        self, category_id: int, text: str, page: int | None, rects: list, **props
    ) -> None:
        """``props``: its years, influence flag and tags (see categories.add_excerpt)."""
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

    def refresh_excerpts(self, *, others: bool = True) -> None:
        """The categories' tab, the preview of the folder's notes (its excerpts), and the
        excerpts of this PDF tinted on its pages; ``others``: those of the period's other
        sides too (other windows, a PDF beside the person's page…)."""
        if others and self.period_id is not None:
            for side in list(_sides().get(self.period_id, ())):
                if side is not self and hasattr(side, "box") and not gone(side.box):
                    side.refresh_excerpts(others=False)
        if self.categories:
            self.categories()
        if self.folder_refresh is not None:
            self.folder_refresh()
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
        if excerpt_id is not None and excerpt_id not in self.excerpt_rows and self.reveal_excerpt:
            self.reveal_excerpt(excerpt_id)
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
        from .citations import citation_hint
        from .folder_notes import folder_notes_editor
        from .papers_pane import PapersPane

        folder_id, name = self.folder
        tip = _("Notes of the folder {folder} on this person (all their documents and papers)")

        def render(text: str) -> str:  # (the excerpts marked: opened when clicked)
            ctx = reports.folder_context(self.stats, self.period_id, anchors=True)
            out = reports.render(text, ctx)
            if (editor := getattr(self, "folder_editor", None)) is not None:
                citation_hint(editor, ctx, out)
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
                self.period_id, render, toolbar=self.folder_tools, side=papers
            )
            render(self.folder_editor.value)  # (the hint on its papers)
            self.folder_editor.editor.on_value_change(typed)
            pane[0].track(self.folder_editor)
            self.folder_editor.on_mode.append(
                lambda mode: pane[0].refresh(force=True) if mode == "side" else None
            )
        self.folder_refresh = refresh
        self.on_render.append(refresh)  # (the papers edited: their tags, notes…)
        self._excerpt_clicks(self.folder_editor)

    def _excerpt_clicks(self, editor: MarkdownEditor) -> None:
        """A click on an excerpt in the preview of the folder's notes (its item, marked: see
        categories.EXCERPT_ANCHOR): its dialog, to edit it. Not on a link, nor when selecting."""
        preview = editor.preview
        ui.on(
            "vr-report-excerpt",
            lambda e: (
                self._edit_excerpt_of(int(e.args["excerpt"]))
                if (e.args or {}).get("id") == preview.id
                else None
            ),
        )
        preview.client.run_javascript(
            "(() => { let n = 0; const t = setInterval(() => {"
            f" const p = getHtmlElement({preview.id});"
            " if (++n > 50) clearInterval(t); if (!p) return; clearInterval(t);"
            " p.addEventListener('click', (ev) => {"
            " if (ev.target.closest('a') || String(getSelection() || '').trim()) return;"
            " const li = ev.target.closest('li'); if (!li || !p.contains(li)) return;"
            " const a = [...li.querySelectorAll('[data-vr-excerpt]')]"
            ".find((x) => x.closest('li') === li); if (!a) return;"
            f" emitEvent('vr-report-excerpt', {{id: {preview.id}, excerpt: a.dataset.vrExcerpt}});"
            " }); }, 100); })()"
        )
        preview.classes("vr-report-preview")

    def _edit_excerpt_of(self, excerpt_id: int) -> None:
        """The dialog of an excerpt (that of its group, for one merged)."""
        if self.period_id is None or self.edit_excerpt is None:
            return
        for e in categories.excerpts(self.period_id, grouped=True):
            if excerpt_id in (e.id, *(m.id for m in e.members)):
                self.edit_excerpt(e)
                return
        ui.notify(_("This excerpt is no longer there"), type="warning")

    def folder_tools(self) -> None:
        """The toolbar of the folder's notes: quote, cite a paper, copy with the references
        (numbered as the folder says), insert a block (the publications, the excerpts)."""
        with tool_group():
            if self.has_pdf or self.docked:
                self.quote_tool(lambda: self.folder_editor, first=True)
            self.cite_tools(lambda: self.folder_editor, "folder-note")
        with tool_group():
            self.insert_tools()
            self.template_tools()
        with tool_group():
            self.copy_tool(
                lambda: self.folder_editor,
                "folder-note",
                lambda: reports.folder_context(self.stats, self.period_id),
            )

    def insert_tools(self) -> None:
        """In the toolbar of the folder's notes: insert a block (kept up to date); those of
        the categories with an id (``[[excerpts#key]]``) too, as they are when opened."""

        def insert(block: str) -> None:
            self.folder_editor.insert_block(f"[[{block}]]")

        @ui.refreshable
        def items() -> None:
            for name, label in (
                (
                    "publications",
                    _("The summary of the period’s publications (as set in the Summary)"),
                ),
                ("excerpts", _("The excerpts, by category")),
                ("influence", _("The excerpts flagged “rayonnement”, by category")),
                ("cv", _("The career (CV)")),
                ("references", _("The papers cited (else at the end, when copied)")),
            ):
                ui.menu_item(label, on_click=lambda name=name: insert(name)).mark(
                    f"folder-note-{name}"
                )
            keyed = [n for n in categories.tree(self.folder[0]) if n.key] if self.folder else []
            if keyed:
                ui.separator()
                ui.label(_("The excerpts of a category")).classes(
                    "text-xs text-grey uppercase px-4 pt-2"
                )
            for n in keyed:
                ui.menu_item(
                    f"{n.path} — #{n.key}", on_click=lambda n=n: insert(f"excerpts#{n.key}")
                ).mark(f"folder-note-excerpts-{n.key}")

        with (
            ui.button(icon="playlist_add")
            .props("flat dense round size=sm")
            .tooltip(_("Insert a block (kept up to date)"))
            .mark("folder-note-insert"),
            ui.menu() as menu,
        ):
            items()
        menu.on("before-show", lambda: items.refresh())

    def template_tools(self) -> None:
        """In the toolbar of the folder's notes: start them from a report template (asked
        first when they are written), or save their headings and blocks as one."""
        from .. import report_templates
        from .report_templates import pick_menu, save_dialog

        def start(text: str) -> None:
            e = self.folder_editor
            if not e.value.strip():
                e.value = text
                return
            confirm(
                _("Replace the notes of the folder on this person with the template?"),
                _("Replace"),
                lambda: setattr(e, "value", text),
                mark="folder-note-template-replace",
            )

        with (
            ui.button(icon="article")
            .props("flat dense round size=sm")
            .tooltip(_("Report templates: start from one, or save this report's structure as one"))
            .mark("folder-note-templates"),
            ui.menu(),
        ):
            ui.label(_("Start from")).classes("text-xs text-grey uppercase px-4 pt-2")
            pick_menu(start, "folder-note-template", report_templates.load(self.folder[0]))
            ui.separator()
            ui.menu_item(
                _("Save as a template…"),
                on_click=lambda: save_dialog(self.folder_editor.value, self.folder[0]),
            ).mark("folder-note-save-template")

    def note_context(self) -> reports.Context:
        """The papers a note cites, by their keys (for the preview and the copy)."""
        return reports.note_context(self.stats, reports.citation_keys(self.stats))

    def cite_tools(self, editor: Callable[[], MarkdownEditor | None], mark: str) -> None:
        """In the toolbar of a note's ``editor``: cite a paper (``[@key]`` at the cursor,
        marked ``{mark}-cite``), see the one cited at the cursor."""

        async def see() -> None:
            if (e := editor()) is not None:
                key = await e.cited_at_cursor()
                if key:
                    self.open_cited(key)
                else:
                    ui.notify(_("Put the cursor on a citation ([@key]) first"), type="warning")

        ui.button(icon="menu_book", on_click=lambda: self.cite_dialog(editor, mark)).props(
            "flat dense round size=sm"
        ).tooltip(_("Cite a paper ([@key], numbered when copied)")).mark(f"{mark}-cite")
        ui.button(icon="open_in_new", on_click=see).props("flat dense round size=sm").tooltip(
            _("See the paper cited at the cursor, in a new window (or ⌘/Ctrl-click its citation)")
        ).mark(f"{mark}-see-cited")

        def made() -> None:  # (the note's editor, made after its toolbar)
            if (e := editor()) is not None:
                e.open_cited = self.open_cited

        ui.timer(0, made, once=True)

    def copy_tool(
        self,
        editor: Callable[[], MarkdownEditor | None],
        mark: str,
        context: Callable[[], reports.Context] | None = None,
    ) -> None:
        """In the toolbar of a note's ``editor``: copy the note with its citations numbered and
        the papers cited listed (``context``: of its citations, by default ``note_context``),
        marked ``{mark}-copy``."""
        context = context or self.note_context
        copy_button(
            lambda: reports.with_references(e.value, context()) if (e := editor()) else None,
            _("Copy the note, its citations numbered and the papers cited listed"),
            f"{mark}-copy",
            _("Copied (with the references)"),
        )

    def open_cited(self, key: str) -> None:
        """The paper of a citation key, in a new window: its details on the person's page."""
        pub = next((i for i, k in reports.citation_keys(self.stats).items() if k == key), None)
        if pub is None:
            ui.notify(_("No paper cited as @{key}").format(key=key), type="warning")
            return
        ui.navigate.to(urls.person_url(self.person_id, self.period_id, pub=pub), new_tab=True)

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

        async def click(e) -> None:  # (shift-clicked: inline, as Shift+Q)
            used()
            if (ed := editor()) is not None:
                await self.quote(ed, inline=bool((e.args or {}).get("shiftKey")))

        button = ui.button(icon="post_add").props("flat dense round size=sm")
        button.on("click", click, ["shiftKey"])
        self.quote_buttons.append(button)
        button.tooltip(
            _(
                "Quote the text selected in the PDF (or the highlight clicked), with its page "
                "(Q) · Shift+click: at the cursor, in quotation marks, with a link back to its "
                "page in a comment (Shift+Q)"
            )
        ).mark("note-quote")

        def made() -> None:  # (the note's editor: listed, last; first once used)
            if (e := editor()) is not None:
                self.notes.insert(0, e) if first else self.notes.append(e)
                e.box.on("focusin", used)

        ui.timer(0, made, once=True)

    async def quote(self, editor: MarkdownEditor | None = None, *, inline: bool = False) -> None:
        """Quote the text selected (in the PDF; else on the page) into ``editor`` (else the
        note last used), as a Markdown quote with its page; ``inline``: at the cursor, in
        quotation marks, with a link back to its page in an Obsidian comment."""
        if editor is None:  # (Q: the notes' tab shown)
            notes = [e for e in self.notes if not e.editor.is_deleted]
            if not notes and self.folder_editor is not None:  # (none used yet)
                notes = [self.folder_editor]
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
        text = pdftext.clean_pdf_text(sel["text"], one_paragraph=True)
        if inline or editor is self.folder_editor:  # (names its source, linked back)
            name, url = self.source_link(page)
            name = name.replace("[", "(").replace("]", ")")
            where = f"[{name}, {where}]({url})" if where else f"[{name}]({url})"
        if inline:
            editor.insert_inline(inline_quote(text, where))
        else:
            editor.insert_block(quote(text, where))

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

    async def career_selection(self) -> None:
        """The career's lines in the text selected (or the areas drawn) in a document, read
        by the LLM at once (see person._extract_dialog)."""
        from .person import _extract_dialog

        sel = await ui.run_javascript("vrPdf.selection()")
        text = (sel or {}).get("text") or ""
        if not text.strip():
            ui.notify(_("Select the text (or an area over it) in the PDF first"), type="warning")
            return
        d = documents.info(self.source[1])
        page = career_extract.Page(d.id, d.name, sel.get("p") or 1, text.strip(), "selection")
        _extract_dialog(self.person_id, lambda: None, page)


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


# An excerpt's markers: (label, tip, icon, colour); their tags in the reports: see
# reports.markers.
MARKER_ICONS = {
    "salient": (N_("Salient"), N_("A highlight of the file (“saillant”)"), "star", "amber-8"),
    "influence": (
        N_("Influence"),
        N_("Shows the person's influence (“rayonnement”: invited talks, prizes…)"),
        "public",
        "teal",
    ),
    "minor": (N_("Minor"), N_("Of lesser weight (“mineur”)"), "south", "blue-grey"),
    "caveat": (N_("Caveat"), N_("A reservation (“bémol”)"), "report", "deep-orange"),
}


def excerpt_properties(
    start: int | None = None,
    end: int | None = None,
    influence: bool = False,
    tags: list[str] | None = None,
    *,
    salient: bool = False,
    minor: bool = False,
    caveat: bool = False,
    cleared: Callable[[], None] | None = None,
    folder_id: int | None = None,
    level: str | None = None,
) -> Callable[[], dict]:
    """The fields of an excerpt's years (a button clears them, then calls ``cleared``),
    markers (see MARKER_ICONS), level (one of the folder's, see reports.levels; none: its
    default) and tags (those of papers and excerpts, or new ones typed); returns their values
    (the keywords of categories.add_excerpt / update_excerpt; its ``set_years``, a function
    setting the years)."""

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
    on = {"salient": salient, "influence": influence, "minor": minor, "caveat": caveat}
    marker_in = MarkerBadges(next((k for k, v in on.items() if v), "")).classes("-mt-2")
    level_in = LevelBadges(folder_id, level).classes("-mt-2")
    tags_in = (
        ui.select(
            sorted(set(categories.tag_names()) | set(tags or []), key=str.lower),
            label=_("Tags"),
            value=list(tags or []),
            multiple=True,
            new_value_mode="add-unique",
            with_input=True,
        )
        .props("dense outlined use-chips hide-dropdown-icon")
        .classes("w-full")
        .tooltip(_("Written as #tags before the excerpt in the Markdown (type a new one, Enter)"))
        .mark("excerpt-tags")
    )

    def values() -> dict:
        return {
            "start": int_or_none(start_in.value),
            "end": int_or_none(end_in.value),
            **{k: k == marker_in.value for k in MARKER_ICONS},
            "tags": list(tags_in.value or []),
            "level": level_in.value or None,
        }

    def set_years(start: int | None, end: int | None) -> None:
        start_in.set_value(start)
        end_in.set_value(end)

    values.set_years = set_years  # type: ignore[attr-defined]
    return values


class MarkerBadges(ui.row):
    """An excerpt's marker (see MARKER_ICONS), as badges: at most one (like its level),
    clicked to choose it, clicked again to clear it; its ``value``, the marker (none: "")."""

    def __init__(self, marker: str) -> None:
        super().__init__()
        self.classes("items-center gap-1").mark("excerpt-marker")
        self.value = marker
        self.chips: dict[str, ui.chip] = {}
        with self:
            ui.label(_("Marker")).classes("text-sm text-grey-8")
            for key, (label, tip, icon, _colour) in MARKER_ICONS.items():
                self.chips[key] = (
                    ui.chip(_(label), icon=icon, on_click=lambda key=key: self.pick(key))
                    .props("dense clickable")
                    .tooltip(_(tip))
                    .mark(f"excerpt-{key}")
                )
        self.show()

    def pick(self, key: str) -> None:
        self.value = "" if self.value == key else key
        self.show()

    def show(self) -> None:
        for key, chip in self.chips.items():
            colour = MARKER_ICONS[key][3]
            chosen = key == self.value
            chip.props(f"color={colour if chosen else 'grey-3'}")
            chip.props(f"text-color={'white' if chosen else 'grey-9'}")
            chip.props(f"icon-color={'white' if chosen else colour}")


class LevelBadges(ui.row):
    """An excerpt's level, as badges: one of the folder's (by its tag; a level no longer
    among them: kept), clicked to choose it, clicked again to clear it (no level: the folder's
    default, named after them); its ``value``, the level's key (none: "")."""

    def __init__(self, folder_id: int | None, level: str | None) -> None:
        super().__init__()
        self.classes("items-center gap-1").mark("excerpt-level")
        self.value = level or ""
        shown = {lv.key: f"#{lv.tag}" for lv in reports.levels(folder_id)}
        if level and level not in shown:
            shown[level] = f"#{level}"
        default = reports.default_level(folder_id)
        self.chips: dict[str, ui.chip] = {}
        with self:
            ui.label(_("Level")).classes("text-sm text-grey-8")
            for key, tag in shown.items():
                self.chips[key] = (
                    ui.chip(tag, on_click=lambda key=key: self.pick(key))
                    .props("dense clickable")
                    .tooltip(
                        _(
                            "Its level (e.g. national), written as a #tag in the Markdown; "
                            "clicked again: no level. The folder's levels and default one: in "
                            "its reports' settings"
                        )
                    )
                    .mark(f"excerpt-level-badge-{key}")
                )
            self.none = (
                ui.label(
                    _("(default: {level})").format(level=shown[default])
                    if default in shown
                    else _("(no level)")
                )
                .classes("text-xs text-grey italic")
                .mark("excerpt-level-none")
            )
        self.show()

    def pick(self, key: str) -> None:
        self.value = "" if self.value == key else key
        self.show()

    def show(self) -> None:
        for key, chip in self.chips.items():
            chosen = key == self.value
            chip.props(f"color={'primary' if chosen else 'grey-3'}")
            chip.props(f"text-color={'white' if chosen else 'grey-9'}")
        self.none.set_visibility(not self.value)


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


def category_suggestions(folder_id: int, text: str, pick: Callable[[int], None]) -> None:
    """The categories suggested for a passage (those closest to its embedding: see
    classifier.py), clicked to pick one; when they were learnt (from the excerpts filed), and
    a button to learn again. Without an embedding model: greyed, saying so."""
    names = {n.id: n.path for n in categories.tree(folder_id)}
    on = bool(llm.embed_model(folder_id))
    with ui.column().classes("w-full gap-0").mark("category-suggestions"):
        # A header (what they come from, when learnt, to learn again), then the suggestions
        # one per line: the category itself stressed, its parents dimmed, its similarity
        with ui.row().classes("w-full items-center gap-1 no-wrap"):
            ui.icon("auto_awesome", size="xs", color="purple" if on else "grey-5").tooltip(
                _("Suggested from the excerpts filed (their embeddings)")
            )
            if not on:
                ui.label(
                    _("Suggestions: turn on the local LLM, choose an embedding model (Settings)")
                ).classes("text-xs text-grey-5 grow").mark("category-suggestions-off")
                ui.button(_("Train"), icon="model_training").props(
                    "flat dense size=sm no-caps disable"
                ).mark("classifier-train")
                return
            info = (
                ui.label()
                .classes("text-xs text-grey grow min-w-0 ellipsis")
                .mark("classifier-trained")
            )
            accuracy = ui.label().classes("text-xs text-grey shrink-0").mark("classifier-accuracy")
            with accuracy:
                accuracy_tip = ui.tooltip()
            train_button = (
                ui.button(_("Train"), icon="model_training")
                .props("flat dense size=sm no-caps color=purple")
                .tooltip(_("Learn again from the excerpts filed in the folder's categories"))
                .mark("classifier-train")
            )
        chips = ui.column().classes("w-full gap-0 pl-4")

    def show_trained() -> None:
        t = classifier.trained(folder_id)
        info.set_text(
            ngettext(
                "Trained on {when} ({n} excerpt)", "Trained on {when} ({n} excerpts)", t.count
            ).format(when=fmt_dt(t.trained_at), n=t.count)
            if t
            else _("Never trained")
        )
        accuracy.set_visibility(bool(t and t.top1 is not None))
        if t and t.top1 is not None:
            accuracy.set_text(_("{top1:.0%} right").format(top1=t.top1))
            accuracy_tip.set_text(
                _(
                    "Each excerpt hidden then suggested: its category first {top1:.0%}, among "
                    "the first three {top3:.0%} ({prototypes} clusters for {categories} "
                    "categories)"
                ).format(top1=t.top1, top3=t.top3, prototypes=t.prototypes, categories=t.categories)
            )

    async def load() -> None:
        try:
            found = await classifier.suggest(folder_id, text)
        except Exception as e:
            found, error = [], str(e)
        else:
            error = None
        chips.clear()
        with chips:
            if error:
                ui.label(_("No suggestion: {error}").format(error=error)).classes(
                    "text-xs text-negative ellipsis"
                )
            elif not found:
                ui.label(_("No suggestion (train first)")).classes("text-xs text-grey")
            for cat, score in found:
                if cat not in names:
                    continue
                *up, leaf = names[cat].split(" › ")
                with (
                    ui.item(on_click=lambda cat=cat: pick(cat))
                    .props("dense")
                    .classes("w-full rounded-borders px-2 py-0 min-h-0 items-center gap-2")
                    .tooltip(_("Similarity: {score:.0%}").format(score=score))
                    .mark(f"category-suggested-{cat}")
                ):
                    # (the parents cut first, so that the category itself shows)
                    with ui.element("div").classes("grow min-w-0 flex no-wrap text-sm"):
                        if up:
                            ui.label(" › ".join(up) + " ›\u00a0").classes(
                                "min-w-0 ellipsis text-grey-7"
                            ).style("flex-shrink: 1000")
                        ui.label(leaf).classes("min-w-0 ellipsis text-purple text-weight-medium")
                    ui.label(f"{score:.0%}").classes("text-xs text-grey shrink-0")

    async def train() -> None:
        train_button.props("loading")
        try:
            t = await classifier.train(folder_id)
        except ValueError:
            ui.notify(_("No excerpt filed yet: nothing to learn from"), type="warning")
        except Exception as e:
            ui.notify(_("Cannot train: {error}").format(error=e), type="negative")
        else:
            ui.notify(
                ngettext("Learnt from {n} excerpt", "Learnt from {n} excerpts", t.count).format(
                    n=t.count
                ),
                type="positive",
            )
        finally:
            train_button.props(remove="loading")
        show_trained()
        await load()

    train_button.on_click(train)
    show_trained()
    ui.timer(0.05, load, once=True)


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
    around: tuple[int, int] | None = None,
    suggest: Callable[[], Awaitable[excerpt_rewrite.Rewrite | None]] | None = None,
) -> None:
    """Choose the category of an excerpt (``chosen`` is called with it): click it, or type to
    find it (Enter: the first one), or name a new one. ``title``, ``done`` (its
    ``{category}`` named): marked with ``N_``, translated here. ``current``: its category, shown;
    ``properties``: also its years (those found in the text, then taken out of it if leading
    or ending it: see categories.split_years; cleared, the text as it was), influence flag
    and tags (passed to ``chosen`` as keywords, with ``text``, so taken out or not);
    ``merge``: or merge it with an excerpt (called with it, and whether only its place is
    cited), the similar ones shown with a warning; ``around``: its years when its text has
    none (found around it: see categories.context_years); ``suggest``: a neutral wording of
    it and its years (see excerpt_rewrite), asked for once the dialog is open and proposed
    there, edited, then used (its text filed, the text selected kept as its original) or not.
    The category chosen last (here, else that
    of the excerpt filed last) is proposed: marked, scrolled to, and Enter picks it (nothing
    typed)."""
    folder_id = side.folder[0]
    tree = categories.tree(folder_id)
    last = side.last_category
    if last is None and side.period_id is not None:
        last = categories.last_category(side.period_id)
    if last == current or all(n.id != last for n in tree):
        last = None
    with side.host.dialogs, transient_dialog(_(title), width="w-full max-w-xl") as (dlg, _card):
        years, filed = categories.split_years(text) if properties else (None, text)
        years = years or around
        quote = ui.label().classes("text-sm text-grey").mark("excerpt-text")

        def show(t: str) -> None:
            nonlocal filed
            filed = t
            short = t if len(t) <= 300 else t[:299] + "…"
            quote.set_text(f"“{' '.join(short.split())}”")

        show(filed)
        if merge is not None and side.period_id is not None:
            similar_excerpts(side, text, lambda lead, ref_only: (merge(lead, ref_only), close(dlg)))
        rewritten = False

        def cleared() -> None:
            nonlocal rewritten
            rewritten = False
            show(text)

        props = (
            excerpt_properties(*(years or (None, None)), cleared=cleared, folder_id=folder_id)
            if properties
            else dict
        )
        if properties and suggest is not None:
            hint = ui.row().classes("w-full items-center no-wrap gap-2").mark("excerpt-rewrite")
            with hint:
                ui.spinner(size="sm")
                ui.label(_("The local AI rewrites the excerpt…")).classes("text-xs text-grey")

            def use(wording: str, r: excerpt_rewrite.Rewrite) -> None:
                nonlocal rewritten
                if wording := " ".join(wording.split()):
                    show(wording)
                    rewritten = True
                if r.start or r.end:
                    props.set_years(r.start, r.end)  # type: ignore[attr-defined]
                hint.set_visibility(False)

            async def proposed() -> None:
                r = await suggest()
                if hint.is_deleted:
                    return
                hint.clear()
                if r is None:
                    hint.set_visibility(False)
                    return
                with hint:
                    ui.icon("auto_awesome", size="xs", color="primary")
                    wording = (
                        ui.input(_("Neutral wording (local AI)"), value=r.text)
                        .props("dense outlined")
                        .classes("grow")
                        .mark("excerpt-rewrite-text")
                    )
                    if r.start or r.end:
                        ui.label(categories.years_label(r.start, r.end)).classes(
                            "text-xs text-grey"
                        ).mark("excerpt-rewrite-years")
                    ui.button(_("Use it"), on_click=lambda: use(wording.value or "", r)).props(
                        "flat dense no-caps"
                    ).tooltip(_("File this wording and these years (the text selected kept)")).mark(
                        "excerpt-rewrite-use"
                    )

            background_tasks.create(proposed(), name="excerpt-rewrite")

        def pick(cat_id: int) -> None:
            side.last_category = cat_id
            chosen(
                cat_id,
                **({"text": filed} if properties else {}),
                **({"original": text} if rewritten else {}),
                **props(),
            )
            name = next((n.path for n in categories.tree(folder_id) if n.id == cat_id), "")
            ui.notify(_(done).format(category=name))
            close(dlg)

        with category_list(side, filed, pick, current=current, last=last):
            ui.space()
            ui.button(_("Close"), on_click=dlg.close).props("flat").mark("category-close")


def category_list(
    side: Side,
    text: str,
    pick: Callable[[int], None],
    *,
    current: int | None = None,
    last: int | None = None,
) -> ui.row:
    """The folder's categories to pick one (``pick`` is called with it, but ``current``):
    click it, or type to find it (Enter: ``last``, nothing typed, else the first one), or name
    a new one; those suggested for ``text`` first. Returns the row of its buttons (to add
    others)."""
    folder_id, folder_name = side.folder
    search = (
        ui.input(
            placeholder=(
                _("Find a category of {folder} (Enter: the last one chosen)")
                if last is not None
                else _("Find a category of {folder} (Enter: the first one)")
            ).format(folder=folder_name)
        )
        .props("dense outlined clearable autofocus")
        .classes("w-full")
        .mark("category-search")
    )

    def matching() -> list[categories.Node]:
        q = (search.value or "").lower().strip()
        nodes = categories.tree(folder_id)
        return [n for n in nodes if q in n.path.lower()] if q else nodes

    def chosen(cat_id: int) -> None:
        if cat_id != current:
            pick(cat_id)

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
                .classes("vr-last-category bg-blue-1" if n.id == last else "")
                .on("click", lambda n=n: chosen(n.id))
                .mark(f"pick-category-{n.id}")
            ):
                ui.icon("label" if n.depth else "folder_special", size="xs", color="primary")
                ui.label(n.path if q else n.name).classes(
                    "grow" + (" text-grey" if n.id == current else "")
                )
                if n.id == last:
                    ui.icon("history", size="xs", color="primary").tooltip(
                        _("The last category chosen (Enter, nothing typed)")
                    ).mark("category-last")
                if n.id == current:
                    ui.label(_("(its category)")).classes("text-xs text-grey")

    def enter() -> None:
        nodes = matching()
        if last is not None and not (search.value or "").strip():
            chosen(last)
        elif nodes:
            chosen(nodes[0].id)
        elif (search.value or "").strip():
            create()

    def create() -> None:
        """A new category (the list stays: click it to file the excerpt)."""
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
    category_suggestions(folder_id, text, chosen)
    with ui.column().classes("w-full gap-0 max-h-96 overflow-auto"):
        listing()
    if last is not None:  # (scrolled to, once shown)
        ui.run_javascript(
            "setTimeout(() => document.querySelector('.vr-last-category')"
            "?.scrollIntoView({block: 'nearest'}), 200);"
        )
    with ui.row().classes("w-full items-center gap-2") as row:
        parent = (
            ui.select(parents(), value=0, label=_("Create under"))
            .props("dense outlined options-dense")
            .classes("w-56")
            .mark("category-parent")
        )
        ui.button(_("Create"), icon="add", on_click=create).props("flat dense").tooltip(
            _("Create a category named as typed above")
        ).mark("category-create")
    return row


COLLAPSED = "ui.excerpts.collapsed"  # the categories shown collapsed in the excerpts (sticky)


class _ExcerptsSection:
    """The excerpts of the person (in the folder) by category: to go to (in this PDF, or
    another one), edit, move, merge (drag one onto another, or its merge icon then a click
    on the other), discard from the generated text, remove, copy as Markdown; the categories
    collapsed (each, or all), the excerpts searched."""

    def __init__(self, side: Side) -> None:
        self.side = side
        self.merging: categories.ExcerptView | None = None  # (merged with the one clicked)
        self.collapsed: set[int] = set(annotations.ui_state(COLLAPSED) or [])
        self.query = ""  # (the excerpts searched: only those matching shown)
        # (the folder's levels: their tags by key, and its default one; see reports.level_tags)
        self.levels: tuple[dict[str, str], str | None] = ({}, None)
        side.edit_excerpt = self.edit_excerpt  # (E on an excerpt filed already)
        side.reveal_excerpt = self.reveal
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
            if nodes:
                ui.button(icon="unfold_less", on_click=lambda: self.collapse_all(nodes)).props(
                    "flat dense round size=sm"
                ).tooltip(_("Collapse all the categories")).mark("categories-collapse-all")
                ui.button(icon="unfold_more", on_click=lambda: self.collapse_all([])).props(
                    "flat dense round size=sm"
                ).tooltip(_("Expand all the categories")).mark("categories-expand-all")
            copy_button(
                lambda: categories.markdown(folder_id, self.side.period_id),
                _("Copy the excerpts, as Markdown"),
                "excerpts-copy",
                _("Copied (Markdown)"),
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
        ui.input(
            placeholder=_("Search the excerpts (their text, tags, or a section's text)"),
            value=self.query,
            on_change=lambda e: self.search(e.value),
        ).props("dense outlined clearable debounce=300").classes("w-full").mark(
            "excerpts-search"
        ).add_slot("prepend", '<q-icon name="search" />')
        self.body()

    @ui.refreshable_method
    def body(self) -> None:
        """The categories, their texts and excerpts (searched: only those matching, and the
        categories with some, or whose text matches; collapsed: not what is within)."""
        if self.side.folder is None or self.side.period_id is None:
            return
        nodes = categories.tree(self.side.folder[0])
        by_cat: dict[int, list[categories.ExcerptView]] = {}
        self.side.excerpt_rows.clear()
        for e in categories.excerpts(self.side.period_id, grouped=True):
            by_cat.setdefault(e.category_id, []).append(e)
        texts = categories.section_texts(self.side.period_id)
        summarised = categories.summarised(self.side.period_id)
        self.levels = reports.level_tags(self.side.folder[0])
        query = self.query.strip().casefold()
        found: set[int] = set()  # (searched: the categories shown, with those above them)
        if query:
            parents = {n.id: n.parent_id for n in nodes}
            for n in nodes:
                hits = [e for e in by_cat.get(n.id, []) if self.matches(e, query)]
                by_cat[n.id] = hits
                if hits or query in texts.get(n.id, "").casefold():
                    i: int | None = n.id
                    while i is not None and i not in found:
                        found.add(i)
                        i = parents.get(i)
            if not found:
                ui.label(_("No excerpt matches")).classes("text-sm text-grey").mark(
                    "excerpts-search-none"
                )
                return
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
        unseen: set[int] = set()  # (within a collapsed category)
        for n in nodes:
            if query and n.id not in found:
                continue
            if n.parent_id in unseen or (not query and n.parent_id in self.collapsed):
                unseen.add(n.id)
                continue
            items = by_cat.get(n.id, [])
            folded = not query and n.id in self.collapsed
            with (
                ui.row()
                .classes("w-full items-center no-wrap gap-1 rounded")
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
                if items or n.children or texts.get(n.id):
                    ui.button(
                        icon="chevron_right" if folded else "expand_more",
                        on_click=lambda n=n: self.toggle(n.id),
                    ).props("flat dense round size=xs color=grey-7").classes("shrink-0").tooltip(
                        _("Show what is within it") if folded else _("Hide what is within it")
                    ).mark(f"category-toggle-{n.id}")
                else:
                    ui.element("div").classes("w-5 shrink-0")
                ui.element("div").classes("w-3 h-3 rounded-full shrink-0").style(
                    f"background: {n.colour}"
                ).tooltip(_("The colour of its excerpts (edit the categories to change it)")).mark(
                    f"category-dot-{n.id}"
                )
                ui.label(n.name).classes("font-medium text-sm" if not n.depth else "text-sm")
                if items:
                    ui.badge(str(len(items))).props("rounded color=amber-8")
                section_text.edit_button(self.side, n)
            if folded:
                unseen.add(n.id)
                continue
            # (summarised by its text only: its excerpts not listed in the notes, here yes)
            hidden = len(items) if n.id in summarised else None
            section_text.show(self.side, n, texts.get(n.id, ""), hidden)
            for e in items:  # (an excerpt, or a group: its lead, then the others)
                with (
                    ui.column()
                    .classes("w-full gap-0" + (" opacity-50" if e.discarded else ""))
                    .style(f"padding-left:{1.2 * n.depth + 0.6}rem")
                    .mark(
                        f"excerpt-{e.id}" + (f" excerpt-discarded-{e.id}" if e.discarded else "")
                    ) as block
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
                        ui.label(e.group_text).classes(
                            "text-sm line-clamp-4" + (" line-through" if e.discarded else "")
                        ).style(f"border-left: 3px solid {e.colour}; padding-left: 0.4rem").tooltip(
                            _("The text of the group (edit it with its pencil)")
                        ).mark(f"excerpt-group-text-{e.id}")
                    for x in e.parts:
                        self.quote(x, e, merging)
                    if e.comment:
                        with (
                            ui.row()
                            .classes("w-full items-start no-wrap gap-1 cursor-pointer")
                            .on("click", lambda e=e: self.edit_excerpt(e, "notes"))
                            .tooltip(_("Its commentary (click to edit it)"))
                            .mark(f"excerpt-comment-text-{e.id}")
                        ):
                            ui.icon("comment", size="xs", color="grey-6").classes("mt-1")
                            ui.markdown(e.comment, extras=NOTE_EXTRAS).classes(
                                "vr-note text-xs text-grey-8 italic grow min-w-0"
                            )

    def level(self, e: categories.ExcerptView) -> tuple[str, bool] | None:
        """An excerpt's level: its tag, and whether it is the folder's default (none: no
        level, or one no longer among the folder's)."""
        tags, default = self.levels
        if e.level:
            return (tags[e.level], False) if e.level in tags else None
        return (tags[default], True) if default in tags else None

    def matches(self, e: categories.ExcerptView, query: str) -> bool:
        """Whether an excerpt (a group) has the text searched: in its text (as edited, or
        selected; that of the group, of the others), its commentary, a tag, or its level's."""
        level = self.level(e)
        words = [e.group_text or "", e.comment or "", *e.tag_names]
        words += [level[0]] if level else []
        words += [t for x in e.parts for t in (x.text, x.original or "")]
        return any(query in w.casefold() for w in words)

    def search(self, value: str | None) -> None:
        self.query = value or ""
        self.body.refresh()

    def toggle(self, cat_id: int) -> None:
        self.collapsed.symmetric_difference_update({cat_id})
        annotations.save_ui_state(COLLAPSED, sorted(self.collapsed))
        self.body.refresh()

    def reveal(self, excerpt_id: int) -> None:
        """An excerpt selected (e.g. on the PDF) shown: the search cleared, the categories
        above it expanded."""
        if self.side.folder is None or self.side.period_id is None:
            return
        found = next(
            (x for x in categories.excerpts(self.side.period_id) if x.id == excerpt_id), None
        )
        if found is None:
            return
        parents = {n.id: n.parent_id for n in categories.tree(self.side.folder[0])}
        i: int | None = found.category_id
        while i is not None:
            self.collapsed.discard(i)
            i = parents.get(i)
        annotations.save_ui_state(COLLAPSED, sorted(self.collapsed))
        self.query = ""
        self.listing.refresh()

    def collapse_all(self, nodes: list[categories.Node]) -> None:
        """Collapse the categories (none: expand them all)."""
        self.collapsed = {n.id for n in nodes}
        annotations.save_ui_state(COLLAPSED, sorted(self.collapsed))
        self.body.refresh()

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
                    + (" line-through" if lead.discarded else "")
                )
                if lead.discarded and not member:
                    label.tooltip(_("Left out of the generated text (the notes, the reports)"))
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
                else:  # (beside a person's page: there; else in a new window)
                    url = excerpt_url(x)
                    label.on("click", js_handler=f"() => vrLink.open({json.dumps(url)})")
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
                    for key, (label, _tip, icon, colour) in MARKER_ICONS.items():
                        if not member and getattr(lead, key):
                            ui.icon(icon, size="xs", color=colour).classes("shrink-0").tooltip(
                                _(label)
                            ).mark(f"excerpt-{key}-{x.id}")
                    if not member and lead.tags:
                        with (
                            ui.row()
                            .classes("gap-1 shrink-0 items-center")
                            .mark(f"excerpt-tags-{x.id}")
                        ):
                            for name, colour in lead.tags:
                                span(chip_html(name, colour, title=_("tag")))
                    if not member and (level := self.level(lead)):
                        ui.label(f"#{level[0]}").classes(
                            "text-xs text-blue-grey-7 shrink-0" + (" italic" if level[1] else "")
                        ).tooltip(
                            _("Its level (the folder's default)") if level[1] else _("Its level")
                        ).mark(f"excerpt-level-{x.id}")
                    if not member and lead.url:
                        ui.button(
                            icon="link",
                            on_click=lambda: ui.navigate.to(lead.url, new_tab=True),
                        ).props("flat dense round size=xs color=primary").classes(
                            "shrink-0"
                        ).tooltip(lead.url).mark(f"excerpt-url-{x.id}")
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
                                _("Edit (text, years, markers, tags…; category; notes)"),
                                self.edit_excerpt,
                            ),
                            (
                                "comment",
                                "comment" if lead.comment else "add_comment",
                                _(
                                    "Its commentary (Markdown): in the notes, a comment after "
                                    "it (%% … %%, not shown)"
                                ),
                                lambda x: self.edit_excerpt(lead, "notes"),
                            ),
                            *cite,
                            (
                                "move",
                                "drive_file_move",
                                _("Move to another category"),
                                lambda x: self.edit_excerpt(lead, "move"),
                            ),
                            (
                                "discard",
                                "replay" if lead.discarded else "clear",
                                _("Put it back in the generated text")
                                if lead.discarded
                                else _(
                                    "Leave it out of the generated text (the notes, the "
                                    "reports): still kept, and listed here"
                                ),
                                self.discard,
                            ),
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

    def discard(self, x: categories.ExcerptView) -> None:
        categories.set_discarded(x.id, not x.discarded)
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

    def originals(self, e: categories.ExcerptView, text: ui.textarea) -> None:
        """The passages as selected (of each excerpt of a group): where, how edited since; to
        copy into the text (that of a group: added to it), or to open (their PDF, at their
        page, in a new tab)."""
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
                    ui.button(icon="open_in_new").on(
                        "click", js_handler=f"() => vrLink.open({json.dumps(excerpt_url(x))})"
                    ).props("flat dense round size=xs color=grey-7").tooltip(
                        _("Open its document (at its page)")
                    ).mark(f"excerpt-original-open-{x.id}")

    def category_line(self, e: categories.ExcerptView) -> None:
        """Under the title of an excerpt's dialog: its category (its path, in its colour)."""
        nodes = categories.tree(self.side.folder[0]) if self.side.folder else []
        path = next((n.path for n in nodes if n.id == e.category_id), None)
        if path is None:
            return
        with ui.row().classes("items-center gap-1 no-wrap -mt-3").mark("excerpt-category"):
            ui.icon("circle", size="0.7rem").style(f"color: {e.colour}")
            ui.label(path).classes("text-sm text-grey-8")

    def edit_excerpt(self, e: categories.ExcerptView, tab: str = "text") -> None:
        """A dialog on an excerpt (that of its group), in tabs: its text (that of a group),
        years, markers, level and tags, the passages as selected shown; its category (one
        picked: all saved, then moved there); its commentary (Markdown) and link."""
        side = self.side
        with (
            side.host.dialogs,
            transient_dialog(
                _("Merged excerpts") if e.members else _("Excerpt"), width="w-[40rem] max-w-full"
            ) as (dialog, _card),
        ):
            self.category_line(e)
            with ui.tabs().props("dense align=left no-caps").classes("w-full") as tabs:
                tab_text = ui.tab("text", _("Excerpt"), icon="format_quote").mark(
                    "excerpt-tab-text"
                )
                ui.tab("move", _("Category"), icon="drive_file_move").mark("excerpt-tab-move")
                ui.tab("notes", _("Notes"), icon="comment").mark("excerpt-tab-notes")
            with ui.tab_panels(tabs, value=tab).classes("w-full"):
                with ui.tab_panel(tab_text).classes("p-1 gap-2"):
                    text = (
                        ui.textarea(
                            _("Text of the group") if e.members else _("Text"), value=e.quoted
                        )
                        .props("outlined autogrow")
                        .classes("w-full")
                        .mark("excerpt-edit-text")
                    )
                    if e.members:
                        ui.label(
                            _(
                                "Its quotes, joined; edited, it is the text of the group (its "
                                "excerpts: their places only). Blank: its quotes again."
                            )
                        ).classes("text-xs text-grey")
                    self.originals(e, text)
                    props = excerpt_properties(
                        e.start_year,
                        e.end_year,
                        e.influence,
                        e.tag_names,
                        salient=e.salient,
                        minor=e.minor,
                        caveat=e.caveat,
                        folder_id=side.folder[0] if side.folder else None,
                        level=e.level,
                    )
                with ui.tab_panel("move").classes("p-1 gap-2"):
                    path = next(
                        (n.path for n in categories.tree(side.folder[0]) if n.id == e.category_id),
                        "",
                    )
                    ui.label(
                        _("In {category}: pick another one to move it there (and save)").format(
                            category=path
                        )
                    ).classes("text-sm text-grey")

                    def moved(cat_id: int) -> None:
                        save()
                        categories.move_excerpt(e.id, cat_id)
                        side.last_category = cat_id
                        name = next(
                            (n.path for n in categories.tree(side.folder[0]) if n.id == cat_id),
                            "",
                        )
                        ui.notify(_("Moved to {category}").format(category=name))
                        close(dialog)
                        side.refresh_excerpts()

                    category_list(side, e.quoted, moved, current=e.category_id)
                with ui.tab_panel("notes").classes("p-1 gap-2"):
                    editor = MarkdownEditor(
                        e.comment or "",
                        render=lambda t: (
                            reports.render(
                                t, reports.folder_context(side.stats, side.period_id)
                            ).text
                        ),
                        mode="split",
                        mark="excerpt-comment-input",
                        height="10rem",
                    )
                    editor.open_cited = side.open_cited
                    ui.label(
                        _(
                            "Its commentary: in the notes and the copy, a comment after the "
                            "excerpt (%% … %%), kept for reference, not shown. Empty: none."
                        )
                    ).classes("text-xs text-grey")
                    with ui.row().classes("w-full items-center no-wrap gap-1"):
                        url = (
                            ui.input(_("Link"), value=e.url or "", placeholder="https://…")
                            .props("dense outlined clearable")
                            .classes("grow")
                            .tooltip(_("A link (URL), written after the excerpt in the notes"))
                            .mark("excerpt-url")
                        )
                        ui.button(
                            icon="open_in_new",
                            on_click=lambda: (
                                ui.navigate.to(url.value.strip(), new_tab=True)
                                if (url.value or "").strip()
                                else None
                            ),
                        ).props("flat dense round size=sm color=grey-7").tooltip(
                            _("Open the link")
                        ).mark("excerpt-url-open")

            def save() -> None:
                if e.members:
                    joined = " […] ".join(x.text for x in e.parts if not x.ref_only)
                    edited = " ".join((text.value or "").split())
                    categories.set_group_text(e.id, None if edited == joined else edited)
                categories.update_excerpt(e.id, text=None if e.members else text.value, **props())
                categories.set_comment(e.id, editor.value)
                categories.set_url(e.id, url.value)
                side.refresh_excerpts()

            actions(dialog, _("Save"), save, mark="excerpt-edit-save")
        if tab == "notes":
            editor.focus()


def categories_section(side: Side) -> Callable[[], None]:
    """The excerpts by category (``_ExcerptsSection``); returns its refresh."""
    return _ExcerptsSection(side).listing.refresh


def excerpt_url(e: categories.ExcerptView) -> str:
    """The PDF of an excerpt, at its page."""
    page = f"page={e.page}" if e.page else ""
    if e.document_id:
        return f"/doc/{e.document_id}" + (f"?{page}" if page else "")
    return f"/pdf/{e.publication_id}" + (f"?{page}" if page else "")
