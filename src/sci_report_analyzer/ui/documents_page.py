"""Documents of a person within a period / folder (their application, a CV…): the person's
Report and Excerpts tabs (their notes and excerpts in the folder; the documents in a menu,
opened beside the page), and a document's page (read and annotated with PDF.js, the person's
papers it mentions found and linked)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from nicegui import app, ui

from .. import annotations, categories, documents, pdfs, reports, urls
from ..i18n import N_, Labels, _
from .dialogs import actions, confirm, ok_handler, transient_dialog
from .mdedit import MarkdownEditor, tool_group
from .pdf_viewer import file_response, gone, install_or_notify, viewer_frame
from .tags import note_editor
from .theme import DEFAULT_COLOUR, bare_page
from .timeline import timeline_view
from .viewer_side import Side, bookmark_here, bookmarks_section, categories_section

if TYPE_CHECKING:
    from .pdf_dock import PdfDock

# A PDF's lines of text (PDF.js: each with its page, text, rectangle and runs), as the server
# keeps them (see documents.set_lines), of all its pages or some; and a document's, extracted
# and sent.
LINES_JS = """
window.sraLines = window.sraLines || (async (d, pages) => {
  const out = [];
  const round = (r) => r.map(v => Math.round(v * 10) / 10);
  for (const p of pages || Array.from({length: d.numPages}, (_, k) => k + 1)) {
    const page = await d.getPage(p);
    const tc = await page.getTextContent();
    let cur = null;
    const flush = () => {
      if (cur && cur.t.trim()) out.push({p, t: cur.t, r: round(cur.r), c: cur.c});
      cur = null;
    };
    for (const it of tc.items) {
      if (typeof it.str !== 'string') continue;
      const t = it.transform, h = Math.hypot(t[2], t[3]) || it.height || 1;
      const x = t[4], y = t[5];
      if (it.str) {
        if (cur && (Math.abs(y - cur.y) > h * 0.6 || x - cur.end > h * 3)) flush();
        if (!cur) cur = {t: '', y, end: x, r: [x, y - h * 0.2, x, y + h * 0.8], c: []};
        else if (x - cur.end > h * 0.15 && !/\\s$/.test(cur.t) && !/^\\s/.test(it.str)) {
          cur.t += ' ';
        }
        // (each run: its offset in the line, and where it starts and ends)
        cur.c.push([cur.t.length, Math.round(x * 10) / 10, Math.round((x + it.width) * 10) / 10]);
        cur.t += it.str; cur.end = x + it.width;
        cur.r[0] = Math.min(cur.r[0], x); cur.r[2] = Math.max(cur.r[2], x + it.width);
        cur.r[1] = Math.min(cur.r[1], y - h * 0.2); cur.r[3] = Math.max(cur.r[3], y + h * 0.8);
      }
      if (it.hasEOL) flush();
    }
    flush();
    page.cleanup();
  }
  return out;
});
window.sraReadText = window.sraReadText || (async (id) => {
  try {
    const lib = await import('/pdfjs/build/pdf.mjs');
    lib.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.mjs';
    const d = await lib.getDocument({url: '/doc-file/' + id}).promise;
    const res = await fetch('/doc-text/' + id, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(await sraLines(d)),
    });
    return res.ok ? '' : await res.text();
  } catch (e) { return String(e); }
});
"""

# Extracts the document's lines of text (sent once, to find its papers in), and shows the
# papers found: their places underlined, a button at each to show the paper.
_DOC_SCRIPT = """
<script>
%(lines)s
Object.assign(window.vrDoc, {
  id: %(id)s, needText: %(need)s,
  texts: %(texts)s,
  async extract() {
    return await sraLines(vrPdf.app().pdfDocument);
  },
  async sendText() {
    this.needText = false;
    vrPdf.status(this.texts.looking);
    try {
      const res = await fetch('/doc-text/' + this.id, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(await this.extract()),
      });
      if (!res.ok) throw new Error(await res.text());
      vrPdf.status('');
      vrPane.emit('vr-doc-text', null, true);
    } catch (e) { vrPdf.status(this.texts.failed.replace('{error}', e.message)); }
  },
});
</script>
"""

KINDS = Labels(
    {
        "title": N_("its title"),
        "id": N_("its DOI / HAL id"),
        "cite": N_("a citation"),
        "manual": N_("linked by hand"),
    }
)


def page_url_of(doc_id: int, *, embed: bool = False) -> str:
    """A document's page; ``embed``: beside the person's page (see pdf_dock)."""
    return f"/doc/{doc_id}" + ("?embed=1" if embed else "")


def register() -> None:
    @app.get("/doc-file/{doc_id}")
    def doc_file(doc_id: int, download: bool = False) -> FileResponse:
        return file_response(documents.file_of(doc_id), download)

    @app.put("/doc-file/{doc_id}")
    async def save_doc(doc_id: int, request: Request) -> PlainTextResponse:
        if documents.file_of(doc_id) is None:
            raise HTTPException(404)
        try:
            documents.save(doc_id, await request.body())
        except pdfs.PdfError as e:
            raise HTTPException(400, str(e)) from e
        return PlainTextResponse("ok")

    @app.post("/doc-text/{doc_id}")
    async def doc_text(doc_id: int, request: Request) -> PlainTextResponse:
        if documents.info(doc_id) is None:
            raise HTTPException(404)
        lines = await request.json()
        if not isinstance(lines, list):
            raise HTTPException(400, "a list of lines")
        documents.set_lines(doc_id, lines)
        return PlainTextResponse("ok")

    @ui.page("/doc/{doc_id}")
    def doc_page(
        doc_id: int,
        page: int | None = None,
        pane: str | None = None,
        report: bool = False,
        embed: bool = False,
    ) -> RedirectResponse | None:
        """``report``: (formerly) opened from the person's Report tab: there, beside it."""
        d = documents.info(doc_id)
        if report and d is not None and not pane:
            key = f"d{d.id}"
            return RedirectResponse(
                urls.person_url(d.person_id, d.period_id, tab="report", pdfs=key, pdf=key)
            )
        bare_page(d.name if d else _("Document"), menu=not embed)
        if d is None or documents.file_of(doc_id) is None:
            ui.label(_("No such document")).classes("p-4")
            return None
        if not pdfs.has_viewer():
            _install()
            return None
        DocumentPage(d, page, pane, embed)
        return None


