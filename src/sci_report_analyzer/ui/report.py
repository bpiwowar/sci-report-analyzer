"""The report on a person within a period / folder (its own page): Markdown citing the
papers (``[@key]``…, see reports.py), each paper to discuss at least once."""

from __future__ import annotations

import re

from nicegui import ui

from .. import annotations, categories, folders, reports
from ..db.models import Period
from ..db.session import session_scope
from . import pdf_viewer
from .mdedit import NOTE_EXTRAS, MarkdownEditor

# The citation at the cursor: its paper highlighted in the sidebar (and scrolled to).
_CSS = ".vr-at-cursor { background: rgba(255, 193, 7, 0.25); box-shadow: inset 3px 0 #ffc107; }"
_CURSOR_JS = r"""
(() => {
  const KEY = /-?@(\w+(?:[:.#$%&+?<>~\/-]\w+)*)/g;
  // The key under the cursor: in a bracketed citation ([@a; @b], the nearest one), or @key.
  const keyAt = (line, col) => {
    for (const b of line.matchAll(/\[[^\[\]]*\]/g)) {
      if (col < b.index || col > b.index + b[0].length) continue;
      let best = null, dist = Infinity;
      for (const m of b[0].matchAll(KEY)) {
        const s = b.index + m.index, e = s + m[0].length;
        const d = col < s ? s - col : col > e ? col - e : 0;
        if (d < dist) { best = m[1]; dist = d; }
      }
      if (best) return best;
    }
    for (const m of line.matchAll(KEY)) {
      if (col >= m.index && col <= m.index + m[0].length) return m[1];
    }
    return null;
  };
  window.vrReportKey = null;
  window.vrReportMark = (scroll) => {
    document.querySelectorAll('.vr-at-cursor').forEach(e => e.classList.remove('vr-at-cursor'));
    if (!vrReportKey) return;
    const row = document.querySelector(`[data-report-key="${CSS.escape(vrReportKey)}"]`);
    if (!row) return;
    row.classList.add('vr-at-cursor');
    if (scroll) row.scrollIntoView({block: 'nearest'});
  };
  let n = 0;
  const t = setInterval(() => {
    const v = getElement({EDITOR})?.editor;
    if (++n > 50) clearInterval(t);
    if (!v) return;
    clearInterval(t);
    const update = () => {
      const pos = v.state.selection.main.head, line = v.state.doc.lineAt(pos);
      const key = keyAt(line.text, pos - line.from);
      if (key === vrReportKey) return;
      vrReportKey = key;
      vrReportMark(true);
    };
    for (const e of ['keyup', 'mouseup', 'focus']) v.dom.addEventListener(e, update);
  }, 100);
})()
"""

_OPTION = r"""
<q-item v-bind="props.itemProps">
  <q-item-section style="flex: 0 0 13rem">{{ props.opt.label.split('\t')[0] }}</q-item-section>
  <q-item-section class="text-grey-7 ellipsis" style="display: block">
    {{ props.opt.label.split('\t')[1] }}
  </q-item-section>
</q-item>
"""
_SELECTED = r"""<span class="ellipsis">{{ props.opt.label.split('\t')[0] }}</span>"""

HELP = (
    "Cite the papers with Pandoc's syntax: `[@key]` its number, `[@a; @b]` several, "
    "`@key` its title, venue, year and category, `[@key]{.notes}` with its notes, "
    "`[@key]{.tags}` with its tags (`{.notes .tags}`: both), `[@key]{**#.index** (.year)}` "
    "a template (`.number`, `.index`: the bare number, `.title`, `.venue`, `.short-venue`, "
    "`.year`, `.tags`, `.notes`). Copy substitutes them."
)


def page_url(period_id: int) -> str:
    return f"/report/{period_id}"


def _period(period_id: int) -> tuple[int, str, str, tuple[int | None, int | None]] | None:
    with session_scope() as s:
        p = s.get(Period, period_id)
        if p is None:
            return None
        return p.person_id, p.person.name, p.name, (p.start_year, p.end_year)


def register() -> None:
    @ui.page("/report/{period_id}")
    def report_page(period_id: int) -> None:
        found = _period(period_id)
        ui.query(".nicegui-content").classes("p-0 gap-0")
        if found is None:
            ui.label("No such period").classes("p-4")
            return
        _, person, name, _ = found
        ui.page_title(f"Report · {person} · {name}")
        box = ui.column().classes("w-full h-screen gap-0 no-wrap")
        with box:
            ui.spinner().classes("m-4")
        ui.timer(0.05, lambda: _build(box, period_id, found), once=True)


