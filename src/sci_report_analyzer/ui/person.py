"""Person page: publications panel, sources (auto-match validation), career, periods,
theses."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from html import escape

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import background_tasks, ui

from .. import (
    annotations,
    career,
    career_extract,
    folders,
    llm,
    manual,
    pdfs,
    persons,
    source_settings,
    theses,
    urls,
)
from ..db.models import Person, SourceLink, Thesis
from ..i18n import _, ngettext
from ..sources import ADAPTERS
from ..sources.scholar import parse_profile, profile_id_from_html
from ..sources.thesesfr import ROLE_LABELS
from ..sync import (
    add_link,
    discover,
    finish_link,
    is_syncing,
    normalize_orcid,
    probe_overlap,
    set_link_status,
    set_orcid,
    start_sync,
    suggested_orcid,
    sync_link,
)
from .dialogs import actions, close_then, ok_handler, transient_dialog
from .documents_page import LINES_JS, ReportTabs, page_url_of
from .folder_notes import NOTES_TIP
from .panel import PublicationsPanel, period_label
from .pdf_dock import PdfDock
from .theme import STATUS_COLOUR, fmt_dt, frame, int_or_none, refresh_alive, source_tag

PROFILE_TABS = ("career", "periods", "sources")  # (the Profile tab's, in order)
REPORT_TABS = ("report", "excerpts")  # (filled once shown, see ReportTabs)


def register() -> None:
    @ui.page("/person/{path:path}")
    def person_page(
        request: Request, path: str, tab: str = "publications", period: int | None = None
    ) -> RedirectResponse | None:
        """A person's page, within a folder or not (see urls.person_url; the former numeric
        addresses: redirected)."""
        found = urls.resolve_person(path)
        if found is None:
            with frame(_("Not found")):
                ui.label(_("Unknown person"))
            return None
        person_id, period_id = found
        period_id = period_id or period
        url = urls.person_url(person_id, period_id)
        if url.split("?")[0] != request.url.path:
            query = {k: v for k, v in request.query_params.items() if k != "period"}
            return RedirectResponse(urls.person_url(person_id, period_id, **query))
        _person_page(request, person_id, tab, period_id)
        return None


def _person_page(request: Request, person_id: int, tab: str, period: int | None) -> None:
    person = persons.load(person_id)
    if person is None:
        with frame(_("Not found")):
            ui.label(_("Unknown person"))
        return
    # The folder the person is seen from: that of the period, else the Reports page's one.
    state_period = period or annotations.panel_state(person_id).get("period_id")
    folder = folders.folder_of_period(state_period)
    if folder is None:
        last = annotations.ui_state("ui.people.folder")
        folder = next(
            ((f.id, f.name) for f in folders.folders() if f.id == last),
            None,
        )
        if folder and not any(
            p.folder_id == folder[0]
            for p in annotations.periods(person_id, include_hidden_folders=True)
        ):
            folder = None
    if tab == "documents":  # (its former name)
        tab = "report"
    # The profile's sub-tabs are named in the address (``tab=profile``: its first one).
    profile_tab = tab if tab in PROFILE_TABS else PROFILE_TABS[0]
    if tab in PROFILE_TABS:
        tab = "profile"
    with frame(person.name):
        tabs = _header(person, tab, folder)
        stale_banner(person_id)

        def address() -> None:  # (the tab, the PDFs beside the page)
            shown = profile_tabs.value if tabs.value == "profile" else tabs.value
            panel.url_extra = {
                **({} if shown == "publications" else {"tab": shown}),
                **dock.params(),
            }
            panel.push_url()

        def tab_changed(e) -> None:
            address()
            if e.value in REPORT_TABS:
                report.fill(e.value)
                ui.run_javascript("window.vrFit?.()")  # (resized meanwhile)

        tabs.on_value_change(tab_changed)
        with ui.tab_panels(tabs, value=tab).classes("w-full"):
            with ui.tab_panel("publications"):
                panel = PublicationsPanel(person_id, period, dict(request.query_params))
                panel.build()
            with ui.tab_panel("profile"):
                _person_actions(person)
                with (
                    ui.tabs(value=profile_tab)
                    .props("inline-label dense align=left")
                    .classes("w-full") as profile_tabs
                ):
                    ui.tab("career", _("Career"), icon="work_history").mark("tab-career")
                    ui.tab("periods", _("Periods"), icon="date_range").mark("tab-periods")
                    ui.tab("sources", _("Sources"), icon="travel_explore").mark("tab-sources")

                profile_tabs.on_value_change(address)
                with ui.tab_panels(profile_tabs, value=profile_tab).classes("w-full"):
                    with ui.tab_panel("career").classes("px-0"):
                        career_view(person_id)
                    with ui.tab_panel("periods").classes("px-0").mark("periods"):
                        periods_view(person_id, on_change=panel.reload_periods)
                    with ui.tab_panel("sources").classes("px-0"):
                        panel.names_box = ui.column().classes("w-full").mark("name-variants")
                        panel.render_names()
                        sources_view(person_id, on_change=panel.reload)
            with ui.tab_panel("theses"):
                ThesesView(person_id, panel)
            boxes = {}
            for which in REPORT_TABS:
                with ui.tab_panel(which).classes("px-0 py-1"):
                    boxes[which] = ui.column().classes("w-full").mark(f"{which}-tab")
        dock = PdfDock(person_id, lambda: panel.period_id, dict(request.query_params), address)
        # (the folder's seen from, else the period's)
        shown = next(
            (
                p.id
                for p in annotations.periods(person_id, include_hidden_folders=True)
                if folder and p.folder_id == folder[0]
            ),
            state_period,
        )
        report = ReportTabs(person_id, shown, dock, boxes, tabs.set_value)
        panel.url_extra = {
            **({} if tab == "publications" else {"tab": profile_tab if tab == "profile" else tab}),
            **dock.params(),
        }
        if tab in REPORT_TABS:
            report.fill(tab)


def page_header(person_id: int, period_id: int | None, tab: str) -> None:
    """A person's header (name, tabs…) atop another page (e.g. a PDF's, within ``period_id``),
    ``tab`` selected: the others open their page there."""
    person = persons.load(person_id)
    if person is None:
        return
    tabs = _header(person, tab, folders.folder_of_period(period_id))

    def go(e) -> None:
        tab = None if e.value == "publications" else e.value
        ui.navigate.to(urls.person_url(person_id, period_id, tab=tab))

    tabs.on_value_change(go)


def _header(person: Person, tab: str, folder: tuple[int, str] | None = None) -> ui.tabs:
    """Name, tabs and edit / delete buttons on one row (wrapping on narrow screens)."""
    with ui.row().classes("w-full items-center justify-between gap-2"):
        with ui.column().classes("gap-0"):  # (small: the space below for the content)
            if folder:
                with ui.row().classes("items-center gap-1 no-wrap"):
                    with (
                        ui.link(target=urls.folder_url(folder[0]))
                        .classes("flex items-center gap-1 text-sm no-underline")
                        .mark("folder-title")
                    ):
                        ui.icon("folder", color="amber-8")
                        ui.label(folder[1])
                    if any(
                        p.folder_id == folder[0]
                        for p in annotations.periods(person.id, include_hidden_folders=True)
                    ):
                        ui.button(
                            icon="edit_note", on_click=lambda: tabs.set_value("report")
                        ).props("flat dense round size=sm").tooltip(_(NOTES_TIP)).mark(
                            "person-folder-notes"
                        )
            with ui.row().classes("items-baseline gap-x-2 gap-y-0"):
                ui.label(person.name).classes("text-xl font-medium")
                if now := career.current(person.id):
                    ui.label(now.line()).classes("text-sm text-grey").tooltip(
                        _("Current position (Profile → Career)")
                    ).mark("current-position")
                elif person.affiliation:
                    ui.label(person.affiliation).classes("text-sm text-grey")
        with ui.tabs(value=tab).props("inline-label dense").classes("grow") as tabs:
            ui.tab("publications", _("Publications"), icon="article")
            ui.tab("profile", _("Profile"), icon="manage_accounts").mark("tab-profile")
            ui.tab("theses", _("Theses"), icon="school")
            ui.tab("report", _("Report"), icon="description").mark("tab-report")
            ui.tab("excerpts", _("Excerpts"), icon="format_quote").mark("tab-excerpts")
    return tabs


def _person_actions(person: Person) -> None:
    """The person's details to edit; their data to purge, or the person to delete."""
    with ui.row().classes("w-full items-center gap-2").mark("person-actions"):
        ui.button(_("Edit the person"), icon="edit", on_click=lambda: _edit_dialog(person)).props(
            "outline"
        ).tooltip(_("Name, affiliation, ORCID, name aliases, notes")).mark("edit-person")
        if synced := persons.last_sync(person):
            ui.label(_("last sync {when}").format(when=fmt_dt(synced))).classes(
                "text-sm text-grey"
            ).tooltip(_("Last synchronization of the person's sources")).mark("person-last-sync")
        ui.space()
        ui.button(
            _("Purge the papers"),
            icon="delete_sweep",
            on_click=lambda: purge_dialog([person.id], person.name, ui.navigate.reload),
        ).props("flat color=negative").tooltip(_("Purge: remove all papers and re-sync")).mark(
            "purge-person"
        )
        ui.button(
            _("Delete the person"), icon="delete", on_click=lambda: _delete_dialog(person)
        ).props("flat color=negative").mark("delete-person")