def _install() -> None:
    with ui.row().classes("p-6 items-center gap-2"):
        ui.spinner(size="sm")
        label = ui.label("")

    async def go() -> None:
        try:
            await install_or_notify(label)
        except pdfs.PdfError as e:
            label.text = str(e)
            return
        ui.navigate.reload()

    ui.timer(0.1, go, once=True)


class DocumentPage:
    """A document, its papers (found, or linked by hand), bookmarks and note."""

    def __init__(
        self,
        d: documents.DocInfo,
        page: int | None = None,
        pane: str | None = None,
        embed: bool = False,
    ) -> None:
        """``pane``: only the side column, in its own window; ``embed``: beside the person's
        page (see viewer_frame)."""
        self.d = d
        self.mentions: list[documents.Mention] = []
        file = documents.file_of(d.id)
        self.side = Side(d.person_id, d.period_id, ("doc", d.id))
        self.side.on_render.append(self.update)
        self.side.link = self.link
        box = viewer_frame(
            d.name,
            (d.person, urls.person_url(d.person_id, d.period_id)),
            (d.person_id, d.period_id, "publications"),
            f"/doc-file/{d.id}",
            int(file.stat().st_mtime) if file else 0,
            self.side,
            ("doc", d.id),
            _DOC_SCRIPT
            % {
                "lines": LINES_JS,
                "id": d.id,
                "need": json.dumps(not d.has_lines),
                "texts": json.dumps(
                    {
                        "looking": _("Looking for the papers…"),
                        "failed": _("Papers not looked for: {error}"),
                    }
                ),
            },
            page=page,
            pane=pane,
            embed=embed,
            rename=lambda title: rename_dialog(d.id, title.text, lambda n: self.renamed(title, n)),
        )
        self.side.attach(box)
        with self.side.section("categories", "category", _("Excerpts, by category")):
            self.side.categories = categories_section(self.side)
        self.side.here = lambda: {m.pub_id for m in self.mentions}
        self.side.folder_notes()
        with self.side.section("notes", "sticky_note_2", _("Notes on the document"), fill=True):
            self.note = note_editor(
                _("Note (on the document)"),
                d.note,
                lambda text: documents.set_note(d.id, text),
                mark="doc-note",
                mode="split",
                stacked=True,
                height="14rem",
                render=lambda text: reports.render(text, self.side.note_context()).text,
                toolbar=self._note_tools,
                fill=True,
            )
        with self.side.section("bookmarks", "bookmarks", _("Bookmarks")):
            self.side.bookmarks = bookmarks_section("doc", d.id)
        with self.side.section("papers", "article", _("Papers in this document")):
            ui.label(f"{d.person} · {d.period}").classes("text-sm text-grey")
            ui.label(_("Papers in this document")).classes("font-medium")
            self.search = (
                ui.input(
                    placeholder=_("Search (title, authors, venue, year)"), on_change=self._filter
                )
                .props("dense clearable")
                .classes("w-full")
                .mark("doc-papers-search")
            )
            self.rows: list[tuple[ui.row, str]] = []
            self.papers = ui.column().classes("w-full gap-1").mark("doc-papers")
            with self.papers:
                ui.spinner()
        self.side.on_attach.append(self._note_again)
        ui.on("vr-doc-text", self.update)
        ui.on("vr-doc-paper", self.clicked)
        ui.on("vr-doc-excerpt", lambda e: self.side.select_excerpt((e.args or {}).get("id")))
        ui.timer(0.05, self._load, once=True)

    def renamed(self, title: ui.label, name: str) -> None:
        """Its title above the PDF; beside a person's page, its tab in the strip too."""
        title.set_text(name)
        key = json.dumps(f"d{self.d.id}")
        ui.run_javascript(
            f"vrPane.dock()?.emit('vr-dock-renamed', {{key: {key}, name: {json.dumps(name)}}})"
        )

    async def _load(self) -> None:
        await self.side.load()
        self.side.refresh_excerpts(others=False)
        self.note.refresh_preview()

    def _note_again(self) -> None:
        """The note as saved (edited in another window meanwhile)."""
        d = documents.info(self.d.id)
        if d is not None and not self.note.is_dirty():
            self.note.adopt(d.note or "")

    # ---- The note's citations ([@key], as in reports) ----

    def _note_tools(self) -> None:
        def note() -> MarkdownEditor | None:
            return getattr(self, "note", None)

        with tool_group():
            self.side.quote_tool(note)
            self.side.cite_tools(note, "doc-note")
        with tool_group():
            self.side.copy_tool(note, "doc-note")

    def update(self) -> None:
        """Find the papers again (in the text, once the viewer sent it), list and show them."""
        if gone(self.papers):
            return
        lines = documents.lines_of(self.d.id)
        self.papers.clear()
        if lines is None:
            with self.papers, ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm")
                ui.label(_("Looking for the papers…")).classes("text-sm text-grey")
            return
        rows = [s for s in self.side.stats if not s.hidden]
        by_id = {s.id: s for s in rows}
        self.mentions = documents.find_papers(lines, rows, documents.links_of(self.d.id))
        links = [
            {
                "i": i,
                "page": m.page,
                "rects": m.rects,
                "kind": m.kind,
                "pdf": bool(by_id[m.pub_id].pdf),
                "title": self.side.cite_hint(by_id[m.pub_id]),
                "colour": "#cf222e" if by_id[m.pub_id].pdf else DEFAULT_COLOUR,
            }
            for i, m in enumerate(self.mentions)
        ]
        self.papers.client.run_javascript(f"vrDoc.show({json.dumps(links)})")
        with self.papers:
            self._listing(by_id)
            self._rejected(by_id, lines)

    def _rejected(self, by_id: dict[int, Any], lines: list[dict[str, Any]]) -> None:
        """The papers rejected here ("Not this paper"): to find them there again."""
        rejected = [(i, x) for i, x in documents.rejected(self.d.id) if x["pub"] in by_id]
        if not rejected:
            return
        untitled = _("(untitled)")
        with (
            ui.expansion(_("Rejected here ({n})").format(n=len(rejected)), icon="link_off")
            .props("dense")
            .classes("w-full text-sm text-grey")
            .mark("doc-rejected")
        ):
            for i, x in rejected:
                s = by_id[x["pub"]]
                line = x.get("line")
                page = lines[line].get("p") if line is not None and line < len(lines) else None
                with ui.row().classes("w-full items-center no-wrap gap-1"):
                    ui.label(
                        f"{s.title or untitled} ({s.year or '?'})"
                        + (" · " + _("p. {page}").format(page=page) if page else "")
                    ).classes("grow min-w-0 text-sm")
                    ui.button(icon="undo", on_click=lambda i=i: self.restore(i)).props(
                        "flat dense round size=sm"
                    ).tooltip(_("Not wrongly found after all: find it here again")).mark(
                        f"doc-restore-{s.id}"
                    )

    def restore(self, index: int) -> None:
        documents.restore(self.d.id, index)
        self.update()
        ui.notify(_("Found here again"))

    def _listing(self, by_id: dict[int, Any]) -> None:
        self.rows = []
        if not self.mentions:
            ui.label(
                _(
                    "None found. Select a reference (or a title) and ‘find’ in the header to "
                    "link it."
                )
            ).classes("text-sm text-grey")
            return
        untitled = _("(untitled)")
        order: dict[int, list[int]] = {}
        for i, m in enumerate(self.mentions):
            order.setdefault(m.pub_id, []).append(i)
        for pub_id, idx in order.items():
            s = by_id[pub_id]
            with (
                ui.row()
                .classes("w-full items-start no-wrap gap-1")
                .mark(f"doc-paper-{pub_id}") as row
            ):
                ui.icon(
                    "picture_as_pdf" if s.pdf else "article",
                    size="xs",
                    color="red-8" if s.pdf else "primary",
                ).classes("mt-1")
                with ui.column().classes("gap-0 grow min-w-0"):
                    ui.label(f"{s.title or untitled} ({s.year or '?'})").classes(
                        "cursor-pointer text-sm text-primary hover:underline"
                    ).on("click", lambda i=idx[0]: self.show(i)).mark(f"doc-show-{pub_id}")
                    with ui.row().classes("gap-2"):
                        self._places([self.mentions[i] for i in idx])
            text = " ".join([s.title or "", *s.authors, s.venue or "", str(s.year or "")])
            self.rows.append((row, text.lower()))
        self.none = ui.label(_("No paper matches")).classes("text-sm text-grey")
        self._filter()

    def _filter(self) -> None:
        """Only the papers matching the search (every word, anywhere)."""
        words = (self.search.value or "").lower().split()
        shown = 0
        for row, text in self.rows:
            row.visible = all(w in text for w in words)
            shown += row.visible
        if self.rows and not gone(self.none):
            self.none.visible = shown == 0

    def _places(self, mentions: list[documents.Mention]) -> None:
        """Where a paper is in the document (to go there): its references, its citations."""
        places: dict[tuple, list[documents.Mention]] = {}
        for m in mentions:
            key = ("cite", m.label) if m.kind == "cite" else (m.kind, m.page, m.line)
            places.setdefault(key, []).append(m)
        for (kind, *_rest), ms in places.items():
            m = ms[0]
            top = max((r[3] for r in m.rects), default=None)
            y = json.dumps(top + 20 if top is not None else None)
            if kind != "cite":
                text = _("p. {page}").format(page=m.page)
            else:  # "[11]", or "Lyu et al., 2023b"
                text = m.label if " " in (m.label or "") else f"[{m.label}]"
            if len(ms) > 1:
                text += f" ×{len(ms)}"
            pages = ", ".join(str(p) for p in dict.fromkeys(x.page for x in ms))
            ui.label(text).classes("text-xs text-primary cursor-pointer").on(
                "click", js_handler=f"() => vrPdf.go({m.page}, {y})"
            ).tooltip(_("{kind}, page {pages}").format(kind=KINDS[kind].capitalize(), pages=pages))

    def clicked(self, e) -> None:
        args = e.args
        if isinstance(args, list):
            args = args[0] if args else {}
        i = args.get("i") if isinstance(args, dict) else args
        if isinstance(i, int) and 0 <= i < len(self.mentions):
            if isinstance(args, dict) and args.get("cite"):  # (shift-click)
                self.side.cite(self.mentions[i].pub_id)
            else:
                self.show(i)

    def show(self, i: int) -> None:
        """The details of a paper found, with where it was found (and a way to undo it)."""
        m = self.mentions[i]

        def header() -> None:
            with ui.row().classes("w-full items-center gap-1 text-sm text-grey"):
                ui.label(_("Page {page}, {kind}").format(page=m.page, kind=KINDS[m.kind]))
                ui.space()
                ui.button(
                    _("Not this paper") if m.kind != "manual" else _("Unlink"),
                    icon="link_off",
                    on_click=lambda: self.reject(m),
                ).props("flat dense size=sm color=grey").tooltip(
                    _("Wrongly found here: forget it")
                ).mark("doc-reject")

        self.side.show_paper(m.pub_id, header)

    def reject(self, m: documents.Mention) -> None:
        documents.reject(self.d.id, m)
        self.side.back()
        self.update()
        if m.kind != "manual":
            ui.notify(_('Not found here any more (undo: "Rejected here", under the papers)'))

    def link(self, pub_id: int, page: int, rects: list, text: str) -> None:
        if not rects:
            ui.notify(_("The selection's place is unknown"), type="warning")
            return
        documents.add_link(self.d.id, pub_id, page, rects, text)
        self.update()
        ui.notify(_("Linked"))


