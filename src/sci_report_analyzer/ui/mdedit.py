"""A Markdown editor (for notes and reports): CodeMirror with Markdown highlighting, a
toolbar (bold, lists, links, LaTeX…), and a rendered preview (beside or instead)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from nicegui import ui

from .. import categories
from ..copy_filters import strip_comments
from ..i18n import N_, _
from ..reports import _BLOCK
from .theme import NOTE_EXTRAS, chip_html

# (icon, tooltip, kind, args): kind "wrap" (before, after, placeholder) or "line" (prefix).
TOOLS = [
    ("format_bold", N_("Bold (⌘/Ctrl-B)"), "wrap", ("**", "**", N_("bold"))),
    ("format_italic", N_("Italic (⌘/Ctrl-I)"), "wrap", ("*", "*", N_("italic"))),
    ("title", N_("Heading"), "line", ("### ",)),
    ("format_list_bulleted", N_("List"), "line", ("- ",)),
    ("format_list_numbered", N_("Numbered list"), "line", ("1. ",)),
    ("format_quote", N_("Quote"), "line", ("> ",)),
    ("code", N_("Code"), "wrap", ("`", "`", N_("code"))),
    ("link", N_("Link"), "wrap", ("[", "](https://)", N_("text"))),
    ("functions", N_("LaTeX ($…$ inline, $$…$$ display)"), "wrap", ("$", "$", "x^2")),
]

MODES = {
    "edit": N_("Edit"),
    "split": N_("Split"),
    "preview": N_("Preview"),
    "side": N_("Papers"),  # (only with a side pane: see MarkdownEditor)
}
MODE_ICONS = {
    "edit": "edit_note",
    "split": "vertical_split",
    "preview": "visibility",
    "side": "checklist",
}


def tool_group() -> ui.row:
    """A group of a toolbar's buttons (of one kind: formatting, quoting, inserting…), framed."""
    return ui.row().classes("items-center no-wrap gap-0 rounded border border-gray-400/40 px-0.5")


class _ModeCycle(ui.button):
    """The editor's modes, as one icon (the mode shown): a click goes to the next one, its
    name and the next one's on hover; a mode's hint (e.g. whether the notes cite the papers):
    a dot of its colour."""

    def __init__(
        self, modes: list[str], value: str, on_change: Callable[[], None], tip: str = ""
    ) -> None:
        super().__init__(on_click=self.next)
        self.modes, self._value, self._on_change, self.tip = modes, value, on_change, tip
        self.hints: dict[str, tuple[str, str]] = {}
        with self:
            self.dot = ui.badge().props("floating rounded").classes("p-1")
            self.tooltip_ = ui.tooltip()
        self._show()

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        if value != self._value:
            self._value = value
            self._show()
            self._on_change()

    def next(self) -> None:
        self.value = self.modes[(self.modes.index(self._value) + 1) % len(self.modes)]

    def _show(self) -> None:
        after = self.modes[(self.modes.index(self._value) + 1) % len(self.modes)]
        lines = [
            _("{mode} · click: {next}").format(mode=_(MODES[self._value]), next=_(MODES[after]))
        ]
        lines += [f"{_(MODES[k])}: {more}" for k, (_c, more) in self.hints.items() if more]
        self.tooltip_.text = " · ".join([*lines, *([self.tip] if self.tip else [])])
        self.props(f"icon={MODE_ICONS[self._value]}")
        colours = [c for c, _m in self.hints.values() if c]
        self.dot.props(f"color={colours[0]}" if colours else "color=transparent")
        self.dot.set_visibility(bool(colours))

    def hint(self, key: str, colour: str, text: str) -> None:
        """A mode's hint (e.g. the papers: whether the notes cite them): a dot of ``colour``
        on the icon, ``text`` on hover."""
        if self.hints.get(key) == (colour, text):
            return
        self.hints = {**self.hints, key: (colour, text)}
        self._show()


def quote(text: str, where: str = "") -> str:
    """``text`` as a Markdown quote (one paragraph: e.g. the lines selected in a PDF), followed
    by ``where`` it is from (e.g. its page), if any."""
    return "> " + " ".join(text.split()) + (f" ({where})" if where else "")


