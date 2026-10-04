"""Documents of a person within a period / folder (their application, a CV…): the Documents
tab of the person's page, and a document's page (read and annotated with PDF.js, the
person's papers it mentions found and linked)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from nicegui import app, ui

from .. import annotations, documents, pdfs, reports
from ..i18n import N_, Labels, _
from .pdf_viewer import file_response, gone, install_or_notify, viewer_frame
from .tags import note_editor
from .viewer_side import Side, bookmarks_section, categories_section

# Extracts the document's lines of text (sent once, to find its papers in), and shows the
# papers found: their places underlined, a button at each to show the paper.
_DOC_SCRIPT = """
<script>
Object.assign(window.vrDoc, {
  id: %(id)s, needText: %(need)s,
  texts: %(texts)s,
  async extract() {
    const d = vrPdf.app().pdfDocument, out = [];
    const round = (r) => r.map(v => Math.round(v * 10) / 10);
    for (let p = 1; p <= d.numPages; p++) {
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


def page_url_of(doc_id: int) -> str:
    return f"/doc/{doc_id}"


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
    def doc_page(doc_id: int, page: int | None = None, pane: str | None = None) -> None:
        d = documents.info(doc_id)
        ui.page_title(f"{d.name if d else _('Document')} · SciReport Analyzer")
        ui.query(".nicegui-content").classes("p-0 gap-0")
        if d is None or documents.file_of(doc_id) is None:
            ui.label(_("No such document")).classes("p-4")
            return
        if not pdfs.has_viewer():
            _install()
            return
        DocumentPage(d, page, pane)


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
        self, d: documents.DocInfo, page: int | None = None, pane: str | None = None
    ) -> None:
        """``pane``: only the side column, in its own window (see viewer_frame)."""
        self.d = d
        self.mentions: list[documents.Mention] = []
        file = documents.file_of(d.id)
        self.side = Side(d.person_id, d.period_id, ("doc", d.id))
        self.side.on_render.append(self.update)
        self.side.link = self.link
        box = viewer_frame(
            d.name,
            (d.person, f"/person/{d.person_id}/{d.period_id}"),
            f"/doc-file/{d.id}",
            int(file.stat().st_mtime) if file else 0,
            self.side,
            ("doc", d.id),
            _DOC_SCRIPT
            % {
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
        ui.timer(0.05, self._load, once=True)

    async def _load(self) -> None:
        await self.side.load()
        self.side.refresh_excerpts()
        self.note.refresh_preview()

    def _note_again(self) -> None:
        """The note as saved (edited in another window meanwhile)."""
        d = documents.info(self.d.id)
        if d is not None and not self.note.is_dirty():
            self.note.adopt(d.note or "")

    # ---- The note's citations ([@key], as in reports) ----

    def _note_tools(self) -> None:
        self.side.quote_tool(lambda: getattr(self, "note", None))
        self.side.cite_tools(lambda: getattr(self, "note", None), "doc-note")

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
                "colour": "#cf222e" if by_id[m.pub_id].pdf else "#0969da",
            }
            for i, m in enumerate(self.mentions)
        ]
        self.papers.client.run_javascript(f"vrDoc.show({json.dumps(links)})")
        with self.papers:
            self._listing(by_id)

    def _listing(self, by_id: dict[int, Any]) -> None:
        if not self.mentions:
            ui.label(
                _(
                    "None found. Select a reference (or a title) and ‘find’ in the header to "
                    "link it."
                )
            ).classes("text-sm text-grey")
            return
        self.rows = []
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

    def link(self, pub_id: int, page: int, rects: list, text: str) -> None:
        if not rects:
            ui.notify(_("The selection's place is unknown"), type="warning")
            return
        documents.add_link(self.d.id, pub_id, page, rects, text)
        self.update()
        ui.notify(_("Linked"))


# ---- The Documents tab of a person ----------------------------------------------------------


def documents_view(person_id: int) -> None:
    """The person's documents, by period / folder: open, upload, rename, delete."""
    ui.label(
        _(
            "Documents of the person within a folder or a period (an application, a CV…): "
            "read and annotated like the papers' PDFs, the papers they mention found and linked."
        )
    ).classes("text-sm text-grey")

    @ui.refreshable
    def listing() -> None:
        periods = annotations.periods(person_id, include_hidden_folders=True)
        docs = documents.of_person(person_id)
        if not periods:
            ui.label(
                _("Put the person in a folder, or add a period (Periods tab), to add documents.")
            ).classes("text-grey").mark("documents-no-period")
            return
        for p in sorted(periods, key=lambda p: p.folder_id is None):
            with ui.card().classes("w-full").mark(f"documents-{p.id}"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("folder" if p.folder else "date_range", color="amber-8")
                    ui.label(p.name).classes("font-medium")
                    if p.folder and p.folder.hidden:
                        ui.badge(_("hidden"), color="grey")
                for d in docs.get(p.id, []):
                    _doc_row(d, listing.refresh)
                if not docs.get(p.id):
                    ui.label(_("No document")).classes("text-sm text-grey")

                async def upload(e, pid=p.id) -> None:
                    try:
                        documents.add(pid, e.file.name, await e.file.read())
                    except pdfs.PdfError as err:
                        ui.notify(f"{e.file.name}: {err}", type="warning")
                        return
                    listing.refresh()

                ui.upload(
                    label=_("Add PDFs"), multiple=True, auto_upload=True, on_upload=upload
                ).props('accept=".pdf,application/pdf" flat bordered').classes("w-80").mark(
                    f"documents-upload-{p.id}"
                )

    listing()


def _doc_row(d: documents.DocView, refresh) -> None:
    with ui.row().classes("w-full items-center no-wrap gap-2").mark(f"document-{d.id}"):
        icon = "edit_document" if d.edited else "description"
        with ui.link(target=page_url_of(d.id), new_tab=True).classes(
            "flex items-center gap-1 grow min-w-0"
        ):
            ui.icon(icon, color="red-8")
            ui.label(d.name).classes("ellipsis")
        if d.note:
            ui.icon("sticky_note_2", size="xs", color="amber-9").tooltip(d.note)
        ui.label(f"{d.added_at:%Y-%m-%d}").classes("text-xs text-grey")
        with ui.link(target=f"/doc-file/{d.id}?download=1"):
            ui.button(icon="download").props("flat dense round size=sm").tooltip(_("Download"))

        def rename() -> None:
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                name = ui.input(_("Name"), value=d.name).classes("w-full").mark("document-name")

                def ok() -> None:
                    documents.rename(d.id, name.value)
                    dlg.close()
                    refresh()

                name.on("keydown.enter", ok)
                with ui.row().classes("w-full justify-end"):
                    ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                    ui.button(_("Rename"), on_click=ok).mark("document-rename-ok")
            dlg.on_value_change(lambda e: None if e.value else dlg.delete())
            dlg.open()

        def delete() -> None:
            with ui.dialog() as dlg, ui.card():
                ui.label(_("Delete “{name}”, with its annotations and notes?").format(name=d.name))

                def ok() -> None:
                    documents.remove(d.id)
                    dlg.close()
                    refresh()

                with ui.row().classes("w-full justify-end"):
                    ui.button(_("Cancel"), on_click=dlg.close).props("flat")
                    ui.button(_("Delete"), color="negative", on_click=ok).mark("document-delete-ok")
            dlg.on_value_change(lambda e: None if e.value else dlg.delete())
            dlg.open()

        ui.button(icon="edit", on_click=rename).props("flat dense round size=sm").mark(
            f"document-rename-{d.id}"
        )
        ui.button(icon="delete", on_click=delete).props(
            "flat dense round size=sm color=negative"
        ).mark(f"document-delete-{d.id}")