# ---- The Report and Excerpts tabs of a person ------------------------------------------------

# (the boxes ``vr-fit`` as high as the room left in the window, the page not scrolled, above the
# strip of the PDFs beside it: at least 24rem)
_FIT_JS = """
window.vrFit = () => {
  const strip = document.querySelector('.vr-dock-strip');
  const below = 36 + (strip ? Math.max(0, innerHeight - strip.getBoundingClientRect().top) : 0);
  for (const e of document.querySelectorAll('.vr-fit')) {
    if (!e.offsetParent) continue;
    const top = e.getBoundingClientRect().top + scrollY;
    e.style.height = Math.max(384, innerHeight - top - below) + 'px';
  }
};
if (!window.vrFitOn) {
  window.vrFitOn = true;
  addEventListener('resize', () => window.vrFit());
}
window.vrFit();
"""


class ReportTabs:
    """A person's Report, Excerpts and Timeline tabs, within a folder (or a period; chosen when
    several, ``period_id`` first): their notes there, with its documents (added here, opened
    beside the page: see pdf_dock); their excerpts there, by category; and by year. Each
    filled once shown (the timeline: each time, the excerpts edited meanwhile). The PDF
    shown beside the page is theirs: quoted into the notes (Q), its passages filed (E), its
    selection's paper found (F), its list's papers tagged (L), its place bookmarked (B), its
    excerpts clicked selected (its side column hidden: see vrDock)."""

    def __init__(
        self,
        person_id: int,
        period_id: int | None,
        dock: PdfDock,
        boxes: dict[str, ui.column],
        show_tab: Callable[[str], None],
    ) -> None:
        """``boxes``: where the tabs go ("report", "excerpts", "timeline")."""
        self.person_id = person_id
        self.dock = dock
        self.show_tab = show_tab
        self.periods = sorted(
            annotations.periods(person_id, include_hidden_folders=True),
            key=lambda p: p.folder_id is None,
        )
        self.period = next(
            (p for p in self.periods if p.id == period_id),
            self.periods[0] if self.periods else None,
        )
        self.boxes = boxes
        self.sides: dict[str, Side] = {}
        self._documents_of()
        dock.on_shown.append(self._shown)
        ui.on("vr-pdf-quote", lambda: self._quote(inline=False))
        ui.on("vr-pdf-quote-inline", lambda: self._quote(inline=True))
        ui.on("vr-pdf-excerpt", self._excerpt)
        ui.on("vr-page-excerpt", self._page_excerpt)
        ui.on("vr-pdf-find", self._find)
        ui.on("vr-pdf-tag-list", self._tag_list)
        ui.on("vr-pdf-bookmark", self._bookmark)
        ui.on("vr-doc-excerpt", lambda e: self._select((e.args or {}).get("id")))

    def fill(self, which: str) -> None:
        """The tab ``which``, unless filled already."""
        box = self.boxes[which]
        if which == "timeline":
            box.clear()
        elif box.default_slot.children:
            return
        with box:
            self._build(which)
        ui.timer(0.05, lambda: ui.run_javascript(_FIT_JS), once=True)  # (once laid out)

    def _build(self, which: str) -> None:
        p = self.period
        if p is None:
            ui.label(
                _("Put the person in a folder, or add a period (Profile tab), to write a report.")
            ).classes("text-grey").mark("documents-no-period")
            return
        if p.folder is None:
            with ui.row().classes("items-center"):
                self._choice(which)
            ui.label(_("Notes and excerpts: only within a folder.")).classes("text-sm text-grey")
            return
        if which == "timeline":
            with ui.row().classes("items-center"):
                self._choice(which)
            timeline_view(self.person_id, p, self._select, self._open, self.show_tab)
            return
        side = (_notes if which == "report" else _excerpts)(self.person_id, p.id)
        self.sides[which] = side
        side.dock(self.dock.source)
        with side.bar:  # (on the row of its tabs: the folder and its documents)
            self._choice(which)

    def _choice(self, which: str) -> None:
        """The period, chosen when several (the folder is named above); the documents."""
        p = self.period
        if len(self.periods) > 1:
            ui.select(
                {q.id: q.name for q in self.periods},
                value=p.id,
                on_change=lambda e: self.choose(e.value),
            ).props("dense options-dense borderless").classes("w-64").mark(f"{which}-period")
        if p.folder and p.folder.hidden:
            ui.badge(_("archived"), color="grey")
        if which == "report":
            self.dock.on_added = _documents(
                p, lambda d: self.dock.open(f"d{d.id}"), self.dock.set_documents
            )

    def _documents_of(self) -> None:
        """Those of the period, always in the strip of the PDFs (within a folder)."""
        p = self.period
        docs = documents.of_person(self.person_id).get(p.id, []) if p and p.folder else []
        self.dock.set_documents(docs)
        self.dock.set_folder(p.id if p and p.folder else None)

    def choose(self, period_id: int) -> None:
        """Another period: the tabs filled again."""
        self.period = next((p for p in self.periods if p.id == period_id), self.period)
        self._documents_of()
        self.sides.clear()
        for which, box in self.boxes.items():
            if box.default_slot.children:
                box.clear()
                self.fill(which)

    def _side(self, which: str) -> Side | None:
        """That of the tab ``which``, shown (filled first)."""
        self.show_tab(which)
        self.fill(which)
        return self.sides.get(which)

    def _shown(self, source: tuple[str, int] | None) -> None:
        for side in self.sides.values():
            side.dock(source)

    async def _quote(self, *, inline: bool) -> None:
        if (side := self._side("report")) is not None:  # (built now: quoted once its editor is)
            await side.quote(inline=inline)

    async def _excerpt(self) -> None:
        doc = self.dock.source is not None and self.dock.source[0] == "doc"
        if doc and (side := self._side("excerpts")) is not None:
            await side.add_excerpt()

    async def _page_excerpt(self) -> None:
        """E on the page: the text selected in the PDF filed (or its excerpt edited); else the
        excerpt selected (its entry clicked, in the Excerpts tab) edited."""
        side = self.sides.get("excerpts")
        sel = await ui.run_javascript("vrPdf.excerpted()") if self.dock.source else None
        if not (sel and (sel.get("text") or "").strip()) and side and side.excerpt is not None:
            lead = next(
                (
                    e
                    for e in categories.excerpts(side.period_id, grouped=True)
                    if side.excerpt in (e.id, *(m.id for m in e.members))
                ),
                None,
            )
            if lead is not None and side.edit_excerpt is not None:
                side.edit_excerpt(lead)
                return
        if self.dock.source is None:
            ui.notify(
                _("Select an excerpt (its entry, or on its PDF) or a passage of a PDF first"),
                type="warning",
            )
            return
        await self._excerpt()

    def _select(self, excerpt_id: int | None) -> None:
        if (side := self._side("excerpts")) is not None:
            side.select_excerpt(excerpt_id)

    def _open(self, e: categories.ExcerptView) -> None:
        """The PDF of an excerpt beside the page, at its page."""
        key = f"d{e.document_id}" if e.document_id else f"p{e.publication_id}"
        self.dock.open(key, e.page)

    async def _find(self) -> None:
        """The paper of the selection (shown in the Report tab; a document's: to link it
        there too)."""
        built = "report" in self.sides
        if self.dock.source is not None and (side := self._side("report")) is not None:
            if not built:  # (its papers, to match: loaded first)
                await side.load()
            side.link = self._link if self.dock.source[0] == "doc" else None
            await side.find_selection()

    def _link(self, pub_id: int, page: int, rects: list, text: str) -> None:
        """The selection in the document shown linked to a paper (see DocumentPage.link):
        its papers found there again."""
        if not rects or self.dock.source is None:
            ui.notify(_("The selection's place is unknown"), type="warning")
            return
        documents.add_link(self.dock.source[1], pub_id, page, rects, text)
        ui.run_javascript("vrDock.win()?.emitEvent('vr-doc-text')")
        ui.notify(_("Linked"))

    async def _tag_list(self) -> None:
        built = "report" in self.sides
        if self.dock.source is not None and (side := self._side("report")) is not None:
            if not built:  # (its papers, to match: loaded first)
                await side.load()
            await side.tag_selection()

    async def _bookmark(self) -> None:
        if (source := self.dock.source) is not None:
            await bookmark_here(*source)


