"""The papers of a person's notes within a folder, beside their editor (in place of the
preview): those to discuss, whether the notes cite them, each cited from there (click its
number), the one cited at the cursor highlighted."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .. import annotations, reports
from ..i18n import _
from ..pubview import tag_order
from . import pdf_viewer
from .mdedit import MarkdownEditor
from .tags import draggable, number_edit, reorder, save_number, step
from .theme import NOTE_EXTRAS

_CSS = ".vr-at-cursor { background: rgba(255, 193, 7, 0.25); box-shadow: inset 3px 0 #ffc107; }"

# The citation at the cursor: its paper highlighted in the pane (and scrolled to).
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
  const state = {key: null};
  const mark = (scroll) => {
    const pane = getHtmlElement({PANE});
    if (!pane) return;
    pane.querySelectorAll('.vr-at-cursor').forEach(e => e.classList.remove('vr-at-cursor'));
    if (!state.key) return;
    const row = pane.querySelector(`[data-paper-key="${CSS.escape(state.key)}"]`);
    if (!row) return;
    row.classList.add('vr-at-cursor');
    if (scroll) row.scrollIntoView({block: 'nearest'});
  };
  window["vrPaneMark{PANE}"] = mark;
  let n = 0;
  const t = setInterval(() => {
    const v = getElement({EDITOR})?.editor;
    if (++n > 50) clearInterval(t);
    if (!v) return;
    clearInterval(t);
    const update = () => {
      const pos = v.state.selection.main.head, line = v.state.doc.lineAt(pos);
      const key = keyAt(line.text, pos - line.from);
      if (key === state.key) return;
      state.key = key;
      mark(true);
    };
    for (const e of ['keyup', 'mouseup', 'focus']) v.dom.addEventListener(e, update);
  }, 100);
})()
"""


