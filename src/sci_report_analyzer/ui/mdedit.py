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


class _ModeToggle(ui.toggle):
    """The editor's modes, as icons (their name on hover)."""

    def _update_options(self) -> None:
        super()._update_options()
        hints = getattr(self, "hints", {})
        for option, key in zip(self._props["options"], self._values, strict=True):
            colour, more = hints.get(key, (None, ""))
            title = _(MODES[key]) + (f" · {more}" if more else "")
            option.update(icon=MODE_ICONS[key], label="", attrs={"title": title})
            if colour:
                option.update(textColor=colour, toggleTextColor=colour)

    def hint(self, key: str, colour: str, text: str) -> None:
        """A mode's icon coloured (e.g. the papers: whether the notes cite them), ``text``
        on hover."""
        if getattr(self, "hints", {}).get(key) == (colour, text):
            return
        self.hints = {**getattr(self, "hints", {}), key: (colour, text)}
        self._update_options()
        self.update()


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
// the PDF shown (e.g. a quote's, /doc/2?page=15), its page there; else in a new tab.
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
    else window.open(u.href, '_blank');
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
            with ui.row().classes("w-full items-center gap-0 vr-tools"):
                for icon, tip, kind, args in TOOLS:
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
                self.mode = (
                    _ModeToggle(modes, value=mode, on_change=lambda: self._layout())
                    .props("dense flat size=sm")
                    .tooltip(
                        _("Edit, edit beside the preview, or the preview only")
                        + (f" · {side_tip}" if side_tip else "")
                    )
                )
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
                    ui.markdown(extras=NOTE_EXTRAS)
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
        """The preview and the editor follow each other's scrolling (at the same proportion);
        the scroll caused by following is ignored, else they would chase each other."""
        self.editor.client.run_javascript(
            "(() => { let n = 0; const t = setInterval(() => {"
            f" const v = getElement({self.editor.id})?.editor,"
            f" p = getHtmlElement({self.preview.id});"
            " if (++n > 50) clearInterval(t); if (!v || !p) return; clearInterval(t);"
            " const e = v.scrollDOM; let busy = false;"
            " const frac = (x) => x.scrollTop / Math.max(1, x.scrollHeight - x.clientHeight);"
            " const follow = (from, to) => { if (busy) return; busy = true;"
            " to.scrollTop = frac(from) * (to.scrollHeight - to.clientHeight);"
            " requestAnimationFrame(() => { busy = false; }); };"
            " e.addEventListener('scroll', () => follow(e, p));"
            " p.addEventListener('scroll', () => follow(p, e));"
            " }, 100); })()"
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
        chips."""
        self.preview.content = tags_as_chips(strip_comments(self._render(self.value)))

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
        self.editor.client.run_javascript(
            f"(() => {{ const v = getElement({self.editor.id})?.editor; if (!v) return; "
            f"{body}; v.focus(); }})()"
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