def inline_quote(text: str, where: str = "") -> str:
    """``text`` quoted within a sentence, followed by ``where`` it is from (e.g. a link to its
    page) in an Obsidian comment: ``"text" %% [Application, p. 4](/doc/1?page=4) %%``."""
    return f'"{" ".join(text.split())}"' + (f" %% {where} %%" if where else "")


# The citation key at a place of a line (window.vrCite.keyAt: also the papers pane's).
ui.add_head_html(
    r"""<script>
window.vrCite = {
  KEY: /-?@(\w+(?:[:.#$%&+?<>~\/-]\w+)*)/g,
  // The key at ``col``: in a bracketed citation ([@a; @b], the nearest one), or @key.
  keyAt(line, col) {
    for (const b of line.matchAll(/\[[^\[\]]*\]/g)) {
      if (col < b.index || col > b.index + b[0].length) continue;
      let best = null, dist = Infinity;
      for (const m of b[0].matchAll(this.KEY)) {
        const s = b.index + m.index, e = s + m[0].length;
        const d = col < s ? s - col : col > e ? col - e : 0;
        if (d < dist) { best = m[1]; dist = d; }
      }
      if (best) return best;
    }
    for (const m of line.matchAll(this.KEY)) {
      if (col >= m.index && col <= m.index + m[0].length) return m[1];
    }
    return null;
  },
  // That at a position of an editor's document.
  at(v, pos) {
    const line = v.state.doc.lineAt(pos);
    return this.keyAt(line.text, pos - line.from);
  },
};
// The link at a place of a line ([text](url), <url> or a bare URL), and following one: to
// the PDF shown (e.g. a quote's, /doc/2?page=15), its page there; to another PDF, beside a
// person's page (see vrDock: also from a PDF embedded there); else in a new tab.
window.vrLink = {
  KINDS: [/\[[^\[\]]*\]\(\s*<?([^()\s<>]+)>?(?:\s+"[^"]*")?\s*\)/g,
          /<(https?:\/\/[^\s<>]+)>/g, /https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"]/g],
  at(line, col) {
    for (const kind of this.KINDS) {
      for (const m of line.matchAll(kind)) {
        if (col >= m.index && col <= m.index + m[0].length) return m[1] || m[0];
      }
    }
    return null;
  },
  open(href) {
    const u = new URL(href, location.href);
    if (u.origin !== location.origin) { window.open(u.href, '_blank', 'noopener'); return; }
    const page = parseInt(u.searchParams.get('page'));
    if (u.pathname === location.pathname && page && window.vrPdf) vrPdf.go(page, null);
    else if (!this.dock()?.openUrl(u.href)) window.open(u.href, '_blank');
  },
  dock() {
    try { return window.vrDock || (window.parent !== window ? window.parent.vrDock : null); }
    catch (e) { return null; }
  },
};
// (a link in a rendered note, e.g. the preview: followed so, this page kept)
document.addEventListener('click', (ev) => {
  const a = ev.target.closest && ev.target.closest('.vr-note a[href]');
  if (!a || ev.defaultPrevented || ev.button !== 0) return;
  if (a.getAttribute('href').startsWith('#')) return;
  ev.preventDefault();
  vrLink.open(a.href);
});
// An editor's and its preview's scrolling kept together: at the lines marked in the preview
// (data-vr-line: see line_marks), in proportion in between (a block, one line long in the
// editor, long once rendered: its line kept in the editor while scrolling over it). The pane
// last pointed at (or typed in) leads; the other's scroll (following) is ignored.
window.vrSync = {
  attach(v, p) {
    const e = v.scrollDOM;
    let lead = e;
    // (editor's offset, preview's) of the marks, both increasing; from the tops to the ends
    const pairs = () => {
      const eMax = e.scrollHeight - e.clientHeight, pMax = p.scrollHeight - p.clientHeight;
      if (eMax <= 0 || pMax <= 0) return null;
      const doc = v.state.doc, top = p.getBoundingClientRect().top - p.scrollTop;
      const off = v.documentTop - e.getBoundingClientRect().top + e.scrollTop;
      const out = [[0, 0]];
      for (const m of p.querySelectorAll('[data-vr-line]')) {
        const n = +m.dataset.vrLine;
        const a = off + (n > doc.lines ? v.lineBlockAt(doc.length).bottom
          : v.lineBlockAt(doc.line(Math.max(1, n)).from).top);
        const at = m.tagName === 'DIV' ? m : m.closest('p, li, h1, h2, h3, h4, h5, h6') || m;
        const b = at.getBoundingClientRect().top - top, [a0, b0] = out[out.length - 1];
        if (a > a0 && b >= b0 && a < eMax && b < pMax) out.push([a, b]);
      }
      out.push([eMax, pMax]);
      return out;
    };
    // (the offset x on one side, i, on the other, j)
    const map = (x, pts, i, j) => {
      let k = 1;
      while (k < pts.length - 1 && x > pts[k][i]) k++;
      const [x0, x1, y0, y1] = [pts[k - 1][i], pts[k][i], pts[k - 1][j], pts[k][j]];
      return x1 > x0 ? y0 + (Math.min(x, x1) - x0) / (x1 - x0) * (y1 - y0) : y0;
    };
    const follow = (from) => {
      const pts = from === lead && pairs();
      if (!pts) return;
      const [to, i, j] = from === e ? [p, 0, 1] : [e, 1, 0];
      const y = Math.round(map(from.scrollTop, pts, i, j));
      if (Math.abs(to.scrollTop - y) > 1) to.scrollTop = y;
    };
    for (const x of [e, p]) {
      for (const k of ['pointerenter', 'pointerdown', 'wheel', 'touchstart', 'keydown']) {
        x.addEventListener(k, () => { lead = x; }, {passive: true});
      }
      x.addEventListener('scroll', () => follow(x));
    }
    new MutationObserver(() => follow(e)).observe(p, {childList: true, subtree: true});
  },
};
// The CodeMirror view of an editor (its element's id), once there: e.g. an editor just built,
// not yet in the page (its element awaited, then the view it makes).
window.vrEditor = (id) => new Promise((resolve) => {
  const found = () => {
    const c = getElement(id);
    if (!c) return false;
    if (c.editor) resolve(c.editor); else c.editorPromise.then(resolve);
    return true;
  };
  if (found()) return;
  const o = new MutationObserver(() => { if (found()) o.disconnect(); });
  o.observe(document.body, {childList: true, subtree: true});
  setTimeout(() => o.disconnect(), 30000);  // (never there: e.g. deleted at once)
});
</script>""",
    shared=True,
)

