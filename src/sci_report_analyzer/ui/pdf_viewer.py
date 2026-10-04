"""The PDF of a paper, read and annotated in the browser with PDF.js (highlight, text,
drawing, images): its edits are saved back into the data directory. Also what the viewer
pages share (the documents' too, see documents.py): bookmarks, the paper of a selection,
and a paper's details next to the PDF."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from weakref import WeakSet

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from nicegui import Client, app, background_tasks, ui

from .. import annotations, documents, livereload, pdfs, pdftext, persons
from ..i18n import N_, _, ngettext
from .dialogs import transient_dialog
from .theme import APP_NAME, bare_page

if TYPE_CHECKING:
    from ..pubview import PubStat
    from .panel import PublicationsPanel
    from .viewer_side import Side

NO_CONFIRM = "ui.pdf.no_confirm"  # AppSetting: download without asking first
SIDE_WIDTH = "ui.pdf.side_width"  # AppSetting: the width (px) of the side column
SIDE_DEFAULT, SIDE_MIN = 384, 240
PANE_TOKEN = re.compile(r"[a-z0-9]{1,40}")  # (of a PDF window, its side panel elsewhere)
_no_confirm: bool | None = None  # (cached NO_CONFIRM)
TAG_LIST_TIP = N_(
    "Tag from a list: find the papers of the selected list (e.g. an area over numbered "
    "references) and tag them (L)"
)

_TYPES = {
    ".mjs": "text/javascript",
    ".js": "text/javascript",
    ".ftl": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".icc": "application/octet-stream",
}


# The scripts of the PDF window and its side pane (ui/static), configured by window.vrConfig.
STATIC = Path(__file__).parent / "static"
STATIC_URL = "/vr-static"


def _scripts(config: dict, *names: str) -> None:
    """``config`` (window.vrConfig), then the static scripts ``names`` (their version: of
    their files, so that a new one is not cached)."""
    tags = [f"<script>window.vrConfig = {json.dumps(config)};</script>"]
    for name in ("shortcuts.js", *names):
        version = int((STATIC / name).stat().st_mtime)
        tags.append(f'<script src="{STATIC_URL}/{name}?v={version}"></script>')
    ui.add_head_html("\n".join(tags))


def _panels() -> dict[int, WeakSet[PublicationsPanel]]:
    """Person id -> their open publications panels, reloaded when a viewer window changes
    one of their papers (its PDF, tags or notes). Kept on the app: one registry, even if
    this module is imported again."""
    if not hasattr(app.state, "pdf_panels"):
        app.state.pdf_panels = {}
    return app.state.pdf_panels


def watch(panel: PublicationsPanel) -> None:
    """Keep ``panel`` up to date with the changes made from viewer windows."""
    _panels().setdefault(panel.person_id, WeakSet()).add(panel)


def changed(person_id: int, *, but: PublicationsPanel | None = None) -> None:
    """A paper of the person changed in a viewer window: reload their open panels (but
    ``but``, where the change was made)."""
    panels = _panels().get(person_id, WeakSet())
    for panel in list(panels):
        if gone(panel.container):
            panels.discard(panel)
        elif panel is not but:
            reload = getattr(panel, "reload_quietly", panel.reload)
            background_tasks.create(reload(), name="reload after a PDF window")


def gone(element: ui.element) -> bool:
    """Whether the page of ``element`` was left."""
    try:
        return element.is_deleted or element.client.id not in Client.instances
    except RuntimeError:  # (its client was deleted)
        return True


def _title(pub_id: int) -> tuple[str, int] | None:
    found = persons.paper(pub_id)
    return (found[0] or _("(untitled)"), found[1]) if found else None


def register() -> None:
    app.add_static_files(STATIC_URL, STATIC)

    @app.get("/pdfjs/{path:path}")
    def viewer_file(path: str) -> FileResponse:
        root = pdfs.viewer_dir().resolve()
        file = (root / path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, media_type=_TYPES.get(file.suffix))

    @app.get("/pdf-file/{pub_id}")
    def pdf_file(pub_id: int, download: bool = False) -> FileResponse:
        return file_response(pdfs.file_of(pub_id), download)

    @app.put("/pdf-file/{pub_id}")
    async def save_pdf(pub_id: int, request: Request) -> PlainTextResponse:
        found = _title(pub_id)
        if found is None or pdfs.file_of(pub_id) is None:
            raise HTTPException(404)
        first = pdfs.stored(found[1]).get(pub_id) is False  # (its first annotations)
        try:
            pdfs.save(pub_id, await request.body(), None, edited=True)
        except pdfs.PdfError as e:
            raise HTTPException(400, str(e)) from e
        if first:
            changed(found[1])
        return PlainTextResponse("ok")

    @ui.page("/pdf/{pub_id}")
    def pdf_page(
        pub_id: int,
        fetch: bool = False,
        period: int | None = None,
        page: int | None = None,
        pane: str | None = None,
    ) -> None:
        found = _title(pub_id)
        bare_page(found[0] if found else "PDF")
        if found is None:
            ui.label(_("No such paper")).classes("p-4")
            return
        title, person_id = found
        if not pdfs.has_viewer() or pdfs.file_of(pub_id) is None:
            _prepare(pub_id, title, fetch, period)
            return
        from .viewer_side import Side, bookmarks_section

        file = pdfs.file_of(pub_id)
        side = Side(person_id, period, ("pub", pub_id))
        box = viewer_frame(
            title,
            (APP_NAME, f"/person/{person_id}"),
            f"/pdf-file/{pub_id}",
            int(file.stat().st_mtime) if file else 0,
            side,
            ("pub", pub_id),
            page=page,
            pane=pane,
        )
        side.attach(box)
        with side.section("notes", "sell", _("Tags and notes"), fill=True):
            tags_box = ui.column().classes("w-full gap-2").mark("pdf-notes")
            with tags_box:
                ui.spinner()
        with side.section("details", "article", _("Publication details"), wide=True):
            details_box = ui.column().classes("w-full gap-2 vr-details").mark("pdf-pub-details")
            with details_box:
                ui.spinner()
        with side.section("bookmarks", "bookmarks", _("Bookmarks")):
            side.bookmarks = bookmarks_section("pub", pub_id)
        side.folder_notes()  # (last: the paper's own first)
        side.on_attach.append(lambda: _side(side, tags_box, details_box, pub_id))
        ui.timer(0.05, lambda: _side(side, tags_box, details_box, pub_id), once=True)


def file_response(file, download: bool) -> FileResponse:
    if file is None:
        raise HTTPException(404)
    return FileResponse(
        file,
        media_type="application/pdf",
        filename=file.name if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-store"},
    )


def viewer_frame(
    title: str,
    home: tuple[str, str],
    file_url: str,
    version: int,
    side: Side,
    bookmarked: tuple[str, int],
    script: str = "",
    page: int | None = None,
    pane: str | None = None,
) -> ui.column:
    """The page of a stored PDF: a header (back to ``home``, saving, bookmarks, finding the
    paper of a selection), PDF.js (opened at ``page``, else where it was last read), and the
    side column (returned). With ``pane`` (the token of a PDF window): only the side column,
    in its own window, kept in sync with that one."""
    if pane and PANE_TOKEN.fullmatch(pane):
        return _pane_frame(title, home, side, bookmarked, pane)
    texts = {
        "saving": _("Saving…"),
        "saved": _("Saved at {time}"),
        "failed": _("Not saved: {error}"),
    }
    ui.add_head_html(pdftext.SCRIPT)
    _scripts({"url": file_url, "texts": texts, "sideMin": SIDE_MIN}, "pdf_viewer.js")
    if script:
        ui.add_head_html(script)
    with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
        ui.link(home[0], home[1]).classes("text-white font-bold no-underline ellipsis max-w-48")
        for icon, step, tip in (
            ("arrow_back", "back", _("Back (after following a link; also ⌥← or ⌘[)")),
            ("arrow_forward", "forward", _("Forward (also ⌥→ or ⌘])")),
        ):
            with ui.element("span").tooltip(tip):  # (a button with its own id shows none)
                ui.button(icon=icon).props(f"flat dense round color=white id=vr-pdf-{step}").on(
                    "click", js_handler=f"() => vrPdf.app()?.pdfHistory?.{step}()"
                ).mark(f"pdf-{step}")
        ui.label(title).classes("ellipsis grow min-w-0 font-medium")
        ui.label("").classes("text-sm opacity-80").props("id=vr-pdf-status").mark("pdf-status")
        livereload.banner(dense=True)  # (--live-reload: a new version, to restart)
        _selection_actions(side, bookmarked, here=True)
        ui.button(_("Save"), icon="save").props("flat dense color=white").on(
            "click", js_handler="() => vrPdf.save(true)"
        ).tooltip(_("Save the annotations into the stored PDF (also every few seconds)"))
        with ui.link(target=f"{file_url}?download=1"):
            ui.button(icon="download").props("flat dense round color=white").tooltip(
                _("Download a copy")
            )
        ui.button(icon="open_in_new").props("flat dense round color=white").on(
            "click", js_handler="() => vrPane.toggle()"
        ).tooltip(
            _(
                "Show the side panel (notes, excerpts, bookmarks…) in another window, e.g. on "
                "another screen; again: back here"
            )
        ).mark("pdf-pane")

        def toggle_side() -> None:
            if side.detached:  # (in another window: back here)
                ui.run_javascript("vrPane.toggle()")
            else:
                box.set_visibility(not box.visible)

        ui.button(icon="view_sidebar", on_click=toggle_side).props(
            "flat dense round color=white"
        ).tooltip(_("Side panel (notes, bookmarks, papers)")).mark("pdf-toggle-notes")
    src = f"/pdfjs/web/viewer.html?file={file_url}%3Fv%3D{version}"
    # (at a page asked for, else where it was last read)
    src += f"#page={page}" if page else documents.place_hash(documents.last_place(*bookmarked))
    ui.on("vr-pdf-at", lambda e: documents.save_last_place(*bookmarked, e.args or {}))
    with ui.row().classes("w-full no-wrap gap-0"):
        ui.element("iframe").props(f'id=vr-pdf-frame src="{src}"').classes("grow").style(
            "height:calc(100vh - 40px); border:0"
        ).mark("pdf-frame")
        splitter = ui.element("div").props("id=vr-pdf-splitter")
        splitter.classes("shrink-0 bg-grey-4 hover:bg-primary").style(
            "width:5px; cursor:col-resize; height:calc(100vh - 40px)"
        ).tooltip(_("Drag to resize the side panel")).mark("pdf-splitter")
        width = max(SIDE_MIN, int(annotations.ui_state(SIDE_WIDTH, SIDE_DEFAULT) or SIDE_DEFAULT))
        box = (
            ui.column()
            .classes("shrink-0 p-3 gap-2 overflow-auto")
            .style(f"width:{width}px;height:calc(100vh - 40px)")
            .props("id=vr-pdf-side")
            .mark("pdf-side")
        )
        ui.on(
            "vr-pdf-side-width",
            lambda e: annotations.save_ui_state(SIDE_WIDTH, max(SIDE_MIN, int(e.args))),
        )

    async def detached(e) -> None:  # (the side panel in another window, or back)
        side.detached = bool(e.args)
        box.set_visibility(not side.detached)
        splitter.set_visibility(not side.detached)
        if not side.detached:
            await side.reattached()

    ui.on("vr-pane", detached)
    ui.timer(0, lambda: ui.run_javascript("vrPane.start()"), once=True)  # (once connected)
    return box


def _selection_actions(side: Side, bookmarked: tuple[str, int], *, here: bool) -> None:
    """The header's actions on the PDF's selection, and the events of their shortcuts.
    ``here``: in the PDF's window (with the area selection; done in the pane while the side
    panel is in another window), else in that other window."""

    def action(event: str, icon: str, tip: str, act: Callable[[], Any], mark: str) -> None:
        # (greyed out without a selection: the tooltip on a wrapper, a disabled button
        # showing none; the id for the PDF's script)
        with ui.element("span").tooltip(tip):
            ui.button(icon=icon, on_click=_here(side, event, act) if here else act).props(
                f"flat dense round color=white id={event}"
            ).mark(mark)
        ui.on(event, act)

    ui.on("vr-pdf-quote", lambda: side.quote())
    action(
        "vr-pdf-find",
        "manage_search",
        _("Find the paper of the selected text, e.g. a reference (F)"),
        side.find_selection,
        "pdf-find",
    )
    action(
        "vr-pdf-tag-list", "playlist_add_check", _(TAG_LIST_TIP), side.tag_selection, "pdf-tag-list"
    )
    if here:
        # An area (a rectangle) of a page, selected: its text, as a text selection.
        with ui.element("span").tooltip(
            _(
                "Select an area of a page (A, or Alt+drag): draw a rectangle on the page, the "
                "text inside it becomes the selection, to quote (Q), add as an excerpt (E), find "
                "its paper (F), tag the papers of a list (L) or copy (⌘C / Ctrl+C). Shift+drag "
                "adds another area (their texts, in order). Escape to leave."
            )
        ):
            ui.button(icon="highlight_alt").props("flat dense round color=white id=vr-pdf-area").on(
                "click", js_handler="() => vrPdf.areaMode()"
            ).mark("pdf-area")
    if side.folder and side.source[0] == "doc":  # (excerpts: of documents, not papers)
        action(
            "vr-pdf-excerpt",
            "playlist_add",
            _(
                "Add the selected text (or the highlight clicked) to a category of {folder} (E)"
            ).format(folder=side.folder[1]),
            side.add_excerpt,
            "pdf-excerpt",
        )
    action(
        "vr-pdf-bookmark",
        "bookmark_add",
        _("Bookmark this place, named after the selected text if any (B)"),
        lambda: side.add_bookmark(*bookmarked),
        "pdf-bookmark",
    )


def _here(side: Side, event: str, act: Callable[[], Any]) -> Callable[[], Awaitable[None]]:
    """A header action on the selection (``event``: its shortcut's), done in the pane while
    the side panel is in another window."""

    async def run() -> None:
        if side.detached:
            ui.run_javascript(f"vrPane.emit({json.dumps(event)})")
        elif inspect.isawaitable(done := act()):
            await done

    return run


def _pane_frame(
    title: str, home: tuple[str, str], side: Side, bookmarked: tuple[str, int], token: str
) -> ui.column:
    """The side column of a PDF window (its ``token``) in a window of its own (e.g. on
    another screen): a header (the actions on the PDF's selection, the way back), the column
    (returned)."""
    ui.page_title(_("{title} · side panel").format(title=title))
    texts = {
        "closed": _("The PDF window is closed"),
        "back": _("The side panel is back in the PDF window: this one can be closed"),
    }
    _scripts({"token": token, "texts": texts}, "pane.js")
    with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
        ui.link(home[0], home[1]).classes("text-white font-bold no-underline ellipsis max-w-48")
        ui.label(title).classes("ellipsis grow min-w-0 font-medium")
        ui.label("").classes("text-sm opacity-80").props("id=vr-pane-status").mark("pane-status")
        livereload.banner(dense=True)
        _selection_actions(side, bookmarked, here=False)
        ui.button(icon="close_fullscreen").props("flat dense round color=white").on(
            "click", js_handler="() => vrPane.back()"
        ).tooltip(_("Back into the PDF window (closes this one)")).mark("pane-back")
    box = (
        ui.column()
        .classes("w-full p-3 gap-2 overflow-auto")
        .style("height:calc(100vh - 40px)")
        .props("id=vr-pdf-side")
        .mark("pdf-side")
    )
    ui.timer(0, lambda: ui.run_javascript("vrPane.start()"), once=True)  # (once connected)
    return box


async def _side(side: Side, box: ui.column, details_box: ui.column, pub_id: int) -> None:
    """The paper's tags and notes (its own, and within a period), and its details (as in
    the publications panel), next to its PDF."""
    from .panel import period_label
    from .pub_details import show_details
    from .tags import paper_tags_and_notes, tags_dialog

    await side.load()
    s = next((x for x in side.stats if x.id == pub_id), None)
    periods = annotations.periods(side.person_id, include_hidden_folders=True)
    state = {"period": next((p for p in periods if p.id == side.host.period_id), None)}
    tags = annotations.all_tags()

    @ui.refreshable
    def body() -> None:
        if s is None:
            ui.label(_("This paper is no longer in the database")).classes("text-grey")
            return
        options = {0: _("All years"), **{p.id: period_label(p) for p in periods}}

        def set_period(e) -> None:
            state["period"] = next((p for p in periods if p.id == e.value), None)
            body.refresh()

        ui.select(
            options, value=state["period"].id if state["period"] else 0, on_change=set_period
        ).props("dense outlined options-dense").classes("w-full").tooltip(
            _("The period whose tags and notes are shown (e.g. a folder's)")
        ).mark("pdf-period")

        def manage() -> None:
            def changed_tags() -> None:
                tags[:] = annotations.all_tags()
                body.refresh()

            with box:
                tags_dialog(changed_tags)

        paper_tags_and_notes(
            s,
            state["period"],
            tags,
            lambda: (
                changed(side.person_id, but=side.host),
                side.folder_refresh and side.folder_refresh(),  # (its citations: numbers…)
            ),
            manage,
            quote_tool=side.quote_tool,
        )

    box.clear()
    with box:
        body()
    if s is None:
        details_box.clear()
        with details_box:
            ui.label(_("This paper is no longer in the database")).classes("text-grey")
    else:  # (tags and notes: in their own tab)
        show_details(side.host, s, details_box, None, notes=False)


async def install_or_notify(label: ui.label) -> None:
    if not pdfs.has_viewer():
        label.text = _("Installing the PDF viewer (PDF.js, once)…")
        await pdfs.install_viewer()


def _prepare(pub_id: int, title: str, fetch: bool, period: int | None) -> None:
    """The viewer page before it can show the PDF: download PDF.js, and the paper's PDF when
    asked to (``fetch``)."""
    with ui.column().classes("p-6 gap-3 w-full max-w-3xl mx-auto") as box:
        ui.label(title).classes("text-lg font-medium")
        status = ui.row().classes("items-center gap-2")
        has_pdf = pdfs.file_of(pub_id) is not None
        if not has_pdf and not fetch:
            with status:
                ui.label(_("No PDF stored for this paper."))
                ui.button(
                    _("Download it"),
                    icon="download",
                    on_click=lambda: ui.navigate.to(page_url(pub_id, period, fetch=True)),
                ).props("flat").mark("pdf-fetch")
            _upload(pub_id, period)
            return
        with status:
            ui.spinner(size="sm")
            label = ui.label("")

    async def prepare() -> None:
        try:
            await _get(pub_id, label)
        except pdfs.PdfError as e:
            status.clear()
            with status:
                ui.icon("error", color="negative")
                ui.label(_("Could not get the PDF: {error}").format(error=e)).classes(
                    "text-negative"
                ).mark("pdf-error")
            with box:
                _upload(pub_id, period)
            return
        ui.navigate.to(page_url(pub_id, period))

    ui.timer(0.1, prepare, once=True)


async def _get(pub_id: int, label: ui.label) -> None:
    try:
        await install_or_notify(label)
        if pdfs.file_of(pub_id) is None:
            label.text = _("Downloading the PDF…")
            await pdfs.download(pub_id)
    finally:
        if found := _title(pub_id):
            changed(found[1])


def _upload(pub_id: int, period: int | None) -> None:
    async def done(e) -> None:
        try:
            pdfs.save(pub_id, await e.file.read(), None)
        except pdfs.PdfError as err:
            ui.notify(str(err), type="warning")
            return
        if found := _title(pub_id):
            changed(found[1])
        ui.navigate.to(page_url(pub_id, period))

    ui.label(_("Or upload it (a PDF file):")).classes("text-sm text-grey")
    ui.upload(on_upload=done, auto_upload=True).props('accept=".pdf,application/pdf"').mark(
        "pdf-upload"
    )


# ---- In the panel ---------------------------------------------------------------------------


def page_url(
    pub_id: int, period: int | None = None, *, fetch: bool = False, page: int | None = None
) -> str:
    """The viewer page of a paper (with the tags and notes of ``period``), at ``page``."""
    params = {
        k: v for k, v in (("fetch", 1 if fetch else None), ("period", period), ("page", page)) if v
    }
    return f"/pdf/{pub_id}" + (f"?{urlencode(params)}" if params else "")


def can_download(s: PubStat) -> bool:
    return bool(s.pdf_urls or s.doi or s.doi_manual)


def pdf_button(panel: PublicationsPanel, s: PubStat) -> None:
    """Next to a paper's title: its stored PDF (opened in a new window), else a button to
    download it (after confirmation) and open it."""
    period = panel.period_id
    if s.pdf:
        edited = s.pdf == "edited"
        with ui.link(target=page_url(s.id, period), new_tab=True).mark(f"view-pdf-{s.id}"):
            ui.icon(
                "edit_document" if edited else "picture_as_pdf", size="xs", color="red-8"
            ).tooltip(_("View the PDF (annotated)") if edited else _("View and annotate the PDF"))
    elif can_download(s):
        icon = (
            ui.icon("picture_as_pdf", size="xs", color="grey-6")
            .classes("vr-src cursor-pointer")
            .tooltip(_("Download the PDF (open access) to view and annotate it"))
            .mark(f"get-pdf-{s.id}")
        )
        if no_confirm():
            icon.on("click", lambda: None, js_handler=_open_js(s.id, period))
        else:
            icon.on("click", lambda: confirm_download(panel, s))


def no_confirm() -> bool:
    global _no_confirm
    if _no_confirm is None:
        _no_confirm = bool(annotations.ui_state(NO_CONFIRM, False))
    return _no_confirm


def _open_js(pub_id: int, period: int | None) -> str:
    # Opened from the click itself (a window opened later would be blocked as a pop-up).
    url = json.dumps(page_url(pub_id, period, fetch=True))
    return f"() => {{ window.open({url}, '_blank'); emit(); }}"


def confirm_download(panel: PublicationsPanel, s: PubStat) -> None:
    with (
        panel.dialogs,
        transient_dialog(_("Download the PDF?"), width="w-full max-w-xl") as (dlg, _card),
    ):
        ui.label(s.title or _("(untitled)")).classes("font-medium")
        with ui.column().classes("gap-0 text-sm"):
            for url in s.pdf_urls:
                ui.link(url, url, new_tab=True).classes("break-all")
            if s.doi or s.doi_manual:
                ui.label(
                    _("Else an open-access copy found by Unpaywall")
                    if s.pdf_urls
                    else _("From an open-access copy found by Unpaywall (by its DOI)")
                ).classes("text-grey")
        ui.label(
            _(
                "It is stored in the data directory, and opens in a new window where you can "
                "highlight and annotate it."
            )
        ).classes("text-sm text-grey")
        again = ui.checkbox(_("Don't ask again")).mark("pdf-no-confirm")

        def go() -> None:
            global _no_confirm
            if again.value:
                annotations.save_ui_state(NO_CONFIRM, True)
                _no_confirm = True
            dlg.close()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Download and open"), icon="download").on(
                "click", go, js_handler=_open_js(s.id, panel.period_id)
            ).mark("pdf-download-ok")


def download_dialog(panel: PublicationsPanel, rows: list[PubStat]) -> None:
    """Download the open-access PDFs of the papers shown (those without one yet)."""
    todo = [s for s in rows if not s.pdf and can_download(s)]
    stored = sum(1 for s in rows if s.pdf)
    with (
        panel.dialogs,
        transient_dialog(_("Download the PDFs"), width="w-full max-w-2xl") as (dlg, _card),
    ):
        ui.label(
            _(
                "{todo} of the {total} papers shown have an open-access link or a DOI and no "
                "stored PDF ({stored} stored already). Their PDFs are downloaded into the data "
                "directory."
            ).format(todo=len(todo), total=len(rows), stored=stored)
        ).classes("text-sm").mark("pdf-batch-info")
        progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress.visible = False
        result = ui.column().classes("w-full gap-1 text-sm")
        titles = {s.id: s.title or _("(untitled)") for s in todo}

        async def run() -> None:
            start.disable()
            progress.visible = True

            def step(done: int, total: int) -> None:
                progress.value = done / total

            batch = await pdfs.download_many([s.id for s in todo], step)
            progress.visible = False
            with result:
                ui.label(
                    ngettext("{n} PDF downloaded", "{n} PDFs downloaded", len(batch.done)).format(
                        n=len(batch.done)
                    )
                ).classes("font-medium").mark("pdf-batch-done")
                if batch.failed:
                    ui.label(
                        ngettext(
                            "Not found for {n} paper:",
                            "Not found for {n} papers:",
                            len(batch.failed),
                        ).format(n=len(batch.failed))
                    ).classes("text-grey")
                    with ui.column().classes("gap-0 max-h-64 overflow-auto"):
                        for pid, why in batch.failed.items():
                            ui.label(f"{titles.get(pid, pid)} — {why}").classes(
                                "text-xs text-grey ellipsis w-full"
                            ).tooltip(why)
            await panel.reload()

        with ui.row().classes("w-full justify-end"):
            ui.button(_("Close"), on_click=dlg.close).props("flat")
            start = ui.button(_("Download"), icon="download", on_click=run).mark("pdf-batch-ok")
            if not todo:
                start.disable()