async def _build(box: ui.column, period_id: int, found) -> None:
    from ..pubview import load_stats

    person_id, person, name, years = found
    span = {
        (False, False): f"{years[0]}–{years[1]}",
        (False, True): f"since {years[0]}",
        (True, False): f"until {years[1]}",
        (True, True): "any year",
    }[(years[0] is None, years[1] is None)]
    stats = await load_stats(person_id)
    keys = reports.citation_keys(stats)
    seen = {"signature": reports.signature(person_id)}
    saved = reports.get(period_id)
    templates = reports.load_templates()
    all_tags = annotations.all_tags()
    names = {t.id: t.name for t in all_tags}
    state = {"dirty": False, "cited": None}

    def context() -> reports.Context:
        tag_ids = [t for t in tags.value or [] if t in names]
        papers = reports.report_papers(stats, keys, tag_ids, period_id, years)
        return reports.Context(
            papers,
            stats,
            keys,
            names,
            hide_tags=set(tag_ids),
            period_id=period_id,
            number_format=fmt.value or reports.NUMBER_FORMAT,
        )

    def rendered() -> reports.Rendered:
        return reports.render(editor.value, context())

    def changed(_text: str = "") -> None:
        state["dirty"] = True
        status.text = "Editing…"
        cited = rendered().cited
        if dict(cited) != state["cited"]:
            sidebar.refresh()

    def example(attrs: str) -> str:
        """The template on a paper of the report (one with notes, if any): its first line,
        as plain text."""
        ctx = context()
        p = next((p for p in ctx.papers if p.stat.note), ctx.papers[0] if ctx.papers else None)
        if p is None:
            return ""
        text = reports.render(templates.cite(p.key, attrs), ctx).text.split("\n")[0]
        text = re.sub(r"[*_`]", "", text)
        return text if len(text) <= 70 else text[:69] + "…"

    def default_changed(attrs: str) -> None:
        templates.default = attrs
        reports.save_templates(templates)
        sidebar.refresh()

    def settings_changed() -> None:
        reports.save(
            period_id,
            tag_ids=list(tags.value or []),
            number_format=fmt.value or reports.NUMBER_FORMAT,
        )
        editor.refresh_preview()
        sidebar.refresh()

    def save() -> None:
        if state["dirty"]:
            reports.save(period_id, text=editor.value)
            state["dirty"] = False
            status.text = "Saved"

    def final_text() -> str:
        ctx = context()
        out = reports.render(editor.value, ctx).text.rstrip()
        if with_list.value:
            out += "\n\n## Papers\n\n" + reports.bibliography(ctx)
        return out + "\n"

    def copy() -> None:
        save()
        ui.clipboard.write(final_text())
        ui.notify("Copied (citations substituted)")

    folder = folders.folder_of_period(period_id)

    def excerpts_button() -> None:
        if folder is None:
            return

        def insert() -> None:
            text = categories.markdown(folder[0], period_id, level=3)
            if not text:
                ui.notify("No excerpt filed yet (select a passage in a PDF)", type="warning")
                return
            editor.insert(text)

        ui.button(icon="format_quote", on_click=insert).props("flat dense round size=sm").tooltip(
            f"Insert the excerpts, by category of {folder[1]} (Markdown)"
        ).mark("report-excerpts")

    box.clear()
    with box:
        with ui.row().classes("w-full items-center no-wrap gap-2 px-3 py-1 bg-primary text-white"):
            ui.link("SciReport Analyzer", f"/person/{person_id}").classes(
                "text-white font-bold no-underline"
            )
            ui.label(f"Report · {person} · {name}").classes("ellipsis grow min-w-0 font-medium")
            status = ui.label("").classes("text-sm opacity-80").mark("report-status")
            with_list = (
                ui.checkbox("with the list of papers", value=False)
                .props("dense dark")
                .tooltip("Copy the papers' list (numbered) after the report")
                .mark("report-with-list")
            )
            ui.button("Copy", icon="content_copy", on_click=copy).props(
                "flat dense color=white"
            ).tooltip("Copy the report, its citations substituted").mark("report-copy")
            ui.button(
                icon="download",
                on_click=lambda: (
                    save(),
                    ui.download.content(final_text(), f"report-{person}-{name}.md"),
                ),
            ).props("flat dense round color=white").tooltip("Download (.md)")
        with ui.row().classes("w-full grow min-h-0 no-wrap gap-0"):
            with ui.column().classes("grow min-w-0 h-full p-3 gap-2 no-wrap"):
                with ui.row().classes("w-full items-center gap-3"):
                    tags = (
                        ui.select(
                            names,
                            value=[t for t in saved.tag_ids if t in names],
                            multiple=True,
                            label="Papers to discuss: with one of the tags",
                            on_change=settings_changed,
                        )
                        .props("dense outlined use-chips clearable")
                        .classes("min-w-64")
                        .tooltip("Without: every paper of the period's years")
                        .mark("report-tags")
                    )
                    fmt = (
                        ui.input(
                            "Paper number",
                            value=saved.number_format,
                            on_change=settings_changed,
                        )
                        .props("dense outlined")
                        .classes("w-36")
                        .tooltip("How a paper's number is written ({n}: the number)")
                        .mark("report-number-format")
                    )
                    cite_as = (
                        ui.select(
                            {t.attrs: f"{t.label}\t{example(t.attrs)}" for t in templates.items},
                            value=templates.default,
                            label="Cite as",
                            on_change=lambda e: default_changed(e.value),
                        )
                        .props(
                            'dense outlined options-dense popup-content-style="max-width: 48rem"'
                        )
                        .classes("w-44")
                        .tooltip(
                            "How a paper is cited when clicked in the side (or Enter); "
                            "the templates are set in Settings → Report templates"
                        )
                        .mark("report-cite-form")
                    )
                    # (label TAB example: in two columns, the examples aligned)
                    cite_as.add_slot("option", _OPTION)
                    cite_as.add_slot("selected-item", _SELECTED)
                    ui.markdown(HELP).classes("text-xs text-grey grow min-w-0")
                editor = MarkdownEditor(
                    saved.text,
                    on_change=changed,
                    render=lambda text: reports.render(text, context()).text,
                    mode="split",
                    mark="report-text",
                    fill=True,
                    keymap={"Mod-k": lambda: search.run_method("focus")},
                    toolbar=excerpts_button,
                )
                ui.add_css(_CSS)
                ui.run_javascript(_CURSOR_JS.replace("{EDITOR}", str(editor.editor.id)))
            with ui.column().classes(
                "w-96 shrink-0 h-full p-3 gap-1 no-wrap overflow-y-auto overflow-x-hidden border-l"
            ):
                search = (
                    ui.input(
                        placeholder="Find a paper (⌘/Ctrl-K), Enter: cite it",
                        on_change=lambda: sidebar.refresh(),
                    )
                    .props("dense outlined clearable autofocus")
                    .classes("w-full")
                    .mark("report-search")
                )

                @ui.refreshable
                def sidebar() -> None:
                    ctx = context()
                    result = reports.render(editor.value, ctx)
                    state["cited"] = dict(result.cited)
                    uncited = reports.uncited(ctx, result.cited)
                    missing = [p for p in uncited if not p.off_period]
                    off = [p for p in ctx.papers if p.off_period]
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.label(
                            f"{len(ctx.papers) - len(off)} papers · {len(missing)} not discussed"
                        ).classes(
                            "text-sm " + ("text-negative" if missing else "text-positive")
                        ).mark("report-count")
                        if off:
                            ui.label(
                                f"+ {len(off)} off-period · "
                                f"{len(uncited) - len(missing)} not discussed"
                            ).classes("text-sm text-grey").tooltip(
                                f"With the tags, but not of the period's years ({span})"
                            ).mark("report-count-off")
                        ui.space()
                        if missing:
                            ui.button(
                                "Cite them",
                                icon="playlist_add",
                                on_click=lambda: editor.insert(
                                    "\n".join(f"- [@{p.key}]{{.notes}}" for p in missing) + "\n"
                                ),
                            ).props("flat dense no-caps size=sm").tooltip(
                                "Insert the papers not discussed yet (with their notes), "
                                "at the cursor"
                            ).mark("report-cite-missing")
                    if result.unknown:
                        ui.label("Unknown: " + ", ".join(f"@{k}" for k in result.unknown)).classes(
                            "text-sm text-negative"
                        ).mark("report-unknown")
                    outside = [p for p in ctx.by_key.values() if not p.in_report]
                    if outside:
                        ui.label(
                            "Cited, without the tags: " + ", ".join(f"@{p.key}" for p in outside)
                        ).classes("text-sm text-warning")
                    words = (search.value or "").lower().split()

                    def matches(p: reports.Paper) -> bool:
                        hay = " ".join(
                            [p.key, str(p.number), p.stat.title or "", *p.stat.authors]
                        ).lower()
                        return all(w in hay for w in words)

                    shown = [p for p in ctx.papers if matches(p) and not p.off_period]
                    shown_off = [p for p in off if matches(p)]
                    others = []
                    if words:  # (also the person's other papers)
                        others = [
                            reports.Paper(s, keys[s.id], 0, in_report=False)
                            for s in stats
                            if keys[s.id] not in ctx.by_key
                            and matches(reports.Paper(s, keys[s.id], 0))
                        ][:20]
                    first = next(iter(shown + shown_off + others), None)
                    state["first"] = first.key if first else None

                    def row(p: reports.Paper) -> None:
                        _paper_row(p, result.cited.get(p.key, 0), ctx, editor, period_id, templates)

                    for p in shown:
                        row(p)
                    if shown_off:
                        ui.label(f"Off-period (not {span})").classes(
                            "w-full text-xs text-grey uppercase mt-3"
                        ).mark("report-off-period")
                        for p in shown_off:
                            row(p)
                    for p in others:
                        row(p)
                    ui.run_javascript("window.vrReportMark && vrReportMark(false)")

                def cite_first() -> None:
                    if key := state.get("first"):
                        editor.insert(templates.cite(key))
                        search.value = ""

                search.on("keydown.enter", cite_first)
                sidebar()
    status.text = "Saved" if saved.text else ""

    async def reload() -> None:
        """The papers edited elsewhere (e.g. their notes, in the PDF's window): updated."""
        nonlocal stats, keys
        if (sig := reports.signature(person_id)) == seen["signature"]:
            return
        seen["signature"] = sig
        stats = await load_stats(person_id)
        keys = reports.citation_keys(stats)
        editor.refresh_preview()
        sidebar.refresh()

    ui.timer(2.0, save)
    ui.timer(3.0, reload)
    ui.context.client.on_disconnect(save)