def _documents(p, open_doc, on_change=None) -> Callable[[], None]:
    """A menu of the documents of the person within a period / folder (an application, a
    CV…): open (or rename, delete…: their ⋮), and the last item to add some; ``on_change``:
    given them, once listed. Returns what lists them again (one added elsewhere)."""
    button = (
        ui.button(icon="description")
        .props("flat dense no-caps icon-right=arrow_drop_down")
        .mark(f"documents-{p.id}")
    )

    @ui.refreshable
    def listing() -> None:
        docs = documents.of_person(p.person_id).get(p.id, [])
        button.text = _("Documents ({n})").format(n=len(docs))
        if on_change is not None:
            on_change(docs)
        for d in docs:
            _doc_item(d, listing.refresh, open_doc)
        if not docs:
            ui.label(_("No document")).classes("text-sm text-grey px-4 py-2")

    async def upload(e) -> None:
        try:
            documents.add(p.id, e.file.name, await e.file.read())
        except pdfs.PdfError as err:
            ui.notify(f"{e.file.name}: {err}", type="warning")
            return
        listing.refresh()

    # (the file picker of the Add… item)
    picker = (
        ui.upload(multiple=True, auto_upload=True, on_upload=upload)
        .props('accept=".pdf,application/pdf"')
        .classes("hidden")
        .mark(f"documents-upload-{p.id}")
    )
    with (
        button,
        ui.menu().props("anchor='bottom left' self='top left'").mark(f"documents-menu-{p.id}"),
    ):
        listing()
        ui.separator()
        with (
            ui.menu_item(on_click=lambda: picker.run_method("pickFiles")).mark(
                f"documents-add-{p.id}"
            ),
            ui.row().classes("items-center gap-2 no-wrap"),
        ):
            ui.icon("add", color="primary")
            ui.label(_("Add…"))
    return listing.refresh