class PapersPane:
    """The papers to discuss (then those cited but not to discuss), each with its number
    coloured by whether the notes cite it; a search also finds the person's other papers."""

    def __init__(
        self,
        editor: Callable[[], MarkdownEditor | None],
        context: Callable[[], reports.Context],
        templates: Callable[[], reports.Templates],
        period_id: int,
        mark: str = "papers-pane",
        on_numbers: Callable[[], None] | None = None,
    ) -> None:
        """``on_numbers``: the papers' numbers within the folder's numbered tag changed here
        (dragged, moved up / down, typed), the pane then rebuilt by it."""
        self._editor = editor
        self._on_numbers = on_numbers
        self._tag = None  # (the numbered tag, when its papers can be ordered here)
        self._order: list[int] = []  # (its papers, in their order)
        self._context = context
        self._templates = templates
        self.period_id = period_id
        self.mark = mark
        self._cited: dict | None = None
        ui.add_css(_CSS)
        with ui.column().classes("w-full gap-1 no-wrap") as self.box:
            self.search = (
                ui.input(
                    placeholder=_("Find a paper, Enter: cite it"),
                    on_change=lambda: self.refresh(force=True),
                )
                .props("dense outlined clearable")
                .classes("w-full")
                .mark(f"{mark}-search")
            )
            self.search.on("keydown.enter", self._cite_first)
            self.rows = ui.column().classes("w-full gap-0 no-wrap").mark(mark)
        self._first: str | None = None

    def track(self, ed: MarkdownEditor) -> None:
        """Highlight the paper cited at the cursor of ``ed``."""
        ed.editor.client.run_javascript(
            _CURSOR_JS.replace("{EDITOR}", str(ed.editor.id)).replace("{PANE}", str(self.box.id))
        )

    def cite(self, key: str, attrs: str | None = None) -> None:
        if (e := self._editor()) is not None:
            e.insert(self._templates().cite(key, attrs))

    def _cite_first(self) -> None:
        if self._first:
            self.cite(self._first)
            self.search.value = ""

    def refresh(self, force: bool = False) -> None:
        """Rebuilt if the papers cited changed (``force``: anyway, e.g. the papers edited)."""
        ed = self._editor()
        ctx = self._context()
        result = reports.render(ed.value if ed else "", ctx)
        if not force and dict(result.cited) == self._cited:
            return
        self._cited = dict(result.cited)
        self.rows.clear()
        with self.rows:
            self._build(ctx, result)
        self.box.client.run_javascript(f'window["vrPaneMark{self.box.id}"]?.(false)')

    def _build(self, ctx: reports.Context, result: reports.Rendered) -> None:
        st = reports.citation_status(ctx, result.cited)
        cited = result.cited
        words = (self.search.value or "").lower().split()

        def matches(p: reports.Paper) -> bool:
            hay = " ".join([p.key, *p.stat.authors, p.stat.title or "", str(p.stat.year or "")])
            return all(w in hay.lower() for w in words)

        off = [p for p in ctx.discuss if p.off_period]
        tid = ctx.numbered_tag if self._on_numbers and not words else None
        self._tag = next((t for t in annotations.all_tags() if t.id == tid), None)
        self._order = [s.id for s in tag_order(ctx.stats, tid, self.period_id)] if tid else []
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(
                _("{cited} of the {n} papers to discuss cited").format(
                    cited=len(st.discuss) - len(st.missing), n=len(st.discuss)
                )
                if st.discuss
                else _("No paper to discuss (set the folder's numbered tag)")
            ).classes("text-sm " + ("text-negative" if st.missing else "text-positive")).mark(
                f"{self.mark}-count"
            )
            ui.space()
            if st.missing:
                ui.button(
                    _("Cite them"),
                    icon="playlist_add",
                    on_click=lambda: (
                        (e := self._editor())
                        and e.insert_block(
                            "\n".join("- " + self._templates().cite(p.key) for p in st.missing)
                        )
                    ),
                ).props("flat dense no-caps size=sm").tooltip(
                    _("Insert the papers not cited yet, at the cursor (as a list of citations)")
                ).mark(f"{self.mark}-cite-missing")
        if result.unknown:
            ui.label(
                _("Unknown: {keys}").format(keys=", ".join(f"@{k}" for k in result.unknown))
            ).classes("text-sm text-negative")
        for err in result.errors:
            ui.label(err).classes("text-sm text-negative")

        shown = [p for p in st.discuss if matches(p)]
        groups = [
            (None, shown),
            (_("Not of the period's years"), [p for p in off if matches(p)]),
            (_("Cited, but not to discuss"), [p for p in st.outside if matches(p)]),
        ]
        if words:  # (also the person's other papers)
            listed = {p.key for _t, ps in groups for p in ps} | {p.key for p in ctx.discuss}
            listed |= {p.key for p in st.outside}
            others = [
                reports.Paper(s, k, 0, in_report=False)
                for s in ctx.stats
                if not s.hidden and (k := ctx.keys.get(s.id)) and k not in listed
            ]
            groups.append((_("Other papers"), [p for p in others if matches(p)][:20]))
        first = next((p for _t, ps in groups for p in ps), None)
        self._first = first.key if first else None
        for title, papers in groups:
            if title and papers:
                ui.label(title).classes("w-full text-xs text-grey uppercase mt-3")
            for p in papers:
                self._row(p, cited.get(p.key, 0), ctx)

    def _row(self, p: reports.Paper, times: int, ctx: reports.Context) -> None:
        s = p.stat
        to_discuss = any(d.key == p.key for d in ctx.discuss)
        colour = (
            "positive"
            if times
            else "grey"
            if not to_discuss
            else "orange"
            if p.off_period
            else "negative"
        )
        numbered = ctx.by_key.get(p.key)
        number = ctx.number(numbered).replace("*", "") if numbered else "—"
        with (
            ui.row()
            .classes("w-full items-start no-wrap gap-2 py-1 border-b")
            .classes("opacity-70" if p.off_period else "")
            .props(f'data-paper-key="{p.key}"')
            .mark(f"{self.mark}-row-{p.key}")
        ) as row:
            ordered = self._tag is not None and s.id in self._order
            if ordered:
                self._orderable(row, s, ctx)
            tip = " · ".join(
                [_("cited {n}×").format(n=times) if times else _("not cited yet")]
                + ([] if to_discuss else [_("not to discuss")])
                + (
                    [_("off-period ({year})").format(year=s.year or _("no year"))]
                    if p.off_period
                    else []
                )
                + [_("click: cite it")]
                + (
                    [_("Alt-click: set its number within “{tag}”").format(tag=self._tag.name)]
                    if ordered
                    else []
                )
            )

            def badge(_n=None) -> ui.element:
                return (
                    ui.badge(number, color=colour)
                    .classes("cursor-pointer shrink-0 mt-1")
                    .tooltip(tip)
                    .mark(f"{self.mark}-cite-{p.key}")
                )

            if ordered:
                tag = self._tag
                number_edit(
                    s.number_of(tag.id, self.period_id),
                    lambda n: (save_number(s, tag, self.period_id, n), self._numbers_changed()),
                    mark=f"{self.mark}-number-{s.id}",
                    plain=lambda: self.cite(p.key),
                    show=badge,
                )
            else:
                badge().on("click", lambda: self.cite(p.key))
            with ui.column().classes("grow min-w-0 gap-0"):
                with ui.row().classes("w-full items-start no-wrap gap-1"):
                    ui.label(s.title or _("(untitled)")).classes(
                        "text-sm leading-tight grow min-w-0"
                    )
                    if s.pdf or pdf_viewer.can_download(s):
                        with ui.link(
                            target=pdf_viewer.page_url(s.id, self.period_id, fetch=not s.pdf),
                            new_tab=True,
                        ):
                            ui.icon(
                                "picture_as_pdf", size="xs", color="red-8" if s.pdf else "grey-6"
                            ).tooltip(
                                _("View the PDF, with its notes (a new window)")
                                if s.pdf
                                else _("Download the PDF (open access) and view it (a new window)")
                            )
                about = " · ".join(x for x in (s.venue, str(s.year or "")) if x)
                ui.label(f"@{p.key} · {about}").classes("w-full text-xs text-grey ellipsis")
                texts = (s.note, s.period_notes.get(self.period_id))
                notes = [n.strip() for n in texts if n and n.strip()]
                if notes:
                    ui.markdown("\n\n".join(notes), extras=NOTE_EXTRAS).classes(
                        "w-full vr-note text-xs text-grey-8"
                    ).mark(f"{self.mark}-note-{p.key}")
            if ordered:
                with ui.column().classes("gap-0 shrink-0"):
                    i = self._order.index(s.id)
                    up = ui.button(
                        icon="keyboard_arrow_up", on_click=lambda: self._step(ctx, s, -1)
                    )
                    up.props("flat dense round size=xs").tooltip(_("Up")).mark(
                        f"{self.mark}-up-{p.key}"
                    )
                    down = ui.button(
                        icon="keyboard_arrow_down", on_click=lambda: self._step(ctx, s, 1)
                    )
                    down.props("flat dense round size=xs").tooltip(_("Down")).mark(
                        f"{self.mark}-down-{p.key}"
                    )
                    if i == 0:
                        up.disable()
                    if i == len(self._order) - 1:
                        down.disable()
            with ui.button(icon="more_vert").props("flat dense round size=sm"), ui.menu():
                for t in self._templates().items:
                    ui.menu_item(t.label, on_click=lambda a=t.attrs: self.cite(p.key, a))

    # ---- ordering the papers of the numbered tag (their numbers) ----------------------

    def _orderable(self, row: ui.element, s, ctx: reports.Context) -> None:
        """Its row dragged onto another one (its handle)."""
        tag = self._tag

        def dropped(src: int, where: str) -> None:
            if reorder(ctx.stats, tag, self.period_id, src, s.id, where):
                self._numbers_changed()

        draggable(row, s.id, dropped)
        with ui.column().classes("gap-0 items-center shrink-0 mt-1"):
            ui.icon("drag_indicator", color="grey").classes("cursor-move").tooltip(
                _(
                    "Drag onto another paper to reorder them (numbered 1, 2… in the new order "
                    "within “{tag}”)"
                ).format(tag=tag.name)
            )

    def _step(self, ctx: reports.Context, s, delta: int) -> None:
        if step(ctx.stats, self._tag, self.period_id, s.id, delta):
            self._numbers_changed()

    def _numbers_changed(self) -> None:
        if self._on_numbers:
            self._on_numbers()