def career_view(person_id: int) -> None:
    """The person's CV: their positions and diplomas, dated, the most recent first."""
    with ui.row().classes("w-full items-center gap-2 mt-2"):
        ui.label(_("Career")).classes("text-lg")
        ui.button(
            _("Add"), icon="add", on_click=lambda: _career_dialog(person_id, None, listing.refresh)
        ).props("flat dense").mark("career-add")
        on = bool(llm.model(task="career"))
        with ui.element("span").tooltip(  # (a disabled button shows no tooltip)
            _(
                "The local LLM reads their documents (the passages about a career) and "
                "profiles, and suggests lines of their CV, to check"
            )
            if on
            else _("Turn on the local LLM and choose a model (Settings → Local LLM)")
        ):
            button = (
                ui.button(
                    _("Read in their documents"),
                    icon="auto_awesome",
                    on_click=lambda: _extract_dialog(person_id, listing.refresh),
                )
                .props("flat dense color=purple")
                .mark("career-extract")
            )
            button.set_enabled(on)

    @ui.refreshable
    def listing() -> None:
        rows = career.entries(person_id)
        found = career.own_theses(person_id)
        if not rows and not found:
            ui.label(
                _(
                    "No position or diploma yet: their CV (e.g. “2019–2023 Postdoc, Inria”, "
                    "“2018 PhD” and its thesis), the current position being that without an "
                    "end; a report shows it with the block [[cv]]."
                )
            ).classes("text-sm text-grey")
        with ui.column().classes("w-full gap-0").mark("career"):
            for e in rows:
                with ui.row().classes("w-full items-start gap-2 no-wrap"):
                    ui.icon(
                        "school" if e.kind == "diploma" else "work",
                        color="primary" if e.current else "grey",
                    ).classes("mt-1")
                    ui.label(e.when()).classes("w-32 shrink-0 text-grey")
                    with ui.column().classes("gap-0 grow"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(e.line()).classes("font-medium" if e.current else "")
                            if e.current:
                                ui.badge(_("current"), color="primary").props("outline")
                            if plus := e.plus():
                                ui.badge(plus, color="blue-grey").props("outline").tooltip(
                                    _("The years since (also in the report)")
                                ).mark(f"career-plus-{e.id}")
                        if e.details:
                            ui.label(e.details).classes(
                                "text-sm text-grey" + (" italic" if e.kind == "diploma" else "")
                            )
                    ui.button(
                        icon="edit",
                        on_click=lambda e=e: _career_dialog(person_id, e, listing.refresh),
                    ).props("flat round dense size=sm").mark(f"career-edit-{e.id}")
        for t in found:  # (their own thesis on theses.fr, to add)
            with ui.row().classes("items-center gap-2 text-sm text-grey"):
                ui.icon("school")
                ui.label(
                    _("Own thesis on theses.fr: {title} ({year})").format(
                        title=t.details or "?", year=t.end or _("in progress")
                    )
                )
                ui.button(
                    _("Add to the career"),
                    on_click=lambda t=t: _career_dialog(person_id, t, listing.refresh),
                ).props("flat dense size=sm").mark("career-add-thesis")

    listing()


def _page_chips(found: list[career_extract.Passage], mark: str) -> None:
    """Pages of documents (each opening it there, in a new tab) or the profiles, as chips."""
    with ui.row().classes("gap-1 items-center").mark(mark):
        for p in found:
            if p.doc_id is None:
                ui.chip(p.source, icon="person").props("dense outline")
                continue
            url = f"{page_url_of(p.doc_id)}?page={p.page}"
            ui.chip(
                p.source, icon="description", on_click=lambda url=url: ui.navigate.to(url, True)
            ).props("dense outline clickable").tooltip(_("Open it at this page"))


_SENT = "text-xs font-mono whitespace-pre-wrap bg-grey-2 rounded p-2 w-full overflow-auto"
# Draws a page of a PDF in a canvas, small (with PDF.js, as the viewer; one at a time; the
# canvas waited for, up to 5 s); hovered, the page larger beside it. sraThumb.hover: an
# element hovered shows a page larger, with rectangles highlighted (e.g. a quote's lines).
_THUMBS_JS = """
window.sraThumb = window.sraThumb || (() => {
  const docs = {}, large = {}, ZOOM = 640;
  let queue = Promise.resolve(), lib, box, shown = 0, watch;
  const open = async (url) => {
    if (!lib) {
      lib = await import('/pdfjs/build/pdf.mjs');
      lib.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.mjs';
    }
    return docs[url] = docs[url] || lib.getDocument({url}).promise;
  };
  const element = async (id) => {  // (a dialog's content: shown after its transition)
    let el;
    for (let i = 0; i < 100 && !(el = document.getElementById(id)); i++)
      await new Promise(resolve => setTimeout(resolve, 50));
    return el;
  };
  const hide = () => {
    shown++;
    clearInterval(watch);
    if (box) box.style.display = 'none';
  };
  const render = async (url, page, width, canvas) => {
    const p = await (await open(url)).getPage(page);
    const scale = width * devicePixelRatio / p.getViewport({scale: 1}).width;
    const viewport = p.getViewport({scale});
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await p.render({canvasContext: canvas.getContext('2d'), viewport}).promise;
    canvas.viewport = viewport;
    return canvas;
  };
  const zoom = async (el, url, page, marks) => {
    const mine = ++shown;
    if (!box) {
      box = document.createElement('canvas');
      box.style.cssText = 'position:fixed;z-index:9999;pointer-events:none;display:none;' +
        'background:white;border-radius:4px;box-shadow:0 4px 24px rgba(0,0,0,.35)';
      document.body.appendChild(box);
    }
    const key = url + '#' + page;
    large[key] = large[key] || render(url, page, ZOOM, document.createElement('canvas'));
    const c = await large[key];
    if (mine !== shown || !el.isConnected) return;
    box.width = c.width;
    box.height = c.height;
    const ctx = box.getContext('2d');
    ctx.drawImage(c, 0, 0);
    ctx.fillStyle = 'rgba(255, 213, 0, 0.35)';
    ctx.strokeStyle = 'rgba(230, 120, 0, 0.9)';
    ctx.lineWidth = 2 * devicePixelRatio;
    for (const r of marks || []) {
      const [x0, y0] = c.viewport.convertToViewportPoint(r[0], r[1]);
      const [x1, y1] = c.viewport.convertToViewportPoint(r[2], r[3]);
      const x = Math.min(x0, x1) - 2, y = Math.min(y0, y1) - 2;
      ctx.fillRect(x, y, Math.abs(x1 - x0) + 4, Math.abs(y1 - y0) + 4);
      ctx.strokeRect(x, y, Math.abs(x1 - x0) + 4, Math.abs(y1 - y0) + 4);
    }
    // (beside it, on the side with more room; over the page if too little)
    const r = el.getBoundingClientRect(), ratio = c.height / c.width;
    const room = Math.max(r.left, innerWidth - r.right) - 20;
    const w = Math.min(ZOOM, (innerHeight - 16) / ratio, Math.max(room, 360)), h = w * ratio;
    const left = innerWidth - r.right >= r.left
      ? Math.min(r.right + 12, innerWidth - w - 8) : Math.max(8, r.left - w - 12);
    const top = Math.max(8, Math.min(r.top + r.height / 2 - h / 2, innerHeight - h - 8));
    Object.assign(box.style, {
      width: w + 'px', height: h + 'px', left: left + 'px', top: top + 'px', display: 'block',
    });
    clearInterval(watch);  // (the dialog closed, the page changed: no mouseleave)
    watch = setInterval(() => el.isConnected || hide(), 300);
  };
  const hovered = (el, url, page, marks) => {
    el.addEventListener('mouseenter', () => zoom(el, url, page, marks).catch(e => {
      console.warn('zoom', e);
      hide();
    }));
    el.addEventListener('mouseleave', hide);
  };
  const thumb = (id, url, page) => {
    queue = queue.then(async () => {
      const canvas = await element(id);
      if (!canvas) return;
      await render(url, page, canvas.clientWidth || 184, canvas);
      canvas.style.aspectRatio = 'auto';
      hovered(canvas, url, page, []);
    }).catch(e => console.warn('thumbnail', e));
  };
  thumb.hover = async (id, url, page, marks) => {
    const el = await element(id);
    if (el) hovered(el, url, page, marks);
  };
  return thumb;
})();
"""
_AROUND_QUOTE = 160  # (characters shown around the words a line comes from)


def _source_view(sug: career_extract.Suggestion, mark: str) -> None:
    """Where a suggested line comes from: its page(s), and its words in the text sent (with
    those around them)."""
    with ui.column().classes("gap-1 w-full pl-2 border-l-4 border-purple-200"):
        with ui.row().classes("items-center gap-1 text-xs text-grey"):
            if not sug.checked:
                ui.icon("warning", color="warning", size="xs").tooltip(
                    _("Its words are not in the documents: to check")
                )
            _page_chips(sug.pages, mark)
        span = career_extract.quoted(sug.pages[0].text, sug.quote) if sug.checked else None
        if span:
            text, (a, b) = sug.pages[0].text, span
            before, after = text[max(0, a - _AROUND_QUOTE) : a], text[b : b + _AROUND_QUOTE]
            ui.html(
                ("…" if a > _AROUND_QUOTE else "")
                + escape(before)
                + f"<mark>{escape(text[a:b])}</mark>"
                + escape(after)
                + ("…" if b + _AROUND_QUOTE < len(text) else ""),
                sanitize=False,
            ).classes("text-xs text-grey-8 whitespace-pre-wrap").mark(f"{mark}-text")
        elif sug.quote:
            ui.label(f"“{sug.quote}”").classes("text-xs italic text-grey-8").mark(f"{mark}-text")


def _extract_dialog(
    person_id: int,
    on_change: Callable[[], None],
    selection: career_extract.Page | None = None,
) -> None:
    """The lines of the CV the LLM reads in the person's documents (see career_extract), in
    steps: the documents and their pages (those likely about a career ticked; read in parts,
    see career_extract.batches); the instructions (to change, and save); the parts read, each
    shown; the lines found, each with the words it comes from in its page: the ticked ones
    added, or one edited then added. ``selection``: only that text read (selected in a
    document's viewer), at once (the instructions shown going back)."""
    with transient_dialog(_("Career read by the LLM"), width="w-full max-w-3xl") as (
        dlg,
        _card,
    ):
        dlg.props("persistent")  # (a click outside, Escape: not closed, the work kept)
        with ui.row().classes("items-center gap-1 -mt-2 text-xs text-grey"):
            ui.icon("lock", size="xs")
            ui.label(
                _(
                    "Model: {model} (local: nothing leaves this machine or the local network)"
                ).format(model=llm.model(task="career"))
            ).mark("career-extract-model")
        steps = ui.label().classes("text-xs text-grey-8 font-medium").mark("career-extract-step")
        # (scrolled: the buttons always seen)
        body = (
            ui.column()
            .classes("w-full gap-1 max-h-[65vh] overflow-auto no-wrap")
            .mark("career-extract-body")
        )
        footer = ui.row().classes("w-full items-center")
    ticks: dict[int, ui.checkbox] = {}
    found: list[career_extract.Suggestion] = []
    docs = career_extract.person_documents(person_id)
    known = career_extract.affiliations(person_id)
    # What is chosen (kept going back): the documents, their profiles' affiliations, the
    # pages (their keys), the instructions.
    chosen_docs = {d.id for d in docs if d.read}
    keys = career_extract.ticked(career_extract.pages(person_id, list(chosen_docs)))
    # (all the pages shown, else those ticked)
    state = {
        "profiles": bool(known) and selection is None,
        "instructions": career_extract.prompt(),
        "all": False,
        "step": 0,
        "stopped": False,
    }
    names = [
        _("1. Selection") if selection else _("1. Pages"),
        _("2. Instructions"),
        _("3. Reading"),
        _("4. Lines found"),
    ]

    def step(i: int) -> None:
        state["step"] = i
        steps.set_text(" › ".join(f"[{n}]" if j == i else n for j, n in enumerate(names)))

    def chosen_pages() -> list[career_extract.Page]:
        if selection:
            return [selection]
        return [p for p in career_extract.pages(person_id, list(chosen_docs)) if p.key in keys]

    def add_ticked() -> None:
        n = 0
        for i, box in ticks.items():
            if box.value:
                career.save(person_id, found[i].entry)
                n += 1
        dlg.close()
        on_change()
        ui.notify(
            ngettext("{n} line added to the career", "{n} lines added to the career", n).format(n=n)
        )

    added: set[int] = set()  # (the lines found added, edited first)

    def edit(i: int) -> None:
        """A line found edited, then added (on top: the others kept)."""

        def saved() -> None:
            added.add(i)
            on_change()
            results(found, state["stopped"])

        with dlg.opener:  # type: ignore[attr-defined]
            _career_dialog(person_id, found[i].entry, saved)

    @ui.refreshable
    def summary() -> None:
        chosen = chosen_pages()
        parts = career_extract.batches([p.passage() for p in chosen])
        ui.label(
            ngettext("{n} page ticked", "{n} pages ticked", len(chosen)).format(n=len(chosen))
            + " · "
            + _("{n} characters").format(n=sum(len(p.text) for p in chosen))
            + " · "
            + ngettext("read in {n} part", "read in {n} parts", len(parts)).format(n=len(parts))
        ).classes("text-sm text-grey-8").mark("career-extract-summary")

    def tick(key: tuple, on: bool) -> None:
        if on:
            keys.add(key)
        else:
            keys.discard(key)
        summary.refresh()

    def page_card(p: career_extract.Page, thumbs: list[str]) -> None:
        """A page: its thumbnail (clicked: ticked or not), whether likely about a career."""
        mark = f"career-extract-page-{p.doc_id}-{p.page}"

        def ticked(on: bool) -> None:
            card.classes(add="bg-purple-1" if on else "", remove="" if on else "bg-purple-1")
            tick(p.key, on)

        with ui.column().classes("gap-0 items-center w-48 rounded p-1") as card:
            if pdfs.has_viewer():
                canvas = (
                    ui.element("canvas")
                    .classes("w-full bg-white shadow-1 cursor-pointer")
                    .style("aspect-ratio: 210 / 297")
                )
                canvas.on("click", lambda: box.set_value(not box.value))
                thumbs.append(f"sraThumb('c{canvas.id}', '/doc-file/{p.doc_id}', {p.page});")
            with ui.row().classes("items-center gap-0 no-wrap"):
                box = (
                    ui.checkbox(
                        _("p. {page}").format(page=p.page),
                        value=p.key in keys,
                        on_change=lambda e: ticked(e.value),
                    )
                    .props("dense")
                    .classes("text-xs")
                    .mark(mark)
                )
                with (
                    ui.button(icon="notes")
                    .props("flat round dense size=xs")
                    .tooltip(_("What is sent of it"))
                    .mark(f"{mark}-show"),
                    ui.menu(),
                ):
                    ui.label(p.text).classes(_SENT + " max-h-64 max-w-md").mark(f"{mark}-text")
                url = f"{page_url_of(p.doc_id)}?page={p.page}"
                ui.button(icon="open_in_new", on_click=lambda: ui.navigate.to(url, True)).props(
                    "flat round dense size=xs"
                ).tooltip(_("Open it at this page"))
            if p.reason == "heading":
                ui.badge(_("CV"), color="purple").props("outline").tooltip(
                    _("Headed as a CV (among its first lines): sent whole")
                )
            elif p.reason == "lines":
                ui.badge(
                    ngettext("{n} line", "{n} lines", p.score).format(n=p.score),
                    color="blue-grey",
                ).props("outline").tooltip(
                    _(
                        "Its lines with a year and a position or diploma: sent, with the "
                        "lines around them"
                    )
                )
        ticked(box.value)

    @ui.refreshable
    def page_list() -> None:
        found_ = career_extract.pages(person_id, list(chosen_docs))
        if not found_:
            ui.label(_("No page (no document ticked, or read)")).classes("text-sm text-grey")
        thumbs: list[str] = []
        for d in docs:
            mine = [p for p in found_ if p.doc_id == d.id]
            if not mine:
                continue
            ui.label(d.name).classes("text-sm font-medium mt-1")
            if not any(p.reason for p in mine):
                ui.label(_("No page about a career found")).classes("text-xs text-grey")
            if not state["all"]:
                mine = [p for p in mine if p.key in keys]
                if not mine:
                    ui.label(_("No page ticked")).classes("text-xs text-grey")
            with ui.row().classes("w-full gap-1").mark(f"career-extract-pages-{d.id}"):
                for p in mine:
                    page_card(p, thumbs)
        if thumbs:
            ui.run_javascript(_THUMBS_JS + "".join(thumbs))

    def choose_doc(d: career_extract.Document, on: bool) -> None:
        mine = career_extract.pages(person_id, [d.id])
        if on:
            chosen_docs.add(d.id)
            keys.update(career_extract.ticked(mine))
        else:
            chosen_docs.discard(d.id)
            keys.difference_update(p.key for p in mine)
        page_list.refresh()
        summary.refresh()

    def choose() -> None:
        """Step 1: the documents (those not read yet, to open first) and their pages."""
        step(0)
        body.clear()
        footer.clear()
        with body:
            ui.label(
                _(
                    "The pages to read: those likely about a career are ticked (a page headed "
                    "as a CV whole; else its lines with a year and a position or diploma). "
                    "The pages following each other are read together, the others apart."
                )
            ).classes("text-sm text-grey-8")
            with ui.row().classes("w-full items-center gap-1 no-wrap"):
                ui.checkbox(
                    ngettext(
                        "{n} affiliation from their profiles (HAL, OpenAlex…)",
                        "{n} affiliations from their profiles (HAL, OpenAlex…)",
                        len(known),
                    ).format(n=len(known)),
                    value=state["profiles"],
                    on_change=lambda e: state.update(profiles=e.value),
                ).props("" if known else "disable").tooltip(
                    _(
                        "The affiliations of their validated profiles, as each source gives "
                        "it (OpenAlex, HAL, Google Scholar…), each once with its sources"
                    )
                ).mark("career-extract-profiles")
                if known:
                    with ui.expansion(_("show")).props("dense").classes("text-xs grow"):
                        for a in known:
                            ui.label(a).classes("text-xs text-grey-8")
            if not docs:
                ui.label(_("No document")).classes("text-sm text-grey").mark(
                    "career-extract-no-document"
                )
            doc_list()
            ui.separator()
            with ui.row().classes("w-full items-center"):
                summary()
                ui.space()
                ui.switch(
                    _("All the pages"),
                    value=state["all"],
                    on_change=lambda e: (state.update(all=e.value), page_list.refresh()),
                ).props("dense").tooltip(
                    _("Show all the pages, to tick others (else only those ticked)")
                ).mark("career-extract-all-pages")
            with ui.column().classes("w-full gap-0"):
                page_list()
        with footer:
            if any(not d.read for d in docs):
                ui.button(_("Refresh"), icon="refresh", on_click=refresh_docs).props("flat").mark(
                    "career-extract-refresh"
                )
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Next"), icon="arrow_forward", on_click=review).mark("career-extract-next")

    reading: dict[int, str] = {}  # (the documents whose text is extracted: none, or why not)

    @ui.refreshable
    def doc_list() -> None:
        for d in docs:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.checkbox(
                    d.name,
                    value=d.id in chosen_docs,
                    on_change=lambda e, d=d: choose_doc(d, e.value),
                ).props("" if d.read else "disable").mark(f"career-extract-doc-{d.id}")
                if d.period:
                    ui.label(d.period).classes("text-xs text-grey")
                if d.read:
                    continue
                if reading.get(d.id) == "":
                    ui.spinner(size="xs")
                    ui.label(_("reading its text…")).classes("text-xs text-grey").mark(
                        f"career-extract-reading-{d.id}"
                    )
                    continue
                ui.label(
                    _("its text could not be read: {error}").format(error=reading[d.id])
                    if reading.get(d.id)
                    else _("not read yet: open it once in the viewer")
                ).classes("text-xs text-warning")
                ui.button(
                    icon="open_in_new",
                    on_click=lambda d=d: ui.navigate.to(page_url_of(d.id), new_tab=True),
                ).props("flat round dense size=sm").tooltip(
                    _("Open it (its text is extracted there), then Refresh")
                ).mark(f"career-extract-open-{d.id}")

    async def read_texts() -> None:
        """The text of the documents not read yet extracted (in the browser, with PDF.js, as
        in the viewer), one after the other; each then ticked, with its likely pages."""
        for d in docs:
            if d.read or d.id in reading:
                continue
            reading[d.id] = ""
            doc_list.refresh()
            try:
                error = await ui.run_javascript(LINES_JS + f"sraReadText({d.id})", timeout=300)
            except Exception as e:  # (the dialog closed, the browser too slow…)
                error = str(e) or type(e).__name__
            if error:
                reading[d.id] = error
            else:
                reading.pop(d.id)
                d.read = True
                choose_doc(d, True)
            doc_list.refresh()
        if state["step"] == 0 and all(d.read for d in docs):
            choose()  # (no Refresh any more)

    def refresh_docs() -> None:
        docs[:] = career_extract.person_documents(person_id)
        for d in docs:  # (those read since: ticked, their likely pages too)
            if d.read and d.id not in chosen_docs:
                choose_doc(d, True)
        choose()

    @ui.refreshable
    def parts_view() -> None:
        parts = career_extract.requests(
            person_id, chosen_pages(), state["profiles"], state["instructions"]
        )
        if not parts:
            ui.label(_("Nothing to read")).classes("text-sm text-grey").mark(
                "career-extract-nothing"
            )
        for i, (part, chat) in enumerate(parts, 1):
            with ui.row().classes("items-center gap-2 mt-1"):
                ui.label(
                    _("Part {i} of {n}").format(i=i, n=len(parts))
                    + f" · {sum(len(m['content']) for m in chat)} "
                    + _("characters")
                ).classes("text-xs text-grey-8 font-medium")
                _page_chips(part, f"career-extract-part-pages-{i}")
            ui.label(chat[1]["content"]).classes(_SENT + " max-h-48").mark(
                f"career-extract-sent-{i}-user"
            )

    def review() -> None:
        """Step 2: the instructions (to change for this time, or save), what is sent."""
        step(1)
        body.clear()
        footer.clear()
        with body:
            ui.label(_("The instructions ({name}: their name), sent with each part:")).classes(
                "text-sm text-grey-8"
            )
            area = (
                ui.textarea(value=state["instructions"])
                .props("outlined autogrow input-class=text-xs")
                .classes("w-full font-mono")
                .mark("career-extract-instructions")
            )

            def changed(e) -> None:
                state["instructions"] = e.value
                parts_view.refresh()

            area.on_value_change(changed)

            def save() -> None:
                career_extract.save_prompt(area.value or "")
                ui.notify(_("Saved as the default instructions"), type="positive")

            with ui.row().classes("gap-2"):
                ui.button(
                    _("Default instructions"),
                    icon="restart_alt",
                    on_click=lambda: area.set_value(career_extract.PROMPT),
                ).props("flat dense no-caps").mark("career-extract-prompt-default")
                ui.button(_("Save as the default"), icon="save", on_click=save).props(
                    "flat dense no-caps"
                ).tooltip(_("Also in Settings → Reading a career")).mark(
                    "career-extract-prompt-save"
                )
            with (
                ui.expansion(_("The answer's format"), icon="data_object")
                .classes("w-full")
                .props("dense")
            ):
                ui.label(
                    _(
                        "The answer is constrained to this JSON schema (Ollama's structured "
                        "outputs): as the career's lines, a position or a diploma and its thesis."
                    )
                ).classes("text-xs text-grey")
                ui.label(json.dumps(career_extract.SCHEMA, indent=1)).classes(_SENT + " max-h-64")
            parts_view()
        with footer:
            if selection is None:
                ui.button(_("Back"), icon="arrow_back", on_click=choose).props("flat").mark(
                    "career-extract-back-pages"
                )
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(
                _("Read"),
                icon="auto_awesome",
                on_click=lambda: run(chosen_pages(), state["profiles"], state["instructions"]),
            ).mark("career-extract-read")

    async def run(chosen: list[career_extract.Page], profiles: bool, instructions: str) -> None:
        """Step 3: the parts read, the one read now shown."""
        step(2)
        body.clear()
        footer.clear()
        got: list[career_extract.Suggestion] = []
        parts = career_extract.requests(person_id, chosen, profiles, instructions)
        state_ = {"part": 0, "parts": 0, "phase": "reading", "chars": 0, "start": time.monotonic()}
        phases = {
            "reading": _("the model reads the text"),
            "thinking": _("the model thinks ({n} characters)"),
            "writing": _("the model writes its answer ({n} characters)"),
        }

        @ui.refreshable
        def current() -> None:
            """The part read now: its pages, and what is sent of them."""
            i = state_["part"]
            if i >= len(parts):
                return
            part = parts[i][0]
            _page_chips(part, "career-extract-current-pages")
            ui.label("\n\n".join(f"[{p.source}]\n{p.text}" for p in part)).classes(
                _SENT + " max-h-64"
            ).mark("career-extract-current")

        with body:
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.spinner()
                status = (
                    ui.label(_("Reading their documents…"))
                    .classes("text-grey-8")
                    .mark("career-extract-status")
                )
            bar = ui.linear_progress(value=0, show_value=False).props("rounded color=purple")
            detail = ui.label().classes("text-xs text-grey").mark("career-extract-detail")
            icons = []  # (each part's: waiting, read now, done)
            for i, (part, _chat) in enumerate(parts):
                with ui.row().classes("items-center gap-1 no-wrap w-full"):
                    icons.append(
                        ui.icon("schedule", size="xs", color="grey").mark(
                            f"career-extract-part-{i}"
                        )
                    )
                    _page_chips(part, f"career-extract-part-pages-{i}")
            ui.label(_("Read now:")).classes("text-xs text-grey-8 font-medium mt-1")
            current()

        def show() -> None:
            n, total = state_["part"], state_["parts"]
            if total:
                status.set_text(
                    _("Part {i} of {n}: {phase}").format(
                        i=min(n + 1, total),
                        n=total,
                        phase=phases[state_["phase"]].format(n=state_["chars"]),
                    )
                )
                bar.set_value(n / total)
            elapsed = int(time.monotonic() - state_["start"])
            detail.set_text(
                ngettext(
                    "{s} s · {n} line found so far",
                    "{s} s · {n} lines found so far",
                    len(got),
                ).format(s=elapsed, n=len(got))
            )

        def progress(done: int, total: int) -> None:
            state_.update(part=done, parts=total, phase="reading", chars=0)
            for i, icon in enumerate(icons):
                icon.set_name(
                    "check_circle" if i < done else "pending" if i == done else "schedule"
                )
                icon.props(f"color={'green' if i < done else 'purple' if i == done else 'grey'}")
            current.refresh()
            show()

        def phase(name: str, chars: int) -> None:
            state_.update(phase=name, chars=chars)

        ticker = ui.timer(0.5, show)

        task = background_tasks.create(
            career_extract.extract(
                person_id,
                chosen,
                out=got,
                profiles=profiles,
                instructions=instructions,
                progress=progress,
                phase=phase,
            ),
            name="career-extract",
        )
        stopped = False

        def stop() -> None:
            nonlocal stopped
            stopped = True
            task.cancel()

        with footer:
            ui.space()
            ui.button(_("Stop"), icon="stop", on_click=stop).props("flat color=negative").tooltip(
                _("Stop reading (the lines found so far are kept)")
            ).mark("career-extract-stop")
        dlg.on("hide", lambda: task.cancel() if not task.done() else None)
        try:
            await task
        except asyncio.CancelledError:
            if not stopped:  # (the dialog closed)
                return
        except Exception as e:
            body.clear()
            footer.clear()
            with body:
                ui.label(_("Cannot read the career: {error}").format(error=e)).classes(
                    "text-negative"
                )
            with footer:
                ui.button(_("Back"), icon="arrow_back", on_click=review).props("flat")
            return
        finally:
            ticker.cancel()
        results(got, stopped)

    def results(got: list[career_extract.Suggestion], stopped: bool) -> None:
        """Step 4: the lines found, as the career's, each with its source."""
        step(3)
        footer.clear()
        if got is not found:
            added.clear()
        found[:] = got
        state["stopped"] = stopped
        ticks.clear()
        body.clear()
        with body:
            if stopped:
                ui.label(_("Stopped: the lines found so far")).classes("text-sm text-warning").mark(
                    "career-extract-stopped"
                )
            if not got:
                ui.label(_("Nothing new found")).classes("text-grey").mark("career-extract-none")
            hovers: list[str] = []
            for i, sug in enumerate(got):
                e = sug.entry
                with ui.row().classes(
                    "w-full items-start gap-2 no-wrap rounded hover:bg-purple-1"
                ) as row:
                    if i in added:
                        ui.icon("check_circle", color="positive").classes("mt-2 mx-2").tooltip(
                            _("Added to the career")
                        ).mark(f"career-extract-added-{i}")
                    else:
                        ticks[i] = ui.checkbox(value=sug.checked).mark(f"career-extract-tick-{i}")
                    ui.icon("school" if e.kind == "diploma" else "work", color="grey").classes(
                        "mt-2"
                    )
                    ui.label(e.when()).classes("w-28 shrink-0 text-grey mt-2")
                    with ui.column().classes("gap-1 grow min-w-0 mt-1"):
                        ui.label(e.line()).classes("font-medium").mark(f"career-extract-line-{i}")
                        if e.details:
                            ui.label(
                                _("Thesis: {title}").format(title=e.details)
                                if e.kind == "diploma"
                                else e.details
                            ).classes(
                                "text-sm text-grey" + (" italic" if e.kind == "diploma" else "")
                            ).mark(f"career-extract-details-{i}")
                        _source_view(sug, f"career-extract-source-{i}")
                    if i not in added:
                        ui.button(icon="edit", on_click=lambda i=i: edit(i)).props(
                            "flat round dense size=sm"
                        ).tooltip(_("Edit it, then add it")).mark(f"career-extract-edit-{i}")
                where = sug.pages[0] if sug.pages else None
                if where and where.doc_id is not None and pdfs.has_viewer():
                    # (hovered: its page, the words it comes from highlighted)
                    marks = career_extract.quote_rects(where.doc_id, where.page, sug.quote)
                    hovers.append(
                        f"sraThumb.hover('c{row.id}', '/doc-file/{where.doc_id}', "
                        f"{where.page}, {json.dumps(marks if sug.checked else [])});"
                    )
            if hovers:
                ui.run_javascript(_THUMBS_JS + "".join(hovers))
        with footer:
            ui.button(_("Back"), icon="arrow_back", on_click=review).props("flat").mark(
                "career-extract-back"
            )
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            if got:
                ui.button(_("Add the ticked ones"), on_click=add_ticked).mark("career-extract-add")

    if selection:
        with dlg:  # (read at once)
            ui.timer(
                0.05,
                lambda: run([selection], state["profiles"], state["instructions"]),
                once=True,
            )
        return
    choose()
    if pdfs.has_viewer() and any(not d.read for d in docs):
        with dlg:
            ui.timer(0.1, read_texts, once=True)


def _career_dialog(person_id: int, entry: career.Entry | None, on_change) -> None:
    """A line of the CV to add (``entry`` without id: a suggested one) or edit."""
    e = entry or career.Entry("position", "")
    with transient_dialog(
        _("Edit the line") if e.id else _("Add to the career"), width="w-full max-w-lg"
    ) as (dlg, _card):
        kind = ui.toggle({k: _(v) for k, v in career.KINDS.items()}, value=e.kind).mark(
            "career-kind"
        )
        title = (
            ui.input(_("Title"), value=e.title)
            .classes("w-full")
            .mark("career-title")
            .bind_label_from(
                kind,
                "value",
                lambda k: (
                    _("Diploma (e.g. PhD in computer science, HDR)")
                    if k == "diploma"
                    else _("Position (e.g. Associate professor)")
                ),
            )
        )
        institution = (
            ui.input(_("Institution"), value=e.institution or "")
            .classes("w-full")
            .mark("career-institution")
        )
        with ui.row().classes("w-full gap-2 no-wrap"):
            start = (
                ui.input(_("From"), value=e.start or "", placeholder="2019, 2019-09")
                .classes("grow")
                .mark("career-start")
            )
            end = (
                ui.input(_("To"), value=e.end or "", placeholder="2023-08")
                .classes("grow")
                .mark("career-end")
                .tooltip(_("A position without an end: the current one; a diploma: when obtained"))
            )
        details = (
            ui.textarea(_("Details"), value=e.details or "")
            .props("autogrow")
            .classes("w-full")
            .mark("career-details")
            .bind_label_from(
                kind,
                "value",
                lambda k: (
                    _("Thesis (its title, supervisors…)")
                    if k == "diploma"
                    else _("Details (team, subject…)")
                ),
            )
        )

        elapsed = (
            ui.checkbox(_("Show the years since (“+12”)"), value=e.elapsed)
            .tooltip(
                _(
                    "After its dates, here and in the report: the years since the diploma was "
                    "obtained, the position started (e.g. the thesis, a grade)"
                )
            )
            .mark("career-elapsed")
        )

        before = career.current(person_id)

        def changed() -> None:
            on_change()
            if career.current(person_id) != before:
                ui.navigate.reload()  # (the current position, in the header)

        def save() -> bool | None:
            if not (title.value or "").strip():
                ui.notify(_("A title is needed"), type="warning")
                return False
            if not (career.valid_date(start.value) and career.valid_date(end.value)):
                ui.notify(_("Dates as 2019, 2019-09 or 2019-09-01"), type="warning")
                return False
            career.save(
                person_id,
                career.Entry(
                    kind.value,
                    title.value,
                    start.value,
                    end.value,
                    institution.value,
                    details.value,
                    e.thesis_id,
                    id=e.id,
                    elapsed=bool(elapsed.value),
                ),
            )
            changed()

        def remove() -> None:
            career.delete(e.id)
            dlg.close()
            changed()

        with ui.row().classes("w-full items-center"):
            if e.id:
                ui.button(_("Delete"), on_click=remove).props("flat color=negative").mark(
                    "career-delete"
                )
            ui.space()
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(_("Save"), on_click=ok_handler(dlg, save)).mark("career-save")


def _edit_dialog(person: Person) -> None:
    with transient_dialog(width="w-96") as (edit, _card):
        name = ui.input(_("Name"), value=person.name).classes("w-full")
        aff = ui.input(_("Affiliation"), value=person.affiliation or "").classes("w-full")
        orcid = (
            ui.input("ORCID", value=person.orcid or "", placeholder="0000-0002-1825-0097")
            .classes("w-full")
            .tooltip(_("Helps matching: profiles with this ORCID are shown in green"))
            .mark("person-orcid-input")
        )
        aliases = (
            ui.textarea(_("Name aliases (one per line)"), value="\n".join(person.aliases or []))
            .classes("w-full")
            .tooltip(_("Other spellings used in author lists, e.g. “B. Doe”"))
        )
        notes = ui.textarea(_("Notes"), value=person.notes or "").classes("w-full")

        def save() -> bool | None:
            value = normalize_orcid(orcid.value)
            if orcid.value.strip() and value is None:
                ui.notify(_("Not an ORCID (e.g. 0000-0002-1825-0097)"), type="warning")
                return False
            persons.update(
                person.id,
                name=name.value,
                affiliation=aff.value,
                aliases=aliases.value.splitlines(),
                notes=notes.value,
            )
            set_orcid(person.id, value)  # and rescore the candidates
            ui.navigate.reload()

        actions(edit, _("Save"), save)


def _delete_dialog(person: Person) -> None:
    def delete() -> None:
        folders.delete_people([person.id])
        ui.navigate.to("/")

    deletion_dialog(
        _("Delete {name}?").format(name=person.name),
        folders.deletion_counts(person_ids=[person.id]),
        delete,
        word=person.name,
    )


def deletion_dialog(
    title: str,
    n: dict[str, int],
    on_ok,
    *,
    word: str | None = None,
    ok_label: str | None = None,
    mark: str = "confirm-delete",
) -> None:
    """Big confirmation of a deletion: what it loses (``folders.deletion_counts``, the
    user's own work in red); when some is, ``word`` typed to confirm (if given)."""
    with transient_dialog(width="w-full max-w-xl") as (dlg, _card):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("warning", size="lg", color="negative")
            ui.label(title).classes("text-xl font-medium")
        with ui.column().classes("gap-1 bg-red-1 rounded p-3 w-full").mark("deletion-losses"):
            ui.label(_("This deletes:")).classes("font-medium")
            for line, precious in _losses(n):
                ui.label(line).classes("text-negative font-medium" if precious else "")
            ui.label(_("This cannot be undone.")).classes("text-negative font-medium")
        typed = None
        if word and n["precious"]:
            typed = (
                ui.input(_("Type “{word}” to confirm").format(word=word))
                .props("dense outlined")
                .classes("w-full")
                .mark("deletion-word")
            )

        def ok() -> bool | None:
            if typed is not None and (typed.value or "").strip() != word:
                ui.notify(_("Type “{word}” to confirm").format(word=word), type="warning")
                return False
            on_ok()

        actions(dlg, ok_label or _("Delete"), ok, danger=True, icon="delete_forever", mark=mark)


def _losses(n: dict[str, int]) -> list[tuple[str, bool]]:
    """The lines of what a deletion loses (those with something), and whether each is the
    user's own work."""
    lines = []
    if n.get("people"):
        lines.append(
            (
                ngettext(
                    "• {n} person, with their sources, career and theses",
                    "• {n} people, with their sources, careers and theses",
                    n["people"],
                ).format(n=n["people"]),
                False,
            )
        )
    if n.get("papers"):
        lines.append(
            (
                ngettext("• {n} paper", "• {n} papers", n["papers"]).format(n=n["papers"]),
                False,
            )
        )
    if n.get("pdfs"):
        lines.append(
            (
                ngettext(
                    "• {n} stored PDF ({annotated} annotated)",
                    "• {n} stored PDFs ({annotated} annotated)",
                    n["pdfs"],
                ).format(n=n["pdfs"], annotated=n["annotated_pdfs"]),
                bool(n["annotated_pdfs"]),
            )
        )
    if n["periods"]:
        lines.append(
            (
                ngettext(
                    "• {n} period (in a folder or their own), with its tags and stars",
                    "• {n} periods (in folders or their own), with their tags and stars",
                    n["periods"],
                ).format(n=n["periods"]),
                False,
            )
        )
    for key, text in (
        (
            "documents",
            ngettext(
                "• {n} document (application, CV…), with its annotations",
                "• {n} documents (applications, CVs…), with their annotations",
                n["documents"],
            ),
        ),
        (
            "notes",
            ngettext(
                "• {n} note (on papers, in folders, on documents)",
                "• {n} notes (on papers, in folders, on documents)",
                n["notes"],
            ),
        ),
        ("excerpts", ngettext("• {n} excerpt", "• {n} excerpts", n["excerpts"])),
        (
            "report_texts",
            ngettext("• {n} report section text", "• {n} report section texts", n["report_texts"]),
        ),
    ):
        if n[key]:
            lines.append((text.format(n=n[key]), True))
    return lines


def purge_dialog(person_ids: list[int], who: str, after=None) -> None:
    """Big confirmation, then remove all papers of these people and re-sync them."""
    from ..sync import purge, purge_counts

    n = purge_counts(person_ids)
    with transient_dialog(width="w-full max-w-xl") as (dlg, _card):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("warning", size="lg", color="negative")
            ui.label(_("Purge the papers of {who}?").format(who=who)).classes("text-xl font-medium")
        with ui.column().classes("gap-1 bg-red-1 rounded p-3 w-full"):
            ui.label(
                ngettext(
                    "This removes, for {n} person:",
                    "This removes, for {n} people:",
                    n["people"],
                ).format(n=n["people"])
            ).classes("font-medium")
            ui.label(
                _("• {papers} paper(s) and {records} source record(s)").format(
                    papers=n["papers"], records=n["records"]
                )
            )
            ui.label(
                _(
                    "• everything set on these papers: {n} paper(s) have tags, "
                    "stars, a venue / rank / kind / track set by hand, corrections, a note, are "
                    "hidden "
                    "or were merged / split by hand"
                ).format(n=n["annotated"])
            ).classes("text-negative" if n["annotated"] else "")
            ui.label(
                _(
                    "Then all their validated sources are synced again from scratch. Sources, "
                    "periods, name aliases, PhD students and venue decisions are kept."
                )
            ).classes("text-sm")
            ui.label(_("This cannot be undone.")).classes("text-negative font-medium")
        word = (
            ui.input(_("Type PURGE to confirm"))
            .props("dense outlined")
            .classes("w-full")
            .mark("purge-word")
        )

        def ok() -> bool | None:
            if (word.value or "").strip().upper() != "PURGE":
                ui.notify(_("Type PURGE to confirm"), type="warning")
                return False
            purge(person_ids)
            for pid in person_ids:
                start_sync(pid)
            ui.notify(
                ngettext(
                    "Papers purged; re-syncing {n} person",
                    "Papers purged; re-syncing {n} people",
                    n["people"],
                ).format(n=n["people"]),
                type="positive",
            )
            if after:
                after()

        actions(
            dlg,
            _("Purge and re-sync"),
            ok,
            danger=True,
            icon="delete_forever",
            mark="purge-confirm",
        )


@ui.refreshable
def stale_banner(person_id: int) -> None:
    person = persons.load(person_id)
    if person is None:  # (deleted, e.g. in another tab)
        return
    stale = [ln for ln in person.links if ln.is_stale or ln.sync_state == "error"]
    if is_syncing(person_id):
        with ui.row().classes("w-full items-center bg-blue-1 text-blue-9 rounded p-2"):
            ui.spinner(size="sm")
            running = [
                ADAPTERS[ln.source].label for ln in person.links if ln.sync_state == "running"
            ]
            ui.label(_("Updating {sources}").format(sources=", ".join(running) or "…"))
        return
    if stale:
        with ui.row().classes("w-full items-center bg-amber-1 text-amber-10 rounded p-2 gap-3"):
            ui.icon("warning")
            names = ", ".join(f"{ADAPTERS[ln.source].label} ({_(ln.status_label)})" for ln in stale)
            ui.label(_("Some sources are not up to date: {names}").format(names=names))
            ui.button(
                _("Sync now"), icon="sync", on_click=lambda: _sync(person_id, only_stale=True)
            ).props("dense")


def _sync(person_id: int, *, only_stale: bool = False) -> None:
    if not start_sync(person_id, only_stale=only_stale):
        ui.notify(_("A sync is already running"))
    refresh_alive(stale_banner)
    refresh_alive(sources_list)


# ---- sources -------------------------------------------------------------------------------


def sources_view(person_id: int, on_change) -> None:
    state = {"was_running": False}

    with ui.row().classes("w-full items-center gap-2"):
        ui.button(_("Re-sync all"), icon="sync", on_click=lambda: _sync(person_id))

        async def search_again() -> None:
            ui.notify(_("Searching every source…"))
            errors = await discover(person_id)
            if errors:
                ui.notify(
                    _("Failed: {errors}").format(
                        errors="; ".join(f"{k}: {v}" for k, v in errors.items())
                    ),
                    type="warning",
                    multi_line=True,
                )
            refresh_alive(sources_list)

        ui.button(_("Search sources again"), icon="search", on_click=search_again).props("outline")
        url = ui.input(_("Add a profile by URL or id")).classes("w-96").props("dense clearable")
        src = ui.select(
            {"": _("auto-detect"), **{k: a.label for k, a in ADAPTERS.items() if a.linkable}},
            value="",
        ).props("dense")

        def add() -> None:
            text = (url.value or "").strip()
            candidates = [src.value] if src.value else list(ADAPTERS)
            for name in candidates:
                ext = ADAPTERS[name].parse_url(text)
                if ext:
                    add_link(person_id, name, ext)
                    ui.notify(
                        _("Added {source} profile {id} — syncing").format(
                            source=ADAPTERS[name].label, id=ext
                        )
                    )
                    url.value = ""
                    background_tasks.create(_sync_one(person_id, None, name, ext))
                    refresh_alive(sources_list)
                    refresh_alive(stale_banner)
                    return
            ui.notify(_("Could not recognise this URL / id"), type="warning")

        ui.button(_("Add"), on_click=add).props("dense")

    sources_list(person_id)

    def tick() -> None:
        running = is_syncing(person_id) or any(
            ln.sync_state == "running" for ln in persons.load(person_id).links
        )
        if running or state["was_running"]:
            refresh_alive(sources_list)
            refresh_alive(stale_banner)
        state["was_running"] = running
        # Reload the publications whenever a merge happened since they were loaded (a quick
        # sync can start and finish between two ticks).
        merged = persons.last_merged_at(person_id)
        if merged != state.get("merged_at"):
            if "merged_at" in state:
                refresh_alive(sources_list)
                refresh_alive(stale_banner)
                background_tasks.create(on_change())
            state["merged_at"] = merged

    ui.timer(1.5, tick)


async def _sync_one(
    person_id: int, link_id: int | None, source: str | None = None, ext: str | None = None
) -> None:
    if link_id is None:
        link_id = persons.link_id(person_id, source, ext)
    await sync_link(link_id)


def link_url(ln: SourceLink) -> str | None:
    return ln.url or ADAPTERS[ln.source].profile_url(ln.external_id)


@ui.refreshable
def sources_list(person_id: int) -> None:
    person = persons.load(person_id)
    if person is None:  # (deleted, e.g. in another tab)
        ui.label(_("This person was deleted.")).classes("text-grey")
        return
    links = [ln for ln in person.links if not manual.is_hal_document(ln)]
    validated = [ln for ln in links if ln.status == "validated"]
    candidates = sorted(
        (ln for ln in links if ln.status == "candidate"), key=lambda ln: (ln.source, -ln.score)
    )
    rejected = [ln for ln in links if ln.status == "rejected"]

    if not person.orcid and (found := suggested_orcid(person)):
        with (
            ui.row()
            .classes("w-full items-center gap-2 bg-green-1 rounded p-2")
            .mark("orcid-suggestion")
        ):
            ui.icon("badge", color="positive")
            ui.label(
                _("The validated profiles give the ORCID {orcid}.").format(orcid=found)
            ).classes("grow")

            def use(o=found) -> None:
                ui.notify(
                    _("ORCID {orcid} set: candidates with it are shown in green").format(orcid=o)
                )
                set_orcid(person_id, o)
                refresh_alive(sources_list)

            ui.button(_("Use it"), icon="check", on_click=use).props("dense").mark("orcid-use")
    ui.label(_("Validated sources")).classes("text-lg mt-2")
    if not validated:
        ui.label(_("None yet: validate candidates below, or add a profile by URL.")).classes(
            "text-grey"
        )
    with ui.column().classes("w-full gap-2"):
        for ln in validated:
            _validated_card(person_id, ln)

    if items := manual.added(person_id):
        _added_list(person_id, items)

    ui.label(_("Candidates to review")).classes("text-lg mt-4")
    if not candidates:
        ui.label(_("No pending candidate.")).classes("text-grey")
    with ui.grid(columns="repeat(auto-fill, minmax(360px, 1fr))").classes("w-full"):
        for ln in candidates:
            _candidate_card(person_id, ln, person.orcid or suggested_orcid(person), bool(validated))

    if rejected:
        with ui.expansion(_("Rejected ({n})").format(n=len(rejected))).classes("w-full"):
            for ln in rejected:
                with ui.row().classes("items-center gap-2"):
                    source_tag(ln.source, url=link_url(ln))
                    ui.link(ln.display_name or ln.external_id, ln.url or "#", new_tab=True)
                    ui.button(
                        _("restore"), on_click=lambda i=ln.id: _set(person_id, i, "candidate")
                    ).props("flat dense")


def _added_list(person_id: int, items: list[manual.AddedItem]) -> None:
    """Publications added by hand (Publications tab → +), with their source record."""
    ui.label(_("Added by hand")).classes("text-lg mt-4")
    with ui.column().classes("w-full gap-1").mark("added-publications"):
        for it in items:
            with ui.row().classes("w-full items-center gap-3 px-2"):
                source_tag(it.kind, url=it.url)
                ui.link(it.title or it.id, it.url, new_tab=True)
                ui.label(it.id).classes("text-grey text-xs")
                if it.state and it.state != "up to date":
                    ui.badge(_(it.state), color=STATUS_COLOUR.get(it.state, "grey"))
                ui.space()

                async def remove(i=it) -> None:
                    await manual.remove(person_id, i)
                    ui.notify(_("Removed {id}").format(id=i.id))
                    refresh_alive(sources_list)

                ui.button(icon="delete", on_click=remove).props(
                    "flat round dense color=negative"
                ).tooltip(_("Remove (the paper stays if another source has it)")).mark(
                    f"remove-added-{it.id}"
                )


def _set(person_id: int, link_id: int, status: str) -> None:
    set_link_status(link_id, status)
    refresh_alive(sources_list)
    refresh_alive(stale_banner)


def _validated_card(person_id: int, ln: SourceLink) -> None:
    adapter = ADAPTERS[ln.source]
    with ui.card().classes("w-full py-2"), ui.row().classes("w-full items-center gap-3"):
        source_tag(ln.source, url=link_url(ln))
        ui.link(
            ln.display_name or ln.external_id,
            ln.url or adapter.profile_url(ln.external_id),
            new_tab=True,
        ).classes("font-medium")
        ui.label(ln.external_id).classes("text-grey text-xs")
        ui.badge(_(ln.status_label), color=STATUS_COLOUR.get(ln.status_label, "grey"))
        if ln.sync_state == "running":
            ui.spinner(size="xs")
        ui.label(_("last sync {when}").format(when=fmt_dt(ln.last_synced_at))).classes(
            "text-sm text-grey"
        )
        if ln.record_count is not None:
            ui.label(
                ngettext("{n} record", "{n} records", ln.record_count).format(n=ln.record_count)
            ).classes("text-sm text-grey")
        ui.space()
        if ln.source == "scholar":
            _scholar_upload(ln)
        ui.button(
            icon="sync", on_click=lambda i=ln.id: background_tasks.create(_sync_one(person_id, i))
        ).props("flat round dense").tooltip(_("Sync this source"))
        ui.button(icon="link_off", on_click=lambda i=ln.id: _set(person_id, i, "rejected")).props(
            "flat round dense color=negative"
        ).tooltip(_("Unlink (reject)"))
    if ln.sync_state == "error" and ln.last_error:
        ui.label(ln.last_error).classes("text-negative text-sm -mt-2 ml-4")


def _scholar_upload(ln: SourceLink) -> None:
    ui.button(icon="upload_file", on_click=lambda: _scholar_dialog(ln)).props(
        "flat round dense"
    ).tooltip(_("Upload a saved profile page"))


def _scholar_dialog(ln: SourceLink) -> None:
    with transient_dialog(_("Upload a saved Google Scholar profile page")) as (dlg, _card):
        ui.markdown(
            _(
                "Open the profile, click **Show more** until every paper is listed, then "
                "save the page (HTML) and upload it here."
            )
        )

        async def handle(e) -> bool:
            html = await e.file.text()
            sid = profile_id_from_html(html)
            if sid and sid != ln.external_id:
                ui.notify(
                    _("This page is for profile {found}, not {expected}").format(
                        found=sid, expected=ln.external_id
                    ),
                    type="warning",
                )
                return False
            result = parse_profile(html, [ln.display_name or ""])
            if not result.publications:
                ui.notify(_("No publications found in this page"), type="warning")
                return False
            finish_link(ln.id, result)
            n = len(result.publications)
            ui.notify(
                ngettext("Imported {n} publication", "Imported {n} publications", n).format(n=n)
            )
            refresh_alive(sources_list)
            refresh_alive(stale_banner)
            return True

        ui.upload(on_upload=ok_handler(dlg, handle), auto_upload=True, max_files=1).props(
            'accept=".html,.htm"'
        )


def _candidate_card(
    person_id: int, ln: SourceLink, orcid: str | None = None, can_probe: bool = False
) -> None:
    """``orcid``: the person's, or that of the validated profiles (a candidate with the same
    is shown in green, another in red). ``can_probe``: there are validated sources to compare
    the candidate's papers with."""
    ev = ln.evidence or {}
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2 w-full"):
            source_tag(ln.source, url=link_url(ln))
            ui.link(ln.display_name or ln.external_id, ln.url or "#", new_tab=True).classes(
                "font-medium"
            )
            ui.space()
            ui.badge(f"{ln.score:.2f}", color="positive" if ln.score >= 0.9 else "grey").tooltip(
                _("match score (name, ORCID, affiliation, known titles, papers in common)")
            )
        orcid_badge(ln, orcid)
        for d in candidate_details(ln):
            ui.label(d).classes("text-sm text-grey")
        for t in ev.get("sample_titles") or []:
            ui.label(f"• {t}").classes("text-xs")
        if ov := ev.get("overlap"):
            share = ov["matched"] / ov["total"] if ov["total"] else 0
            ui.badge(
                _("{matched}/{total} papers already known ({share})").format(
                    matched=ov["matched"], total=ov["total"], share=f"{share:.0%}"
                ),
                color="positive" if share >= 0.3 else "warning" if share > 0 else "negative",
            ).tooltip(_("its papers found in the validated sources")).mark(
                f"candidate-overlap-{ln.id}"
            )
        with ui.row():
            ui.button(
                _("Validate"), icon="check", on_click=lambda i=ln.id: _validate(person_id, i)
            ).props("dense")
            ui.button(
                _("Reject"), icon="close", on_click=lambda i=ln.id: _set(person_id, i, "rejected")
            ).props("dense flat color=negative")
            if can_probe and ADAPTERS[ln.source].provides_publications:
                ui.button(icon="join_inner", on_click=lambda i=ln.id: _probe(person_id, i)).props(
                    "dense flat"
                ).tooltip(_("Fetch its papers and check how many the validated sources have")).mark(
                    f"candidate-probe-{ln.id}"
                )


def candidate_details(ln: SourceLink) -> list[str]:
    """What a candidate profile says of the person (affiliation, works, topics, roles)."""
    ev = ln.evidence or {}
    details = []
    if ev.get("affiliation"):
        details.append(ev["affiliation"])
    if ev.get("works_count") is not None:
        details.append(
            ngettext("{n} work", "{n} works", ev["works_count"]).format(n=ev["works_count"])
        )
    if ev.get("topics"):
        details.append(", ".join(t for t in ev["topics"] if t))
    if ev.get("roles"):
        details.append(", ".join(f"{k.split(' /')[0]}: {v}" for k, v in ev["roles"].items()))
    return details


def orcid_badge(ln: SourceLink, orcid: str | None) -> None:
    """The candidate's ORCID, if any: green if the person's (``orcid``), red if another."""
    own_id = ln.external_id if ln.source == "orcid" else ""
    theirs = normalize_orcid((ln.evidence or {}).get("orcid") or own_id)
    if not theirs:
        return
    same = orcid is not None and theirs == orcid
    ui.badge(
        f"ORCID {theirs}" + (" ✓" if same else " ≠" if orcid else ""),
        color="positive" if same else "negative" if orcid else "grey-6",
    ).props("outline" if not orcid else "").tooltip(
        _("the person's ORCID")
        if same
        else _("not the person's ORCID ({orcid})").format(orcid=orcid)
        if orcid
        else _("set the person's ORCID (edit) to check it")
    ).mark(f"candidate-orcid-{ln.id}")


async def _probe(person_id: int, link_id: int) -> None:
    ui.notify(_("Fetching the candidate's papers…"))
    try:
        await probe_overlap(link_id)
    except Exception as e:
        ui.notify(_("Could not fetch its papers: {error}").format(error=e), type="warning")
        return
    refresh_alive(sources_list)


def _validate(person_id: int, link_id: int) -> None:
    # Before refreshing the list: it holds the clicked button (deleted with it).
    ui.notify(_("Validated — syncing this source"), type="positive")
    _set(person_id, link_id, "validated")
    background_tasks.create(_sync_one(person_id, link_id))


# ---- periods -------------------------------------------------------------------------------


def periods_view(person_id: int, on_change) -> None:
    @ui.refreshable
    def listing() -> None:
        items = [p for p in annotations.periods(person_id) if p.folder_id is None]
        if not items:
            ui.label(
                _(
                    "No period yet. Periods are named year ranges (e.g. “HDR 2015–2024”); "
                    "papers can be starred, tagged and annotated within each period."
                )
            ).classes("text-grey")
        for p in items:
            with ui.row().classes("items-center gap-2"):
                name = ui.input(_("Name"), value=p.name).props("dense")
                start = (
                    ui.number(_("From"), value=p.start_year, format="%d")
                    .props("dense")
                    .classes("w-24")
                )
                end = (
                    ui.number(_("To"), value=p.end_year, format="%d").props("dense").classes("w-24")
                )
                ui.label(f"★ {len(p.stars)}").classes("text-amber-8")

                def save(pid=p.id, n=name, a=start, b=end) -> None:
                    annotations.save_period(
                        person_id, n.value, int_or_none(a.value), int_or_none(b.value), pid
                    )
                    ui.notify(_("Period saved"))
                    on_change()

                def remove(pid=p.id) -> None:
                    annotations.delete_period(pid)
                    listing.refresh()
                    on_change()

                ui.button(icon="save", on_click=save).props("flat round dense")
                ui.button(icon="delete", on_click=remove).props("flat round dense color=negative")

    listing()
    with ui.row().classes("items-center gap-2 mt-4"):
        name = ui.input(_("New period name")).props("dense")
        start = ui.number(_("From"), format="%d").props("dense").classes("w-24")
        end = ui.number(_("To"), format="%d").props("dense").classes("w-24")

        def add() -> None:
            if not name.value:
                return
            annotations.save_period(
                person_id, name.value, int_or_none(start.value), int_or_none(end.value)
            )
            name.value = ""
            listing.refresh()
            on_change()

        ui.button(_("Add period"), icon="add", on_click=add)

    # (taken out of a folder, a person may keep their period there as one of their own)
    folders_section(person_id, lambda: (listing.refresh(), on_change()))


def folders_section(person_id: int, on_change) -> None:
    """The person's period in each of their folders."""
    ui.label(_("In folders")).classes("text-lg mt-6")
    ui.label(
        _("A folder (e.g. a committee) groups people; each person has their own period in it.")
    ).classes("text-sm text-grey")

    @ui.refreshable
    def listing() -> None:
        mine = [
            p
            for p in annotations.periods(person_id, include_hidden_folders=True)
            if p.folder_id is not None
        ]
        for p in mine:
            with ui.row().classes("items-center gap-2"):
                ui.icon("folder", color="amber-8")
                ui.link(p.folder.name, urls.folder_url(p.folder_id)).classes("w-56")
                if p.folder.date:
                    ui.label(p.folder.date.isoformat()).classes("text-sm text-grey")
                if p.folder.hidden:
                    ui.badge(_("hidden"), color="grey")
                start = (
                    ui.number(_("From"), value=p.start_year, format="%d")
                    .props("dense")
                    .classes("w-24")
                )
                end = (
                    ui.number(_("To"), value=p.end_year, format="%d").props("dense").classes("w-24")
                )
                ui.label(f"★ {len(p.stars)}").classes("text-amber-8")
                tags = (
                    ui.select(
                        sorted(set(p.tags or []), key=str.lower),
                        value=list(p.tags or []),
                        label=_("Tags"),
                        multiple=True,
                        new_value_mode="add-unique",
                        with_input=True,
                    )
                    .props("dense use-chips")
                    .classes("w-56")
                    .mark(f"folder-tags-{p.id}")
                )
                inherited = source_settings.primary_of_folder(p.folder_id)
                primary = (
                    ui.select(
                        {
                            None: _("The folder's ({source})").format(
                                source=ADAPTERS[inherited].label
                                if inherited in ADAPTERS
                                else _("none")
                            ),
                            source_settings.NO_PRIMARY: _("None"),
                            **{
                                n: a.label
                                for n, a in ADAPTERS.items()
                                if a.linkable and a.provides_publications
                            },
                        },
                        value=p.primary_source,
                        label=_("Primary source"),
                    )
                    .props("dense")
                    .classes("w-44")
                    .tooltip(_("Papers this source doesn't list for the person are not counted"))
                    .mark(f"period-primary-{p.id}")
                )

                def save(pid=p.id, a=start, b=end, t=tags, src=primary) -> None:
                    folders.set_period(pid, int_or_none(a.value), int_or_none(b.value))
                    folders.set_tags(pid, t.value or [])
                    folders.set_period_primary(pid, src.value)
                    ui.notify(_("Period saved"))
                    on_change()

                def left() -> None:
                    listing.refresh()
                    on_change()

                ui.button(icon="save", on_click=save).props("flat round dense")
                ui.button(
                    icon="drive_file_move",
                    on_click=lambda fid=p.folder_id: move_to_folder_dialog(
                        fid, person_id, lambda _to: left()
                    ),
                ).props("flat round dense").tooltip(_("Move to another folder")).mark(
                    f"move-{p.folder_id}"
                )
                ui.button(
                    icon="folder_off",
                    on_click=lambda fid=p.folder_id: remove_from_folder_dialog(
                        fid, person_id, left
                    ),
                ).props("flat round dense color=negative").tooltip(
                    _("Remove from this folder")
                ).mark(f"leave-{p.folder_id}")
        joined = {p.folder_id for p in mine}
        others = {f.id: f.name for f in folders.folders() if f.id not in joined}
        if others:
            with ui.row().classes("items-center gap-2 mt-2"):
                pick = (
                    ui.select(others, label=_("Add to a folder"))
                    .props("dense outlined")
                    .classes("w-64")
                )

                def join() -> None:
                    if pick.value:
                        folders.add_person(pick.value, person_id)
                        listing.refresh()
                        on_change()

                ui.button(_("Add"), icon="create_new_folder", on_click=join).props("dense")

    listing()


def remove_from_folder_dialog(folder_id: int, person_id: int, done) -> None:
    """Take a person out of a folder, keeping their data there (as one of their own periods)
    or deleting it (confirmed again, listing it, if some is the user's own work)."""

    def remove(keep: bool) -> None:
        folders.remove_person(folder_id, person_id, keep=keep)
        done()

    def delete() -> None:
        period = folders.period_in(folder_id, person_id)
        n = folders.deletion_counts(period_ids=[period] if period else [])
        if not n["precious"]:
            remove(False)
            return
        close_then(
            dlg,
            lambda: deletion_dialog(
                _("Delete the person's data in the folder?"),
                n,
                lambda: remove(False),
                mark="remove-delete-confirm",
            ),
        )

    with transient_dialog(width="w-[32rem]") as (dlg, _card):
        ui.label(
            _(
                "Remove from the folder: keep the person's stars, tags, notes, documents, "
                "excerpts and report there as one of their own periods (Profile tab), or "
                "delete them?"
            )
        )
        with ui.row().classes("justify-end w-full"):
            ui.button(_("Cancel"), on_click=dlg.close).props("flat")
            ui.button(
                _("Delete the data"),
                color="negative",
                on_click=ok_handler(dlg, delete),
            ).props("flat").mark("remove-delete")
            ui.button(_("Keep as a period"), on_click=ok_handler(dlg, lambda: remove(True))).mark(
                "remove-keep"
            )


def move_to_folder_dialog(folder_id: int, person_id: int, done) -> None:
    """Move a person to another folder, with their data in this one; then ``done(to_id)``."""
    mine = {
        p.folder_id
        for p in annotations.periods(person_id, include_hidden_folders=True)
        if p.folder_id is not None
    }
    options = {
        n.id: n.path + (_(" (archived)") if n.hidden else "")
        for n in folders.tree()
        if n.id not in mine
    }
    with transient_dialog(_("Move to another folder"), width="w-[32rem]") as (dlg, _card):
        ui.label(
            _(
                "The person's period in this folder goes with them: its years, tags, stars, "
                "notes, documents, report and excerpts."
            )
        ).classes("text-sm")
        if not options:
            ui.label(_("They are already in every folder.")).classes("text-grey")
        target = (
            ui.select(options, label=_("Folder"), with_input=True)
            .props("dense outlined")
            .classes("w-full")
            .mark("move-target")
        )

        def move() -> bool | None:
            if target.value is None:
                return False
            try:
                added = folders.move_person(person_id, folder_id, target.value)
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return False
            if added:
                ui.notify(
                    ngettext(
                        "{n} category added to the folder's settings for their excerpts",
                        "{n} categories added to the folder's settings for their excerpts",
                        added,
                    ).format(n=added)
                )
            done(target.value)

        actions(dlg, _("Move"), move, mark="move-ok", icon="drive_file_move")


# ---- theses --------------------------------------------------------------------------------


def student_aliases_view(person_id: int, rows: list[Thesis]) -> None:
    """Aliases of supervised PhD students, used to highlight them in author lists."""
    students = sorted(
        {n for t in rows if t.role == "director" for n in (t.student or "").split(", ") if n}
    )
    if not students:
        return
    current = persons.student_aliases(person_id)
    with ui.expansion(_("PhD student name aliases (for highlighting in author lists)")).classes(
        "w-full"
    ):
        inputs = {}
        for name in students:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(name).classes("w-56")
                inputs[name] = (
                    ui.input(
                        placeholder=_("other spellings, comma-separated"),
                        value=", ".join(current.get(name, [])),
                    )
                    .props("dense outlined")
                    .classes("grow")
                )

        def save() -> None:
            persons.save_student_aliases(
                person_id,
                {
                    n: [a.strip() for a in i.value.split(",") if a.strip()]
                    for n, i in inputs.items()
                },
            )
            ui.notify(_("Aliases saved (reload the publications to see them)"))

        ui.button(_("Save aliases"), icon="save", on_click=save)


class ThesesView:
    """The person's theses by role; within the years of the publications panel (its
    period) unless "all" is asked."""

    def __init__(self, person_id: int, panel: PublicationsPanel) -> None:
        self.person_id = person_id
        self.panel = panel
        self.show_all = False
        self.shown: tuple | None = None
        self.rows = persons.theses(person_id)
        if not self.rows:
            ui.label(_("No thesis: validate a theses.fr profile in the Sources tab.")).classes(
                "text-grey"
            )
            return
        student_aliases_view(person_id, self.rows)
        self.container = ui.column().classes("w-full gap-2").mark("theses")
        self.render()
        panel.on_render.append(self.refresh)

    def refresh(self) -> None:
        """Re-render when the period changed."""
        if self.container.is_deleted:
            self.panel.on_render.remove(self.refresh)
        elif self.shown != (self.panel.lo, self.panel.hi):
            self.render()

    def render(self) -> None:
        lo, hi = self.panel.lo, self.panel.hi
        self.shown = (lo, hi)
        bounded = lo is not None or hi is not None
        rows = (
            self.rows
            if self.show_all or not bounded
            else [t for t in self.rows if theses.in_period(t, lo, hi)]
        )
        self.container.clear()
        with self.container:
            if bounded:
                period = next((p for p in self.panel.periods if p.id == self.panel.period_id), None)
                rng = f"{lo or '…'}–{hi or '…'}"
                where = (
                    period_label(period)
                    if period
                    and (lo, hi)
                    == (
                        period.start_year,
                        period.end_year,
                    )
                    else rng
                )
                with ui.row().classes("items-center gap-4"):
                    ui.label(
                        _("{n} of {total} theses in {where}").format(
                            n=len(rows), total=len(self.rows), where=where
                        )
                        if not self.show_all
                        else _("All {total} theses (period: {where})").format(
                            total=len(self.rows), where=where
                        )
                    ).classes("text-grey").mark("theses-period")

                    def toggle(e) -> None:
                        self.show_all = e.value
                        self.render()

                    ui.switch(_("show all"), value=self.show_all, on_change=toggle).tooltip(
                        _(
                            "Also the theses outside the period (supervision overlapping it; "
                            "juries: the defence within it)"
                        )
                    ).mark("theses-all")
            outcomes = persons.student_outcomes(self.person_id)
            for role in ["director", "rapporteur", "examiner", "president", "author", "other"]:
                items = [t for t in rows if t.role == role]
                if items:
                    _theses_table(role, items, self.person_id, outcomes)


def _theses_table(role: str, items: list[Thesis], person_id: int, outcomes: dict) -> None:
    spans = {t.id: theses.span(t) for t in items}
    items.sort(key=lambda t: spans[t.id].end or spans[t.id].start or "9999", reverse=True)
    with ui.expansion(f"{ROLE_LABELS[role]} ({len(items)})", value=role != "author").classes(
        "w-full"
    ):
        columns = [
            {"name": "start", "label": _("Start"), "field": "start", "sortable": True},
            {"name": "end", "label": _("End"), "field": "end", "sortable": True},
            {"name": "student", "label": _("PhD student"), "field": "student", "sortable": True},
            {"name": "title", "label": _("Title"), "field": "title", "align": "left"},
            {"name": "sup", "label": _("Supervisors"), "field": "sup", "align": "left"},
            {"name": "inst", "label": _("Institution"), "field": "inst", "align": "left"},
            {"name": "status", "label": _("Status"), "field": "status"},
        ]
        supervised = role == "director"
        if supervised:
            columns.append(
                {
                    "name": "outcome",
                    "label": _("After the PhD"),
                    "field": "outcome",
                    "align": "left",
                }
            )
        table = (
            ui.table(
                columns=columns,
                rows=[
                    {
                        "id": t.id,
                        "start": spans[t.id].start or "",
                        "estimated": spans[t.id].start_estimated,
                        "end": spans[t.id].end or "",
                        "student": t.student,
                        "title": t.title,
                        "sup": ", ".join(t.supervisors or []),
                        "inst": t.institution,
                        "status": _("defended")
                        if t.status == "soutenue"
                        else _("in progress")
                        if t.status == "enCours"
                        else t.status,
                        "url": t.url,
                        **(
                            {
                                "outcome": outcomes.get(t.student or "", ""),
                                "links": theses.search_links(t.student or ""),
                            }
                            if supervised
                            else {}
                        ),
                    }
                    for t in items
                ],
                row_key="id",
            )
            .classes("w-full")
            .props("dense flat wrap-cells")
            .mark(f"theses-{role}")
        )
        estimated = escape(
            _(
                "Estimated: {n} years before the defence "
                "(theses.fr only gives the start of the theses in progress)"
            ).format(n=theses.TYPICAL_YEARS)
        )
        table.add_slot(
            "body-cell-start",
            f"""
            <q-td :props="props">
              <span v-if="props.row.estimated" class="text-grey">≈ {{{{ props.value }}}}
                <q-tooltip>{estimated}</q-tooltip>
              </span>
              <span v-else>{{{{ props.value }}}}</span>
            </q-td>""",
        )
        table.add_slot(
            "body-cell-title",
            """
            <q-td :props="props"><a :href="props.row.url" target="_blank">{{ props.value }}</a>
            </q-td>""",
        )
        if supervised:
            # Search links, and a note (click to edit) on what became of the student.
            table.add_slot(
                "body-cell-outcome",
                """
                <q-td :props="props" style="min-width: 14rem">
                  <div class="row items-center q-gutter-x-xs">
                    <q-btn v-for="l in props.row.links" :key="l.url" :href="l.url"
                           target="_blank" :label="l.label" flat dense no-caps size="sm"
                           color="primary">
                      <q-tooltip>__SEARCH__</q-tooltip>
                    </q-btn>
                  </div>
                  <div class="cursor-pointer" :class="props.value ? '' : 'text-grey'">
                    {{ props.value || __ADD_NOTE__ }}
                    <q-popup-edit v-model="props.row.outcome" buttons v-slot="scope"
                        @save="v => $parent.$emit('outcome',
                                                  {student: props.row.student, note: v})">
                      <q-input v-model="scope.value" type="textarea" autogrow dense autofocus
                               label="__OUTCOME_LABEL__" />
                    </q-popup-edit>
                  </div>
                </q-td>""".replace(
                    "__SEARCH__",
                    escape(_("Search {name} on {source}")).format(
                        name="{{ l.name }}", source="{{ l.label }}"
                    ),
                )
                .replace("__ADD_NOTE__", json.dumps(_("add a note…")))
                .replace(
                    "__OUTCOME_LABEL__",
                    escape(_("What became of them (position, employer…)"), quote=True),
                ),
            )

            def save_outcome(e) -> None:
                annotations.set_student_outcome(person_id, e.args["student"], e.args["note"])
                for r in table.rows:
                    if r["student"] == e.args["student"]:
                        r["outcome"] = e.args["note"].strip()
                ui.notify(_("Note saved"))

            table.on("outcome", save_outcome)


__all__ = ["register"]