def rename_dialog(doc_id: int, name: str, then: Callable[[str], None]) -> None:
    """A document renamed, once its new name is given (``then``: with it)."""
    with transient_dialog(width="w-96") as (dlg, _card):
        field = ui.input(_("Name"), value=name).classes("w-full").mark("document-name")

        def ok() -> bool | None:
            if not (field.value or "").strip():
                return False
            documents.rename(doc_id, field.value.strip())
            then(field.value.strip())

        field.on("keydown.enter", ok_handler(dlg, ok))
        actions(dlg, _("Rename"), ok, mark="document-rename-ok")


def _doc_item(d: documents.DocView, refresh, open_doc) -> None:
    """A document in the menu: opened when clicked; its ⋮ to open it in a new tab, download,
    rename or delete it."""

    def rename() -> None:
        rename_dialog(d.id, d.name, lambda _name: refresh())

    def delete() -> None:
        def ok() -> None:
            documents.remove(d.id)
            refresh()

        confirm(
            _("Delete “{name}”, with its annotations and notes?").format(name=d.name),
            _("Delete"),
            ok,
            mark="document-delete-ok",
        )

    with (
        ui.menu_item(on_click=lambda: open_doc(d)).mark(f"document-open-{d.id}"),
        ui.row().classes("w-full items-center gap-2 no-wrap").mark(f"document-{d.id}"),
    ):
        ui.icon("edit_document" if d.edited else "description", color="red-8")
        ui.label(d.name).classes("ellipsis grow min-w-0 max-w-80")
        if d.note:
            ui.icon("sticky_note_2", size="xs", color="amber-9").tooltip(d.note)
        ui.label(f"{d.added_at:%Y-%m-%d}").classes("text-xs text-grey")
        with (
            ui.button(icon="more_vert")
            .props("flat dense round size=sm")
            .on("click.stop", lambda: None)
            .mark(f"document-more-{d.id}"),
            ui.menu().props("anchor='top end' self='top start'").classes("min-w-48"),
        ):
            ui.menu_item(
                _("Open in a new tab"),
                on_click=lambda: ui.navigate.to(page_url_of(d.id), new_tab=True),
            )
            ui.menu_item(
                _("Download"), on_click=lambda: ui.navigate.to(f"/doc-file/{d.id}?download=1")
            )
            ui.menu_item(_("Rename…"), on_click=rename).mark(f"document-rename-{d.id}")
            ui.menu_item(_("Delete"), on_click=delete).classes("text-negative").mark(
                f"document-delete-{d.id}"
            )


def _side_box(person_id: int, period_id: int, mark: str) -> Side:
    """A side of the person's page (as next to their PDFs; of the PDF shown beside it, see
    Side.dock), as high as the room left in the window."""
    side = Side(person_id, period_id, ("notes", 0))
    side.docked = True
    side.attach(ui.column().classes("w-full gap-2 vr-fit").style("height:24rem").mark(mark))
    return side


def _loaded(side: Side) -> Side:
    side.tab_bar.set_visibility(False)  # (one tab)

    async def load() -> None:
        await side.load()
        side.refresh_excerpts(others=False)

    ui.timer(0.05, load, once=True)
    return side


def _notes(person_id: int, period_id: int) -> Side:
    """The person's notes in the folder."""
    side = _side_box(person_id, period_id, "notes-side")
    side.folder_notes()
    return _loaded(side)


def _excerpts(person_id: int, period_id: int) -> Side:
    """The person's excerpts in the folder, by category."""
    side = _side_box(person_id, period_id, "excerpts-side")
    with side.section("categories", "category", _("Excerpts, by category")):
        side.categories = categories_section(side)
    return _loaded(side)