# A #tag (Obsidian's: not "#1", nor a heading's "#"; also right after a tag, e.g. an
# excerpt's anchor), shown as a chip in the preview.
_TAG_RE = re.compile(r"(?<![^\s>])#([\w/-]*[^\W\d][\w/-]*)")
# Code (fenced blocks, inline spans): its #tags kept as typed.
_CODE_RE = re.compile(r"(?m)(^\s*(?:```|~~~).*$(?:.|\n)*?^\s*(?:```|~~~).*$|`[^`\n]*`)")
_TAG_GREY = "#8c959f"  # (a tag without a colour, e.g. a marker's or a level's)


def tags_as_chips(text: str) -> str:
    """The #tags of a Markdown text (but in code) as chips, in their tags' colours."""
    colours = categories.tag_colours()

    def chip(m: re.Match) -> str:
        return chip_html(m[1], colours.get(m[1]) or _TAG_GREY, title=f"#{m[1]}")

    parts = _CODE_RE.split(text)
    return "".join(p if i % 2 else _TAG_RE.sub(chip, p) for i, p in enumerate(parts))


# The marks of the source lines in a preview (see line_marks): after a line, around a block.
_MARK = '<span data-vr-line="{n}"></span>'
_BEFORE = '\n{indent}<div data-vr-line="{n}"></div>\n\n'
_AFTER = '\n\n{indent}<div data-vr-line="{n}" data-vr-end></div>\n'
_MARKS_RE = re.compile(
    r'<span data-vr-line="\d+"></span>|\n[ \t]*<div data-vr-line="\d+"></div>\n\n'
    r'|\n\n[ \t]*<div data-vr-line="\d+" data-vr-end></div>\n'
)
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(?=\S)")
_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
_RULE_RE = re.compile(r"^ {0,3}([-*_=])(?:[ \t]*\1){2,}[ \t]*$")
_PARAGRAPH_RE = re.compile(r"^ {0,3}[^\s|<$]")  # (not a table, HTML, display LaTeX)