def _paper_row(
    p: reports.Paper,
    times: int,
    ctx: reports.Context,
    editor: MarkdownEditor,
    period_id: int,
    templates: reports.Templates,
) -> None:
    s = p.stat
    colour = (
        "positive"
        if times
        else "grey"
        if not p.in_report
        else "orange"
        if p.off_period
        else "negative"
    )
    with (
        ui.row()
        .classes("w-full items-start no-wrap gap-2 py-1 border-b")
        .classes("opacity-70" if p.off_period else "")
        .props(f'data-report-key="{p.key}"')
    ):
        number = ctx.number(p).replace("*", "") if p.number else "—"
        ui.badge(number, color=colour).classes("cursor-pointer shrink-0 mt-1").on(
            "click", lambda: editor.insert(templates.cite(p.key))
        ).tooltip(
            (f"cited {times}×" if times else "not cited yet")
            + (" · not in the report's papers" if not p.in_report else "")
            + (f" · off-period ({s.year or 'no year'})" if p.off_period else "")
            + " · click: cite it"
        ).mark(f"report-cite-{p.key}")
        with ui.column().classes("grow min-w-0 gap-0"):
            with ui.row().classes("w-full items-start no-wrap gap-1"):
                ui.label(s.title or "(untitled)").classes("text-sm leading-tight grow min-w-0")
                if s.pdf or pdf_viewer.can_download(s):
                    with ui.link(
                        target=pdf_viewer.page_url(s.id, period_id, fetch=not s.pdf),
                        new_tab=True,
                    ).mark(f"report-pdf-{p.key}"):
                        ui.icon(
                            "picture_as_pdf", size="xs", color="red-8" if s.pdf else "grey-6"
                        ).tooltip(
                            "View the PDF, with its notes (a new window)"
                            if s.pdf
                            else "Download the PDF (open access) and view it (a new window)"
                        )
            about = " · ".join(x for x in (s.venue, str(s.year or ""), s.category.label) if x)
            ui.label(f"@{p.key} · {about}").classes("w-full text-xs text-grey ellipsis").tooltip(
                about
            )
            notes = [n.strip() for n in (s.note, s.period_notes.get(period_id)) if n and n.strip()]
            if notes:
                ui.markdown("\n\n".join(notes), extras=NOTE_EXTRAS).classes(
                    "w-full vr-note text-xs text-grey-8 report-note"
                ).mark(f"report-note-{p.key}")
        with ui.button(icon="more_vert").props("flat dense round size=sm"), ui.menu():
            for t in templates.items:
                ui.menu_item(
                    t.label, on_click=lambda a=t.attrs: editor.insert(templates.cite(p.key, a))
                )