def line_marks(text: str) -> str:
    """``text`` with its lines marked, for the preview to follow the editor (see vrSync): a
    heading, a list item, a paragraph's first line: an empty span (with its line, from 1); a
    block (``[[excerpts]]``…: one line, long once rendered): an empty div before it, one after
    it (with the next line). Not in code."""
    out, code, text_before = [], False, False
    for n, line in enumerate(text.split("\n"), 1):
        mark, prose = _MARK.format(n=n), False
        if _FENCE_RE.match(line):
            code = not code
        elif code or not line.strip() or _RULE_RE.match(line):
            pass
        elif _BLOCK.match(line):
            indent = line[: len(line) - len(line.lstrip())]
            before, after = (
                _BEFORE.format(indent=indent, n=n),
                _AFTER.format(indent=indent, n=n + 1),
            )
            line = before + line + after
        elif m := _HEADING_RE.match(line):
            line = m[0] + mark + line[m.end() :]
        else:
            prose = True
            if _ITEM_RE.match(line) or (not text_before and _PARAGRAPH_RE.match(line)):
                line += mark
        out.append(line)
        text_before = prose
    return "\n".join(out)


class _Preview(ui.markdown):
    """The rendered text: shown with its source lines marked (see line_marks), its content
    without them."""

    marked = ""

    def show(self, marked: str) -> None:
        self.marked = marked
        self.content = _MARKS_RE.sub("", marked)
        self._handle_content_change(self.content)  # (the marks moved only: shown too)

    def _handle_content_change(self, content: str) -> None:
        marked = self.marked if _MARKS_RE.sub("", self.marked) == content else content
        super()._handle_content_change(marked)


_SHARE = "; flex:1 1 0; min-height:6rem"  # (a stacked editor and preview, sharing the height)


class MarkdownEditor:
    def __init__(
        self,
        value: str = "",
        *,
        on_change: Callable[[str], None] | None = None,
        render: Callable[[str], str] | None = None,
        mode: str = "edit",
        mark: str | None = None,
        height: str = "10rem",
        keymap: dict | None = None,
        toolbar: Callable[[], None] | None = None,
        stacked: bool = False,
        fill: bool = False,
        side: Callable[[], None] | None = None,
        side_tip: str = "",
    ) -> None:
        """``render``: the Markdown shown in the preview (e.g. with citations substituted);
        ``toolbar``: more buttons, at the toolbar's right; ``stacked``: the preview below the
        editor (else beside it); ``fill``: the height left in its (flex) column, rather than
        ``height``; ``side``: builds a pane shown in place of the preview in the mode "side"
        (``side_tip``: what it shows, e.g. the papers to cite)."""
        self._render = render or (lambda t: t)
        # (set by the note editors saving it: unsaved text typed, and a text saved elsewhere)
        self.is_dirty: Callable[[], bool] = lambda: False
        self.adopt: Callable[[str], None] = lambda text: setattr(self, "value", text)
        self.rebase: Callable[[str], None] = lambda text: None  # (saved elsewhere, as shown)
        self._on_change = on_change
        if fill:
            # (stacked: the editor and the preview share the height; else both as high as it)
            height = "0" if stacked else "100%"
        with ui.column().classes("w-full gap-1" + (" grow min-h-0" if fill else "")) as self.box:
            with ui.row().classes("w-full items-center gap-1 vr-tools"):
                for group in ("wrap", "line"):  # (within the line, then of whole lines)
                    with tool_group():
                        for icon, tip, kind, args in TOOLS:
                            if kind != group:
                                continue
                            ui.button(
                                icon=icon,
                                on_click=lambda kind=kind, args=args: (
                                    self.wrap(*args[:2], _(args[2]))
                                    if kind == "wrap"
                                    else self.prefix_lines(*args)
                                ),
                            ).props("flat dense round size=sm").tooltip(_(tip))
                ui.space()
                if toolbar:
                    toolbar()
                modes = [m for m in MODES if m != "side" or side]
                with tool_group():
                    self.mode = _ModeCycle(
                        modes,
                        mode,
                        self._layout,
                        _("Edit, edit beside the preview, or the preview only")
                        + (f" · {side_tip}" if side_tip else ""),
                    ).props("flat dense round size=sm")
                if mark:
                    self.mode.mark(f"{mark}-mode")
            panes = ui.column().classes("w-full gap-2") if stacked else ui.row()
            with panes.classes(
                "w-full no-wrap gap-2 items-stretch" + (" grow min-h-0" if fill else "")
            ):
                keys = {
                    "Mod-b": lambda: self.wrap("**", "**", _("bold")),
                    "Mod-i": lambda: self.wrap("*", "*", _("italic")),
                }
                self.editor = (
                    ui.codemirror(
                        value or "",
                        language="Markdown",
                        line_wrapping=True,
                        indent="  ",
                        keymap={**keys, **(keymap or {})},
                        on_change=lambda e: self._changed(e.value),
                    )
                    .classes("w-full border rounded")
                    .style(
                        f"height:{height}; font-size:0.875rem"
                        + (_SHARE if fill and stacked else "")
                    )
                )
                if mark:
                    self.editor.mark(mark)
                self.preview = (
                    _Preview(extras=NOTE_EXTRAS)
                    .classes("w-full vr-note overflow-auto border rounded px-3")
                    .style(f"height:{height}" + (_SHARE if fill and stacked else ""))
                )
                self.side: ui.column | None = None
                if side:
                    with (
                        ui.column()
                        .classes("w-full gap-0 no-wrap overflow-auto border rounded px-2")
                        .style(f"height:{height}" + (_SHARE if fill and stacked else ""))
                    ) as self.side:
                        side()
        self.on_mode: list[Callable[[str], None]] = []
        # (set by the note editors citing papers: shows the paper of a key, e.g. ⌘-clicked)
        self.open_cited: Callable[[str], None] | None = None
        self._layout()
        self._sync_scroll()
        self._cite_clicks()

    def _sync_scroll(self) -> None:
        """The preview and the editor follow each other's scrolling, line by line (see
        vrSync)."""
        self.editor.client.run_javascript(
            "(() => { let n = 0; const t = setInterval(() => {"
            f" const v = getElement({self.editor.id})?.editor,"
            f" p = getHtmlElement({self.preview.id});"
            " if (++n > 50) clearInterval(t); if (!v || !p) return; clearInterval(t);"
            " vrSync.attach(v, p); }, 100); })()"
        )

    def _cite_clicks(self) -> None:
        """⌘/Ctrl-click on a citation (``[@key]``): its paper shown (see ``open_cited``); on a
        link: followed (see vrLink); rather than another cursor."""
        ui.on(
            "vr-cite-open",
            lambda e: (
                self.open_cited(e.args["key"])
                if self.open_cited and (e.args or {}).get("id") == self.editor.id
                else None
            ),
        )
        self.editor.client.run_javascript(
            "(() => { let n = 0; const t = setInterval(() => {"
            f" const v = getElement({self.editor.id})?.editor;"
            " if (++n > 50) clearInterval(t); if (!v) return; clearInterval(t);"
            " v.dom.addEventListener('mousedown', (ev) => {"
            " if (!(ev.metaKey || ev.ctrlKey) || ev.button !== 0) return;"
            " const pos = v.posAtCoords({x: ev.clientX, y: ev.clientY}); if (pos == null) return;"
            " const line = v.state.doc.lineAt(pos), href = vrLink.at(line.text, pos - line.from);"
            " if (href) { ev.preventDefault(); ev.stopPropagation(); vrLink.open(href); return; }"
            " const key = vrCite.at(v, pos); if (!key) return;"
            " ev.preventDefault(); ev.stopPropagation();"
            f" emitEvent('vr-cite-open', {{id: {self.editor.id}, key}});"
            " }, true); }, 100); })()"
        )

    async def cited_at_cursor(self) -> str | None:
        """The key of the citation at the cursor, if any."""
        return await self.editor.client.run_javascript(
            f"(() => {{ const v = getElement({self.editor.id})?.editor;"
            " return v ? vrCite.at(v, v.state.selection.main.head) : null; })()"
        )

    @property
    def value(self) -> str:
        return self.editor.value or ""

    @value.setter
    def value(self, text: str) -> None:
        self.editor.value = text

    def _changed(self, text: str) -> None:
        if self.mode.value in ("split", "preview"):
            self.refresh_preview()
        if self._on_change:
            self._on_change(text or "")

    def refresh_preview(self) -> None:
        """The rendered text, without its Obsidian comments (``%% … %%``), its #tags as
        chips; its source lines marked (to scroll along)."""
        self.preview.show(tags_as_chips(strip_comments(self._render(line_marks(self.value)))))

    def _layout(self) -> None:
        mode = self.mode.value or "edit"
        self.editor.set_visibility(mode != "preview")
        self.preview.set_visibility(mode in ("split", "preview"))
        if self.side is not None:
            self.side.set_visibility(mode == "side")
        if mode in ("split", "preview"):
            self.refresh_preview()
        for f in self.on_mode:
            f(mode)

    # ---- Editing at the cursor (in the browser) ----

    def _js(self, body: str) -> None:
        """``body`` run on the editor's view ``v`` (the editor just built: once it is made in
        the browser, see vrEditor)."""
        self.editor.client.run_javascript(
            f"vrEditor({self.editor.id}).then((v) => {{ {body}; v.focus(); }})"
        )

    def insert(self, text: str) -> None:
        """Insert ``text`` at the cursor (replacing the selection)."""
        if self.mode.value == "preview":
            self.mode.value = "split"
        self._js(f"v.dispatch(v.state.replaceSelection({json.dumps(text)}))")

    def insert_inline(self, text: str) -> None:
        """Insert ``text`` at the cursor (replacing the selection), apart from the words around
        it (a space before, unless after an opening bracket; after, unless punctuation follows)."""
        if self.mode.value == "preview":
            self.mode.value = "split"
        self._js(
            "const d = v.state.doc, s = v.state.selection.main; "
            "const b = d.sliceString(Math.max(0, s.from - 1), s.from), "
            "a = d.sliceString(s.to, s.to + 1); "
            f"const t = (b && !/[\\s(\\[]/.test(b) ? ' ' : '') + {json.dumps(text)} "
            "+ (a && !/[\\s.,;:!?)\\]]/.test(a) ? ' ' : ''); "
            "v.dispatch({changes: {from: s.from, to: s.to, insert: t}, "
            "selection: {anchor: s.from + t.length}, scrollIntoView: true})"
        )

    def insert_block(self, text: str) -> None:
        """Insert ``text`` as a paragraph of its own at the cursor (replacing the selection;
        within a line: after it; at the start: at the end, as in an editor never clicked)."""
        if self.mode.value == "preview":
            self.mode.value = "split"
        self._js(
            "const d = v.state.doc, s = v.state.selection.main; "
            "let [from, to] = s.head === 0 && d.length ? [d.length, d.length] : [s.from, s.to]; "
            "const line = d.lineAt(from); "
            "if (from === to && from > line.from) from = to = line.to; "
            "const before = d.sliceString(0, from), after = d.sliceString(to); "
            "const pre = !before || before.endsWith('\\n\\n') ? '' "
            ": before.endsWith('\\n') ? '\\n' : '\\n\\n'; "
            "const post = after.startsWith('\\n\\n') ? '' : after.startsWith('\\n') ? '\\n' "
            ": after ? '\\n\\n' : '\\n'; "
            f"const t = pre + {json.dumps(text)} + post; "
            "v.dispatch({changes: {from, to, insert: t}, selection: {anchor: from + t.length}, "
            "scrollIntoView: true})"
        )

    def wrap(self, before: str, after: str, placeholder: str = "") -> None:
        """Put the selection (else ``placeholder``, selected) between ``before`` and ``after``."""
        b, a, ph = (json.dumps(x) for x in (before, after, placeholder))
        self._js(
            "const s = v.state.selection.main; "
            f"const t = v.state.sliceDoc(s.from, s.to) || {ph}; "
            f"v.dispatch({{changes: {{from: s.from, to: s.to, insert: {b} + t + {a}}}, "
            f"selection: {{anchor: s.from + {b}.length, head: s.from + {b}.length + t.length}}}})"
        )

    def prefix_lines(self, prefix: str) -> None:
        """Start each line of the selection with ``prefix``."""
        self._js(
            "const s = v.state.selection.main, d = v.state.doc; const changes = []; "
            "for (let l = d.lineAt(s.from).number; l <= d.lineAt(s.to).number; l++) "
            f"changes.push({{from: d.line(l).from, insert: {json.dumps(prefix)}}}); "
            "v.dispatch({changes})"
        )

    def focus(self) -> None:
        self._js("")
